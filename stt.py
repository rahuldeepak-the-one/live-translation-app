"""Speech-to-text on faster-whisper (distil-large-v3, English-only)."""
import asyncio
import logging
import re

import numpy as np
import torch

from config import (
    WHISPER_MODEL, SOURCE_LANG, NO_SPEECH_PROB_MAX, AVG_LOGPROB_MIN,
    COMPRESSION_RATIO_MAX, HALLUCINATION_PHRASES, WHISPER_INITIAL_PROMPT,
    WHISPER_HOTWORDS, FILLER_TOKENS,
)

logger = logging.getLogger(__name__)

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_BRACKETED_ONLY = re.compile(r"^[\s\[\(\*♪♫]*[^\]\)]*[\]\)\*♪♫\s.]*$")


def _normalize(text):
    """Lowercase, strip punctuation, collapse whitespace — for blocklist match."""
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


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
    if all(word in FILLER_TOKENS for word in normalized.split()):
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
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._transcribe_sync, audio_np)

    def _transcribe_sync(self, audio_np):
        if audio_np.dtype == np.int16:
            audio_np = audio_np.astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            audio_np,
            language=SOURCE_LANG,   # distil-large-v3 is English-only
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            # Each chunk is an independent transcribe() call, so carrying text
            # across them only lets one hallucination seed the next. Turning it
            # off does NOT discard initial_prompt: prompt_reset_since starts at
            # 0, so the first segment of every call still sees the prompt.
            condition_on_previous_text=False,
            initial_prompt=WHISPER_INITIAL_PROMPT,
            hotwords=WHISPER_HOTWORDS,
        )
        return strip_hallucinations(segments)
