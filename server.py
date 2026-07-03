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
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from audio_buffer import AudioBuffer
from config import HOST, PORT, SAMPLE_RATE
from hub import BroadcastHub
from pipeline import UtterancePipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(stt=None, translator=None):
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
                application.state.stt, application.state.translator, application.state.hub
            )
        logger.info("Server ready on http://%s:%d", HOST, PORT)
        yield

    app = FastAPI(title="Church Live Translation", lifespan=lifespan)
    hub = BroadcastHub()
    app.state.hub = hub
    app.state.stt = stt
    app.state.translator = translator
    # Build eagerly when both deps are already supplied (e.g. tests injecting
    # stubs via a bare TestClient) so we don't depend on the ASGI lifespan
    # "startup" event having fired. Real deployments pass stt=translator=None
    # and the pipeline is built lazily in lifespan once the models load; its
    # `is None` guards keep the two paths idempotent.
    app.state.pipeline = (
        UtterancePipeline(stt, translator, hub) if stt is not None and translator is not None else None
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
                await ws.receive_text()  # keepalive/no-op; raises on disconnect
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
