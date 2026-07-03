"""
Live Translation Server — Self-hosted STT + Translation
Uses faster-whisper (GPU) for speech-to-text and NLLB-200 for translation.
Tablet connects via WebSocket over local WiFi.
"""

import asyncio
import json
import time
import logging
import struct
import numpy as np
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
WHISPER_MODEL_SIZE = "medium"          # Options: tiny, base, small, medium, large-v3
NLLB_MODEL_NAME = "facebook/nllb-200-distilled-600M"
SILENCE_THRESHOLD = 300                # RMS below this = silence
SILENCE_DURATION_S = 0.6               # Seconds of silence to trigger processing
MAX_BUFFER_S = 8.0                     # Force process after this many seconds
MIN_SPEECH_S = 0.5                     # Don't process less than this
SAMPLE_RATE = 16000                    # Expected sample rate from client
HOST = "0.0.0.0"
PORT = 8080

# ============================================================
# WHISPER STT
# ============================================================
class WhisperSTT:
    def __init__(self, model_size=WHISPER_MODEL_SIZE):
        from faster_whisper import WhisperModel
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        logger.info(f"Loading Whisper {model_size} on {device} ({compute_type})...")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.lock = asyncio.Lock()
        logger.info("Whisper model loaded.")

    async def transcribe(self, audio_np, language=None):
        async with self.lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._transcribe_sync, audio_np, language)

    def _transcribe_sync(self, audio_np, language):
        # Normalize to float32 in [-1, 1]
        if audio_np.dtype == np.int16:
            audio_np = audio_np.astype(np.float32) / 32768.0

        lang_arg = language if language and language != "auto" else None
        segments, info = self.model.transcribe(
            audio_np,
            language=lang_arg,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        text = " ".join([s.text.strip() for s in segments])
        return text.strip()


# ============================================================
# NLLB TRANSLATOR
# ============================================================
class NLLBTranslator:
    LANG_MAP = {
        "en": "eng_Latn",
        "ml": "mal_Mlym",
        "hi": "hin_Deva",
        "te": "tel_Telu",
    }

    def __init__(self, model_name=NLLB_MODEL_NAME):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Loading NLLB translation model on {device}...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(device)
        self.device = device
        self.lock = asyncio.Lock()
        logger.info("NLLB model loaded.")

    async def translate(self, text, source_lang, target_lang):
        if not text or source_lang == target_lang:
            return text
        async with self.lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._translate_sync, text, source_lang, target_lang)

    def _translate_sync(self, text, source_lang, target_lang):
        src_code = self.LANG_MAP.get(source_lang, "eng_Latn")
        tgt_code = self.LANG_MAP.get(target_lang, "mal_Mlym")

        self.tokenizer.src_lang = src_code
        inputs = self.tokenizer(
            text, return_tensors="pt", padding=True, truncation=True, max_length=512
        ).to(self.device)

        tgt_lang_id = self.tokenizer.convert_tokens_to_ids(tgt_code)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                forced_bos_token_id=tgt_lang_id,
                max_new_tokens=512,
            )

        translated = self.tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]
        return translated


# ============================================================
# AUDIO BUFFER with Silence Detection
# ============================================================
class AudioBuffer:
    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.buffer = bytearray()
        self.last_speech_time = time.time()

    def add_chunk(self, pcm_bytes):
        self.buffer.extend(pcm_bytes)

    def duration_seconds(self):
        return len(self.buffer) / (self.sample_rate * 2)  # 2 bytes per int16 sample

    def has_trailing_silence(self):
        """Check if the end of the buffer has silence."""
        check_samples = int(SILENCE_DURATION_S * self.sample_rate)
        check_bytes = check_samples * 2

        if len(self.buffer) < check_bytes:
            return False

        tail = np.frombuffer(self.buffer[-check_bytes:], dtype=np.int16)
        rms = np.sqrt(np.mean(tail.astype(np.float32) ** 2))
        return rms < SILENCE_THRESHOLD

    def should_process(self):
        duration = self.duration_seconds()
        if duration < MIN_SPEECH_S:
            return False
        if duration >= MAX_BUFFER_S:
            return True
        if duration >= 1.5 and self.has_trailing_silence():
            return True
        return False

    def get_audio_and_clear(self):
        audio_np = np.frombuffer(bytes(self.buffer), dtype=np.int16)
        self.buffer.clear()
        return audio_np

    def clear(self):
        self.buffer.clear()


# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="Live Translation Server")

# Global model instances
whisper_stt: WhisperSTT = None
nllb_translator: NLLBTranslator = None


@app.on_event("startup")
async def startup():
    global whisper_stt, nllb_translator
    whisper_stt = WhisperSTT()
    nllb_translator = NLLBTranslator()
    logger.info(f"Server ready on http://{HOST}:{PORT}")
    logger.info("Open this URL on your tablet browser to start translating.")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    logger.info("Client connected.")

    config = {"source_lang": "en", "target_lang": "ml", "sample_rate": SAMPLE_RATE}
    audio_buf = AudioBuffer(SAMPLE_RATE)

    await ws.send_json({"type": "status", "status": "ready", "message": "Connected to server"})

    try:
        while True:
            message = await ws.receive()

            # Text message = JSON config/control
            if "text" in message:
                try:
                    msg = json.loads(message["text"])
                    msg_type = msg.get("type", "")

                    if msg_type == "config":
                        config["source_lang"] = msg.get("sourceLang", config["source_lang"])
                        config["target_lang"] = msg.get("targetLang", config["target_lang"])
                        config["sample_rate"] = msg.get("sampleRate", config["sample_rate"])
                        audio_buf.sample_rate = config["sample_rate"]
                        logger.info(f"Config updated: {config}")
                        await ws.send_json({"type": "config_ack", "config": config})

                    elif msg_type == "clear":
                        audio_buf.clear()
                        await ws.send_json({"type": "cleared"})

                except json.JSONDecodeError:
                    pass

            # Binary message = audio PCM data
            elif "bytes" in message:
                pcm_data = message["bytes"]
                audio_buf.add_chunk(pcm_data)

                if audio_buf.should_process():
                    await ws.send_json({"type": "status", "status": "processing"})

                    audio_np = audio_buf.get_audio_and_clear()

                    # Transcribe
                    t0 = time.time()
                    original = await whisper_stt.transcribe(audio_np, config["source_lang"])
                    t_stt = time.time() - t0

                    if not original:
                        await ws.send_json({"type": "status", "status": "listening"})
                        continue

                    # Translate
                    t1 = time.time()
                    translated = await nllb_translator.translate(
                        original, config["source_lang"], config["target_lang"]
                    )
                    t_translate = time.time() - t1

                    logger.info(
                        f"STT({t_stt:.2f}s): '{original}' → "
                        f"Translate({t_translate:.2f}s): '{translated}'"
                    )

                    await ws.send_json({
                        "type": "translation",
                        "original": original,
                        "translated": translated,
                        "timing": {"stt": round(t_stt, 2), "translate": round(t_translate, 2)},
                    })

                    await ws.send_json({"type": "status", "status": "listening"})

    except WebSocketDisconnect:
        logger.info("Client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")


# Serve static files (the client frontend)
STATIC_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def serve_client():
    return FileResponse(STATIC_DIR / "client.html")


@app.get("/styles.css")
async def serve_css():
    return FileResponse(STATIC_DIR / "styles.css")


@app.get("/client.js")
async def serve_js():
    return FileResponse(STATIC_DIR / "client.js")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
