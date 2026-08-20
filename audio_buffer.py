"""Rolling audio buffer with trailing-silence detection (M1 chunked engine)."""
import numpy as np

from config import (
    SAMPLE_RATE, SILENCE_THRESHOLD, SILENCE_DURATION_S,
    MAX_BUFFER_S, MIN_SPEECH_S, MIN_TRIGGER_S, SPEECH_WINDOW_S,
)


class AudioBuffer:
    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.buffer = bytearray()

    def add_chunk(self, pcm_bytes):
        self.buffer.extend(pcm_bytes)

    def duration_seconds(self):
        return len(self.buffer) / (self.sample_rate * 2)  # int16 = 2 bytes

    def _window_rms(self):
        """RMS of each SPEECH_WINDOW_S slice of the buffer."""
        samples = np.frombuffer(bytes(self.buffer), dtype=np.int16).astype(np.float32)
        step = max(1, int(SPEECH_WINDOW_S * self.sample_rate))
        n = (len(samples) // step) * step
        if n == 0:
            return np.array([], dtype=np.float32)
        windows = samples[:n].reshape(-1, step)
        return np.sqrt(np.mean(windows ** 2, axis=1))

    def has_speech(self):
        """True if any window in the buffer is louder than SILENCE_THRESHOLD.

        Whisper hallucinates confident text ("Thank you.", "Thanks for
        watching!") when handed silence or room hiss, so a buffer that never
        rises above the threshold must never reach it. Checked per-window
        rather than over the whole buffer because a short sentence inside a
        long silence averages down below the threshold.
        """
        rms = self._window_rms()
        return bool(rms.size and rms.max() >= SILENCE_THRESHOLD)

    def has_trailing_silence(self):
        check_bytes = int(SILENCE_DURATION_S * self.sample_rate) * 2
        if len(self.buffer) < check_bytes:
            return False
        tail = np.frombuffer(bytes(self.buffer[-check_bytes:]), dtype=np.int16)
        rms = np.sqrt(np.mean(tail.astype(np.float32) ** 2))
        return rms < SILENCE_THRESHOLD

    def should_process(self):
        duration = self.duration_seconds()
        if duration < MIN_SPEECH_S:
            return False
        if not self.has_speech():
            # Nothing but silence/hiss — drop it rather than force-flushing at
            # MAX_BUFFER_S, which is what produced the phantom captions.
            return False
        if duration >= MAX_BUFFER_S:
            return True
        return bool(duration >= MIN_TRIGGER_S and self.has_trailing_silence())

    def get_audio_and_clear(self):
        audio = np.frombuffer(bytes(self.buffer), dtype=np.int16)
        self.buffer.clear()
        return audio

    def clear(self):
        self.buffer.clear()
