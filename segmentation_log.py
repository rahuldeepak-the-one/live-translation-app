"""Stage 2b instrumentation: one record per audio cut, for choosing a threshold.

WHY THIS EXISTS. 15 of 37 utterances on 2026-08-21 opened with an orphaned
sentence-tail — "...to contrast moral character." / "with practical foresight."
— because SILENCE_DURATION_S is 0.6s and 600ms is a breath, not a sentence
ending. The speaker inhales, the buffer cuts, Whisper sees audio stopping and
adds a confident full stop, and looks_complete() believes it.

The fix is a grace hold: do not flush a terminated sentence immediately, wait
SENTENCE_GRACE_S to see whether speech resumes. That value MUST NOT be guessed.
MAX_SENTENCE_HOLD_S was guessed at 4.0s once and split 26 of 28 sentences.

WHAT TO MEASURE, AND WHY IT IS NOT THE OBVIOUS THING. should_process() fires the
instant 0.6s of silence exists, so the silence present at a cut is always
~0.6-0.9s — it cannot separate a breath from a finished thought. The
discriminator is measured AFTER the cut: how long until speech resumes. A
breath resumes fast; a finished sentence resumes slowly. SENTENCE_GRACE_S is
the value that sits between those two populations.

So a record is only complete once the NEXT speech arrives, which is why this
class holds a pending record rather than writing at cut time.

HOW TO ANALYSE THE RESULT. Every record where `looked_complete` is true was
treated as a sentence ending. For each, check whether the following record's
`text` begins lowercase — Whisper's capitalisation marks continuations, and all
24 continuation chunks in the 2026-08-21 session began lowercase. Those are the
wrong cuts. Plot their `speech_gap_s` against the right ones and pick the
threshold from where the populations separate.

This module only records. It changes no cutting behaviour, so the captured data
describes the system as it runs today — which is the baseline the change will be
measured against.
"""
import logging

from pipeline import looks_complete

logger = logging.getLogger(__name__)


class SegmentationLog:
    """Accumulates one cut record until the gap to the next speech is known.

    Best-effort throughout: instrumentation must never cost an utterance, so
    every public method swallows its own failures the way TranscriptLog does.
    """

    def __init__(self, transcript, clock):
        self._transcript = transcript
        self._clock = clock
        self._pending = None

    def record_cut(self, reason, chunk_s, trailing_silence_s):
        """A cut just happened. Flush any previous record and open a new one.

        A previous record still open here means speech never resumed before the
        next cut — unusual, but it must still reach the file, with a null gap
        rather than being silently dropped.
        """
        try:
            self._flush(None)
            self._pending = {
                "kind": "cut",
                "reason": reason,
                "chunk_s": round(chunk_s, 3),
                "trailing_silence_s": round(trailing_silence_s, 3),
                "looked_complete": None,
                "text": None,
                "cut_at": self._clock(),
            }
        except Exception:
            logger.exception("record_cut failed — continuing without it")

    def record_text(self, text):
        """Attach the raw transcription of the chunk that was just cut.

        `looked_complete` is computed here with the REAL predicate rather than
        being passed in, because the question the analysis asks is precisely
        "what did the shipping code decide about this chunk, and was it right".
        Recomputing it with anything else would measure the wrong thing.
        """
        try:
            if self._pending is not None:
                self._pending["text"] = text
                self._pending["looked_complete"] = looks_complete(text)
        except Exception:
            logger.exception("record_text failed — continuing without it")

    def record_speech_resumed(self):
        """Speech is audible again. This completes and writes the open record."""
        try:
            if self._pending is None:
                return
            self._flush(round(self._clock() - self._pending["cut_at"], 3))
        except Exception:
            logger.exception("record_speech_resumed failed — continuing without it")

    def close(self):
        """Mic disconnected. Write whatever is still open, with a null gap."""
        try:
            self._flush(None)
        except Exception:
            logger.exception("close failed — continuing without it")

    def _flush(self, speech_gap_s):
        record = self._pending
        self._pending = None
        if record is None or self._transcript is None:
            return
        record.pop("cut_at", None)
        self._transcript.write(speech_gap_s=speech_gap_s, **record)
