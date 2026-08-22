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

    def trailing_silence_seconds(self):
        """How much silence is actually at the tail, not merely whether 0.6s is.

        has_trailing_silence() answers a yes/no at SILENCE_DURATION_S, and
        should_process() fires the instant that becomes true — so the silence
        present at a cut is always ~0.6-0.9s and tells us nothing. A breath and
        a finished thought both clear that bar. Measuring the real length is
        step one of choosing SENTENCE_GRACE_S from evidence instead of guessing
        it, which is how MAX_SENTENCE_HOLD_S came to be 4.0s and split 26 of 28
        sentences on 2026-08-21.

        Walks back from the end while each window is below the threshold, so
        earlier silence in the buffer is not counted — only the final run.
        """
        rms = self._window_rms()
        if not rms.size:
            return 0.0
        quiet = 0
        for value in rms[::-1]:
            if value >= SILENCE_THRESHOLD:
                break
            quiet += 1
        return quiet * SPEECH_WINDOW_S

    def cut_reason(self):
        """Why this buffer is being cut, or None if it is not.

        Names the branch of should_process() that fires, in the same order, so
        the label describes what the code actually did. "silence" means the
        speaker paused; "max_buffer" means they never did and the cut lands
        mid-word. That distinction is the whole point of the instrumentation:
        forced cuts end unterminated and are safely rejoined, while
        silence-triggered cuts are the ones Whisper punctuates as though the
        sentence had ended.
        """
        duration = self.duration_seconds()
        if duration < MIN_SPEECH_S:
            return None
        if not self.has_speech():
            return None
        if duration >= MAX_BUFFER_S:
            return "max_buffer"
        if duration >= MIN_TRIGGER_S and self.has_trailing_silence():
            return "silence"
        return None

    def should_process(self):
        # Delegates so the decision and its label can never drift apart.
        return self.cut_reason() is not None

    def get_audio_and_clear(self):
        audio = np.frombuffer(bytes(self.buffer), dtype=np.int16)
        self.buffer.clear()
        return audio

    def clear(self):
        self.buffer.clear()
