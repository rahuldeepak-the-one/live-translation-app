"""Rolling audio buffer with trailing-silence detection (M1 chunked engine)."""
import numpy as np

from config import (
    SAMPLE_RATE, SILENCE_THRESHOLD, SILENCE_DURATION_S,
    MAX_BUFFER_S, MIN_SPEECH_S, MIN_TRIGGER_S,
)


class AudioBuffer:
    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.buffer = bytearray()

    def add_chunk(self, pcm_bytes):
        self.buffer.extend(pcm_bytes)

    def duration_seconds(self):
        return len(self.buffer) / (self.sample_rate * 2)  # int16 = 2 bytes

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
        if duration >= MAX_BUFFER_S:
            return True
        return bool(duration >= MIN_TRIGGER_S and self.has_trailing_silence())

    def get_audio_and_clear(self):
        audio = np.frombuffer(bytes(self.buffer), dtype=np.int16)
        self.buffer.clear()
        return audio

    def clear(self):
        self.buffer.clear()
