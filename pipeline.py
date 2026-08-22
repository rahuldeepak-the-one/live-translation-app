"""One utterance through the system: audio -> English -> 3 translations -> screens.

English is published as soon as Whisper produces it, so the screens stay live.
Translation is deferred until the sentence is actually complete — see
config.SENTENCE_END_CHARS for why a fragment must never reach the translator.
"""
import logging
import time

from config import SENTENCE_END_CHARS, MAX_SENTENCE_HOLD_S, MAX_PENDING_CHARS
from translator import split_source_sentences

logger = logging.getLogger(__name__)


# Whisper marks a cut-off utterance with an ellipsis. Its final "." used to
# satisfy looks_complete(), flushing the fragment instantly — which is how
# "The steward's unethical behavior is never..." reached the translator without
# its verb and came back as "it was endorsed" in all three languages.
# A trail-off is the opposite of a closed sentence, so it must be held.
_TRAIL_OFF = ("...", "\u2026")


def looks_complete(text):
    stripped = text.rstrip()
    if stripped.endswith(_TRAIL_OFF):
        return False
    return stripped.endswith(tuple(SENTENCE_END_CHARS))


# Punctuation that carries nothing into a verb-final language. A trailing comma
# or colon marks a clause the target cannot render on its own, and a trailing
# ellipsis reliably seeds a decoder repetition loop — segment 3 of 2026-08-21
# ended "an example of someone who..." and produced 214-230 consecutive full
# stops in all three languages. Terminal .?! are kept: they tell the model the
# sentence is closed.
_DANGLING_TAIL = " \t,;:-\u2013\u2014"


def strip_trail_off(text):
    """Drop trailing ellipses. Used at the join seam and before translating.

    Joining "...is never..." to "endorsed." without this leaves the ellipsis
    mid-sentence, which is no better for the translator than the split was.
    """
    out = text.rstrip()
    while True:
        for tail in _TRAIL_OFF:
            if out.endswith(tail):
                out = out[: -len(tail)].rstrip()
                break
        else:
            return out


def drop_partial_word(text):
    """Remove a word Whisper cut at a chunk edge ("...ability to un-").

    The next chunk re-transcribes the whole word, so concatenating produces
    "un- understand". Dropping the stub yields the real sentence. Only a
    trailing hyphen counts — an internal one ("self-serving") is a real word.
    """
    out = text.rstrip()
    if not out.endswith("-"):
        return out
    head, _, _tail = out.rpartition(" ")
    return head.rstrip() if head else ""


def clean_for_translation(text):
    """Trim meaningless trailing punctuation on the way to the translator.

    Applied to the translator's input only. The screens and the transcript keep
    the raw English, so what a reader sees is still what Whisper heard.
    """
    out = strip_trail_off(text)
    while True:
        before = out
        out = strip_trail_off(out.rstrip(_DANGLING_TAIL))
        if out == before:
            return out


class UtterancePipeline:
    def __init__(self, stt, translator, hub, transcript=None, clock=time.time,
                 on_chunk_text=None):
        self.stt = stt
        self.translator = translator
        self.hub = hub
        self.transcript = transcript
        # Stage 2b instrumentation. Reports the RAW text of each chunk, which
        # process() otherwise swallows into the joined pending sentence. Ground
        # truth for a bad boundary is whether the next chunk starts lowercase,
        # and only the per-chunk text can answer that. A callback rather than a
        # changed return shape, so no existing caller moves.
        self._on_chunk_text = on_chunk_text
        self._clock = clock
        self._counter = 0
        self._pending = None       # {"id", "text", "touched"} or None
        # The previous flushed sentence, handed to the translator so it has
        # something to be consistent with. Cleared on silence: a term chosen
        # before the speaker changed passage must not propagate all service.
        self._context = ""

    async def process(self, audio_np):
        t0 = self._clock()
        text = await self.stt.transcribe(audio_np)
        if not text:
            return None
        t_stt = self._clock() - t0

        if self._on_chunk_text is not None:
            try:
                self._on_chunk_text(text)
            except Exception:
                # Instrumentation must never cost an utterance — same rule the
                # transcript log follows. A dropped measurement is a nuisance;
                # a dropped sentence is a congregation reading nothing.
                logger.exception("on_chunk_text failed — continuing without it")

        # Speech arriving after the hold expired belongs to a new sentence, not
        # the held one: that much silence means the speaker moved on, and
        # welding across it invents sentences nobody said. Normally the mic
        # socket's flush_if_stale() has already cleared this on a quiet chunk.
        if (self._pending is not None
                and self._clock() - self._pending["touched"] >= MAX_SENTENCE_HOLD_S):
            stale_id, stale_text = self._pending["id"], self._pending["text"]
            self._pending = None
            logger.info("#%d flushed on new speech after silence", stale_id)
            await self._flush(stale_id, stale_text, 0.0)
            self._context = ""

        if self._pending is None:
            self._counter += 1
            sid = self._counter
            joined = text
        else:
            sid = self._pending["id"]
            # The pending text's ellipsis marked "cut off here" — once the
            # continuation arrives it is stale, and leaving it mid-sentence is
            # as bad for the translator as the split was.
            joined = (
                f"{drop_partial_word(strip_trail_off(self._pending['text']))} {text}"
            ).strip()
        # The hold clock measures SILENCE, not sentence age: it exists for a
        # speaker who stopped mid-sentence, so fresh speech resets it. Timing it
        # from the sentence's start flushed sentences that spanned three chunks
        # while the speaker was still going. MAX_PENDING_CHARS bounds the length.
        touched = self._clock()

        # Stage 2: the published unit is a SENTENCE, not a chunk. A chunk ran a
        # median of 3 sentences and 136 chars on 2026-08-21, so a lane advanced
        # in jumps rather than flowing, and `final` was coarse — a three-sentence
        # chunk stayed grey until its last sentence completed, showing text that
        # would never change as though it might.
        #
        # The FIRST sentence inherits `sid`, because it is the continuation of
        # whatever was pending; later ones take fresh ids. That is what keeps a
        # held sentence revising in place instead of duplicating under a new id.
        parts = split_source_sentences(joined) or [joined]
        # Time is deliberately absent from this decision. Silence is what ends
        # a held sentence, and flush_if_stale() is where it is measured; the
        # `held_for >= MAX_SENTENCE_HOLD_S` disjunct that used to sit here was
        # computed from two clock reads with nothing between them, so it was
        # always ~0 -- and had it measured anything it would have measured
        # sentence AGE, which is what the 2026-08-21 audit removed.
        tail_complete = (looks_complete(parts[-1])
                         or len(parts[-1]) >= MAX_PENDING_CHARS)

        numbered = []
        for i, part in enumerate(parts):
            if i == 0:
                part_id = sid
            else:
                self._counter += 1
                part_id = self._counter
            numbered.append((part_id, part))

        # English first, always — this is what keeps the captions feeling live.
        # Re-publishing an id revises that row rather than duplicating it.
        for i, (part_id, part) in enumerate(numbered):
            is_last = i == len(numbered) - 1
            await self.hub.publish_sentence(
                part_id, part, final=(tail_complete or not is_last))

        complete = numbered if tail_complete else numbered[:-1]
        if not tail_complete:
            pending_id, pending_text = numbered[-1]
            self._pending = {"id": pending_id, "text": pending_text,
                             "touched": touched}
            logger.debug("#%d held (%d chars): %s",
                         pending_id, len(pending_text), pending_text)
        else:
            self._pending = None

        if complete:
            # ONE translator call for every complete sentence in this chunk.
            # Per-sentence publishing must not become a GPU round trip per
            # sentence: the batched call ran mt median 0.8s for a whole chunk,
            # and serialising three behind the GPU lock would undo that.
            await self._flush_many(complete, t_stt)

        last_id, last_text = numbered[-1]
        return last_id, last_text

    async def flush_if_stale(self):
        """Translate a held sentence the speaker never finished.

        process() only runs when speech arrives, so without this the last
        sentence before a long pause — or before the mic goes quiet for good —
        would sit on screen in English and never be translated. The mic socket
        calls this on every chunk that doesn't trigger processing.

        The clock runs from the last chunk that extended the sentence, so this
        fires on silence rather than on a long sentence.
        """
        if self._pending is None:
            return None
        if self._clock() - self._pending["touched"] < MAX_SENTENCE_HOLD_S:
            return None
        sid, joined = self._pending["id"], self._pending["text"]
        self._pending = None
        logger.info("#%d flushed unfinished after %.1fs", sid, MAX_SENTENCE_HOLD_S)
        result = await self._flush(sid, joined, 0.0)
        self._context = ""      # the speaker stopped; the next line is new
        return result[0]

    async def _flush_many(self, numbered, t_stt):
        """Translate and publish several complete sentences in ONE call.

        The batching is the point. Publishing per sentence is a presentation
        change; it must not turn a chunk's single batched generate into one GPU
        round trip per sentence, because the GPU lock serialises them and the
        latency would stack (mt median 0.8s per chunk on 2026-08-21).
        """
        t1 = self._clock()
        cleaned = [clean_for_translation(text) for _sid, text in numbered]
        results = await self.translator.translate_sentences(
            cleaned, context=self._context)
        t_mt = self._clock() - t1

        if cleaned:
            self._context = cleaned[-1]

        for (sid, raw), source, translations in zip(numbered, cleaned, results):
            await self.hub.publish_translation(sid, translations)
            if self.transcript is not None:
                self.transcript.write(
                    id=sid, en=raw, translations=translations,
                    stt_s=round(t_stt, 3), mt_s=round(t_mt, 3),
                )
            logger.info("#%d stt=%.2fs mt=%.2fs: %s", sid, t_stt, t_mt, raw)

    async def _flush(self, sid, joined, t_stt):
        self._pending = None
        t1 = self._clock()
        cleaned = clean_for_translation(joined)
        translations = await self.translator.translate_all(
            cleaned, context=self._context
        )
        t_mt = self._clock() - t1
        self._context = cleaned
        await self.hub.publish_translation(sid, translations)

        if self.transcript is not None:
            self.transcript.write(
                id=sid, en=joined, translations=translations,
                stt_s=round(t_stt, 3), mt_s=round(t_mt, 3),
            )
        logger.info("#%d stt=%.2fs mt=%.2fs: %s", sid, t_stt, t_mt, joined)
        return sid, joined
