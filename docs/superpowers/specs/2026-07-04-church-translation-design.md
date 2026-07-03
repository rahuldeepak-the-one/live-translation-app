# Church Live Translation — Design Spec

**Date:** 2026-07-04
**Status:** Approved pending user review

## Goal

One person speaks English into a microphone during a ~3-hour church service. Live captions appear on screens in **English, Malayalam, Telugu, and Hindi** — flowing word-by-word for English, sentence-by-sentence for translations. Runs **entirely on the owner's laptop** (RTX 3060 Laptop 6GB VRAM, 14GB RAM): no cloud services, no per-use cost, works without internet at the venue.

## Decisions made (with rationale)

| Decision | Choice | Why |
|---|---|---|
| Display model | Projector tablet shows all 4 languages **and** phones can open a personal page | Covers whole congregation + personal readability |
| Caption style | Live and flowing (streaming partials) | User preference; feels fast |
| STT model | faster-whisper **`distil-large-v3`** | Source speech is always English; distil is ~5× faster than `medium` with better English accuracy → lower latency |
| Translation model | **IndicTrans2** (AI4Bharat) `en→indic dist-200M` first, `1B` if VRAM allows | Purpose-built for Indian languages, better ml/te/hi quality than NLLB; batches all 3 targets in one GPU call. NLLB-600M kept as coded fallback |
| Streaming method | Rolling-window re-transcription + **LocalAgreement** commit | Whisper can't natively stream; words identical across two consecutive passes are committed, rest shown as grey live tail |
| Audio input | Pluggable `AudioSource`: server-local capture (mixer cable / laptop mic) or phone-mic over WebSocket | Venue mic setup undecided; mixer line-out cable is the recommended target, phone mic is the fallback. Switching is an admin-page dropdown |
| Network | Any LAN; laptop hotspot as offline-safe default | Venue has projector + tablet; WiFi situation unclear |
| Server machine | This laptop travels to church | Design targets its exact specs |

## Architecture

```
 [mixer cable]──────┐   ┌────────────────── LAPTOP ─────────────────────┐
 [laptop mic]───────┼──▶│ AudioSource → StreamingSTT → SentenceAssembler │
 [phone mic page]───┘   │                                    │           │
                        │                              Translator        │
                        │                              (IndicTrans2)     │
                        │                                    │           │
                        │              BroadcastHub ◀────────┘           │
                        └───────────────────│────────────────────────────┘
                                            ▼ WebSocket JSON
                          /display (tablet→projector, all languages)
                          /view    (phones, one chosen language, QR entry)
                          /admin   (operator: source picker, level meter, status)
```

### Components (one job each)

1. **AudioSource** — abstract source of 16kHz mono PCM frames. Implementations: `LocalCapture` (system audio device — mixer cable or internal mic) and `BrowserMic` (existing WebSocket PCM path). Selected at runtime from `/admin`.
2. **StreamingSTT** — owns Whisper. Every ~0.7s re-transcribes the last ~10s of audio; words identical in two consecutive passes are *committed*, remainder is the *live tail*. Emits both.
3. **SentenceAssembler** — accumulates committed words; cuts at `.` `?` `!` or long pause; emits complete English sentences exactly once (monotonic ids).
4. **Translator** — owns IndicTrans2. One English sentence in → `{ml, te, hi}` out in a single batched generate. FIFO queue; never drops sentences.
5. **BroadcastHub** — tracks connected screens; pushes messages below; retains last ~10 sentences for late joiners; appends everything to a per-service transcript file.

### Message protocol (server → screens)

```json
{"type": "partial",     "text": "for God so loved the"}
{"type": "sentence",    "id": 41, "en": "For God so loved the world."}
{"type": "translation", "id": 41, "ml": "...", "te": "...", "hi": "..."}
{"type": "status",      "state": "listening", "audio_level": 0.4}
```

`id` ties translations to their sentence. Inbound phone-mic audio: binary PCM chunks every 250ms (unchanged from current client).

### Pages

- **/display** — last 2–3 sentences × 4 languages, large type, dark background, grey live tail, QR code corner → `/view`. Passive; holds a screen wake lock.
- **/view** — language picker (persisted), one language, last ~10 sentences.
- **/admin** — audio source dropdown, live level meter, NO-AUDIO warning, pause/resume, per-stage latency readout.

### Latency budget

Speech → English partial ≈ 1–1.5s. Speech → translations ≈ 2.5–4s (sentence must complete first). Config file exposes window sizes/intervals for tuning.

## 3-hour session resilience

- **Memory:** rolling audio window only; screens keep recent sentences; full transcript streams to `transcripts/<date>.txt` (few hundred KB per service).
- **Power/thermal:** GPU load is bursty, fine for hours; start script blocks system suspend/screen-blank; church checklist requires wall power.
- **Recovery matrix:** screen WiFi blip → auto-reconnect + re-sync; tablet sleep → wake lock; model/pipeline crash → watchdog restarts component, screens show "reconnecting"; dead mic/cable → admin level-meter flatline + NO AUDIO banner; songs/music → VAD filters, tested explicitly for hallucinated text.
- **Ops:** `./start.sh` = venv + model presence check (first run downloads ~3GB, needs internet once) + server + printed URLs/QR. One command on Sunday.

## Testing

- Unit tests for pure logic (SentenceAssembler, hub bookkeeping).
- Fixture WAVs (read Bible passage, sermon clip) through the real pipeline; assert key phrases in committed text and non-empty translations for all 3 languages.
- Latency harness printing per-stage timings from a fixture run.
- Dress rehearsal checklist: 3-hour soak with YouTube sermon, tablet + 2 phones, deliberate failure drills (WiFi kill, cable yank, tablet sleep), music segment.

## Milestones

- **M1 — Multi-language on current chunked engine.** IndicTrans2 (batch 3 targets), BroadcastHub, `/display` + `/view`. Usable at church; settles VRAM fit and translation quality first.
- **M2 — Streaming engine.** Rolling window + LocalAgreement replaces chunk buffer; config flag reverts to M1 behavior.
- **M3 — Church-day polish.** `/admin`, transcript files, wake lock, QR, suspend-blocking, watchdog, hotspot doc.

## Housekeeping

- `index.html` + `app.js` (gen-1 cloud prototype) move to `archive/`.
- Git repository initialized; specs live in `docs/superpowers/specs/`.

## Out of scope (YAGNI)

- Speech output (TTS) — captions only.
- Non-English source speech (Whisper large multi-lingual could do it later; not now).
- Recording/streaming video, remote (internet) viewers, authentication.
