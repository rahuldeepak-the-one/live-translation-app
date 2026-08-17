# Church Live Translation

Live captions for a church service: one person speaks **English** into a mic, and screens show the words in **English, Malayalam (മലയാളം), Telugu (తెలుగు), and Hindi (हिन्दी)** — a few seconds behind the speaker. Everything runs locally on one laptop (RTX 3060). No cloud, no cost per use, no internet needed at the venue.

## How it works

```
 speaker's voice                LAPTOP (the brain)                    screens
 ───────────────   ┌──────────────────────────────────────┐   ─────────────────────
 mixer cable ───┐  │                                      │   tablet → projector
 laptop mic ────┼─▶│  1. LISTEN   Whisper (distil-large-v3)│──▶  /display all 4 languages
 phone mic ─────┘  │     speech → English text, streaming  │
                   │  2. CUT      sentence assembler       │   people's phones
                   │     words → complete sentences        │──▶  /view   pick a language
                   │  3. TRANSLATE IndicTrans2             │
                   │     English → ml + te + hi in one go  │   operator (you)
                   │  4. BROADCAST WebSocket hub           │──▶  /admin  mic meter, controls
                   └──────────────────────────────────────┘
```

1. **Listen** — audio (from a cable, the laptop mic, or a phone's browser) streams into the server as 16kHz samples. [Whisper](https://github.com/openai/whisper) (`distil-large-v3` via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)) re-transcribes the last ~10 seconds every ~0.7s. Words that come out identical twice in a row are "committed"; the still-changing tail shows on screen as grey live text. (This trick is called *LocalAgreement* — Whisper can't truly stream, so we fake it well.)
2. **Cut** — committed words are grouped into sentences (punctuation or a long pause ends one).
3. **Translate** — each finished English sentence goes through [IndicTrans2](https://github.com/AI4Bharat/IndicTrans2) (AI4Bharat), which produces Malayalam, Telugu, and Hindi in a single batched GPU call.
4. **Broadcast** — every connected screen gets JSON updates over WebSocket: English partials instantly, translations ~1–2s per sentence later. Everything is also appended to a transcript file (`transcripts/`).

Expected feel: English appears ~1–1.5s behind the speaker; translations ~2.5–4s behind (a sentence must finish before it can be translated).

## The pages

| URL | Who | What |
|---|---|---|
| `/display` | tablet plugged into the projector | all 4 languages, big type, QR code to `/view` |
| `/view` | anyone's phone on the same WiFi | choose your language, read along |
| `/admin` | the operator | pick audio source, see mic level (dead-mic warning), pause, latency stats |

## Running it

```bash
./start.sh   # creates venv, checks models (first run downloads ~3GB — needs internet ONCE), starts server
```

Then open the printed URL on the tablet/phones (same WiFi or the laptop's hotspot). Sunday checklist: wall power, audio cable in, one command, point tablet at URL.

## Project status & files

Design approved 2026-07-04 — see [`docs/superpowers/specs/2026-07-04-church-translation-design.md`](docs/superpowers/specs/2026-07-04-church-translation-design.md). Being built in three milestones: **M1** multi-language + broadcast on the existing chunked engine → **M2** streaming captions → **M3** church-day polish (admin page, transcripts, watchdog).

| File | Role |
|---|---|
| `server.py` | FastAPI + WebSocket server, Whisper + translation pipeline |
| `config.py` | every tunable (models, thresholds, port) |
| `stt.py` / `translator.py` / `pipeline.py` / `hub.py` / `audio_buffer.py` | the five pipeline modules |
| `static/mic.*`, `static/display.*`, `static/view.*` | the three web pages |
| `tests/` | fast unit tests + `-m slow` model tests |
| `start.sh` | one-command startup |
| `archive/` | first prototype (browser-only, cloud translation) — superseded |
| `docs/superpowers/specs/` | design documents |
| `LEARNING.md` | plain-language guide to every concept this project touches |

## Hardware this targets

ASUS ROG Zephyrus G15 (RTX 3060 Laptop 6GB VRAM, 14GB RAM). Both models together fit in ~3–4GB VRAM. Note: the laptop's internal mic required a kernel-level fix on Linux (see `~/mic-fix/`); the fix is installed and persistent.
