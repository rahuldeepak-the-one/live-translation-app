"""Keep the audio the pipeline was actually given, so a bad caption can be
checked against it instead of guessed at.

WHY: when a caption reads "off off off off off of off off off", there are four
places it could have come from — the board, the USB transport, the buffer's
segmentation, or Whisper itself — and the transcript alone cannot tell them
apart. Listening to the exact samples that were handed to the model settles it
in seconds.

TWO captures, because they answer different questions:

  session.wav   every byte the server RECEIVED, unbroken. Answers "did the
                speech even reach us?" — the one thing a missing caption can
                never tell you, since a sentence dropped before the buffer and
                a sentence mistranscribed after it look identical downstream.

  chunks/*.wav  each segment as handed to transcribe(), named by the same
                timestamp the transcript log writes its text under. Answers
                "what did the model hear when it said THAT?" — pairing is the
                whole point, so the names must match the log exactly.

The difference between the two is itself the measurement: audio present in
session.wav with no chunk covering it is audio the segmenter discarded.
"""
import logging
import wave
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioTee:
    def __init__(self, directory, sample_rate, now=datetime.now):
        self.sample_rate = sample_rate
        self._now = now
        stamp = now().strftime("%Y-%m-%d_%H-%M-%S")
        self.root = Path(directory) / stamp
        self.chunk_dir = self.root / "chunks"
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self._session = self._open(self.root / "session.wav")
        self.received_bytes = 0
        logger.info("Audio tee recording to %s", self.root)

    def _open(self, path):
        handle = wave.open(str(path), "wb")
        handle.setnchannels(1)
        handle.setsampwidth(2)          # int16, matching the wire format
        handle.setframerate(self.sample_rate)
        return handle

    def received(self, pcm_bytes):
        """Every byte off the socket, before the buffer sees it."""
        if not self._session:
            return
        try:
            self._session.writeframes(pcm_bytes)
            self.received_bytes += len(pcm_bytes)
        except Exception:
            logger.exception("Audio tee failed on the session file — closing it")
            self.close_session()

    def chunk(self, pcm_bytes, reason):
        """One segment, exactly as handed to transcribe(). Returns its name so
        the caller can put it in the log beside the text it produced."""
        name = "%s_%s.wav" % (self._now().strftime("%H-%M-%S.%f")[:-3], reason)
        try:
            handle = self._open(self.chunk_dir / name)
            handle.writeframes(pcm_bytes)
            handle.close()
        except Exception:
            logger.exception("Audio tee failed to write chunk %s", name)
            return None
        return name

    def close_session(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def close(self):
        self.close_session()


def demo():
    """Self-check: a tee writes what it was given, and pairs names with chunks."""
    import struct, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tee = AudioTee(tmp, 16000)
        pcm = struct.pack("<8h", *range(8))
        tee.received(pcm)
        tee.received(pcm)
        name = tee.chunk(pcm, "silence")
        tee.close()

        assert name and name.endswith("_silence.wav"), name
        with wave.open(str(tee.root / "session.wav")) as w:
            assert w.getframerate() == 16000, w.getframerate()
            assert w.getnchannels() == 1
            # Both writes must be there — a tee that silently drops audio is
            # worse than no tee, since it would frame the transport for a loss
            # it caused itself.
            assert w.getnframes() == 16, w.getnframes()
        with wave.open(str(tee.chunk_dir / name)) as w:
            assert w.getnframes() == 8, w.getnframes()
            assert w.readframes(8) == pcm
        assert tee.received_bytes == 2 * len(pcm)
    print("audio_tee demo ok")


if __name__ == "__main__":
    demo()
