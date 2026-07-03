# M1: Multi-Language Broadcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the single-language chunked translation server into a multi-language broadcast system: English speech in → English + Malayalam + Telugu + Hindi pushed simultaneously to a projector page and per-person phone pages.

**Architecture:** The monolithic `server.py` splits into focused modules (config, audio buffer, STT, translator, pipeline, broadcast hub) wired by a thin FastAPI app. The chunked engine (silence-triggered) is kept for M1; the streaming engine replaces it in M2. Translation switches NLLB → IndicTrans2 with all 3 targets in one batched GPU call; NLLB remains as a config-selectable fallback.

**Tech Stack:** Python 3.10+, FastAPI + uvicorn (WebSockets), faster-whisper (`distil-large-v3`), HuggingFace transformers + IndicTransToolkit (IndicTrans2 `en-indic-dist-200M`), pytest + pytest-asyncio, vanilla JS frontend.

## Global Constraints

- Fully local: no network calls at runtime (model downloads happen once, on first start, from HuggingFace).
- Hardware target: RTX 3060 Laptop 6GB VRAM / 14GB RAM. Models loaded fp16 on CUDA; combined budget must stay under ~4GB VRAM.
- Whisper model: `distil-large-v3` (English-only — always pass `language="en"`).
- Translation model: `ai4bharat/indictrans2-en-indic-dist-200M`; fallback `facebook/nllb-200-distilled-600M`.
- Language codes: internal short codes `en, ml, te, hi`; FLORES codes `eng_Latn, mal_Mlym, tel_Telu, hin_Deva`.
- Server: host `0.0.0.0`, port `8080`. Audio: 16kHz mono PCM int16.
- Fast tests must run without GPU/models (`pytest` default skips `slow`); model tests marked `@pytest.mark.slow`.
- Python venv at `.venv/` (existing). Run all commands from repo root with `.venv/bin/python` / `.venv/bin/pytest`.

---

### Task 1: Test infrastructure, config module, AudioBuffer extraction

**Files:**
- Create: `config.py`, `audio_buffer.py`, `tests/test_audio_buffer.py`, `pytest.ini`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `config` constants (see code below) used by every later task.
- Produces: `AudioBuffer(sample_rate)` with `.add_chunk(pcm_bytes)`, `.should_process() -> bool`, `.get_audio_and_clear() -> np.ndarray[int16]`, `.duration_seconds() -> float`, `.clear()`.

- [ ] **Step 1: Add test deps and pytest config**

Append to `requirements.txt`:

```
IndicTransToolkit
pytest
pytest-asyncio
```

Create `pytest.ini`:

```ini
[pytest]
markers =
    slow: needs GPU + downloaded models
addopts = -m "not slow"
asyncio_mode = auto
```

Install: `.venv/bin/pip install pytest pytest-asyncio` (IndicTransToolkit installs in Task 3; it's heavy).

- [ ] **Step 2: Create `config.py`**

```python
"""Central configuration — every tunable lives here."""

# --- Models ---
WHISPER_MODEL = "distil-large-v3"          # English-only distilled Whisper
TRANSLATOR_BACKEND = "indictrans2"          # "indictrans2" | "nllb"
INDICTRANS2_MODEL = "ai4bharat/indictrans2-en-indic-dist-200M"
NLLB_MODEL = "facebook/nllb-200-distilled-600M"

# --- Languages ---
SOURCE_LANG = "en"
TARGET_LANGS = ["ml", "te", "hi"]           # translation targets (en is the source)
FLORES_CODES = {
    "en": "eng_Latn",
    "ml": "mal_Mlym",
    "te": "tel_Telu",
    "hi": "hin_Deva",
}

# --- Audio / chunking (M1 engine) ---
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 300      # RMS below this = silence
SILENCE_DURATION_S = 0.6     # trailing silence that triggers processing
MAX_BUFFER_S = 8.0           # force-process after this long
MIN_SPEECH_S = 0.5           # ignore blips shorter than this
MIN_TRIGGER_S = 1.5          # need at least this much audio before silence-trigger

# --- Broadcast ---
HISTORY_SIZE = 10            # sentences replayed to late-joining screens

# --- Server ---
HOST = "0.0.0.0"
PORT = 8080
```

- [ ] **Step 3: Write failing tests for AudioBuffer**

Create `tests/test_audio_buffer.py`:

```python
import numpy as np
from audio_buffer import AudioBuffer
from config import SAMPLE_RATE


def loud(seconds):
    """Loud int16 noise (well above SILENCE_THRESHOLD)."""
    n = int(seconds * SAMPLE_RATE)
    rng = np.random.default_rng(42)
    return (rng.uniform(-0.5, 0.5, n) * 20000).astype(np.int16).tobytes()


def silence(seconds):
    n = int(seconds * SAMPLE_RATE)
    return np.zeros(n, dtype=np.int16).tobytes()


def test_empty_buffer_not_processed():
    buf = AudioBuffer(SAMPLE_RATE)
    assert buf.should_process() is False


def test_short_speech_not_processed():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(0.3))
    assert buf.should_process() is False


def test_speech_then_trailing_silence_triggers():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(2.0))
    buf.add_chunk(silence(0.7))
    assert buf.should_process() is True


def test_continuous_speech_no_silence_waits():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(3.0))
    assert buf.should_process() is False


def test_max_buffer_forces_processing():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(8.5))
    assert buf.should_process() is True


def test_get_audio_and_clear():
    buf = AudioBuffer(SAMPLE_RATE)
    buf.add_chunk(loud(2.0))
    audio = buf.get_audio_and_clear()
    assert audio.dtype == np.int16
    assert len(audio) == 2 * SAMPLE_RATE
    assert buf.duration_seconds() == 0.0
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_audio_buffer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audio_buffer'`

- [ ] **Step 5: Create `audio_buffer.py`** (extracted from `server.py`, constants from config)

```python
"""Rolling audio buffer with trailing-silence detection (M1 chunked engine)."""
import numpy as np

from config import (
    SAMPLE_RATE, SILENCE_THRESHOLD, SILENCE_DURATION_S,
    MAX_BUFFER_S, MIN_SPEECH_S, MIN_TRIGGER_S,
)


class AudioBuffer:
    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.buffer = bytearray()

    def add_chunk(self, pcm_bytes):
        self.buffer.extend(pcm_bytes)

    def duration_seconds(self):
        return len(self.buffer) / (self.sample_rate * 2)  # int16 = 2 bytes

    def has_trailing_silence(self):
        check_bytes = int(SILENCE_DURATION_S * self.sample_rate) * 2
        if len(self.buffer) < check_bytes:
            return False
        tail = np.frombuffer(bytes(self.buffer[-check_bytes:]), dtype=np.int16)
        rms = np.sqrt(np.mean(tail.astype(np.float32) ** 2))
        return rms < SILENCE_THRESHOLD

    def should_process(self):
        duration = self.duration_seconds()
        if duration < MIN_SPEECH_S:
            return False
        if duration >= MAX_BUFFER_S:
            return True
        return duration >= MIN_TRIGGER_S and self.has_trailing_silence()

    def get_audio_and_clear(self):
        audio = np.frombuffer(bytes(self.buffer), dtype=np.int16)
        self.buffer.clear()
        return audio

    def clear(self):
        self.buffer.clear()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_audio_buffer.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add config.py audio_buffer.py tests/test_audio_buffer.py pytest.ini requirements.txt
git commit -m "feat: extract config and AudioBuffer with tests"
```

---

### Task 2: BroadcastHub

**Files:**
- Create: `hub.py`, `tests/test_hub.py`

**Interfaces:**
- Consumes: `config.HISTORY_SIZE`.
- Produces: `BroadcastHub(history_size=HISTORY_SIZE)` with async `.register(ws)`, `.unregister(ws)`, `.broadcast(dict)`, `.publish_sentence(sentence_id: int, en_text: str)`, `.publish_translation(sentence_id: int, translations: dict)`, `.publish_status(state: str)`. Any object with `async send_json(dict)` works as a client. Register replays history as `{"type": "history", "sentences": [{"id", "en", "translations"}]}`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_hub.py`:

```python
import pytest
from hub import BroadcastHub


class FakeWS:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, msg):
        if self.fail:
            raise ConnectionError("gone")
        self.sent.append(msg)


async def test_register_replays_empty_history():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    assert ws.sent == [{"type": "history", "sentences": []}]


async def test_sentence_then_translation_broadcast_to_all():
    hub = BroadcastHub()
    a, b = FakeWS(), FakeWS()
    await hub.register(a)
    await hub.register(b)
    await hub.publish_sentence(1, "God is love.")
    await hub.publish_translation(1, {"ml": "M", "te": "T", "hi": "H"})
    for ws in (a, b):
        assert {"type": "sentence", "id": 1, "en": "God is love."} in ws.sent
        assert {"type": "translation", "id": 1, "ml": "M", "te": "T", "hi": "H"} in ws.sent


async def test_late_joiner_gets_history_with_translations():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Hello.")
    await hub.publish_translation(1, {"ml": "M", "te": "T", "hi": "H"})
    late = FakeWS()
    await hub.register(late)
    history = late.sent[0]
    assert history["type"] == "history"
    assert history["sentences"] == [
        {"id": 1, "en": "Hello.", "translations": {"ml": "M", "te": "T", "hi": "H"}}
    ]


async def test_history_capped():
    hub = BroadcastHub(history_size=3)
    for i in range(5):
        await hub.publish_sentence(i, f"s{i}")
    late = FakeWS()
    await hub.register(late)
    ids = [s["id"] for s in late.sent[0]["sentences"]]
    assert ids == [2, 3, 4]


async def test_dead_client_is_dropped():
    hub = BroadcastHub()
    good, dead = FakeWS(), FakeWS(fail=True)
    await hub.register(good)
    hub._clients.add(dead)  # simulate a client whose socket died
    await hub.publish_status("listening")
    assert dead not in hub._clients
    assert {"type": "status", "state": "listening"} in good.sent


async def test_unregister_stops_delivery():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    hub.unregister(ws)
    await hub.publish_status("listening")
    assert len(ws.sent) == 1  # only the history replay
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_hub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hub'`

- [ ] **Step 3: Implement `hub.py`**

```python
"""Broadcast hub — pushes caption events to every connected screen."""
import logging
from collections import deque

from config import HISTORY_SIZE

logger = logging.getLogger(__name__)


class BroadcastHub:
    def __init__(self, history_size=HISTORY_SIZE):
        self._clients = set()
        self._history = deque(maxlen=history_size)

    async def register(self, ws):
        await ws.send_json({"type": "history", "sentences": list(self._history)})
        self._clients.add(ws)

    def unregister(self, ws):
        self._clients.discard(ws)

    async def broadcast(self, message):
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)
            logger.info("Dropped dead client (%d left)", len(self._clients))

    async def publish_sentence(self, sentence_id, en_text):
        self._history.append({"id": sentence_id, "en": en_text, "translations": None})
        await self.broadcast({"type": "sentence", "id": sentence_id, "en": en_text})

    async def publish_translation(self, sentence_id, translations):
        for item in self._history:
            if item["id"] == sentence_id:
                item["translations"] = translations
                break
        await self.broadcast({"type": "translation", "id": sentence_id, **translations})

    async def publish_status(self, state):
        await self.broadcast({"type": "status", "state": state})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_hub.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add hub.py tests/test_hub.py
git commit -m "feat: BroadcastHub with history replay and dead-client cleanup"
```

---

### Task 3: Translator module (IndicTrans2 + NLLB fallback)

**Files:**
- Create: `translator.py`, `tests/test_translator.py`
- Modify: nothing else (server.py keeps its old inline copy until Task 5)

**Interfaces:**
- Consumes: `config.TARGET_LANGS`, `config.FLORES_CODES`, `config.INDICTRANS2_MODEL`, `config.NLLB_MODEL`, `config.TRANSLATOR_BACKEND`.
- Produces: `load_translator() -> translator` where translator has `async translate_all(text: str) -> dict` returning `{"ml": str, "te": str, "hi": str}`. Also classes `IndicTrans2Translator`, `NLLBTranslator` with the same interface.

- [ ] **Step 1: Install IndicTransToolkit**

Run: `.venv/bin/pip install IndicTransToolkit`
Expected: installs cleanly. If it fails to build, STOP and report — the factory's NLLB fallback covers us, but we want to know.

- [ ] **Step 2: Write the (slow) integration tests**

Create `tests/test_translator.py`:

```python
import pytest

pytestmark = pytest.mark.slow  # every test here needs GPU + model download

ML = (0x0D00, 0x0D7F)   # Malayalam unicode block
TE = (0x0C00, 0x0C7F)   # Telugu
HI = (0x0900, 0x097F)   # Devanagari


def has_script(text, block):
    lo, hi = block
    return any(lo <= ord(ch) <= hi for ch in text)


@pytest.fixture(scope="module")
def translator():
    from translator import load_translator
    return load_translator()


async def test_translates_into_three_scripts(translator):
    out = await translator.translate_all("God is love and He loves the world.")
    assert set(out.keys()) == {"ml", "te", "hi"}
    assert has_script(out["ml"], ML), f"not Malayalam: {out['ml']}"
    assert has_script(out["te"], TE), f"not Telugu: {out['te']}"
    assert has_script(out["hi"], HI), f"not Hindi: {out['hi']}"


async def test_empty_text_returns_empties(translator):
    out = await translator.translate_all("")
    assert out == {"ml": "", "te": "", "hi": ""}
```

- [ ] **Step 3: Implement `translator.py`**

```python
"""Translation backends: IndicTrans2 (primary) and NLLB (fallback).

Both expose:  async translate_all(text) -> {"ml": ..., "te": ..., "hi": ...}
IndicTrans2 translates all targets in ONE batched generate call.
"""
import asyncio
import logging

import torch

from config import (
    TARGET_LANGS, FLORES_CODES, INDICTRANS2_MODEL, NLLB_MODEL, TRANSLATOR_BACKEND,
)

logger = logging.getLogger(__name__)


class _AsyncTranslatorBase:
    """Serializes GPU access and keeps the event loop unblocked."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def translate_all(self, text):
        if not text or not text.strip():
            return {lang: "" for lang in TARGET_LANGS}
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.translate_all_sync, text)


class IndicTrans2Translator(_AsyncTranslatorBase):
    def __init__(self, model_name=INDICTRANS2_MODEL):
        super().__init__()
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        try:
            from IndicTransToolkit.processor import IndicProcessor
        except ImportError:  # older package layout
            from IndicTransToolkit import IndicProcessor

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        logger.info("Loading IndicTrans2 (%s) on %s...", model_name, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            model_name, trust_remote_code=True, torch_dtype=dtype
        ).to(self.device)
        self.model.eval()
        self.processor = IndicProcessor(inference=True)
        logger.info("IndicTrans2 loaded.")

    def translate_all_sync(self, text):
        # Build one batch: the same sentence tagged for each target language.
        batch = []
        for lang in TARGET_LANGS:
            batch.extend(
                self.processor.preprocess_batch(
                    [text], src_lang=FLORES_CODES["en"], tgt_lang=FLORES_CODES[lang]
                )
            )
        inputs = self.tokenizer(
            batch, truncation=True, padding="longest",
            return_tensors="pt", max_length=256,
        ).to(self.device)

        with torch.no_grad():
            out = self.model.generate(
                **inputs, max_length=256, num_beams=1, num_return_sequences=1
            )

        decoded = self.tokenizer.batch_decode(
            out, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )
        result = {}
        for lang, raw in zip(TARGET_LANGS, decoded):
            result[lang] = self.processor.postprocess_batch(
                [raw], lang=FLORES_CODES[lang]
            )[0]
        return result


class NLLBTranslator(_AsyncTranslatorBase):
    def __init__(self, model_name=NLLB_MODEL):
        super().__init__()
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading NLLB (%s) on %s...", model_name, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(self.device)
        self.model.eval()
        logger.info("NLLB loaded.")

    def translate_all_sync(self, text):
        result = {}
        for lang in TARGET_LANGS:
            self.tokenizer.src_lang = FLORES_CODES["en"]
            inputs = self.tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512
            ).to(self.device)
            tgt_id = self.tokenizer.convert_tokens_to_ids(FLORES_CODES[lang])
            with torch.no_grad():
                out = self.model.generate(
                    **inputs, forced_bos_token_id=tgt_id, max_new_tokens=512
                )
            result[lang] = self.tokenizer.batch_decode(out, skip_special_tokens=True)[0]
        return result


def load_translator():
    """Build the configured backend; fall back to NLLB if IndicTrans2 fails."""
    if TRANSLATOR_BACKEND == "indictrans2":
        try:
            return IndicTrans2Translator()
        except Exception:
            logger.exception("IndicTrans2 failed to load — falling back to NLLB")
    return NLLBTranslator()
```

- [ ] **Step 4: Run the slow tests (downloads ~1GB on first run; needs internet once)**

Run: `.venv/bin/pytest tests/test_translator.py -v -m slow`
Expected: 2 passed. Note startup time (model download + load). If `IndicTrans2Translator` fell back to NLLB, the log says so — investigate before continuing; the scripts assertion passes either way, but we want IndicTrans2.

- [ ] **Step 5: Verify fast suite still green and skips slow**

Run: `.venv/bin/pytest -v`
Expected: audio_buffer + hub tests pass; translator tests shown as deselected.

- [ ] **Step 6: Commit**

```bash
git add translator.py tests/test_translator.py
git commit -m "feat: IndicTrans2 batched translator with NLLB fallback"
```

---

### Task 4: STT module on distil-large-v3

**Files:**
- Create: `stt.py`, `tests/test_stt.py`

**Interfaces:**
- Consumes: `config.WHISPER_MODEL`, `config.SOURCE_LANG`.
- Produces: `WhisperSTT()` with `async transcribe(audio_np: np.ndarray[int16|float32]) -> str` (empty string when no speech).

- [ ] **Step 1: Write the (slow) test**

Create `tests/test_stt.py`:

```python
import numpy as np
import pytest

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def stt():
    from stt import WhisperSTT
    return WhisperSTT()


async def test_silence_transcribes_to_empty(stt):
    silence = np.zeros(2 * 16000, dtype=np.int16)
    text = await stt.transcribe(silence)
    assert text == ""
```

- [ ] **Step 2: Implement `stt.py`** (extracted from `server.py`, model swapped)

```python
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
```

- [ ] **Step 3: Run the slow test (downloads ~1.5GB first run)**

Run: `.venv/bin/pytest tests/test_stt.py -v -m slow`
Expected: 1 passed.

- [ ] **Step 4: Manual sanity check with real speech**

Record 5 seconds and transcribe:

```bash
timeout 5 parecord --channels=1 --rate=16000 /tmp/say_something.wav
.venv/bin/python -c "
import asyncio, wave, numpy as np
from stt import WhisperSTT
w = wave.open('/tmp/say_something.wav')
audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
print(asyncio.run(WhisperSTT().transcribe(audio)))
"
```

Speak a sentence during the recording; expected: your words printed (close enough is fine).

- [ ] **Step 5: Commit**

```bash
git add stt.py tests/test_stt.py
git commit -m "feat: STT module on distil-large-v3"
```

---

### Task 5: UtterancePipeline + thin server rewire

**Files:**
- Create: `pipeline.py`, `tests/test_pipeline.py`, `tests/test_server.py`
- Modify: `server.py` (full rewrite — becomes thin wiring)

**Interfaces:**
- Consumes: `WhisperSTT.transcribe`, `translate_all`, `BroadcastHub.publish_*`, `AudioBuffer`.
- Produces: `UtterancePipeline(stt, translator, hub)` with `async process(audio_np) -> tuple[int, str] | None` (sentence id + English text, or None if no speech).
- Produces: `create_app(stt=None, translator=None) -> FastAPI` in `server.py`; WebSocket routes `/ws/mic` (binary PCM in, JSON feedback out) and `/ws/captions` (JSON out per hub protocol); pages `/mic`, `/display`, `/view` served from `static/`; `/` redirects to `/display`.

- [ ] **Step 1: Write failing pipeline tests**

Create `tests/test_pipeline.py`:

```python
from hub import BroadcastHub
from pipeline import UtterancePipeline
from tests.test_hub import FakeWS


class StubSTT:
    def __init__(self, text):
        self.text = text

    async def transcribe(self, audio_np):
        return self.text


class StubTranslator:
    async def translate_all(self, text):
        return {"ml": f"ml:{text}", "te": f"te:{text}", "hi": f"hi:{text}"}


async def test_speech_flows_to_screens():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT("Hello world."), StubTranslator(), hub)

    result = await pipe.process(None)  # stub ignores audio

    assert result == (1, "Hello world.")
    types = [m["type"] for m in screen.sent]
    assert types == ["history", "sentence", "translation"]
    assert screen.sent[2]["ml"] == "ml:Hello world."


async def test_ids_increment():
    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT("Hi."), StubTranslator(), hub)
    assert (await pipe.process(None))[0] == 1
    assert (await pipe.process(None))[0] == 2


async def test_empty_transcription_publishes_nothing():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT(""), StubTranslator(), hub)

    assert await pipe.process(None) is None
    assert [m["type"] for m in screen.sent] == ["history"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Implement `pipeline.py`**

```python
"""One utterance through the system: audio -> English -> 3 translations -> screens."""
import logging
import time

logger = logging.getLogger(__name__)


class UtterancePipeline:
    def __init__(self, stt, translator, hub):
        self.stt = stt
        self.translator = translator
        self.hub = hub
        self._counter = 0

    async def process(self, audio_np):
        t0 = time.time()
        text = await self.stt.transcribe(audio_np)
        if not text:
            return None
        t_stt = time.time() - t0

        self._counter += 1
        sid = self._counter
        await self.hub.publish_sentence(sid, text)

        t1 = time.time()
        translations = await self.translator.translate_all(text)
        await self.hub.publish_translation(sid, translations)
        logger.info(
            "#%d stt=%.2fs mt=%.2fs: %s", sid, t_stt, time.time() - t1, text
        )
        return sid, text
```

- [ ] **Step 4: Run pipeline tests**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: 3 passed

- [ ] **Step 5: Write failing server integration test** (fast — uses stubs, no models)

Create `tests/test_server.py`:

```python
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
```

- [ ] **Step 6: Rewrite `server.py`**

```python
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
    app = FastAPI(title="Church Live Translation")
    hub = BroadcastHub()
    app.state.hub = hub
    app.state.stt = stt
    app.state.translator = translator
    app.state.pipeline = None

    @app.on_event("startup")
    async def startup():
        if app.state.stt is None:
            from stt import WhisperSTT
            app.state.stt = WhisperSTT()
        if app.state.translator is None:
            from translator import load_translator
            app.state.translator = load_translator()
        app.state.pipeline = UtterancePipeline(app.state.stt, app.state.translator, hub)
        logger.info("Server ready on http://%s:%d", HOST, PORT)

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
                        result = await app.state.pipeline.process(buf.get_audio_and_clear())
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
```

- [ ] **Step 7: Create placeholder static pages so the server test passes**

The real pages come in Tasks 6–8. Create minimal valid files now:

```bash
mkdir -p static
for f in mic display view; do
  printf '<!DOCTYPE html><html><head><title>%s</title></head><body>%s placeholder</body></html>' "$f" "$f" > static/$f.html
done
```

- [ ] **Step 8: Run the full fast suite**

Run: `.venv/bin/pytest -v`
Expected: all fast tests pass (audio_buffer 6, hub 6, pipeline 3, server 2).

- [ ] **Step 9: Commit**

```bash
git add pipeline.py server.py tests/test_pipeline.py tests/test_server.py static/
git commit -m "feat: utterance pipeline and multi-screen server wiring"
```

---

### Task 6: Shared styles + display page (projector)

**Files:**
- Create: `static/common.css`, `static/display.html`, `static/display.js` (replaces placeholder)

**Interfaces:**
- Consumes: `/ws/captions` protocol: `history`, `sentence {id,en}`, `translation {id,ml,te,hi}`, `status {state}`.
- Produces: `static/common.css` classes (`caption-row`, `lang-line`, `lang-tag`, `pending`) reused by the view page in Task 7.

- [ ] **Step 1: Create `static/common.css`**

```css
/* Shared look for all caption screens — dark, high-contrast, projector-friendly */
:root { color-scheme: dark; }
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: #0b0f14;
  color: #e8eef5;
  font-family: system-ui, "Noto Sans", "Noto Sans Malayalam", "Noto Sans Telugu",
               "Noto Sans Devanagari", sans-serif;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.6rem 1.2rem; font-size: 0.9rem; color: #7d8fa3;
  border-bottom: 1px solid #1c2733;
}

#status-dot {
  display: inline-block; width: 0.7em; height: 0.7em; border-radius: 50%;
  background: #ef4444; margin-right: 0.4em;
}
#status-dot.connected { background: #22c55e; }

main { flex: 1; overflow-y: auto; padding: 1.2rem; display: flex;
       flex-direction: column; justify-content: flex-end; gap: 1.4rem; }

.caption-row { border-left: 4px solid #2b3948; padding-left: 1rem; }
.caption-row:last-child { border-left-color: #3b82f6; }

.lang-line { display: flex; gap: 0.8rem; align-items: baseline; margin: 0.35rem 0; }
.lang-tag {
  flex: 0 0 2.6em; font-size: 0.55em; font-weight: 700; letter-spacing: 0.08em;
  color: #7d8fa3; text-transform: uppercase;
}
.lang-text { flex: 1; }
.lang-line.pending .lang-text { color: #4b5a6a; font-style: italic; }

.empty-state { color: #4b5a6a; text-align: center; margin: auto; font-size: 1.4rem; }
```

- [ ] **Step 2: Create `static/display.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Live Translation — Display</title>
  <link rel="stylesheet" href="/static/common.css">
  <style>
    main { font-size: clamp(1.4rem, 3.4vw, 2.6rem); }
    footer { padding: 0.5rem 1.2rem; color: #7d8fa3; font-size: 0.95rem;
             border-top: 1px solid #1c2733; text-align: center; }
  </style>
</head>
<body>
  <header>
    <span><span id="status-dot"></span><span id="status-text">connecting…</span></span>
    <span>Live Translation</span>
  </header>
  <main id="captions">
    <div class="empty-state" id="empty">Waiting for the speaker…</div>
  </main>
  <footer>On your phone: <strong id="view-url"></strong></footer>
  <script src="/static/display.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `static/display.js`**

```javascript
/* Projector display: shows the last N sentences in all four languages. */
const SHOW_LAST = 3;
const LANGS = [
  ["en", "EN"], ["ml", "ML"], ["te", "TE"], ["hi", "HI"],
];

const state = { sentences: [] };  // [{id, en, translations|null}]
const els = {
  captions: document.getElementById("captions"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  viewUrl: document.getElementById("view-url"),
};
els.viewUrl.textContent = `http://${window.location.host}/view`;

function upsert(sentence) {
  const i = state.sentences.findIndex((s) => s.id === sentence.id);
  if (i >= 0) state.sentences[i] = { ...state.sentences[i], ...sentence };
  else state.sentences.push(sentence);
  state.sentences = state.sentences.slice(-SHOW_LAST);
  render();
}

function render() {
  els.empty.style.display = state.sentences.length ? "none" : "";
  els.captions.querySelectorAll(".caption-row").forEach((n) => n.remove());
  for (const s of state.sentences) {
    const row = document.createElement("div");
    row.className = "caption-row";
    for (const [code, tag] of LANGS) {
      const text = code === "en" ? s.en : s.translations?.[code];
      const line = document.createElement("div");
      line.className = "lang-line" + (text ? "" : " pending");
      const tagEl = document.createElement("span");
      tagEl.className = "lang-tag";
      tagEl.textContent = tag;
      const textEl = document.createElement("span");
      textEl.className = "lang-text";
      textEl.textContent = text || "…";
      line.append(tagEl, textEl);
      row.appendChild(line);
    }
    els.captions.appendChild(row);
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/captions`);

  ws.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "history") {
      state.sentences = msg.sentences.slice(-SHOW_LAST);
      render();
    } else if (msg.type === "sentence") {
      upsert({ id: msg.id, en: msg.en, translations: null });
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      upsert({ id, translations });
    }
  };
  ws.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}
connect();
```

Note `upsert({id, translations})` merges onto the existing row — `en` stays from the earlier sentence message.

- [ ] **Step 4: Manual verification with stub server**

Create nothing new — run the real server with stubs via a throwaway command:

```bash
.venv/bin/python -c "
import uvicorn
from server import create_app
from tests.test_pipeline import StubSTT, StubTranslator
app = create_app(stt=StubSTT('For God so loved the world.'), translator=StubTranslator())
uvicorn.run(app, host='127.0.0.1', port=8080)
"
```

Open `http://127.0.0.1:8080/display` in a browser, then in another terminal drive a fake mic:

```bash
.venv/bin/python -c "
import numpy as np, json
from websockets.sync.client import connect
rng = np.random.default_rng(1)
loud = (rng.uniform(-0.5, 0.5, 32000) * 20000).astype(np.int16).tobytes()
quiet = np.zeros(16000, dtype=np.int16).tobytes()
with connect('ws://127.0.0.1:8080/ws/mic') as ws:
    ws.send(loud); ws.send(quiet)
    for _ in range(3): print(ws.recv())
"
```

Expected in browser: a caption row appears — EN line with the stub sentence, ML/TE/HI lines showing `ml:…`, `te:…`, `hi:…`. Header dot green. Kill and restart the server; page must show "reconnecting…" then recover.

- [ ] **Step 5: Run fast suite (pages test now covers real display.html)**

Run: `.venv/bin/pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add static/common.css static/display.html static/display.js
git commit -m "feat: projector display page with 4-language captions"
```

---

### Task 7: Personal view page (phones)

**Files:**
- Create: `static/view.html`, `static/view.js` (replaces placeholder)

**Interfaces:**
- Consumes: `/ws/captions` protocol and `static/common.css` classes from Task 6.

- [ ] **Step 1: Create `static/view.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Live Translation — My Language</title>
  <link rel="stylesheet" href="/static/common.css">
  <style>
    main { font-size: 1.5rem; }
    #picker { display: flex; gap: 0.5rem; padding: 0.8rem 1.2rem; flex-wrap: wrap; }
    #picker button {
      flex: 1; min-width: 5.5rem; padding: 0.7rem 0.4rem; font-size: 1rem;
      background: #16202b; color: #e8eef5; border: 1px solid #2b3948;
      border-radius: 0.5rem; cursor: pointer;
    }
    #picker button.active { background: #1d4ed8; border-color: #3b82f6; }
  </style>
</head>
<body>
  <header>
    <span><span id="status-dot"></span><span id="status-text">connecting…</span></span>
    <span>Live Translation</span>
  </header>
  <nav id="picker">
    <button data-lang="en">English</button>
    <button data-lang="ml">മലയാളം</button>
    <button data-lang="te">తెలుగు</button>
    <button data-lang="hi">हिन्दी</button>
  </nav>
  <main id="captions">
    <div class="empty-state" id="empty">Waiting for the speaker…</div>
  </main>
  <script src="/static/view.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `static/view.js`**

```javascript
/* Personal phone view: one chosen language, remembered across visits. */
const KEEP_LAST = 10;

const state = {
  lang: localStorage.getItem("lang") || "ml",
  sentences: [],  // [{id, en, translations|null}]
};
const els = {
  captions: document.getElementById("captions"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  buttons: document.querySelectorAll("#picker button"),
};

els.buttons.forEach((btn) => {
  btn.addEventListener("click", () => {
    state.lang = btn.dataset.lang;
    localStorage.setItem("lang", state.lang);
    updatePicker();
    render();
  });
});

function updatePicker() {
  els.buttons.forEach((b) => b.classList.toggle("active", b.dataset.lang === state.lang));
}

function textFor(s) {
  if (state.lang === "en") return s.en;
  return s.translations?.[state.lang] || null;  // null -> still translating
}

function upsert(sentence) {
  const i = state.sentences.findIndex((x) => x.id === sentence.id);
  if (i >= 0) state.sentences[i] = { ...state.sentences[i], ...sentence };
  else state.sentences.push(sentence);
  state.sentences = state.sentences.slice(-KEEP_LAST);
  render();
}

function render() {
  els.empty.style.display = state.sentences.length ? "none" : "";
  els.captions.querySelectorAll(".caption-row").forEach((n) => n.remove());
  for (const s of state.sentences) {
    const text = textFor(s);
    const row = document.createElement("div");
    row.className = "caption-row";
    const line = document.createElement("div");
    line.className = "lang-line" + (text ? "" : " pending");
    const textEl = document.createElement("span");
    textEl.className = "lang-text";
    textEl.textContent = text || "…";
    line.appendChild(textEl);
    row.appendChild(line);
    els.captions.appendChild(row);
  }
  els.captions.scrollTop = els.captions.scrollHeight;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/captions`);
  ws.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "history") {
      state.sentences = msg.sentences.slice(-KEEP_LAST);
      render();
    } else if (msg.type === "sentence") {
      upsert({ id: msg.id, en: msg.en, translations: null });
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      upsert({ id, translations });
    }
  };
  ws.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

updatePicker();
connect();
```

- [ ] **Step 3: Manual verification**

Reuse the Task 6 stub-server + fake-mic commands. Open `http://127.0.0.1:8080/view`:
- Tap each language button — active button highlights, list re-renders in that language (stub strings like `ml:For God…`).
- Reload the page — chosen language persists (localStorage).
- Late join: drive the fake mic first, then open the page — history should populate immediately.

- [ ] **Step 4: Run fast suite**

Run: `.venv/bin/pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add static/view.html static/view.js
git commit -m "feat: personal phone view with language picker"
```

---

### Task 8: Mic page (move + adapt existing client)

**Files:**
- Move: `client.html` → `static/mic.html`, `client.js` → `static/mic.js`, `styles.css` → `static/styles.css`
- Modify: the three moved files (small targeted edits, listed exactly)

**Interfaces:**
- Consumes: `/ws/mic` — sends binary PCM + `{"type":"config"}` JSON; receives `{"type":"status","state":...}` and `{"type":"sentence","en":...}`.

- [ ] **Step 1: Move the files**

```bash
git mv client.html static/mic.html
git mv client.js static/mic.js
git mv styles.css static/styles.css
```

- [ ] **Step 2: Fix asset paths in `static/mic.html`**

Find the `<link rel="stylesheet" href="styles.css">` tag and the `<script src="client.js">` tag; change to:

```html
<link rel="stylesheet" href="/static/styles.css">
```

```html
<script src="/static/mic.js"></script>
```

- [ ] **Step 3: Point `static/mic.js` at the new endpoint and message schema**

Edit 1 — in `ServerConnection.connect()`, change the URL line:

```javascript
      const url = `${protocol}//${window.location.host}/ws/mic`;
```

Edit 2 — in `ws.onmessage`'s `switch (msg.type)`, replace the `'translation'` case with:

```javascript
          case 'sentence':
            if (this.onTranslation) this.onTranslation(msg);
            break;
```

Edit 3 — in `ClientApp._setupConnection()`, replace the `onTranslation` handler body:

```javascript
    this.conn.onTranslation = (msg) => {
      this.display.showInterim('');
      this.display.addSegment(msg.en, 'heard ✓', null);
    };
```

Edit 4 — in `ws.onmessage`'s `'status'` case, the server now sends `msg.state` (not `msg.status`); change the handler call:

```javascript
          case 'status':
            if (this.onStatus) this.onStatus(msg.state, msg.message);
            break;
```

- [ ] **Step 4: Manual verification (real models this time)**

```bash
.venv/bin/python server.py
```

Wait for "Server ready". Open `http://127.0.0.1:8080/mic`, press Start, allow mic, speak a sentence, pause. Expected: page shows the transcribed English with "heard ✓" tag. Open `http://127.0.0.1:8080/display` in a second tab: all four languages appear for the sentence you spoke. Watch server log for timing lines (`#1 stt=…s mt=…s`).

- [ ] **Step 5: Run fast suite, commit**

Run: `.venv/bin/pytest -v` — all pass.

```bash
git add -A
git commit -m "feat: mic capture page on /mic endpoint"
```

---

### Task 9: Startup script, docs, and end-to-end verification

**Files:**
- Modify: `start.sh`, `README.md`

- [ ] **Step 1: Update `start.sh` URL block**

Replace the "Open this URL on your tablet" block (the `LOCAL_IP` section) with:

```bash
LOCAL_IP=$(hostname -I | awk '{print $1}')
echo "📺 Projector/tablet:  http://${LOCAL_IP}:8080/display"
echo "📱 Personal phones:   http://${LOCAL_IP}:8080/view"
echo "🎤 Microphone page:   http://${LOCAL_IP}:8080/mic"
```

- [ ] **Step 2: Update `README.md` file table**

Replace the rows for `client.html / client.js / styles.css` with:

```markdown
| `config.py` | every tunable (models, thresholds, port) |
| `stt.py` / `translator.py` / `pipeline.py` / `hub.py` / `audio_buffer.py` | the five pipeline modules |
| `static/mic.*`, `static/display.*`, `static/view.*` | the three web pages |
| `tests/` | fast unit tests + `-m slow` model tests |
```

- [ ] **Step 3: Full end-to-end dress rehearsal (two devices)**

1. `./start.sh` on the laptop.
2. Laptop browser → `/mic`, Start, speak several sentences with pauses.
3. Second device (phone on same WiFi) → `http://<laptop-ip>:8080/view`, pick Malayalam.
4. Verify: display tab shows 4 languages within ~3–6s of each pause; phone shows Malayalam only; phone reload → history reappears; kill server (Ctrl-C) → both pages show "reconnecting…"; restart server → pages recover without manual reload.

- [ ] **Step 4: Run everything one last time**

Run: `.venv/bin/pytest -v` (fast) and `.venv/bin/pytest -v -m slow` (models)
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add start.sh README.md
git commit -m "docs: M1 startup URLs and module table"
```

---

## Plan Self-Review Notes

- Spec M1 coverage: IndicTrans2 batched translator ✓ (Task 3), distil-large-v3 ✓ (Task 4), BroadcastHub + history ✓ (Task 2), `/display` ✓ (Task 6), `/view` ✓ (Task 7), mic page ✓ (Task 8), chunked engine retained ✓ (Task 1/5). `/admin`, transcripts, wake lock, watchdog, QR are M3 by spec; `partial` messages are M2.
- Deliberate deviation: none.
- Type consistency: `translate_all -> {"ml","te","hi"}` used by pipeline (Task 5) and stubbed identically in tests; hub method names match between Tasks 2/5; `create_app(stt=, translator=)` matches test usage.
