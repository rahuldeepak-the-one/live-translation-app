"""Live Translation Server — thin wiring around the pipeline modules.

Routes:
  /            -> redirect to /display
  /mic         -> phone/laptop page that captures audio
  /display     -> projector page (all languages)
  /view        -> personal phone page (choose language)
  /ws/mic      -> binary PCM in; JSON status/sentence feedback out
  /ws/captions -> JSON caption stream out (hub protocol)
"""
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from audio_buffer import AudioBuffer
from config import HOST, PORT, SAMPLE_RATE
from hub import BroadcastHub
from netinfo import local_urls
from qr import svg_for, view_url_for
from pipeline import UtterancePipeline
from transcript_log import TranscriptLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

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


def _fallback_view_url():
    """Used when the Host header is missing or malformed."""
    urls = local_urls(PORT)
    base = urls[0][1] if urls else f"http://127.0.0.1:{PORT}"
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
            )
        logger.info("Server ready on http://%s:%d", HOST, PORT)
        yield

    app = FastAPI(title="Church Live Translation", lifespan=lifespan)
    hub = BroadcastHub()
    app.state.hub = hub
    app.state.stt = stt
    app.state.translator = translator
    app.state.transcript = transcript if transcript is not None else TranscriptLog()
    # Build eagerly when both deps are already supplied (e.g. tests injecting
    # stubs via a bare TestClient) so we don't depend on the ASGI lifespan
    # "startup" event having fired. Real deployments pass stt=translator=None
    # and the pipeline is built lazily in lifespan once the models load; its
    # `is None` guards keep the two paths idempotent.
    app.state.pipeline = (
        UtterancePipeline(stt, translator, hub, transcript=app.state.transcript)
        if stt is not None and translator is not None else None
    )

    @app.websocket("/ws/mic")
    async def ws_mic(ws: WebSocket):
        await ws.accept()
        logger.info("Mic connected.")
        buf = AudioBuffer(SAMPLE_RATE)
        await ws.send_json({"type": "status", "state": "ready"})
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
                elif "bytes" in message and message["bytes"] is not None:
                    buf.add_chunk(message["bytes"])
                    if buf.should_process():
                        await ws.send_json({"type": "status", "state": "processing"})
                        await app.state.hub.publish_status("processing")
                        try:
                            result = await app.state.pipeline.process(buf.get_audio_and_clear())
                        except Exception:
                            logger.exception("Pipeline failure — utterance dropped")
                            result = None
                        if result:
                            await ws.send_json({"type": "sentence", "en": result[1]})
                        await ws.send_json({"type": "status", "state": "listening"})
                        await app.state.hub.publish_status("listening")
                    else:
                        # Quiet chunk: the only chance to translate a sentence
                        # the speaker started and never finished, since
                        # process() runs on speech only.
                        await app.state.pipeline.flush_if_stale()
        except WebSocketDisconnect:
            pass
        logger.info("Mic disconnected.")

    @app.websocket("/ws/captions")
    async def ws_captions(ws: WebSocket):
        await ws.accept()
        await hub.register(ws)
        logger.info("Screen connected (%d total).", len(hub._clients))
        try:
            while True:
                # Screens are read-only; only /control sends anything, and it
                # always sends a COMPLETE state, so there is nothing to merge.
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict) or msg.get("type") != "display":
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
            logger.info("Screen disconnected (%d left).", len(hub._clients))

    @app.get("/")
    async def root():
        return RedirectResponse("/display")

    @app.get("/mic")
    async def mic_page():
        return FileResponse(STATIC_DIR / "mic.html")

    @app.get("/qr.svg")
    async def qr_svg(request: Request):
        # Built from the Host the client actually used, so a tablet that
        # reached us on 192.168.1.29 hands out that same address rather than
        # whichever of this machine's thirteen IPs the server guessed.
        url = view_url_for(request.headers.get("host", "")) or _fallback_view_url()
        return Response(
            svg_for(url),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/control/{token}")
    async def control_page(token: str):
        # compare_digest, and an identical 404 either way: a wrong token must be
        # indistinguishable from a path that was never a page, so the endpoint
        # cannot be probed. compare_digest requires ASCII-only str (it raises
        # TypeError otherwise) — encode both sides so a non-ASCII token 404s
        # like any other wrong guess instead of 500ing and logging a traceback.
        if not secrets.compare_digest(token.encode(), _CONTROL_TOKEN.encode()):
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / "control.html")

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
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
