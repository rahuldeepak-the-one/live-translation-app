# Flowing wall + operator control — design

Status: approved 2026-08-22. Supersedes the `/display` section of
[`2026-07-04-church-translation-design.md`](2026-07-04-church-translation-design.md);
everything else in that document still stands.

## Why

The projector currently shows the last 3 utterances × 4 languages as discrete
blocks. Measured against the real service recorded in
`transcripts/2026-08-21.jsonl` (37 utterances), that is:

| | median | max |
|---|---|---|
| gap between utterances | 7.9 s | 19.4 s |
| English per utterance | 136 chars / 21 words / 3 sentences | 175 chars |
| ml / te / hi per utterance | 157 / 140 / 138 chars | 354 / 344 / 387 |
| STT | 0.4 s | 1.7 s |
| MT | 0.8 s | 4.3 s |

Three utterances × four languages puts a median **1,713 characters** on the wall
at `clamp(1.4rem, 3.4vw, 2.6rem)`. On a 1080p projector that is roughly 3–4×
what fits. `common.css:33` makes `<main>` a scroll box and `display.js:41` pins
it to the bottom, so the overflow does not wrap or shrink — the oldest rows slide
off the top edge unread. Nobody scrolls a projector.

Two further defects the audit surfaced, both still live:

1. **Blocks are chunks, not thoughts.** A row is one audio chunk, so it usually
   starts mid-sentence. In 15 of 37 utterances the row opened with an orphaned
   sentence-tail because Whisper dropped a spurious full stop at a chunk edge and
   `pipeline.looks_complete()` trusted it — "…to contrast moral character." /
   "with practical foresight." was translated as two unrelated sentences.
2. **A mis-heard English word corrupts every language.** Utterance 19's
   "Trudeness" (for *shrewdness*) produced ml യുക്തി "logic", te తెలివి
   "cleverness", hi कठोरता **"harshness"** — the Hindi wall asserted that
   *harshness* is about clarity, foresight and decisiveness. Utterance 25's
   "sons of life" (Luke 16:8 reads *sons of light*) went literally into all
   three. Neither is reachable by the regex-and-glossary approach in
   `config.py`, which can only fix errors already seen.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Correction window | **The current sentence.** Grey text may change; solid text never does. | The repair pass then runs *inside* the existing `MAX_SENTENCE_HOLD_S` window, i.e. before the translator — so ml/te/hi are correct on first paint and never repaint. Only the English lane visibly changes, and only while grey. |
| Wall layout | **Independent flowing lanes** — one per enabled language, each flowing at its own pace | No lane ever waits for another, so nothing stalls. Lanes drift (ml runs ~15% longer than en) but nobody reads two languages at once. |
| Alignment | **Not preserved** | Sentence-locked lanes force every lane to wait for the slowest; the resulting blank space is what reads as "frozen" from the back of a hall. |
| Control surface | **Separate `/control` page**, pushed to the wall over WebSocket | `/display` is the projected surface — controls placed on it get projected too. |
| Control access | **Random path per run**, printed by `start.sh` | The `/view` address is QR-coded for the whole congregation; `/control` must not be guessable from it. |
| Focus mode | **Manual pin + optional auto-rotate** | Pin one language full-wall when a passage matters; rotate on a timer otherwise. |
| Rejected: horizontal ticker | — | A missed word is gone off the left edge and reading pace is set by the animation, not the reader. Poor for comprehension. |

## Staging

Stage 1 is deployable on its own and changes no pipeline code.

| Stage | Delivers | Pipeline risk |
|---|---|---|
| **1** | Flowing lanes on `/display`, `/control` page with toggles, focus, auto-rotate | None — presentation only |
| **2** | A sentence (not a chunk) becomes the published unit; `final` becomes a server guarantee | Moderate |
| **2b** | Grace hold so a breath mid-sentence stops being read as a full stop | Low, but needs instrumentation first |
| **3** | LLM repair pass inside the hold window | High — new model, VRAM, latency |
| **4** | Streaming STT (LocalAgreement), original M2 | Moderate; optional |

This document specifies **stages 1 and 2 in full**. Stages 3 and 4 are sketched
only far enough to prove the protocol below does not have to change again.

---

## Protocol

Two changes to the hub protocol. Both are additive; existing pages keep working.

### `final` on `sentence`

```json
{"type":"sentence", "id":41, "en":"Trudeness is about clarity",              "final":false}
{"type":"sentence", "id":41, "en":"Shrewdness is about clarity, foresight.", "final":true}
```

`final:false` renders grey and may change. `final:true` renders solid and is
frozen forever. That boolean is the entire correction contract.

The current pipeline already knows this value — it is exactly
`self._pending is None` after the flush decision at `pipeline.py:139-145`. So the
contract is honest from stage 1, before any pipeline work.

**This is what makes stage 3 a server-only change.** The repair pass republishes
the same id with corrected English while `final:false`, then flips to `true`.
No page changes.

`translation` carries no `final` flag: under the chosen correction window a
sentence is translated only after its English is final, so translations are
final by construction. If stage 3 ever needs to retract a translation, that is a
new message type, not a change to this one.

### `display` — retained control state

```json
{"type":"display", "lanes":["ml","te","hi"], "focus":null, "rotate":20}
```

- `lanes` — enabled languages, in wall order. Any subset of `["en","ml","te","hi"]`.
- `focus` — `null` for all lanes, or a language code pinned full-wall.
- `rotate` — seconds between auto-rotate steps; `0` disables.

The three modes are mutually exclusive, which keeps the state unambiguous:

```
rotate == 0, focus == null    ->  all enabled lanes, stacked
rotate == 0, focus == "ml"    ->  ml pinned, full wall
rotate >  0                   ->  rotating
```

Setting `rotate > 0` forces `focus` to `null` server-side, and the **display**
advances the rotation locally on its own timer. The server runs no timer and
broadcasts nothing per rotation step — otherwise a server-side `focus` would
fight the display's position, and a 20-second rotation would put a broadcast
storm on the same socket carrying captions.

The hub retains the latest `display` state and replays it on `register()`
alongside `history`, so a wall that drops WiFi mid-service reconnects to the
right lanes instead of reverting to all four.

`/control` sends the same shape inbound. The server validates it (known language
codes, non-empty `lanes`, `rotate` within bounds) and rebroadcasts; `/control`
holds no authoritative state of its own, so two operator phones cannot disagree.

### Unchanged

`history`, `translation`, `status` keep their current shape. `history` gains
`final` inside each retained sentence.

---

## Stage 1 — the wall and the operator page

### Lane rendering

Each enabled language is an independent flowing paragraph — the model
`/view` already uses (`view.html:32-49`), instantiated once per lane instead of
once per page.

A lane whose translation has not arrived shows **the English in grey**, swapped
in place for the translation when it lands. This replaces the literal `"…"` at
`display.js:32`. A Malayalam reader briefly sees English rather than a hole;
a hole for up to 16 seconds reads as broken.

Lane heights divide the wall evenly. With English disabled the remaining three
lanes simply get taller — no special case.

### Focus mode

`focus` overrides `lanes` for rendering only: the named language takes the whole
wall and the others are not painted. They are still received, still translated
and still buffered, so leaving focus restores lanes that are already full of
text rather than blank.

Owning the full wall, a focused lane shows **more history, not larger text** by
default — roughly four times the vertical space, so where a lane showed one or
two lines it now shows six to eight. This is the "past lines" the operator asked
for, and it is what makes focus useful for someone who looked away: they can
read back rather than only catch the live edge. Type size stays on the same
`clamp()` scale so a focused lane and an unfocused one are legible from the same
distance.

Auto-rotate steps through `lanes` in wall order, skipping anything disabled, and
is inert while `focus` is non-null. Disabling the currently focused language
clears `focus` rather than blanking the wall.

### `static/flow.js` — extracted renderer

`view.js` already solves the three problems a flowing caption has, and
`/display` needs the same solution up to four times over:

1. Incremental DOM updates — a full rebuild resets `scrollTop` every message.
2. Autoscroll only when already at the live edge.
3. Grey English standing in for a pending translation.

Extract that into `flow.js` exposing one class:

```js
new Flow(containerEl, langCode)   // .apply(sentence) .setLang(code) .clear()
```

`/view` becomes one instance; `/display` becomes N. This is the only
refactoring in scope — it exists because the alternative is maintaining the same
tricky scroll logic twice.

### `/control`

Served at a random path generated per run (below). Contents:

- Four lane toggles, showing each language in its own script.
- Focus: "all lanes" plus one radio per enabled language.
- Auto-rotate: on/off and interval, disabled while a focus is pinned.
- A read-only mirror of what the wall is currently showing, so the operator can
  confirm without turning around.

Every interaction sends a complete `display` message. No partial updates, no
client-side merge — the wall state is always one object.

### Secret control path

`start.sh` generates a random token per run and prints it with the other URLs:

```
  display   http://192.168.1.7:8080/display
  phones    http://192.168.1.7:8080/view
  control   http://192.168.1.7:8080/control/7f3a9c
```

The server accepts `/control/<token>` only for the token generated at startup;
every other path under `/control` returns 404, identically, so the endpoint
cannot be probed. The QR code in the `/display` footer continues to encode
`/view` only.

The secret path is obscurity, not authentication: it hides the control *page*
from someone guessing at URLs. It does not defend against anyone who can read
the operator's screen or the server log.

**The channel is authenticated separately, and that is the half that matters.**
Amended 2026-08-23, after the final review demonstrated the gap: every screen on
the church WiFi holds an open `/ws/captions`, and `/view` is QR-coded for the
whole congregation, so any phone with a developer console could send a `display`
message and reconfigure the projector without ever knowing the path.

`/ws/captions` therefore splits reading from writing:

```
/ws/captions            connect, receive everything — no token needed
/ws/captions?t=<token>  may additionally SEND `display`
```

An inbound `display` on an unauthenticated socket is logged with the offending
payload and ignored. Reading stays open on purpose: a screen must never be
refused, because a caption feed that dies over a mistyped token is a worse
failure than an unlocked wall. `/control` supplies the token from its own path,
so nothing extra has to be embedded in a page.

### Config

```python
DEFAULT_LANES = ["en", "ml", "te", "hi"]   # wall state at startup
ROTATE_INTERVAL_S = 20                     # default; 0 disables
ROTATE_MIN_S, ROTATE_MAX_S = 5, 120        # inbound validation bounds
```

---

## Stage 2 — a sentence becomes the unit

Today one id is a whole chunk: median 3 sentences, 136 characters. Two
consequences follow, and both are visible on the wall.

- A lane advances in 136-character jumps rather than flowing.
- `final` is coarse. A three-sentence chunk stays grey until its last sentence
  completes, so text that will never change is displayed as though it might.

`UtterancePipeline` gains a splitting step: `translator.split_source_sentences()`
(which already exists, `translator.py:61`) cuts the joined text, and each
complete sentence is published under its own id and translated independently —
which the translator already does internally at `translator.py:227`. Only the
trailing incomplete sentence stays pending.

Ids stay monotonic. `HISTORY_SIZE` counts sentences rather than chunks and rises
accordingly (10 chunks ≈ 30 sentences).

**Splitting on `.` alone inherits the boundary errors described in Why §1.** But
those errors are a timing bug, not a comprehension bug, and are addressed by
stage 2b below rather than by the stage 3 LLM.

### Stage 2b — the breath problem

Measured on the 2026-08-21 session, comparing chunk duration against whether the
following chunk began mid-sentence:

```
BROKEN boundaries (n=15)   median chunk 5.8s   range 0.0-6.9s   >=7s: 0 of 15
GOOD   boundaries (n=10)   median chunk 7.3s   range 4.5-19.0s  >=7s: 7 of 10
```

Not one broken boundary came from a chunk longer than 7 seconds. The forced cuts
at `MAX_BUFFER_S` are safe precisely because they end mid-word with no
punctuation, so they are already held and joined. The damage comes from short,
silence-triggered cuts.

The cause is `SILENCE_DURATION_S = 0.6`. That is a breath, not a sentence
ending. The speaker inhales mid-sentence, `has_trailing_silence()` fires, Whisper
sees audio ending in silence and adds a confident full stop, and
`looks_complete()` believes it.

**Fix:** a terminated sentence does not flush immediately. It is held for
`SENTENCE_GRACE_S` of wall time to see whether speech resumes. Resumes — it was a
breath, join it. Stays quiet — a real ending, flush. This reuses the hold
machinery `MAX_SENTENCE_HOLD_S` already provides for unterminated sentences,
with a much shorter timer, and only costs latency when the speaker has in fact
stopped talking. Whisper's capitalisation corroborates: all 24 continuation
chunks in that session began lowercase.

**`SENTENCE_GRACE_S` must not be guessed.** The threshold cannot be derived from
the existing transcript, which records neither the cut reason nor any silence
duration. Step one is instrumentation — replay a service, then choose the value
from the distribution, the way every other tuned constant in `config.py` was
justified.

**Instrumentation shipped 2026-08-23** (`segmentation_log.py`,
`analyze_segmentation.py`). It is measurement only: no cutting behaviour
changed, so what it captures describes the system as it actually runs.

A subtlety worth keeping: the obvious measurement is useless. `should_process()`
fires the instant 0.6s of silence exists, so the silence present *at* a cut is
always ~0.6–0.9s and cannot separate a breath from a finished thought. The
discriminator is measured *after* the cut — `speech_gap_s`, how long until
speech resumes. A record therefore stays open until the next speech arrives.

Each cut writes a `kind: "cut"` row to `transcripts/<date>.jsonl` carrying
`reason`, `chunk_s`, `trailing_silence_s`, `speech_gap_s`, the raw chunk `text`,
and `looked_complete`. Rows without `kind` remain utterances.

```
python analyze_segmentation.py transcripts/<date>.jsonl
```

grades every cut that was flushed as a sentence ending against whether the next
chunk begins lowercase — Whisper's capitalisation marks continuations, and all
24 continuation chunks on 2026-08-21 began lowercase — then prints both
populations and proposes a value.

**Read its coverage line before trusting the number.** If the populations
overlap, no threshold separates them, and the honest conclusion is that timing
alone cannot fix these cuts. That result is not a failure of the exercise; it is
the finding that would justify the Stage 3 repair pass instead.

Stage 2 makes the flow smooth and `final` honest. Stage 2b makes the boundaries
correct.

---

## Stage 3 — repair pass (sketch)

A small instruct LLM between STT and MT, inside the existing hold window. Input:
the pending English plus the previous final sentence. Output: corrected English
— the "Trudeness" / "sons of life" class of error, which no amount of timing work
reaches because it requires knowing what the sermon is about.

Stage 2b, not this, fixes sentence boundaries. If instrumentation shows the grace
hold leaves a residue of bad boundaries, this pass can also judge completeness
and replace `looks_complete()` outright — but that is a fallback, not the plan.

It edits the translator's input only. `/display`, `/view` and the transcript keep
what Whisper heard, exactly as `clean_for_translation()` does today
(`pipeline.py:69`). It subsumes `SOURCE_REWRITES` and most of `GLOSSARY`.

Open questions, all requiring measurement on the actual RTX 3060 rather than
estimation: model and quantisation; VRAM headroom beside Whisper (~1.5 GB) and
IndicTrans2 (~0.5 GB) in 6 GB; added latency against the 16 s budget; and whether
a repair pass ever makes text *worse*, which needs the same A/B discipline every
row of `SOURCE_REWRITES` was held to.

No protocol change. Server-side only.

## Stage 4 — streaming STT (sketch)

The original M2: rolling-window re-transcription with LocalAgreement, so the grey
tail advances every ~0.7 s instead of once per chunk. Emits more frequent
`sentence` messages with `final:false`. No protocol change, no page change.

---

## Testing

Following the existing split between pure logic, hub bookkeeping and browser
tests.

**Pure functions** — lane-toggle and focus/rotate transitions as plain reducers,
so the state machine is testable without a browser: rotate skips disabled lanes,
rotate is inert while focus is pinned, disabling the focused language clears
focus rather than blanking the wall, disabling the last lane is rejected.

**`tests/test_hub.py`** — display state retained and replayed on register;
replay ordering against `history`; `final` propagated through
`publish_sentence`; inbound validation rejects unknown codes, empty `lanes` and
out-of-range `rotate`.

**`tests/test_server.py`** — `/control/<token>` serves for the generated token;
every other `/control/*` path 404s identically.

**`tests/ui/`** — N enabled languages renders N lanes; focus collapses to one and
restores; a lane pending translation shows grey English, not `…`; reconnect
restores lanes. The scroll trap documented at `common.css:36-42` —
`justify-content:flex-end` on a scrolling flex container making overflow
permanently unreachable — must be covered for the new lanes, since that bug is
invisible until someone tries to scroll back.

**Stage 2** — sentence splitting preserves text exactly under
concatenation; ids stay monotonic; a trailing incomplete sentence stays pending
while its predecessors go final.

## Out of scope

- User accounts, sessions, or TLS. The control *channel* is authenticated by a
  shared per-run token (see Protocol above); the control *path* remains
  obscurity. Both travel in the clear on the venue LAN, which is accepted.
- Per-viewer lane choice on `/display`. The wall is one shared surface; personal
  choice is what `/view` is for.
- Changing `/view`'s behaviour beyond adopting the shared renderer.
- TTS, non-English source speech, remote viewers — unchanged from the 2026-07-04
  spec.
