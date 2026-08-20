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
2. **Cut** — a chunk is transcribed when the buffer holds real speech (silence never reaches Whisper — it hallucinates on it). If the text doesn't end in `.`/`?`/`!` it is held and joined to the next chunk, so the translator only ever sees whole sentences. *(True streaming with LocalAgreement is still M2; today a chunk is the unit.)*
3. **Translate** — each finished English sentence goes through [IndicTrans2](https://github.com/AI4Bharat/IndicTrans2) (AI4Bharat), which produces Malayalam, Telugu, and Hindi in a single batched GPU call.
4. **Broadcast** — every connected screen gets JSON updates over WebSocket: English the moment Whisper produces it (revised in place as a held sentence grows), the translation once the sentence is complete. Every translated utterance is appended to `transcripts/<date>.jsonl` with its English, all three translations, and STT/MT timings.

Expected feel: English appears ~1–1.5s behind the speaker; translations ~2.5–4s behind (a sentence must finish before it can be translated).

## The pages

| URL | Who | What |
|---|---|---|
| `/display` | tablet plugged into the projector | all 4 languages, big type, scannable QR code to `/view` |
| `/view` | anyone's phone on the same WiFi | choose your language, read along as one flowing transcript; scroll back, resize text, screen stays awake |
| `/admin` | the operator | pick audio source, see mic level (dead-mic warning), pause, latency stats |

## Running it

```bash
./start.sh   # creates venv, checks models (first run downloads ~3GB — needs internet ONCE), starts server
```

`start.sh` prints the URLs **and names the WiFi network phones must be joined to** — that is the usual reason a phone "can't open the link", especially in a building with several similarly-named networks. On the projector, people scan the QR in the footer rather than typing an address.

Sunday checklist: wall power, audio cable in, one command, point tablet at the `/display` URL.

If a phone still cannot connect, check in this order: is it on the WiFi `start.sh` named? is the address typed with `http://` in front (browsers force https otherwise)? then `sudo iptables -S INPUT`.

## Project status & files

Design approved 2026-07-04 — see [`docs/superpowers/specs/2026-07-04-church-translation-design.md`](docs/superpowers/specs/2026-07-04-church-translation-design.md). Being built in three milestones: **M1** multi-language + broadcast on the existing chunked engine → **M2** streaming captions → **M3** church-day polish (admin page, watchdog). Transcripts landed early — without a record there was no way to measure whether an STT or MT change helped.

| File | Role |
|---|---|
| `server.py` | FastAPI + WebSocket server, Whisper + translation pipeline |
| `config.py` | every tunable (models, thresholds, port) |
| `stt.py` / `translator.py` / `pipeline.py` / `hub.py` / `audio_buffer.py` | the five pipeline modules |
| `transcript_log.py` | append-only `transcripts/<date>.jsonl` record of the service |
| `netinfo.py` | picks the one reachable URL out of this machine's many addresses |
| `qr.py` | offline QR code for `/view`, served at `/qr.svg` |
| `static/mic.*`, `static/display.*`, `static/view.*` | the three web pages |
| `tests/` | fast unit tests, `-m slow` model tests, `-m browser` headless-Chrome tests for the caption pages |
| `start.sh` | one-command startup |
| `archive/` | first prototype (browser-only, cloud translation) — superseded |
| `docs/superpowers/specs/` | design documents |
| `LEARNING.md` | plain-language guide to every concept this project touches |

## Hardware this targets

ASUS ROG Zephyrus G15 (RTX 3060 Laptop 6GB VRAM, 14GB RAM). Both models together fit in ~3–4GB VRAM. Note: the laptop's internal mic required a kernel-level fix on Linux (see `~/mic-fix/`); the fix is installed and persistent.
