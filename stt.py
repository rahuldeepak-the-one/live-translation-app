"""Speech-to-text on faster-whisper (large-v3-turbo, language detected per chunk)."""
import asyncio
import logging
import re

import numpy as np
import torch

from config import (
    WHISPER_MODEL, SOURCE_LANG, SOURCE_LANGS, LANG_SWITCH_MIN_PROB, NO_SPEECH_PROB_MAX, AVG_LOGPROB_MIN,
    COMPRESSION_RATIO_MAX, HALLUCINATION_PHRASES, WHISPER_INITIAL_PROMPT,
    WHISPER_HOTWORDS, FILLER_TOKENS, WHISPER_VAD_FILTER, SILENCE_ARTIFACTS,
)

logger = logging.getLogger(__name__)

# Both are whole-segment-only tests: a segment made of nothing but these is
# noise, but either word inside a real sentence is just a word.
_WHOLE_SEGMENT_NOISE = FILLER_TOKENS | SILENCE_ARTIFACTS

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_BRACKETED_ONLY = re.compile(r"^[\s\[\(\*♪♫]*[^\]\)]*[\]\)\*♪♫\s.]*$")


def _normalize(text):
    """Lowercase, strip punctuation, collapse whitespace — for blocklist match."""
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())



def _hotwords():
    """The hotword list, but only when nothing else is claiming the prompt.

    Silently dropping one of two conflicting settings is normally a bad habit;
    here the alternative is worse. The conflict is invisible (no exception, no
    warning — just captions that read "up up up up up up"), and it cost a live
    service to find. A loud log line and a working decode beats a faithful
    reproduction of a misconfiguration.
    """
    if WHISPER_HOTWORDS and WHISPER_INITIAL_PROMPT:
        logger.warning(
            "Both WHISPER_HOTWORDS and WHISPER_INITIAL_PROMPT are set; sending "
            "only the prompt. Together they overflow Whisper's prompt budget "
            "and the decoder repeats itself — see config.py.")
        return None
    return WHISPER_HOTWORDS or None


def _field(segment, name, default):
    """Read from either a faster-whisper Segment or a plain dict."""
    if isinstance(segment, dict):
        return segment.get(name, default)
    return getattr(segment, name, default)


def keep_segment(text, no_speech_prob, avg_logprob, compression_ratio):
    """False when this segment looks like a Whisper hallucination.

    Five independent signals, cheapest first:
      * no_speech_prob   — Whisper's own verdict that there was no speech
      * avg_logprob      — low confidence, i.e. the mangled-proper-noun case
      * compression_ratio— high means the text repeats ("eh, eh, eh, eh")
      * all-filler       — the whole segment is a grunt, nothing to translate
      * blocklist        — YouTube outros baked into the training data
    """
    stripped = text.strip()
    if not stripped:
        return False
    if no_speech_prob > NO_SPEECH_PROB_MAX:
        return False
    if avg_logprob < AVG_LOGPROB_MIN:
        return False
    if compression_ratio > COMPRESSION_RATIO_MAX:
        return False
    normalized = _normalize(stripped)
    if not normalized:  # e.g. "[Music]" reduced to nothing, or bare "♪♪"
        return False
    # Whole-segment filler only. "Uh, turn with me to Ephesians" keeps its false
    # start; "Ugh." has nothing in it worth putting on three screens.
    if all(word in _WHOLE_SEGMENT_NOISE for word in normalized.split()):
        return False
    return not any(
        normalized == phrase or normalized.startswith(phrase)
        for phrase in HALLUCINATION_PHRASES
    )


def strip_hallucinations(segments):
    """Join the segments that survive keep_segment() into one utterance."""
    kept = []
    for segment in segments:
        text = _field(segment, "text", "")
        if keep_segment(
            text,
            _field(segment, "no_speech_prob", 0.0),
            _field(segment, "avg_logprob", 0.0),
            _field(segment, "compression_ratio", 1.0),
        ):
            kept.append(text.strip())
        elif text.strip():
            logger.debug("Filtered probable hallucination: %r", text.strip())
    return " ".join(kept).strip()


def choose_language(probs, allowed=SOURCE_LANGS, switch_min=LANG_SWITCH_MIN_PROB):
    """The chunk's language, from detect_language()'s [(lang, prob), ...].

    English unless another allowed language clears switch_min on its own:
    Indian-accented English is the main input and Whisper can read it as
    Hindi, and a wrong switch costs the whole chunk its translations.
    """
    best, p = max(((lang, prob) for lang, prob in probs
                   if lang in allowed and lang != SOURCE_LANG),
                  key=lambda lp: lp[1], default=(SOURCE_LANG, 0.0))
    return best if p >= switch_min else SOURCE_LANG


class WhisperSTT:
    def __init__(self, model_size=WHISPER_MODEL):
        from faster_whisper import WhisperModel
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        logger.info("Loading Whisper %s on %s (%s)...", model_size, device, compute_type)
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._lock = asyncio.Lock()
        logger.info("Whisper loaded.")

    async def transcribe(self, audio_np):
        """(text, lang) for one chunk."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._transcribe_sync, audio_np)

    def _transcribe_sync(self, audio_np):
        if audio_np.dtype == np.int16:
            audio_np = audio_np.astype(np.float32) / 32768.0
        lang = SOURCE_LANG
        if len(SOURCE_LANGS) > 1:
            _top, _p, probs = self.model.detect_language(audio_np)
            lang = choose_language(probs)
            logger.debug("lang=%s top=%s %.2f", lang, _top, _p)
        english = lang == SOURCE_LANG
        segments, _info = self.model.transcribe(
            audio_np,
            language=lang,
            vad_filter=WHISPER_VAD_FILTER,
            vad_parameters=(dict(min_silence_duration_ms=500)
                            if WHISPER_VAD_FILTER else None),
            # Each chunk is an independent transcribe() call, so carrying text
            # across them only lets one hallucination seed the next. Turning it
            # off does NOT discard initial_prompt: prompt_reset_since starts at
            # 0, so the first segment of every call still sees the prompt.
            # Must stay off for the multilingual model too: conditioning on a
            # previous segment in another language degrades mixed chunks.
            condition_on_previous_text=False,
            # The prompt and hotwords are English sermon vocabulary; on a
            # Malayalam chunk they only pull the decoder toward English.
            initial_prompt=WHISPER_INITIAL_PROMPT if english else None,
            # NEVER both — see the measurement on WHISPER_HOTWORDS in config.
            # faster-whisper folds hotwords and initial_prompt into one decoder
            # prompt; past ~224 tokens it truncates and the decoder loops, at a
            # measured 42 stutters per 100 words. Either alone scored zero.
            hotwords=_hotwords() if english else None,
        )
        return strip_hallucinations(segments), lang
