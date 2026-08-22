import numpy as np
from audio_buffer import AudioBuffer
from config import SAMPLE_RATE, MAX_BUFFER_S


def loud(seconds):
    """Loud int16 noise (well above SILENCE_THRESHOLD)."""
    n = int(seconds * SAMPLE_RATE)
    rng = np.random.default_rng(42)
    return (rng.uniform(-0.5, 0.5, n) * 20000).astype(np.int16).tobytes()


def silence(seconds):
    n = int(seconds * SAMPLE_RATE)
    return np.zeros(n, dtype=np.int16).tobytes()


def test_empty_buffer_not_processed():
    buf = AudioBuffer(SAMPLE_RATE)
    assert buf.should_process() is False


def test_short_speech_not_processed():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(0.3))
    assert buf.should_process() is False


def test_speech_then_trailing_silence_triggers():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(2.0))
    buf.add_chunk(silence(0.7))
    assert buf.should_process() is True


def test_continuous_speech_no_silence_waits():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(3.0))
    assert buf.should_process() is False


def test_max_buffer_forces_processing():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(8.5))
    assert buf.should_process() is True


def test_get_audio_and_clear():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(2.0))
    audio = buf.get_audio_and_clear()
    assert audio.dtype == np.int16
    assert len(audio) == 2 * SAMPLE_RATE
    assert buf.duration_seconds() == 0.0


def test_pure_silence_is_never_processed():
    """Silence must not reach Whisper — it hallucinates ("Thank you.") on it."""
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(silence(2.0))
    assert buf.should_process() is False


def test_long_silence_does_not_force_processing():
    """MAX_BUFFER_S force-flush must still require actual speech in the buffer."""
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(silence(9.0))
    assert buf.should_process() is False


def test_room_noise_below_threshold_is_not_speech():
    buf = AudioBuffer(SAMPLE_RATE)
    n = int(2.0 * SAMPLE_RATE)
    rng = np.random.default_rng(7)
    hiss = (rng.uniform(-0.5, 0.5, n) * 200).astype(np.int16).tobytes()
    buf.add_chunk(hiss)
    assert buf.should_process() is False


def test_brief_speech_inside_long_silence_is_processed():
    """A short sentence surrounded by silence must still get through."""
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(silence(1.0))
    buf.add_chunk(loud(0.8))
    buf.add_chunk(silence(0.7))
    assert buf.should_process() is True


# --- Stage 2b instrumentation -------------------------------------------------
# These exist to capture WHY a chunk was cut and how much silence was really at
# its tail, so SENTENCE_GRACE_S can be chosen from a measured distribution
# rather than guessed. MAX_SENTENCE_HOLD_S was guessed at 4.0s once and split
# 26 of 28 sentences; this is the machinery that stops that repeating.


def test_cut_reason_is_none_when_the_buffer_should_not_be_processed():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(0.3))
    assert buf.cut_reason() is None


def test_cut_reason_names_the_silence_branch():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(2.0))
    buf.add_chunk(silence(1.0))
    assert buf.cut_reason() == "silence"


def test_cut_reason_names_the_forced_branch():
    # Continuous speech past MAX_BUFFER_S with no trailing pause at all: the
    # speaker never stopped, so the cut lands mid-word. These are the SAFE
    # cuts — they end unterminated and get joined.
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(MAX_BUFFER_S + 0.5))
    assert buf.cut_reason() == "max_buffer"


def test_cut_reason_agrees_with_should_process():
    for chunks in ([loud(0.3)], [loud(2.0), silence(1.0)], [loud(MAX_BUFFER_S + 0.5)]):
        buf = AudioBuffer(SAMPLE_RATE)
        for c in chunks:
            buf.add_chunk(c)
        assert (buf.cut_reason() is not None) is buf.should_process()


def test_trailing_silence_is_measured_not_thresholded():
    # has_trailing_silence() only answers yes/no at SILENCE_DURATION_S. For the
    # analysis we need the real length, because a breath and a finished thought
    # both clear that threshold.
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(1.0))
    buf.add_chunk(silence(1.5))
    measured = buf.trailing_silence_seconds()
    assert 1.3 <= measured <= 1.6, measured


def test_trailing_silence_is_zero_when_speech_runs_to_the_edge():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(2.0))
    assert buf.trailing_silence_seconds() < 0.2


def test_trailing_silence_of_an_empty_buffer_is_zero():
    assert AudioBuffer(SAMPLE_RATE).trailing_silence_seconds() == 0.0


def test_trailing_silence_stops_at_the_last_speech_not_the_buffer_start():
    # Silence, speech, silence: only the FINAL run counts.
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(silence(2.0))
    buf.add_chunk(loud(1.0))
    buf.add_chunk(silence(0.8))
    measured = buf.trailing_silence_seconds()
    assert 0.6 <= measured <= 0.95, measured
