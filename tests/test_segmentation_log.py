"""The Stage 2b cut recorder.

The property that matters: a record is only written once the gap to the next
speech is known, because that gap is the only field that separates a breath
from a finished sentence. Everything else in the record is context for it.
"""
import pytest

from segmentation_log import SegmentationLog


class FakeTranscript:
    def __init__(self):
        self.rows = []

    def write(self, **record):
        self.rows.append(record)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make():
    transcript, clock = FakeTranscript(), FakeClock()
    return SegmentationLog(transcript, clock), transcript, clock


def test_nothing_is_written_until_speech_resumes():
    # The gap is the whole point; a record without it answers nothing.
    log, transcript, _ = make()
    log.record_cut("silence", chunk_s=5.8, trailing_silence_s=0.72)
    log.record_text("...to contrast moral character.")
    assert transcript.rows == []


def test_speech_resuming_writes_the_completed_record():
    log, transcript, clock = make()
    log.record_cut("silence", chunk_s=5.8, trailing_silence_s=0.72)
    log.record_text("...to contrast moral character.")
    clock.advance(0.41)
    log.record_speech_resumed()

    assert len(transcript.rows) == 1
    row = transcript.rows[0]
    assert row["kind"] == "cut"
    assert row["reason"] == "silence"
    assert row["chunk_s"] == 5.8
    assert row["trailing_silence_s"] == 0.72
    assert row["looked_complete"] is True
    assert row["text"] == "...to contrast moral character."
    assert row["speech_gap_s"] == pytest.approx(0.41, abs=0.001)


def test_the_internal_cut_timestamp_does_not_reach_the_file():
    log, transcript, clock = make()
    log.record_cut("silence", 2.0, 0.7)
    clock.advance(1.0)
    log.record_speech_resumed()
    assert "cut_at" not in transcript.rows[0]


def test_a_short_gap_and_a_long_gap_are_both_preserved_faithfully():
    # These two populations are exactly what the threshold has to sit between,
    # so rounding or clamping either one would defeat the measurement.
    log, transcript, clock = make()
    log.record_cut("silence", 5.8, 0.7)
    clock.advance(0.28)                      # a breath
    log.record_speech_resumed()
    log.record_cut("silence", 6.4, 0.9)
    clock.advance(4.75)                      # a finished thought
    log.record_speech_resumed()
    assert [r["speech_gap_s"] for r in transcript.rows] == [0.28, 4.75]


def test_a_second_cut_before_speech_resumes_still_writes_the_first():
    # Unusual, but it must not vanish: a null gap is data, a missing row is not.
    log, transcript, _ = make()
    log.record_cut("silence", 2.0, 0.7)
    log.record_text("first")
    log.record_cut("max_buffer", 8.0, 0.0)
    assert len(transcript.rows) == 1
    assert transcript.rows[0]["text"] == "first"
    assert transcript.rows[0]["speech_gap_s"] is None


def test_close_writes_a_record_left_open_by_a_disconnect():
    log, transcript, _ = make()
    log.record_cut("silence", 3.0, 0.8)
    log.record_text("the last thing said")
    log.close()
    assert len(transcript.rows) == 1
    assert transcript.rows[0]["speech_gap_s"] is None
    assert transcript.rows[0]["text"] == "the last thing said"


def test_close_on_an_empty_log_writes_nothing():
    log, transcript, _ = make()
    log.close()
    assert transcript.rows == []


def test_speech_resuming_twice_writes_one_record():
    log, transcript, _ = make()
    log.record_cut("silence", 2.0, 0.7)
    log.record_speech_resumed()
    log.record_speech_resumed()
    assert len(transcript.rows) == 1


def test_text_arriving_with_no_open_cut_is_harmless():
    log, transcript, _ = make()
    log.record_text("orphan")
    assert transcript.rows == []


def test_a_failing_transcript_never_propagates():
    # Same rule as TranscriptLog: instrumentation cannot be allowed to take the
    # service down. A dropped measurement is a nuisance; a dropped service is a
    # congregation reading nothing.
    class Exploding:
        def write(self, **record):
            raise OSError("disk full")

    log = SegmentationLog(Exploding(), FakeClock())
    log.record_cut("silence", 2.0, 0.7)
    log.record_text("x")
    log.record_speech_resumed()
    log.close()


def test_works_without_a_transcript_at_all():
    log = SegmentationLog(None, FakeClock())
    log.record_cut("silence", 2.0, 0.7)
    log.record_speech_resumed()
    log.close()


def test_looked_complete_is_derived_from_the_real_predicate():
    # Terminal punctuation -> the shipping code would have flushed this chunk
    # as a finished sentence. That decision is what the analysis grades.
    log, transcript, _ = make()
    log.record_cut("silence", 5.8, 0.7)
    log.record_text("...the purpose is to contrast moral character.")
    log.record_speech_resumed()
    assert transcript.rows[0]["looked_complete"] is True


def test_looked_complete_is_false_for_a_chunk_cut_mid_word():
    log, transcript, _ = make()
    log.record_cut("max_buffer", 8.0, 0.0)
    log.record_text("The man's ability to un-")
    log.record_speech_resumed()
    assert transcript.rows[0]["looked_complete"] is False


def test_a_trailing_ellipsis_is_not_treated_as_complete():
    # looks_complete() rejects a trail-off specifically; the recorder must
    # inherit that rather than re-deciding with a looser rule.
    log, transcript, _ = make()
    log.record_cut("silence", 4.0, 0.7)
    log.record_text("He's an example of someone who...")
    log.record_speech_resumed()
    assert transcript.rows[0]["looked_complete"] is False
