"""One utterance through the system: audio -> English -> 3 translations -> screens.

English is published as soon as Whisper produces it, so the screens stay live.
Translation is deferred until the sentence is actually complete — see
config.SENTENCE_END_CHARS for why a fragment must never reach the translator.
"""
import logging
import time

from config import SENTENCE_END_CHARS, MAX_SENTENCE_HOLD_S, MAX_PENDING_CHARS

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
    def __init__(self, stt, translator, hub, transcript=None, clock=time.time):
        self.stt = stt
        self.translator = translator
        self.hub = hub
        self.transcript = transcript
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

        held_for = self._clock() - touched
        flush = (
            looks_complete(joined)
            or held_for >= MAX_SENTENCE_HOLD_S
            or len(joined) >= MAX_PENDING_CHARS
        )

        # English first, always — this is what keeps the captions feeling live.
        # Re-publishing the same id revises the row rather than duplicating it.
        # `final` is exactly the flush decision: a held sentence is still
        # growing and renders grey, a flushed one is frozen and renders solid.
        await self.hub.publish_sentence(sid, joined, final=flush)

        if not flush:
            self._pending = {"id": sid, "text": joined, "touched": touched}
            logger.debug("#%d held (%.1fs, %d chars): %s", sid, held_for, len(joined), joined)
            return sid, joined

        return await self._flush(sid, joined, t_stt)

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
