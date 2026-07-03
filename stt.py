"""Speech-to-text on faster-whisper (distil-large-v3, English-only)."""
import asyncio
import logging

import numpy as np
import torch

from config import WHISPER_MODEL, SOURCE_LANG

logger = logging.getLogger(__name__)


class WhisperSTT:
    def __init__(self, model_size=WHISPER_MODEL):
        from faster_whisper import WhisperModel
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        logger.info("Loading Whisper %s on %s (%s)...", model_size, device, compute_type)
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._lock = asyncio.Lock()
        logger.info("Whisper loaded.")

    async def transcribe(self, audio_np):
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._transcribe_sync, audio_np)

    def _transcribe_sync(self, audio_np):
        if audio_np.dtype == np.int16:
            audio_np = audio_np.astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            audio_np,
            language=SOURCE_LANG,   # distil-large-v3 is English-only
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        return " ".join(s.text.strip() for s in segments).strip()
