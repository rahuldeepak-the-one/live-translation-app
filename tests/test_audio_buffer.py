import numpy as np
from audio_buffer import AudioBuffer
from config import SAMPLE_RATE


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
