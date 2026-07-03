import numpy as np
from fastapi.testclient import TestClient

from server import create_app
from tests.test_pipeline import StubSTT, StubTranslator
from config import SAMPLE_RATE


def make_client():
    app = create_app(stt=StubSTT("Praise the Lord."), translator=StubTranslator())
    return TestClient(app)


def loud(seconds):
    n = int(seconds * SAMPLE_RATE)
    rng = np.random.default_rng(7)
    return (rng.uniform(-0.5, 0.5, n) * 20000).astype(np.int16).tobytes()


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16).tobytes()


def test_mic_audio_reaches_caption_screens():
    client = make_client()
    with client.websocket_connect("/ws/captions") as screen:
        assert screen.receive_json()["type"] == "history"
        with client.websocket_connect("/ws/mic") as mic:
            assert mic.receive_json()["type"] == "status"  # ready
            mic.send_bytes(loud(2.0))
            mic.send_bytes(silence(1.0))  # trailing silence triggers processing
            # mic gets feedback: processing -> sentence -> listening
            got = [mic.receive_json()["type"] for _ in range(3)]
            assert got == ["status", "sentence", "status"]
        msgs = [screen.receive_json() for _ in range(4)]
        types = [m["type"] for m in msgs]
        assert "sentence" in types and "translation" in types
        sent = next(m for m in msgs if m["type"] == "sentence")
        assert sent["en"] == "Praise the Lord."


def test_pages_served():
    client = make_client()
    for path in ("/mic", "/display", "/view"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "<html" in r.text.lower()
