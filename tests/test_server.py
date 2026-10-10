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


def speaker_stops(mic, monkeypatch):
    """Stage 2b holds the last sentence until the speaker actually stops.

    A terminated tail is no longer flushed at the cut: it waits
    SENTENCE_GRACE_S for the next chunk's first letter, which is what decides
    whether Whisper's full stop was real or invented at a breath. These socket
    tests are about plumbing, not about that constant — tests/test_pipeline.py
    owns it — so the wait collapses to zero and one more silent chunk carries
    the flush.
    """
    monkeypatch.setattr("pipeline.SENTENCE_GRACE_S", 0.0)
    mic.send_bytes(silence(0.5))


def read_until_translation(screen, cap=10):
    """Drain the screen socket up to the translation.

    The message count is no longer fixed: a held sentence is published once as
    `final:false` and again as `final:true` when it flushes, so asserting on a
    hard-coded range() would have to be re-derived on every change here.
    """
    msgs = []
    for _ in range(cap):
        msgs.append(screen.receive_json())
        if msgs[-1]["type"] == "translation":
            return msgs
    raise AssertionError(
        f"no translation in {cap} messages: {[m['type'] for m in msgs]}")


def test_mic_audio_reaches_caption_screens(monkeypatch):
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
            speaker_stops(mic, monkeypatch)
        msgs = read_until_translation(screen)
        types = [m["type"] for m in msgs]
        assert "sentence" in types and "translation" in types
        sent = next(m for m in msgs if m["type"] == "sentence")
        assert sent["en"] == "Praise the Lord."


def test_pages_served():
    client = make_client()
    for path in ("/display", "/view"):
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


def test_transcript_records_a_real_utterance(tmp_path, monkeypatch):
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
        speaker_stops(mic, monkeypatch)

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

        async def flush_if_stale(self, has_speech=False):
            self.flushes += 1
            return await self.inner.flush_if_stale(has_speech=has_speech)

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
    assert _wall_lanes(client) == ["en", "ml", "te", "hi", "pa"]


def test_socket_with_the_token_can_change_the_wall():
    client = make_client()
    token = server_module.control_token()
    assert _wall_lanes(client, f"?t={token}") == ["ml"]


def test_socket_with_a_wrong_token_cannot_change_the_wall():
    client = make_client()
    assert _wall_lanes(client, "?t=definitely-not-the-token") == ["en", "ml", "te", "hi", "pa"]


def test_socket_with_a_non_ascii_token_is_rejected_not_crashed():
    # Same TypeError trap as the /control route: compare_digest refuses a
    # non-ASCII str, and an exception here would tear down the caption feed.
    client = make_client()
    assert _wall_lanes(client, "?t=caf%C3%A9") == ["en", "ml", "te", "hi", "pa"]


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


def test_instrumentation_does_not_change_what_the_screens_see(tmp_path, monkeypatch):
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
            speaker_stops(mic, monkeypatch)
        msgs = read_until_translation(screen)
    types = [m["type"] for m in msgs]
    assert "sentence" in types and "translation" in types
    sent = next(m for m in msgs if m["type"] == "sentence")
    assert sent["en"] == "Praise the Lord."


def test_mic_loop_tells_the_pipeline_whether_the_chunk_had_speech():
    """Stage 2b's grace timer measures SILENCE, so it needs this from the socket.

    Speech resumes a median 1.3s after a cut, but the chunk carrying it is not
    cut and transcribed for a median 6.2s. A 3s timer counting elapsed time
    would fire in that window and flush the held sentence before the letter
    that decides it ever existed — so an audible chunk has to refresh it.
    """
    app = create_app(stt=StubSTT("an unfinished thought"), translator=StubTranslator())

    class SpyPipeline:
        def __init__(self, inner):
            self.inner = inner
            self.speech_flags = []

        async def process(self, audio):
            return await self.inner.process(audio)

        async def flush_if_stale(self, has_speech=False):
            self.speech_flags.append(has_speech)
            return await self.inner.flush_if_stale(has_speech=has_speech)

    spy = SpyPipeline(app.state.pipeline)
    app.state.pipeline = spy

    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()            # ready
        mic.send_bytes(silence(0.2))  # too short to trigger a cut
        mic.send_bytes(loud(0.2))     # audible, still too short to cut

    assert spy.speech_flags == [False, True], spy.speech_flags


# ---------------------------------------------------------------- control room
# /status can stop the microphone mid-sermon, so it is gated exactly like
# /control. The capture DEVICE lives in the mic page's getUserMedia call, so
# the server never changes it — it only relays the request.

def read_until(socket, wanted, where=None, cap=12):
    """Next message of a type, optionally matching a predicate.

    `where` matters more than it looks: registering sends a RETAINED snapshot
    of both the wall and the mic, so the first `display`/`mic` a socket sees is
    the state as it already was, not the change under test.
    """
    for _ in range(cap):
        msg = socket.receive_json()
        if msg.get("type") == wanted and (where is None or where(msg)):
            return msg
    raise AssertionError(f"no matching {wanted!r} message within {cap} frames")


def test_status_page_served_for_the_generated_token():
    client = make_client()
    token = server_module.control_token()
    assert client.get(f"/status/{token}").status_code == 200


def test_wrong_status_token_is_indistinguishable_from_a_missing_page():
    client = make_client()
    real = client.get("/status/definitely-not-the-token")
    absent = client.get("/status/")
    assert real.status_code == 404
    assert real.status_code == absent.status_code


def test_status_api_needs_the_token():
    client = make_client()
    assert client.get("/api/status").status_code == 404
    assert client.get("/api/status?t=wrong").status_code == 404
    ok = client.get(f"/api/status?t={server_module.control_token()}")
    assert ok.status_code == 200
    assert ok.json()["sampleRate"] == SAMPLE_RATE


def test_status_token_is_not_guessable_from_the_public_pages():
    client = make_client()
    token = server_module.control_token()
    for path in ("/view", "/display", "/qr.svg"):
        assert token not in client.get(path).text


def test_capture_state_reaches_an_operator_but_not_a_plain_screen():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect(f"/ws/captions?t={token}") as operator:
        with client.websocket_connect("/ws/captions") as screen:
            assert screen.receive_json()["type"] == "history"
            assert screen.receive_json()["type"] == "display"
            with client.websocket_connect("/ws/mic") as mic:
                mic.send_json({
                    "type": "mic_state", "capturing": True, "deviceId": "usb-1",
                    "deviceLabel": "Yamaha MG10XU", "level": 0.4,
                    "devices": [{"id": "usb-1", "label": "Yamaha MG10XU"}],
                })
                state = read_until(operator, "mic", lambda m: m["connected"])
                assert state["deviceLabel"] == "Yamaha MG10XU"
                assert state["capturing"] is True

                # Now make something the screen IS entitled to. If the mic
                # state had leaked to it, that frame would be queued ahead of
                # this one — so the type of the very next message is the proof.
                operator.send_json(
                    {"type": "display", "lanes": ["ml"], "focus": None, "rotate": 0})
                assert screen.receive_json()["type"] == "display"


def test_operator_stop_reaches_the_capture_page():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect("/ws/mic") as mic:
        assert mic.receive_json()["type"] == "status"      # "ready"
        with client.websocket_connect(f"/ws/captions?t={token}") as operator:
            operator.send_json({"type": "mic", "action": "stop"})
            assert mic.receive_json() == {"type": "command", "action": "stop"}


def test_operator_can_name_the_input_to_switch_to():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        with client.websocket_connect(f"/ws/captions?t={token}") as operator:
            operator.send_json({"type": "mic", "action": "set_device", "deviceId": "usb-1"})
            assert mic.receive_json() == {
                "type": "command", "action": "set_device", "deviceId": "usb-1"}


def test_a_screen_without_the_token_cannot_stop_the_microphone():
    client = make_client()
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        with client.websocket_connect("/ws/captions") as screen:
            screen.send_json({"type": "mic", "action": "stop"})
            # Prove nothing arrived by sending something that DOES: if the stop
            # had been relayed it would be sitting ahead of this in the queue.
            with client.websocket_connect(
                    f"/ws/captions?t={server_module.control_token()}") as operator:
                operator.send_json({"type": "mic", "action": "start"})
                assert mic.receive_json() == {"type": "command", "action": "start"}


def test_unknown_and_incomplete_mic_actions_are_dropped():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()
        with client.websocket_connect(f"/ws/captions?t={token}") as operator:
            operator.send_json({"type": "mic", "action": "reboot"})
            operator.send_json({"type": "mic", "action": "set_device"})   # no id
            operator.send_json({"type": "mic", "action": "stop"})
            # Only the legitimate one got through, and the operator socket
            # survived the other two.
            assert mic.receive_json() == {"type": "command", "action": "stop"}


def test_a_command_with_no_capture_page_does_not_kill_the_operator_socket():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect(f"/ws/captions?t={token}") as operator:
        operator.send_json({"type": "mic", "action": "stop"})
        # Still usable afterwards: the wall still answers.
        operator.send_json({"type": "display", "lanes": ["ml"], "focus": None, "rotate": 0})
        assert read_until(operator, "display", lambda m: m["lanes"] == ["ml"])


def test_losing_the_capture_page_is_announced_to_the_operator():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect(f"/ws/captions?t={token}") as operator:
        with client.websocket_connect("/ws/mic") as mic:
            mic.send_json({"type": "mic_state", "capturing": True,
                           "deviceId": "usb-1", "deviceLabel": "Yamaha", "devices": []})
            assert read_until(operator, "mic", lambda m: m["connected"])["capturing"] is True
        gone = read_until(operator, "mic", lambda m: not m["connected"])
        # The device it WAS on survives the drop — that is the thing you need
        # to read when the capture page disappears mid-service.
        assert gone["deviceLabel"] == "Yamaha" and gone["capturing"] is False


def test_operator_can_clear_the_caption_backlog():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect(f"/ws/captions?t={token}") as operator:
        read_until(operator, "display")                 # drain registration
        operator.send_json({"type": "wall", "action": "clear"})
        assert read_until(operator, "history")["sentences"] == []


def test_a_screen_without_the_token_cannot_clear_the_wall():
    client = make_client()
    with client.websocket_connect("/ws/captions") as screen:
        assert screen.receive_json()["type"] == "history"   # registration
        assert screen.receive_json()["type"] == "display"
        screen.send_json({"type": "wall", "action": "clear"})
        # Prove nothing happened by sending something that DOES: a clear that
        # had been honoured would sit ahead of this in the queue.
        with client.websocket_connect(
                f"/ws/captions?t={server_module.control_token()}") as operator:
            operator.send_json(
                {"type": "display", "lanes": ["hi"], "focus": None, "rotate": 0})
            assert screen.receive_json()["type"] == "display"


def test_an_unknown_wall_action_is_ignored_without_dropping_the_socket():
    client = make_client()
    token = server_module.control_token()
    with client.websocket_connect(f"/ws/captions?t={token}") as operator:
        read_until(operator, "display")
        operator.send_json({"type": "wall", "action": "detonate"})
        operator.send_json({"type": "wall", "action": "clear"})
        assert read_until(operator, "history")["sentences"] == []


def test_audio_ingest_does_not_wait_for_the_pipeline():
    """G4: process() ran inline in the receive loop, so while one chunk was in
    STT + MT nothing was read off the socket and the next cut was late by the
    whole of it. A second cut must be taken while the first is still running."""
    import asyncio
    import threading
    import time as _time

    app = create_app(stt=StubSTT("Praise the Lord."), translator=StubTranslator())
    release = threading.Event()
    order = []

    class SlowPipeline:
        async def process(self, audio):
            order.append("process")
            while not release.is_set():
                await asyncio.sleep(0.01)

        async def flush_if_stale(self, has_speech=False):
            pass

    app.state.pipeline = SlowPipeline()
    cuts = []
    record_cut = app.state.seglog.record_cut
    app.state.seglog.record_cut = lambda *a, **k: (cuts.append(1), record_cut(*a, **k))

    client = TestClient(app)
    with client.websocket_connect("/ws/mic") as mic:
        mic.receive_json()                         # ready
        for _ in range(2):
            mic.send_bytes(loud(2.0))
            mic.send_bytes(silence(1.0))
        deadline = _time.time() + 5
        while len(cuts) < 2 and _time.time() < deadline:
            _time.sleep(0.01)
        taken_while_busy = len(cuts)
        release.set()

    assert taken_while_busy == 2
    assert order == ["process", "process"]     # neither cut was dropped
