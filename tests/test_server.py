import numpy as np
from fastapi.testclient import TestClient

import server as server_module
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


class ExplodingSTT:
    async def transcribe(self, audio_np):
        raise RuntimeError("model exploded")


def test_pipeline_failure_keeps_mic_socket_alive():
    app = create_app(stt=ExplodingSTT(), translator=StubTranslator())
    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        assert mic.receive_json()["type"] == "status"  # ready
        mic.send_bytes(loud(2.0))
        mic.send_bytes(silence(1.0))
        got = [mic.receive_json()["type"] for _ in range(2)]
        assert got == ["status", "status"]  # processing -> listening, no crash
        # socket still usable afterwards
        mic.send_bytes(silence(0.1))


def test_pipeline_gets_a_transcript_log(tmp_path):
    """Real deployments must persist the service; nothing was logged before."""
    from transcript_log import TranscriptLog
    log = TranscriptLog(tmp_path)
    app = create_app(stt=StubSTT("Praise the Lord."), translator=StubTranslator(),
                     transcript=log)
    assert app.state.pipeline.transcript is log


def test_transcript_records_a_real_utterance(tmp_path):
    import json
    from transcript_log import TranscriptLog

    app = create_app(stt=StubSTT("Praise the Lord."), translator=StubTranslator(),
                     transcript=TranscriptLog(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(loud(2.0))
        mic.send_bytes(silence(1.0))
        [mic.receive_json() for _ in range(3)]

    written = list(tmp_path.glob("*.jsonl"))
    assert len(written) == 1
    # The file is JSONL and now carries Stage 2b `kind: "cut"` rows alongside
    # utterances, so pick the utterance rather than parsing the whole file as
    # one object. A row with no `kind` is an utterance.
    rows = [json.loads(line) for line in
            written[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    record = next(r for r in rows if r.get("kind") is None)
    assert record["en"] == "Praise the Lord."
    assert record["translations"]["hi"] == "hi:Praise the Lord."


def test_mic_loop_flushes_a_stale_sentence_during_silence():
    """Chunks that don't trigger processing are the chance to flush a held one."""
    app = create_app(stt=StubSTT("an unfinished thought"), translator=StubTranslator())

    class SpyPipeline:
        def __init__(self, inner):
            self.inner = inner
            self.flushes = 0

        async def process(self, audio):
            return await self.inner.process(audio)

        async def flush_if_stale(self):
            self.flushes += 1
            return await self.inner.flush_if_stale()

    spy = SpyPipeline(app.state.pipeline)
    app.state.pipeline = spy

    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()          # ready
        mic.send_bytes(silence(0.2))  # too short to trigger processing
        mic.send_bytes(silence(0.2))

    assert spy.flushes >= 1


def test_qr_endpoint_serves_svg():
    client = make_client()
    response = client.get("/qr.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.lstrip().startswith("<svg")


def test_qr_encodes_the_host_the_client_connected_to():
    """A tablet reaching us on 192.168.1.29 must get a QR for that same address,
    not for whatever the server guessed its own IP to be."""
    from qr import svg_for, view_url_for

    client = make_client()
    body = client.get("/qr.svg", headers={"host": "192.168.1.29:8080"}).text
    assert body == svg_for(view_url_for("192.168.1.29:8080"))

    other = client.get("/qr.svg", headers={"host": "10.0.0.5:8080"}).text
    assert other != body


def test_qr_falls_back_when_the_host_header_is_junk():
    client = make_client()
    response = client.get("/qr.svg", headers={"host": "not a valid host"})
    assert response.status_code == 200
    assert response.text.lstrip().startswith("<svg")


def test_display_page_shows_the_qr():
    client = make_client()
    assert '/qr.svg' in client.get("/display").text


def test_control_page_served_for_the_generated_token():
    client = make_client()
    token = server_module.control_token()
    assert client.get(f"/control/{token}").status_code == 200


def test_wrong_control_token_is_indistinguishable_from_a_missing_page():
    client = make_client()
    real = client.get("/control/definitely-not-the-token")
    absent = client.get("/control/")
    assert real.status_code == 404
    assert absent.status_code == 404


def test_non_ascii_control_token_is_a_404_not_a_500():
    # secrets.compare_digest raises TypeError on a non-ASCII str, which would
    # otherwise surface as an unhandled 500 — a crash log is as good a signal
    # to a prober that /control/{token} is a live route as a distinguishable
    # response would be. One accented character must 404 exactly like any
    # other wrong guess.
    client = make_client()
    response = client.get("/control/caf%C3%A9")
    assert response.status_code == 404


def test_control_token_is_not_guessable_from_the_public_pages():
    client = make_client()
    token = server_module.control_token()
    for path in ("/display", "/view", "/qr.svg"):
        assert token not in client.get(path).text


def _wall_lanes(client, sender_query=""):
    """Open a caption socket, try to narrow the wall to ml, report what stuck.

    Uses a second, read-only socket as the observer, because the retained state
    the hub replays on connect is the wall's real state — not whatever the
    sender hoped for.
    """
    with client.websocket_connect(f"/ws/captions{sender_query}") as sender:
        sender.receive_json()          # history
        sender.receive_json()          # display
        sender.send_json({"type": "display", "lanes": ["ml"],
                          "focus": None, "rotate": 0})
        # A second connection forces the server to replay whatever it retained.
        with client.websocket_connect("/ws/captions") as observer:
            observer.receive_json()    # history
            return observer.receive_json()["lanes"]


def test_socket_without_the_token_cannot_change_the_wall():
    # Every phone on the church WiFi holds one of these sockets — /view is
    # QR-coded for the whole congregation — so an unauthenticated socket must
    # not be able to reconfigure the projector from a developer console.
    client = make_client()
    assert _wall_lanes(client) == ["en", "ml", "te", "hi"]


def test_socket_with_the_token_can_change_the_wall():
    client = make_client()
    token = server_module.control_token()
    assert _wall_lanes(client, f"?t={token}") == ["ml"]


def test_socket_with_a_wrong_token_cannot_change_the_wall():
    client = make_client()
    assert _wall_lanes(client, "?t=definitely-not-the-token") == ["en", "ml", "te", "hi"]


def test_socket_with_a_non_ascii_token_is_rejected_not_crashed():
    # Same TypeError trap as the /control route: compare_digest refuses a
    # non-ASCII str, and an exception here would tear down the caption feed.
    client = make_client()
    assert _wall_lanes(client, "?t=caf%C3%A9") == ["en", "ml", "te", "hi"]


def test_unauthenticated_socket_still_receives_captions():
    # Reading stays open. A screen must never be refused for lacking a token —
    # a caption feed that dies on a mistyped token is worse than an open wall.
    client = make_client()
    with client.websocket_connect("/ws/captions") as screen:
        assert screen.receive_json()["type"] == "history"
        assert screen.receive_json()["type"] == "display"


def _cut_rows(tmp_path):
    import json
    written = list(tmp_path.glob("*.jsonl"))
    if not written:
        return []
    rows = [json.loads(line) for line in
            written[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("kind") == "cut"]


def test_a_cut_reaches_the_transcript_with_its_reason_and_text(tmp_path):
    # The whole point of Stage 2b's instrumentation: after a service there must
    # be a file that says, per cut, why we cut and what the chunk said.
    from transcript_log import TranscriptLog

    app = create_app(stt=StubSTT("...to contrast moral character."),
                     translator=StubTranslator(), transcript=TranscriptLog(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(loud(2.0))
        mic.send_bytes(silence(1.0))       # trailing silence -> a "silence" cut
        [mic.receive_json() for _ in range(3)]

    cuts = _cut_rows(tmp_path)
    assert len(cuts) == 1, cuts
    cut = cuts[0]
    assert cut["reason"] == "silence"
    assert cut["text"] == "...to contrast moral character."
    assert cut["looked_complete"] is True      # exactly the wrong call we hunt
    assert cut["chunk_s"] > 0
    assert cut["ts"]


def test_a_forced_cut_is_labelled_max_buffer(tmp_path):
    # Continuous speech past MAX_BUFFER_S: the safe kind of cut, and the label
    # has to distinguish it or the analysis cannot separate the populations.
    from transcript_log import TranscriptLog
    from config import MAX_BUFFER_S

    app = create_app(stt=StubSTT("the man's ability to un-"),
                     translator=StubTranslator(), transcript=TranscriptLog(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(loud(MAX_BUFFER_S + 0.5))
        [mic.receive_json() for _ in range(3)]

    cuts = _cut_rows(tmp_path)
    assert len(cuts) == 1, cuts
    assert cuts[0]["reason"] == "max_buffer"
    assert cuts[0]["looked_complete"] is False


def test_speech_resuming_after_a_cut_records_the_gap(tmp_path):
    # speech_gap_s is the field the threshold is chosen from; if the wiring
    # never fills it, a whole service produces an unusable file.
    from transcript_log import TranscriptLog

    app = create_app(stt=StubSTT("first chunk."), translator=StubTranslator(),
                     transcript=TranscriptLog(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        mic.send_bytes(loud(2.0))
        mic.send_bytes(silence(1.0))          # cut here
        [mic.receive_json() for _ in range(3)]
        mic.send_bytes(loud(0.3))             # speech again, below trigger size

    cuts = _cut_rows(tmp_path)
    assert len(cuts) == 1, cuts
    assert cuts[0]["speech_gap_s"] is not None
    assert cuts[0]["speech_gap_s"] >= 0


def test_instrumentation_does_not_change_what_the_screens_see(tmp_path):
    # Stage 2b step one is measurement only. If recording altered the captions,
    # the data would describe a system we do not actually run on Sunday.
    from transcript_log import TranscriptLog

    app = create_app(stt=StubSTT("Praise the Lord."), translator=StubTranslator(),
                     transcript=TranscriptLog(tmp_path))
    client = TestClient(app)
    with client.websocket_connect("/ws/captions") as screen:
        assert screen.receive_json()["type"] == "history"
        assert screen.receive_json()["type"] == "display"
        with client.websocket_connect("/ws/mic") as mic:
            mic.receive_json()
            mic.send_bytes(loud(2.0))
            mic.send_bytes(silence(1.0))
            [mic.receive_json() for _ in range(3)]
        msgs = [screen.receive_json() for _ in range(4)]
    types = [m["type"] for m in msgs]
    assert "sentence" in types and "translation" in types
    sent = next(m for m in msgs if m["type"] == "sentence")
    assert sent["en"] == "Praise the Lord."
