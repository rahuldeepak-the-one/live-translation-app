"""Live Translation Server — thin wiring around the pipeline modules.

Routes:
  /            -> redirect to /display
  /status/<tok>-> operator control room (which mic, what it hears, start/stop)
  /display     -> projector page (all languages)
  /view        -> personal phone page (choose language)
  /ws/mic      -> binary PCM in; JSON status/sentence feedback out, and the
                  capture process's device list in / operator commands out.
                  Spoken by ./feed-mic.py, which is the only ingress.
  /ws/captions -> JSON caption stream out (hub protocol)
"""
import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from audio_buffer import AudioBuffer
from config import (
    HOST, PORT, SAMPLE_RATE, SSL_CERTFILE, WHISPER_MODEL, TRANSLATOR_BACKEND,
    INDICTRANS2_MODEL, NLLB_MODEL, TARGET_LANGS, SILENCE_THRESHOLD, MAX_BUFFER_S,
    HISTORY_SIZE, TRANSCRIPT_DIR,
)
from hub import BroadcastHub
from netinfo import local_urls
from qr import svg_for, view_url_for
from pipeline import UtterancePipeline
from segmentation_log import SegmentationLog
from transcript_log import TranscriptLog
import tls
from audio_tee import AudioTee

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
# Mic workers still draining after their socket closed (see ws_mic).
_mic_workers = set()

STATIC_DIR = Path(__file__).parent / "static"

# The /view address is QR-coded for the whole congregation, so /control must not
# be reachable by guessing from it. start.sh generates the token so it can print
# it in the startup banner before the server boots; running server.py directly
# generates one and logs it.
#
# This is obscurity, not authentication. It stops a bored teenager on the church
# WiFi. It does not stop anyone who can read the operator's screen or this log.
_CONTROL_TOKEN = os.environ.get("CONTROL_TOKEN")
if not _CONTROL_TOKEN:
    # start.sh always sets CONTROL_TOKEN itself (and shows it in its own
    # banner, printed before the server ever boots); this branch is only hit
    # by `python server.py` directly, so it is the only place that needs to
    # tell a developer where to find /control at all. Never send this to a
    # page the congregation can load — see /view, /qr.svg above.
    _CONTROL_TOKEN = secrets.token_hex(3)
    logger.info("No CONTROL_TOKEN set — generated one for this run: %s", _CONTROL_TOKEN)


def control_token():
    return _CONTROL_TOKEN


def _token_matches(candidate):
    """Constant-time token check, tolerant of anything a client can send.

    One helper for both the /control route and the caption socket, because the
    encoding detail below is easy to get right in one place and wrong in the
    other: compare_digest raises TypeError on a non-ASCII str, so comparing raw
    input turned `/control/café` into a 500 that advertised the route existed.
    Encoding both sides makes every wrong guess fail identically. `None` covers
    a socket that simply omitted the query param.
    """
    if candidate is None:
        return False
    return secrets.compare_digest(candidate.encode(), _CONTROL_TOKEN.encode())


def _fallback_view_url():
    """Used when the Host header is missing or malformed."""
    urls = local_urls(PORT, tls.scheme())
    base = urls[0][1] if urls else f"{tls.scheme()}://127.0.0.1:{PORT}"
    return f"{base}/view"


def create_app(stt=None, translator=None, transcript=None):
    @asynccontextmanager
    async def lifespan(application):
        if application.state.stt is None:
            from stt import WhisperSTT
            application.state.stt = WhisperSTT()
        if application.state.translator is None:
            from translator import load_translator
            application.state.translator = load_translator()
        if application.state.pipeline is None:
            application.state.pipeline = UtterancePipeline(
                application.state.stt, application.state.translator,
                application.state.hub, transcript=application.state.transcript,
                on_chunk_text=application.state.seglog.record_text,
            )
        logger.info("Server ready on http://%s:%d", HOST, PORT)
        yield

    app = FastAPI(title="Church Live Translation", lifespan=lifespan)
    hub = BroadcastHub()
    app.state.hub = hub
    # The single live capture socket, so the control room can talk BACK to the
    # process holding the microphone. Only one feed-mic.py is ever meant to
    # run; a second one replaces the first here, which matches what the audio
    # pipeline does anyway (both would feed the same buffer).
    app.state.mic_ws = None
    # Opt-in: this writes every received sample to disk (~115 MB/hour), which is
    # the right trade while a transcript is under suspicion and the wrong one as
    # a standing default. start.sh does not set it; you turn it on deliberately.
    app.state.tee = (AudioTee(Path(TRANSCRIPT_DIR) / "audio", SAMPLE_RATE)
                     if os.environ.get("AUDIO_TEE") else None)
    app.state.started_at = time.time()
    app.state.stt = stt
    app.state.translator = translator
    app.state.transcript = transcript if transcript is not None else TranscriptLog()
    # Stage 2b instrumentation. App-level rather than per-connection because the
    # pipeline that feeds it chunk text is app-level too, and the deployment has
    # exactly one microphone. Two concurrent mics would interleave records;
    # that is a non-case here, not a supported one.
    app.state.seglog = SegmentationLog(app.state.transcript, time.time)
    # Build eagerly when both deps are already supplied (e.g. tests injecting
    # stubs via a bare TestClient) so we don't depend on the ASGI lifespan
    # "startup" event having fired. Real deployments pass stt=translator=None
    # and the pipeline is built lazily in lifespan once the models load; its
    # `is None` guards keep the two paths idempotent.
    app.state.pipeline = (
        UtterancePipeline(stt, translator, hub, transcript=app.state.transcript,
                          on_chunk_text=app.state.seglog.record_text)
        if stt is not None and translator is not None else None
    )

    @app.websocket("/ws/mic")
    async def ws_mic(ws: WebSocket):
        await ws.accept()
        logger.info("Mic connected.")
        buf = AudioBuffer(SAMPLE_RATE)
        app.state.mic_ws = ws
        await ws.send_json({"type": "status", "state": "ready"})
        # G4: STT + MT run on this one worker, never in the receive loop. Inline,
        # a 2.5s chunk held the socket unread, so feed-mic.py's audio backed up
        # behind it and every later cut was late by that much. One worker, one
        # FIFO: cuts and flush checks run in arrival order, so sentence assembly
        # is exactly as serial as it was.
        # ponytail: unbounded queue; if the GPU ever falls behind real time it
        # grows rather than drops, which is the right failure for a sermon.
        work = asyncio.Queue()

        async def send(payload):
            try:
                await ws.send_json(payload)
            except Exception:
                pass    # the mic went away mid-utterance; captions still publish

        async def worker():
            while (item := await work.get()) is not None:
                kind, arg = item
                try:
                    if kind == "flush":
                        await app.state.pipeline.flush_if_stale(has_speech=arg)
                        continue
                    await send({"type": "status", "state": "processing"})
                    await app.state.hub.publish_status("processing")
                    try:
                        result = await app.state.pipeline.process(arg)
                    except Exception:
                        logger.exception("Pipeline failure — utterance dropped")
                        result = None
                    if result:
                        await send({"type": "sentence", "en": result[1]})
                    await send({"type": "status", "state": "listening"})
                    await app.state.hub.publish_status("listening")
                except Exception:
                    logger.exception("Mic worker step failed")
            # Whatever cut is still open gets written with a null gap rather
            # than being lost — the last utterance before the mic goes quiet is
            # exactly the one a naive implementation drops. Here, not in the
            # handler, so it runs after that utterance's text is recorded.
            app.state.seglog.close()

        # The loop holds only a weak reference to a task; keep it alive until
        # it has drained.
        task = asyncio.create_task(worker())
        _mic_workers.add(task)
        task.add_done_callback(_mic_workers.discard)
        try:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if "text" in message and message["text"] is not None:
                    try:
                        msg = json.loads(message["text"])
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") == "config":
                        buf.sample_rate = msg.get("sampleRate", SAMPLE_RATE)
                    elif msg.get("type") == "clear":
                        buf.clear()
                    elif msg.get("type") == "mic_state":
                        # Which input the capture process picked, what else it
                        # can see, and how loud it is. Operators only —
                        # see BroadcastHub.publish_mic.
                        try:
                            await app.state.hub.publish_mic(msg)
                        except ValueError:
                            logger.warning("Rejected mic state: %r", msg)
                elif "bytes" in message and message["bytes"] is not None:
                    if app.state.tee:
                        app.state.tee.received(message["bytes"])
                    buf.add_chunk(message["bytes"])
                    reason = buf.cut_reason()
                    if reason is not None:
                        # Stage 2b: record WHY we cut and how much silence was
                        # really at the tail, before the buffer is cleared.
                        # See segmentation_log for why the useful number is the
                        # gap measured AFTER this, not anything available now.
                        # Pull the audio out BEFORE recording the cut: this is
                        # the array transcribe() is about to be given, and the
                        # whole value of the tee is that it is that array and
                        # not a reconstruction of it.
                        # Measure the buffer BEFORE emptying it — both of
                        # these read the samples that get_audio_and_clear takes
                        # away, and would report 0.0 afterwards.
                        chunk_s = buf.duration_seconds()
                        trailing_s = buf.trailing_silence_seconds()
                        # This is the array transcribe() is about to be given,
                        # and the whole value of the tee is that it is that
                        # array and not a reconstruction of it.
                        audio = buf.get_audio_and_clear()
                        clip = (app.state.tee.chunk(audio.tobytes(), reason)
                                if app.state.tee else None)
                        app.state.seglog.record_cut(
                            reason, chunk_s, trailing_s, audio=clip,
                        )
                        work.put_nowait(("process", audio))
                    else:
                        # Speech audible again after a cut closes the open
                        # segmentation record: this gap is the one measurement
                        # that separates a breath from a finished sentence.
                        # Idempotent, so calling it on every speech-bearing
                        # chunk is fine.
                        speech = buf.has_speech()
                        if speech:
                            app.state.seglog.record_speech_resumed()
                        # Quiet chunk: the only chance to translate a sentence
                        # the speaker started and never finished, since
                        # process() runs on speech only.
                        #
                        # Stage 2b: `speech` is what stops the short grace timer
                        # firing between speech resuming and the chunk carrying
                        # it being transcribed — a median 6.2s window in which a
                        # held sentence would otherwise be flushed before the
                        # letter that decides it exists.
                        work.put_nowait(("flush", speech))
        except WebSocketDisconnect:
            pass
        finally:
            # Drain, don't cancel: the last cut before the mic dropped is
            # still someone's sentence. Not awaited — the worker closes the
            # seglog itself once drained, so nothing below waits on the GPU.
            work.put_nowait(None)
        # Only clear the slot if it is still OURS. feed-mic.py reconnects on a
        # 2s timer, and a reconnect that lands before this handler unwinds has
        # already installed its own socket here; blanking it would leave the
        # control room unable to reach a mic that is in fact live.
        if app.state.mic_ws is ws:
            app.state.mic_ws = None
        await app.state.hub.publish_mic_gone()
        logger.info("Mic disconnected.")

    MIC_ACTIONS = ("start", "stop", "set_device")

    async def relay_to_mic(msg):
        """Pass an operator's instruction down to the process holding the mic.

        The capture device belongs to feed-mic.py's parec child, not to this
        process, so the server cannot change it — it can only ask. A failure
        here is logged and dropped rather than raised: the control room must
        not lose its socket because the feed died mid-tap.
        """
        action = msg.get("action")
        if action not in MIC_ACTIONS:
            logger.warning("Ignored unknown mic action: %r", action)
            return
        target = app.state.mic_ws
        if target is None:
            logger.info("Mic action %r dropped — no capture page connected.", action)
            return
        command = {"type": "command", "action": action}
        if action == "set_device":
            device_id = msg.get("deviceId")
            if not isinstance(device_id, str) or not device_id:
                logger.warning("Ignored set_device with no device id: %r", msg)
                return
            command["deviceId"] = device_id
        try:
            await target.send_json(command)
        except Exception:
            logger.warning("Could not reach the capture page for %r", action)

    @app.websocket("/ws/captions")
    async def ws_captions(ws: WebSocket):
        await ws.accept()
        # Reading is open; WRITING needs the token. Every screen on the church
        # WiFi holds one of these sockets — the /view address is QR-coded for
        # the whole congregation — so without this check any phone with a JS
        # console could reconfigure the projector. The secret /control path
        # guards the control PAGE; this guards the control CHANNEL, which is
        # the half that actually changes the wall.
        #
        # Unauthenticated sockets still connect and still receive everything.
        # A screen must never be refused: a caption feed that drops because a
        # token was mistyped is a worse failure than an unlocked wall.
        may_control = _token_matches(ws.query_params.get("t"))
        await hub.register(ws, operator=may_control)
        logger.info(
            "Screen connected (%d total, %s).",
            len(hub._clients), "operator" if may_control else "read-only",
        )
        try:
            while True:
                # Screens are read-only; only /control sends anything, and it
                # always sends a COMPLETE state, so there is nothing to merge.
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict) or msg.get("type") not in ("display", "mic", "wall"):
                    continue
                if not may_control:
                    # Logged, not ignored silently: if the wall does something
                    # odd mid-service this is how you tell an operator from a
                    # congregant experimenting with the developer console.
                    logger.warning(
                        "Ignored control state from an unauthenticated socket: %r", msg)
                    continue
                if msg.get("type") == "mic":
                    await relay_to_mic(msg)
                    continue
                if msg.get("type") == "wall":
                    if msg.get("action") == "clear":
                        await hub.clear_history()
                        logger.info("Operator cleared the caption backlog.")
                    else:
                        logger.warning("Ignored unknown wall action: %r", msg.get("action"))
                    continue
                try:
                    await hub.publish_display(msg)
                except ValueError:
                    # A malformed control message must never blank the wall.
                    logger.warning("Rejected control state: %r", msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister(ws)
            await hub.publish_presence()
            logger.info("Screen disconnected (%d left).", len(hub._clients))

    @app.get("/")
    async def root():
        return RedirectResponse("/display")

    @app.get("/qr.svg")
    async def qr_svg(request: Request):
        # Built from the Host the client actually used, so a tablet that
        # reached us on 192.168.1.29 hands out that same address rather than
        # whichever of this machine's thirteen IPs the server guessed.
        # request.url.scheme, not tls.scheme(): the QR must carry whatever the
        # client actually reached us on, so it keeps working from behind
        # anything that terminates TLS in front of us.
        url = (view_url_for(request.headers.get("host", ""), request.url.scheme)
               or _fallback_view_url())
        return Response(
            svg_for(url),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/control/{token}")
    async def control_page(token: str):
        # An identical 404 either way: a wrong token must be indistinguishable
        # from a path that was never a page, so the endpoint cannot be probed.
        # See _token_matches for why the comparison encodes both sides.
        if not _token_matches(token):
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / "control.html")

    @app.get("/status/{token}")
    async def status_page(token: str):
        # Same gate as /control, and for a stronger reason: this page can stop
        # the microphone mid-sermon.
        if not _token_matches(token):
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / "status.html")

    @app.get("/api/status")
    async def api_status(request: Request):
        """The facts that never change during a service, for the control room.

        Everything that DOES change arrives over the socket instead; this is
        only here so the page can show what it is running without a round trip
        per field.
        """
        if not _token_matches(request.query_params.get("t")):
            raise HTTPException(status_code=404)
        return {
            "whisper": WHISPER_MODEL,
            "translator": TRANSLATOR_BACKEND,
            "translatorModel": (INDICTRANS2_MODEL if TRANSLATOR_BACKEND == "indictrans2"
                                else NLLB_MODEL),
            "sampleRate": SAMPLE_RATE,
            "targetLangs": list(TARGET_LANGS),
            "silenceThreshold": SILENCE_THRESHOLD,
            "maxBufferS": MAX_BUFFER_S,
            "historySize": HISTORY_SIZE,
            "uptimeS": round(time.time() - app.state.started_at, 1),
            "tls": bool(getattr(app.state, "tls", None)) or Path(SSL_CERTFILE).exists(),
            # tls.scheme(), not the default: the status page prints these
            # for an operator to type into a phone, and an http:// address
            # while the server speaks TLS is a dead link, not a typo.
            "viewUrls": local_urls(PORT, tls.scheme()),
        }

    @app.get("/display")
    async def display_page():
        return FileResponse(STATIC_DIR / "display.html")

    @app.get("/view")
    async def view_page():
        return FileResponse(STATIC_DIR / "view.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    # Missing certificate files are not fatal — see tls.py. Since the capture
    # path moved out of the browser (feed-mic.py), nothing here NEEDS a secure
    # context any more: HTTPS only keeps the caption stream off the clear. A
    # plain-HTTP boot is now a complete, working service.
    certfile, keyfile = tls.paths()
    if certfile:
        logger.info("Serving HTTPS (self-signed).")
    else:
        logger.info("No certificate at %s — serving plain HTTP. Run feed-mic.py with --url ws://127.0.0.1:%d/ws/mic.", SSL_CERTFILE, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                ssl_certfile=certfile, ssl_keyfile=keyfile)
