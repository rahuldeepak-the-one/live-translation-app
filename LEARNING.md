# Learning Guide — the concepts behind this project

Everything this project touches, in plain language, in the order the audio flows. Each section says **what it is**, **where it lives in this project**, and **what to search/read to go deeper**. You don't need all of it to use the app — read a section when you're about to work on that part.

## 1. Digital audio basics (the raw material)

Sound is a wave; a microphone turns it into voltage; a sound card measures ("samples") that voltage thousands of times per second. Two numbers describe the result:

- **Sample rate** — measurements per second. We use **16,000 Hz (16kHz)** because that's what speech models are trained on. Music uses 44.1/48kHz; speech doesn't need it.
- **Bit depth / format** — how precisely each measurement is stored. We use **16-bit integers** ("PCM int16"): each sample is a number from −32768 to +32767. Silence = 0, loudness = big numbers, and **clipping** = the signal hitting ±32767 and getting flattened (we hit real clipping while fixing your mic — the waveform slams into the ceiling and words become unrecognizable to models).
- **RMS** — the "average loudness" of a chunk of samples; how our silence detection and the admin level-meter work.

*In this project:* `client.js` (mic → 16kHz PCM chunks), `AudioBuffer` in `server.py`.
*Search:* "PCM audio explained", "sample rate bit depth", "audio clipping waveform".

## 2. How Linux handles audio (the mic saga)

Layers, bottom to top: **hardware** (mic → codec chip like the ALC285) → **kernel driver** (ALSA — exposes "cards" and "capture devices", see `arecord -l`) → **sound server** (**PipeWire** — mixes apps, picks default devices, `pactl list sources`) → applications. Your laptop's internal mic was broken at the *kernel* layer: the driver defaulted to a mic pin with nothing physically wired to it. Lesson learned: when audio fails, test each layer separately (`arecord` for ALSA, `parecord` for PipeWire) instead of guessing at the top.

*Search:* "ALSA vs PipeWire explained", "arecord test microphone", "HDA pin configuration".

## 3. Speech-to-text / STT (Whisper)

A neural network that eats audio and outputs text. **Whisper** (OpenAI, open-source) is the workhorse: trained on ~680,000 hours of speech, handles accents and noise well. Variants matter:

- Sizes tiny → large-v3: accuracy vs speed vs VRAM.
- **`distil-large-v3`** (what we use): a "distilled" copy — a small model trained to imitate the big one — ~5× faster, English-only, *better* English accuracy than `medium`.
- **faster-whisper**: same model rebuilt on the CTranslate2 inference engine — ~4× faster than OpenAI's own code. Engine vs model: same brain, faster body.

*In this project:* `WhisperSTT` in `server.py`.
*Search:* "How Whisper works" (encoder-decoder, mel spectrogram), "knowledge distillation neural networks", "CTranslate2".

## 4. VAD — Voice Activity Detection

A tiny, fast model that answers one question: "is anyone speaking right now?" Used to skip silence/music instead of wasting the big model on it, and to notice pauses. Without VAD, Whisper fed with music or silence **hallucinates** — invents plausible text that nobody said (a famous failure mode; we explicitly test for it during singing).

*In this project:* `vad_filter=True` in the transcribe call (Silero VAD, bundled with faster-whisper).
*Search:* "Silero VAD", "Whisper hallucination silence".

## 5. Streaming STT — the LocalAgreement trick

Whisper fundamentally processes *finished* chunks; it cannot emit words as they're spoken. The workaround powering our "live and flowing" captions:

1. Keep a rolling window of the last ~10s of audio.
2. Re-transcribe it every ~0.7s (fast model makes this cheap).
3. Compare consecutive outputs: the shared prefix that came out **identical twice in a row** is almost certainly right → *commit* it (it never changes on screen). The differing tail is shown grey as a *live preview*.

That's **LocalAgreement** — simple, no special model needed, and the foundation of most "real-time Whisper" projects.

*Search:* "whisper_streaming LocalAgreement", "UFAL whisper streaming paper".

## 6. Machine translation (seq2seq, NLLB, IndicTrans2)

Translation models are **sequence-to-sequence transformers**: an *encoder* reads the English sentence into an internal representation; a *decoder* writes the target language word-by-word. Key ideas:

- **Tokenizers**: models don't see words — text is chopped into subword "tokens" (English "loved" might be one token; a Malayalam word may be several).
- **NLLB-200** (Meta, "No Language Left Behind"): one model, 200 languages — broad but shallow for any single pair. What the current code uses.
- **IndicTrans2** (AI4Bharat, IIT Madras): built *only* for English ↔ 22 Indian languages, trained on far more Indic data → clearly better Malayalam/Telugu/Hindi. What we're switching to.
- **Batching**: GPUs love doing identical work in parallel — translating one sentence into 3 languages at once costs barely more than into 1. Free speedup.

*Search:* "transformer encoder decoder explained", "subword tokenization BPE", "IndicTrans2 paper AI4Bharat".

## 7. GPUs, VRAM, and why models fit or don't

A model is millions of numbers ("weights") that must sit in the GPU's own memory (**VRAM** — your 3060 has 6GB) to run fast. Rough math: weights × bytes-per-weight. **float16** (2 bytes) halves memory vs float32 with no visible quality loss; **int8** halves it again with slight loss ("quantization"). Our budget: Whisper distil-large-v3 ≈ 1.5GB + IndicTrans2 ≈ 0.5–2.2GB + CUDA overhead ≈ 1GB — fits.

*Search:* "model quantization explained", "float16 vs int8 inference", "VRAM requirements LLM math".

## 8. The web plumbing (FastAPI, WebSocket, asyncio)

- **HTTP** is request→response→done. Useless for live captions.
- **WebSocket** is a phone call: the connection stays open, either side sends whenever it wants. The server pushes each caption to every connected screen the instant it exists.
- **FastAPI + uvicorn**: the Python web framework/server hosting both the pages and the WebSocket endpoints.
- **asyncio**: Python's way to juggle many connections in one process — code `await`s while idle so others run. Heavy model work runs in an *executor* (side thread) so a 0.5s GPU call never freezes every connected screen.
- **QR codes / wake lock**: a QR is just a URL as a picture; the Screen Wake Lock browser API stops the tablet from sleeping mid-service.

*In this project:* all of `server.py`; reconnection logic in `client.js`.
*Search:* "WebSocket vs HTTP", "python asyncio event loop explained", "FastAPI websocket tutorial".

## 9. Latency engineering (the mindset)

Total delay = mic → chunks (0.25s) + window step (0.7s) + model time + network (~0ms on LAN) + render. Nothing magic: **measure each stage, shrink the biggest one**. That's why the design logs per-stage timings to the admin page — real systems are tuned with numbers, not vibes. Related idea: **pipelining** — while Whisper chews on new audio, the translator works on the previous sentence; stages overlap so throughput stays high even though each stage adds delay.

*Search:* "latency vs throughput", "pipeline parallelism".

## 10. Suggested learning path

1. Watch: any 10-min "how Whisper works" explainer (3Blue1Brown's transformer series if you want depth).
2. Play: `arecord`/`parecord` a WAV, look at it in Audacity — see sample rate, waveform, clipping with your own eyes.
3. Read: the LocalAgreement section of the `whisper_streaming` README (short, brilliant idea).
4. Tinker: change `WHISPER_MODEL_SIZE` in `server.py`, feel the speed/accuracy trade-off yourself.
5. Later: AI4Bharat's IndicTrans2 blog post — good story of why India-specific models beat global ones.
