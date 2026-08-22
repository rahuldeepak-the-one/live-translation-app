# Stage 1 — Flowing Wall + Operator Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the projector's block display with independent flowing lanes, and add an operator page that toggles lanes, pins one language full-wall, or auto-rotates — without touching pipeline code.

**Architecture:** The wall's entire configuration is one object (`lanes`, `focus`, `rotate`) held authoritatively by the hub, pushed to every screen over the existing `/ws/captions` socket, and replayed on connect. `/control` sends whole objects, never partial updates, so two operator phones cannot disagree. Rendering is done by one extracted module, `flow.js`, instantiated once on `/view` and once per lane on `/display`. Auto-rotation runs on the display's own timer, so the server needs no timer and no broadcast storm.

**Tech Stack:** FastAPI + WebSockets, vanilla ES modules (no build step), pytest with `asyncio_mode = auto`, headless-Chrome browser suites under `tests/ui/`.

**Spec:** `docs/superpowers/specs/2026-08-22-flowing-wall-and-operator-control-design.md`

## Global Constraints

- **No pipeline changes.** `pipeline.py`, `stt.py`, `translator.py`, `audio_buffer.py` are not modified by any task in this plan. The one exception is passing `final` through to the hub in Task 2, which is a single added argument.
- **Additive protocol only.** Existing `history`, `sentence`, `translation`, `status` messages keep their current shape. Pages that ignore the new fields keep working.
- **Languages** are exactly `en`, `ml`, `te`, `hi` — from `config.SOURCE_LANG` and `config.TARGET_LANGS`. Never hard-code the list in new modules.
- **No build step.** Browser code is plain ES modules served from `static/`. No bundler, no npm.
- **Tests:** unit tests run under plain `pytest`. Browser tests are marked `browser` and run with `pytest -m browser`; they need Chrome on PATH.
- **Dark, projector-first styling** lives in `static/common.css` and is shared by all caption pages. Do not fork it.
- **The wall must never scroll-trap.** `common.css:36-42` documents why `justify-content: flex-end` is forbidden on a scrolling flex container. Any new scrolling container follows the `margin-top: auto` pattern instead.

### State model (exact semantics — all tasks depend on this)

```
rotate == 0, focus == null    ->  all enabled lanes shown, stacked
rotate == 0, focus == "ml"    ->  ml pinned, full wall, more history
rotate >  0                   ->  rotating; focus is forced to null by the
                                  server, and the DISPLAY advances its own
                                  position locally every `rotate` seconds
```

The three modes are mutually exclusive. Pinning sets `rotate: 0`. Enabling rotation clears any pin. The server never runs a rotation timer.

---

### Task 1: Display state reducer

Pure Python, no I/O. Everything the wall can be told, validated in one place.

**Files:**
- Create: `display_state.py`
- Modify: `config.py` (append a new section)
- Test: `tests/test_display_state.py`

**Interfaces:**
- Consumes: `config.SOURCE_LANG`, `config.TARGET_LANGS`
- Produces:
  - `KNOWN_LANGS: tuple[str, ...]` — `("en", "ml", "te", "hi")`
  - `initial_state() -> dict` — `{"lanes": [...], "focus": None, "rotate": 0}`
  - `validate(raw: dict) -> dict` — clean state, or raises `ValueError`

- [ ] **Step 1: Add config**

Append to `config.py`:

```python
# --- Wall display (stage 1) ---
# The wall's startup state. /control overrides it at runtime; nothing persists
# across a restart, which is deliberate — a service always begins predictable.
DEFAULT_LANES = ["en", "ml", "te", "hi"]
# Auto-rotate bounds. Below 5s nobody finishes a line; above 120s it is not
# rotation, it is a pin the operator forgot about.
ROTATE_MIN_S = 5
ROTATE_MAX_S = 120
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_display_state.py`:

```python
import pytest

from display_state import KNOWN_LANGS, initial_state, validate


def test_initial_state_shows_every_language():
    assert initial_state() == {
        "lanes": ["en", "ml", "te", "hi"], "focus": None, "rotate": 0}


def test_initial_state_is_not_shared_between_calls():
    first = initial_state()
    first["lanes"].append("xx")
    assert "xx" not in initial_state()["lanes"]


def test_known_langs_comes_from_config():
    assert KNOWN_LANGS == ("en", "ml", "te", "hi")


def test_valid_state_passes_through_with_lane_order_preserved():
    raw = {"lanes": ["hi", "ml"], "focus": "ml", "rotate": 0}
    assert validate(raw) == {"lanes": ["hi", "ml"], "focus": "ml", "rotate": 0}


def test_missing_keys_fall_back_to_defaults():
    assert validate({"lanes": ["ml"]}) == {
        "lanes": ["ml"], "focus": None, "rotate": 0}


@pytest.mark.parametrize("lanes", [[], ["xx"], ["ml", "ml"], "ml", None])
def test_bad_lanes_rejected(lanes):
    with pytest.raises(ValueError):
        validate({"lanes": lanes})


@pytest.mark.parametrize("rotate", [-1, 4, 121, "20", 1.5])
def test_bad_rotate_rejected(rotate):
    with pytest.raises(ValueError):
        validate({"lanes": ["ml"], "rotate": rotate})


@pytest.mark.parametrize("rotate", [0, 5, 20, 120])
def test_rotate_bounds_accepted(rotate):
    assert validate({"lanes": ["ml", "te"], "rotate": rotate})["rotate"] == rotate


def test_focus_on_a_disabled_language_is_cleared_not_rejected():
    # Repairing rather than raising: the operator disabling the focused lane is
    # an ordinary action, and blanking the wall over it would be worse than
    # falling back to showing everything.
    assert validate({"lanes": ["ml", "te"], "focus": "hi"})["focus"] is None


def test_unknown_focus_is_rejected():
    with pytest.raises(ValueError):
        validate({"lanes": ["ml"], "focus": "xx"})


def test_rotation_clears_any_pin():
    # The display owns the rotation position; a server-side focus would fight it.
    assert validate({"lanes": ["ml", "te"], "focus": "ml", "rotate": 20})["focus"] is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_display_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'display_state'`

- [ ] **Step 4: Write the implementation**

Create `display_state.py`:

```python
"""What the projector wall is currently showing.

One object describes the whole wall, so /control always sends a complete state
and never has to merge. Two operator phones therefore cannot drift apart: the
last complete message wins.

Pure functions, no I/O — the wall's rules are testable without a browser or a
server, which is where every interesting edge case lives.
"""
from config import (
    SOURCE_LANG, TARGET_LANGS, DEFAULT_LANES, ROTATE_MIN_S, ROTATE_MAX_S,
)

KNOWN_LANGS = (SOURCE_LANG, *TARGET_LANGS)


def initial_state():
    """The wall at startup. A fresh dict each call — callers mutate it."""
    return {"lanes": list(DEFAULT_LANES), "focus": None, "rotate": 0}


def _clean_lanes(raw):
    if not isinstance(raw, list) or not raw:
        raise ValueError("lanes must be a non-empty list")
    if any(lang not in KNOWN_LANGS for lang in raw):
        raise ValueError(f"lanes must be drawn from {KNOWN_LANGS}")
    if len(set(raw)) != len(raw):
        raise ValueError("lanes must not repeat")
    return list(raw)          # order is the wall order, so it is preserved


def _clean_rotate(raw):
    # bool is an int subclass and True would sail through the range check.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("rotate must be an integer number of seconds")
    if raw != 0 and not (ROTATE_MIN_S <= raw <= ROTATE_MAX_S):
        raise ValueError(f"rotate must be 0 or {ROTATE_MIN_S}-{ROTATE_MAX_S}")
    return raw


def validate(raw):
    """Clean an inbound state, or raise ValueError.

    Rejects unknown values rather than guessing, with one deliberate exception:
    a focus on a language that is no longer enabled is CLEARED rather than
    rejected. Disabling the focused lane is an ordinary operator action, and
    refusing it would leave the wall pinned to a language nobody selected.
    """
    if not isinstance(raw, dict):
        raise ValueError("state must be an object")

    lanes = _clean_lanes(raw.get("lanes", DEFAULT_LANES))
    rotate = _clean_rotate(raw.get("rotate", 0))

    focus = raw.get("focus")
    if focus is not None:
        if focus not in KNOWN_LANGS:
            raise ValueError(f"focus must be null or one of {KNOWN_LANGS}")
        if focus not in lanes:
            focus = None
    # Rotation and a pin are mutually exclusive; the display advances the
    # rotation locally, so a server-side focus would fight its timer.
    if rotate:
        focus = None

    return {"lanes": lanes, "focus": focus, "rotate": rotate}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_display_state.py -v`
Expected: PASS, 16 tests

- [ ] **Step 6: Commit**

```bash
git add display_state.py tests/test_display_state.py config.py
git commit -m "feat: display state reducer for the projector wall"
```

---

### Task 2: Hub retains display state, and `final` on sentences

**Files:**
- Modify: `hub.py`
- Test: `tests/test_hub.py`

**Interfaces:**
- Consumes: `display_state.initial_state`, `display_state.validate`
- Produces:
  - `BroadcastHub.display_state -> dict` (attribute, current wall state)
  - `async BroadcastHub.publish_sentence(sentence_id, en_text, final=False)`
  - `async BroadcastHub.publish_display(state: dict)` — validates, retains, broadcasts
  - `register()` now sends `history` **then** `display`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hub.py`:

```python
from display_state import initial_state


async def test_register_replays_display_state_after_history():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    # Order matters: a page applies history first, then configures its lanes.
    assert [m["type"] for m in ws.sent] == ["history", "display"]
    assert ws.sent[1] == {"type": "display", **initial_state()}


async def test_publish_display_is_retained_for_late_joiners():
    hub = BroadcastHub()
    await hub.publish_display({"lanes": ["ml", "te"], "focus": "ml", "rotate": 0})
    late = FakeWS()
    await hub.register(late)
    assert late.sent[1] == {
        "type": "display", "lanes": ["ml", "te"], "focus": "ml", "rotate": 0}


async def test_publish_display_broadcasts_to_connected_screens():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    ws.sent.clear()
    await hub.publish_display({"lanes": ["hi"], "focus": None, "rotate": 0})
    assert ws.sent == [
        {"type": "display", "lanes": ["hi"], "focus": None, "rotate": 0}]


async def test_publish_display_rejects_bad_state_and_keeps_the_old_one():
    hub = BroadcastHub()
    await hub.publish_display({"lanes": ["ml"], "focus": None, "rotate": 0})
    with pytest.raises(ValueError):
        await hub.publish_display({"lanes": []})
    assert hub.display_state["lanes"] == ["ml"]


async def test_sentence_carries_final_flag():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    await hub.publish_sentence(1, "Half a sen", final=False)
    await hub.publish_sentence(1, "Half a sentence finished.", final=True)
    sentences = [m for m in ws.sent if m["type"] == "sentence"]
    assert sentences == [
        {"type": "sentence", "id": 1, "en": "Half a sen", "final": False},
        {"type": "sentence", "id": 1, "en": "Half a sentence finished.", "final": True},
    ]


async def test_history_retains_final():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Done.", final=True)
    late = FakeWS()
    await hub.register(late)
    assert late.sent[0]["sentences"] == [
        {"id": 1, "en": "Done.", "translations": None, "final": True}]


async def test_revising_a_sentence_updates_final_in_history():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Growing", final=False)
    await hub.publish_sentence(1, "Growing still.", final=True)
    late = FakeWS()
    await hub.register(late)
    assert late.sent[0]["sentences"][0]["final"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hub.py -v`
Expected: FAIL — `TypeError: publish_sentence() got an unexpected keyword argument 'final'`, and `AttributeError: 'BroadcastHub' object has no attribute 'publish_display'`

- [ ] **Step 3: Implement**

In `hub.py`, add the import and extend `__init__`:

```python
from config import HISTORY_SIZE
from display_state import initial_state, validate
```

```python
    def __init__(self, history_size=HISTORY_SIZE):
        self._clients = set()
        self._history = deque(maxlen=history_size)
        # Authoritative wall configuration. Retained so a screen that drops
        # WiFi mid-service reconnects to the lanes it had, rather than
        # reverting to all four in front of the congregation.
        self.display_state = initial_state()
```

Replace `register`:

```python
    async def register(self, ws):
        snapshot = list(self._history)
        self._clients.add(ws)
        await ws.send_json({"type": "history", "sentences": snapshot})
        # After history, never before: a page applies the backlog and then
        # configures which lanes to render it into.
        await ws.send_json({"type": "display", **self.display_state})
```

Replace `publish_sentence`:

```python
    async def publish_sentence(self, sentence_id, en_text, final=False):
        """Publish (or revise) the English for a sentence.

        `final` is the correction contract the screens render: False is grey and
        may still change, True is solid and never will. Revision matters because
        the pipeline holds an unfinished sentence and re-publishes the same id
        with more words appended as the speaker keeps going.
        """
        for item in self._history:
            if item["id"] == sentence_id:
                item["en"] = en_text
                item["final"] = final
                break
        else:
            self._history.append(
                {"id": sentence_id, "en": en_text,
                 "translations": None, "final": final}
            )
        await self.broadcast(
            {"type": "sentence", "id": sentence_id, "en": en_text, "final": final})
```

Add after `publish_translation`:

```python
    async def publish_display(self, state):
        """Validate, retain and broadcast a complete wall configuration.

        Raises ValueError on a bad state, leaving the retained one untouched —
        a malformed control message must never blank the wall.
        """
        self.display_state = validate(state)
        await self.broadcast({"type": "display", **self.display_state})
```

- [ ] **Step 4: Pass `final` from the pipeline**

In `pipeline.py`, the flush decision at line 139 already computes this. Move the
`publish_sentence` call to after it and pass the flag. Replace lines 134-147:

```python
        held_for = self._clock() - touched
        flush = (
            looks_complete(joined)
            or held_for >= MAX_SENTENCE_HOLD_S
            or len(joined) >= MAX_PENDING_CHARS
        )

        # English first, always — this is what keeps the captions feeling live.
        # Re-publishing the same id revises the row rather than duplicating it.
        # `final` is exactly the flush decision: a held sentence is still
        # growing and renders grey, a flushed one is frozen and renders solid.
        await self.hub.publish_sentence(sid, joined, final=flush)

        if not flush:
            self._pending = {"id": sid, "text": joined, "touched": touched}
            logger.debug("#%d held (%.1fs, %d chars): %s", sid, held_for, len(joined), joined)
            return sid, joined
```

Note `held_for` is now computed before it is used in the log line, and the
`publish_sentence` call moved below the `flush` computation. Nothing else in
`process()` changes.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest -v`
Expected: PASS. `tests/test_pipeline.py` exercises `publish_sentence` through a fake hub — if that fake has a positional-only signature, widen it to accept `final=False`.

- [ ] **Step 6: Commit**

```bash
git add hub.py pipeline.py tests/test_hub.py
git commit -m "feat: hub retains wall state and marks sentences final"
```

---

### Task 3: Secret control path and inbound control messages

**Files:**
- Modify: `server.py`, `netinfo.py`, `start.sh`
- Test: `tests/test_server.py`, `tests/test_netinfo.py`

**Interfaces:**
- Consumes: `hub.publish_display`, `display_state.validate`
- Produces:
  - `server.control_token() -> str` — the token for this process
  - route `GET /control/{token}` -> `static/control.html`
  - `/ws/captions` accepts inbound `{"type":"display", ...}`
  - `netinfo.startup_banner(port, ..., control_token="")` prints the control URL

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
import server as server_module


def test_control_page_served_for_the_generated_token(client):
    token = server_module.control_token()
    assert client.get(f"/control/{token}").status_code == 200


def test_wrong_control_token_is_indistinguishable_from_a_missing_page(client):
    real = client.get("/control/definitely-not-the-token")
    absent = client.get("/control/")
    assert real.status_code == 404
    assert absent.status_code == 404


def test_control_token_is_not_guessable_from_the_public_pages(client):
    token = server_module.control_token()
    for path in ("/display", "/view", "/qr.svg"):
        assert token not in client.get(path).text
```

Append to `tests/test_netinfo.py`:

```python
def test_banner_prints_the_control_url_when_a_token_is_supplied():
    banner = startup_banner(8080, ROUTE_OUTPUT, ADDR_OUTPUT, control_token="7f3a9c")
    assert "/control/7f3a9c" in banner


def test_banner_omits_control_line_without_a_token():
    banner = startup_banner(8080, ROUTE_OUTPUT, ADDR_OUTPUT)
    assert "/control" not in banner
```

Use whatever fixture names `tests/test_server.py` and `tests/test_netinfo.py`
already define for the client and the sample `ip` command output; do not
introduce new ones.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_server.py tests/test_netinfo.py -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'control_token'`

- [ ] **Step 3: Implement the token and the route**

In `server.py`, add imports and the token near the top:

```python
import os
import secrets

from display_state import validate as validate_display
```

```python
# The /view address is QR-coded for the whole congregation, so /control must not
# be reachable by guessing from it. start.sh generates the token so it can print
# it in the startup banner before the server boots; running server.py directly
# generates one and logs it.
#
# This is obscurity, not authentication. It stops a bored teenager on the church
# WiFi. It does not stop anyone who can read the operator's screen or this log.
_CONTROL_TOKEN = os.environ.get("CONTROL_TOKEN") or secrets.token_hex(3)


def control_token():
    return _CONTROL_TOKEN
```

Add the route beside the other page routes:

```python
    @app.get("/control/{token}")
    async def control_page(token: str):
        # compare_digest, and an identical 404 either way: a wrong token must be
        # indistinguishable from a path that was never a page, so the endpoint
        # cannot be probed.
        if not secrets.compare_digest(token, _CONTROL_TOKEN):
            return Response(status_code=404)
        return FileResponse(STATIC_DIR / "control.html")
```

- [ ] **Step 4: Accept inbound control messages**

Replace the body of `ws_captions` in `server.py`:

```python
    @app.websocket("/ws/captions")
    async def ws_captions(ws: WebSocket):
        await ws.accept()
        await hub.register(ws)
        try:
            while True:
                # Screens are read-only; only /control sends anything, and it
                # always sends a COMPLETE state, so there is nothing to merge.
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") != "display":
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
```

Note `hub.publish_display` validates; `validate_display` is imported for the
banner test only if needed — remove the import if unused.

- [ ] **Step 5: Print the control URL**

In `netinfo.py`, change the `startup_banner` signature and lines:

```python
def startup_banner(port, route_output, addr_output, nmcli_output="", control_token=""):
```

After the `Microphone page` line, before the blank string:

```python
    if control_token:
        lines.append(f"\U0001f39b️  Operator control:  {primary}/control/{control_token}")
```

And in `local_banner`:

```python
def local_banner(port, control_token=""):
    return startup_banner(
        port,
        _run("ip", "route", "get", "1.1.1.1"),
        _run("ip", "-o", "-4", "addr", "show"),
        _run("nmcli", "-t", "-f", "active,ssid", "dev", "wifi"),
        control_token,
    )
```

If `netinfo.py`'s `__main__` block calls `local_banner(PORT)`, have it read
`os.environ.get("CONTROL_TOKEN", "")` and pass it through.

- [ ] **Step 6: Generate the token in start.sh**

In `start.sh`, before the banner is printed (currently line 57):

```bash
# --- Operator control token ---
# New every run, so a token seen once is useless next Sunday. Exported so the
# banner below and the server itself agree on it.
export CONTROL_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(3))')"
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_server.py tests/test_netinfo.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add server.py netinfo.py start.sh tests/test_server.py tests/test_netinfo.py
git commit -m "feat: secret control path and inbound wall-state messages"
```

---

### Task 4: Extract `flow.js` and move `/view` onto it

Behaviour-neutral. `/view` must look and act exactly as it does today; the
existing browser suite is the proof.

**Files:**
- Create: `static/flow.js`
- Modify: `static/view.js`, `static/view.html`
- Test: `tests/ui/view_tests.js` (existing suite must keep passing unchanged)

**Interfaces:**
- Produces: `class Flow` exported from `/static/flow.js`
  - `new Flow(containerEl, lang)` — `containerEl` is the element sentences are appended into
  - `.setLang(lang)` — re-paints every sentence in the new language
  - `.apply(sentence)` — `{id, en, translations|null, final}`; inserts or revises by id
  - `.reset(sentences)` — clear and repaint from a history array
  - `.trim(max)` — drop oldest beyond `max`
  - `.isEmpty -> boolean`

- [ ] **Step 1: Create the module**

Create `static/flow.js`:

```js
/* One flowing, self-revising transcript in one language.
 *
 * Extracted from view.js because /display needs the same thing once per lane.
 * Three things it is careful about, all of which cost real bugs to learn:
 *   1. Never rebuild the DOM on an update — that resets scrollTop and destroys
 *      the ability to read back through the service.
 *   2. Sentences are inline spans in one paragraph, so the message reads as
 *      continuous prose rather than a stack of blocks.
 *   3. A sentence with no translation yet shows its ENGLISH in grey rather
 *      than a placeholder, so a lane never looks stalled while the server
 *      waits for the sentence to finish.
 *
 * Scrolling is deliberately NOT handled here: /view keeps a jump-to-live button
 * and /display never scrolls by hand. The owner decides.
 */
export class Flow {
  constructor(container, lang) {
    this.container = container;
    this.lang = lang;
    this.order = [];                 // sentence ids, oldest first
    this.sentences = new Map();      // id -> {id, en, translations, final}
    this.spans = new Map();          // id -> HTMLSpanElement
  }

  get isEmpty() {
    return this.order.length === 0;
  }

  setLang(lang) {
    this.lang = lang;
    for (const id of this.order) this._paint(this.sentences.get(id));
  }

  apply(s) {
    const existing = this.sentences.get(s.id);
    if (existing) {
      Object.assign(existing, s);
      this._paint(existing);
      return;
    }
    const row = { translations: null, final: false, ...s };
    this.sentences.set(row.id, row);
    this.order.push(row.id);
    this._paint(row);
    this._markLive();
  }

  reset(sentences) {
    this.container.textContent = "";
    this.order = [];
    this.sentences.clear();
    this.spans.clear();
    for (const s of sentences) this.apply(s);
  }

  trim(max) {
    while (this.order.length > max) {
      const id = this.order.shift();
      this.sentences.delete(id);
      this.spans.get(id)?.remove();
      this.spans.delete(id);
    }
  }

  _textFor(s) {
    if (this.lang === "en") return { text: s.en, awaiting: false };
    const translated = s.translations?.[this.lang];
    return translated
      ? { text: translated, awaiting: false }
      : { text: s.en, awaiting: true };
  }

  _paint(s) {
    let span = this.spans.get(s.id);
    if (!span) {
      span = document.createElement("span");
      span.dataset.sid = String(s.id);
      this.spans.set(s.id, span);
      this.container.appendChild(span);
    }
    const { text, awaiting } = this._textFor(s);
    span.textContent = `${text} `;   // trailing space so the paragraph wraps
    span.classList.toggle("awaiting", awaiting);
    // The correction contract: grey while the server may still revise this
    // sentence, solid once it has frozen. See the spec's `final` section.
    span.classList.toggle("provisional", s.final === false);
    this._markLive();
  }

  _markLive() {
    const newest = this.order[this.order.length - 1];
    for (const [id, span] of this.spans) span.classList.toggle("live", id === newest);
  }
}
```

- [ ] **Step 2: Move `/view` onto it**

In `static/view.html`, change the script tag to a module:

```html
  <script type="module" src="/static/view.js"></script>
```

Add the provisional rule next to the existing `.awaiting` rule:

```css
    /* Still being revised by the server — see the `final` flag. Distinct from
       .awaiting, which means "translated text has not arrived yet". */
    #transcript span.provisional { opacity: 0.75; }
```

In `static/view.js`: delete `textFor`, `paint`, `upsert`, `markLive`, `trim`,
and the `spans` map. Import and construct the Flow, and rewrite the four call
sites:

```js
import { Flow } from "/static/flow.js";

const flow = new Flow(els.transcript, state.lang);
```

- `rebuild()` becomes:

```js
function rebuild() {
  flow.setLang(state.lang);
  els.empty.style.display = flow.isEmpty ? "" : "none";
  scrollToLive();
  updateJumpLive();
}
```

- `update(mutate)` becomes:

```js
function update(mutate) {
  const stick = atLiveEdge();
  mutate();
  flow.trim(MAX_SENTENCES);
  els.empty.style.display = flow.isEmpty ? "" : "none";
  if (stick) scrollToLive();
  updateJumpLive();
}
```

- `applySentence` becomes `flow.apply({ id: msg.id, en: msg.en, final: msg.final })`
- `applyTranslation(id, translations)` becomes `flow.apply({ id, translations })`
- the `history` branch becomes `flow.reset(msg.sentences); flow.trim(MAX_SENTENCES); rebuild();`

The language picker keeps writing `state.lang` and calling `rebuild()`.

- [ ] **Step 3: Run the existing browser suite**

Run: `pytest -m browser -v -k view`
Expected: PASS with no changes to `tests/ui/view_tests.js`. If it fails, the
extraction changed behaviour — fix `flow.js`, not the test.

- [ ] **Step 4: Commit**

```bash
git add static/flow.js static/view.js static/view.html
git commit -m "refactor: extract the flowing transcript renderer as flow.js"
```

---

### Task 5: `/display` becomes flowing lanes

**Files:**
- Modify: `static/display.html`, `static/display.js`, `static/common.css`
- Test: `tests/ui/display_tests.js` (rewritten)

**Interfaces:**
- Consumes: `Flow` from Task 4, `display` message from Task 2
- Produces: `renderLanes(state)` in `display.js`, and DOM contract
  `.lane[data-lang]` each containing `.lane-tag` and `.lane-flow`

- [ ] **Step 1: Write the failing browser tests**

Replace `tests/ui/display_tests.js`:

```js
/* The projector wall: independent flowing lanes, one per enabled language. */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const sentence = (id, en, final = true) => ({ type: "sentence", id, en, final });
const translation = (id) => ({
  type: "translation", id,
  ml: `മലയാളം വാക്യം ${id}.`,
  te: `తెలుగు వాక్యం ${id}.`,
  hi: `हिन्दी वाक्य ${id}।`,
});
const display = (lanes, focus = null, rotate = 0) =>
  ({ type: "display", lanes, focus, rotate });

async function run() {
  const { win, doc } = await loadPage("/display", { width: 1280, height: 720 });
  const ws = win.__sockets[0];
  check("display.js opened a caption socket", !!ws, ws && ws.url);
  if (!ws) return;
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });
  ws.deliver(display(["en", "ml", "te", "hi"]));

  for (let i = 1; i <= 12; i++) {
    ws.deliver(sentence(i, `Projector caption number ${i} is fairly long.`));
    ws.deliver(translation(i));
  }

  const lanes = () => [...doc.querySelectorAll(".lane")];
  check("one lane per enabled language", lanes().length === 4, lanes().length);
  check("lanes are labelled by language",
        lanes().map((l) => l.dataset.lang).join(",") === "en,ml,te,hi",
        lanes().map((l) => l.dataset.lang).join(","));

  const mlText = doc.querySelector('.lane[data-lang="ml"] .lane-flow').textContent;
  check("malayalam lane shows malayalam", mlText.includes("മലയാളം"), mlText.slice(0, 40));

  // Lanes flow: many sentences share one paragraph, not one block each.
  const mlSpans = doc.querySelectorAll('.lane[data-lang="ml"] .lane-flow span');
  check("lane is one flowing paragraph of spans", mlSpans.length === 12, mlSpans.length);

  // Turning English off must leave three lanes, not a gap.
  ws.deliver(display(["ml", "te", "hi"]));
  check("disabling english leaves three lanes", lanes().length === 3, lanes().length);
  check("english lane is gone",
        !doc.querySelector('.lane[data-lang="en"]'), "en lane still present");

  // The whole point of a projector: newest text must be visible, and the
  // container must remain scrollable (common.css:36 documents the trap).
  for (const lane of lanes()) {
    const flow = lane.querySelector(".lane-flow");
    const scrollable = flow.scrollHeight >= flow.clientHeight;
    check(`lane ${lane.dataset.lang} is not scroll-trapped`, scrollable,
          `scrollHeight=${flow.scrollHeight} clientHeight=${flow.clientHeight}`);
    check(`lane ${lane.dataset.lang} is pinned to the live edge`,
          flow.scrollHeight - flow.clientHeight - flow.scrollTop <= 2,
          `${flow.scrollHeight - flow.clientHeight - flow.scrollTop}px from bottom`);
  }

  // A sentence with no translation shows grey English, never "…".
  ws.deliver(sentence(99, "Just spoken, not yet translated.", false));
  const teFlow = doc.querySelector('.lane[data-lang="te"] .lane-flow');
  check("untranslated lane shows english, not an ellipsis",
        teFlow.textContent.includes("Just spoken") && !teFlow.textContent.includes("…"),
        teFlow.textContent.slice(-60));
  const newest = teFlow.querySelector('span[data-sid="99"]');
  check("unfinalised sentence is marked provisional",
        newest && newest.classList.contains("provisional"),
        newest && newest.className);

  // Reconnect must restore lanes, not revert to all four.
  ws.onclose();
  const ws2 = win.__sockets[win.__sockets.length - 1];
  check("display reconnected", ws2 !== ws, `${win.__sockets.length} sockets`);
}

finish(run());
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest -m browser -v -k display`
Expected: FAIL — no `.lane` elements; the page still renders `.caption-row`.

- [ ] **Step 3: Add lane CSS**

Append to `static/common.css`:

```css
/* --- Projector lanes (stage 1) --------------------------------------------
   Each language is an independent flowing paragraph. Lanes divide the wall
   evenly and never wait for one another, so nothing stalls on screen. */
.wall { flex: 1; min-height: 0; display: flex; flex-direction: column;
        gap: 0.5rem; padding: 1rem 1.4rem; }
.lane { flex: 1; min-height: 0; display: flex; gap: 0.8rem; }
.lane-tag {
  flex: 0 0 2.4em; font-size: 0.5em; font-weight: 700; letter-spacing: 0.08em;
  color: #5b6e83; text-transform: uppercase; padding-top: 0.4em;
}
/* The scroll container. As with main::before above, growth-from-bottom MUST
   NOT be justify-content: flex-end — that makes scrollHeight == clientHeight
   and the overflow permanently unreachable. */
.lane-flow { flex: 1; min-width: 0; overflow-y: auto; line-height: 1.5; }
.lane-flow::before { content: ""; display: block; margin-top: auto; }
.lane-flow { display: flex; flex-direction: column; }
.lane-flow span { transition: color 120ms ease-out; }
.lane-flow span.awaiting { color: #6b7c8f; }
.lane-flow span.provisional { opacity: 0.75; }
```

- [ ] **Step 4: Rewrite the page**

`static/display.html` — replace `<main id="captions">` and the script tag:

```html
  <main id="captions">
    <div class="empty-state" id="empty">Waiting for the speaker…</div>
    <div class="wall" id="wall"></div>
  </main>
  <script type="module" src="/static/display.js"></script>
```

Replace `static/display.js`:

```js
/* Projector wall: independent flowing lanes, one per enabled language.
 *
 * Lanes are rebuilt only when the SET of languages changes. A caption arriving
 * never rebuilds anything — it is handed to each lane's Flow, which revises in
 * place. Rebuilding on every caption would reset every lane's scroll position.
 */
import { Flow } from "/static/flow.js";

const MAX_SENTENCES = 400;        // per lane; bounds the DOM over a long service
const LANG_TAGS = { en: "EN", ml: "ML", te: "TE", hi: "HI" };

const state = {
  display: { lanes: ["en", "ml", "te", "hi"], focus: null, rotate: 0 },
  sentences: new Map(),           // id -> {id, en, translations, final}
  order: [],
};
const flows = new Map();          // lang -> Flow

const els = {
  wall: document.getElementById("wall"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  viewUrl: document.getElementById("view-url"),
};
els.viewUrl.textContent = `http://${window.location.host}/view`;

/* ------------------------------------------------------------------- lanes */

function visibleLanes() {
  return state.display.lanes;
}

function renderLanes() {
  const wanted = visibleLanes();
  const current = [...flows.keys()];
  if (current.join(",") === wanted.join(",")) return;   // nothing structural changed

  els.wall.textContent = "";
  flows.clear();
  for (const lang of wanted) {
    const lane = document.createElement("div");
    lane.className = "lane";
    lane.dataset.lang = lang;

    const tag = document.createElement("span");
    tag.className = "lane-tag";
    tag.textContent = LANG_TAGS[lang] || lang.toUpperCase();

    const flowEl = document.createElement("div");
    flowEl.className = "lane-flow";

    lane.append(tag, flowEl);
    els.wall.appendChild(lane);

    const flow = new Flow(flowEl, lang);
    flow.reset(state.order.map((id) => state.sentences.get(id)));
    flow.trim(MAX_SENTENCES);
    flows.set(lang, flow);
    scrollLaneToLive(flowEl);
  }
}

function scrollLaneToLive(flowEl) {
  flowEl.scrollTop = flowEl.scrollHeight;
}

/* -------------------------------------------------------------- captions */

function remember(s) {
  const existing = state.sentences.get(s.id);
  if (existing) {
    Object.assign(existing, s);
    return existing;
  }
  const row = { translations: null, final: false, ...s };
  state.sentences.set(row.id, row);
  state.order.push(row.id);
  while (state.order.length > MAX_SENTENCES) {
    state.sentences.delete(state.order.shift());
  }
  return row;
}

function applyToLanes(row) {
  for (const [, flow] of flows) {
    flow.apply(row);
    flow.trim(MAX_SENTENCES);
    scrollLaneToLive(flow.container);
  }
  els.empty.style.display = state.order.length ? "none" : "";
}

/* ------------------------------------------------------------- connection */

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
      state.sentences.clear();
      state.order = [];
      for (const s of msg.sentences) remember(s);
      renderLanes();
      for (const [, flow] of flows) {
        flow.reset(state.order.map((id) => state.sentences.get(id)));
        scrollLaneToLive(flow.container);
      }
      els.empty.style.display = state.order.length ? "none" : "";
    } else if (msg.type === "display") {
      const { type, ...display } = msg;
      state.display = display;
      renderLanes();
    } else if (msg.type === "sentence") {
      applyToLanes(remember({ id: msg.id, en: msg.en, final: msg.final }));
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      applyToLanes(remember({ id, translations }));
    }
  };

  ws.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

renderLanes();
connect();
```

- [ ] **Step 5: Run the browser suite**

Run: `pytest -m browser -v -k display`
Expected: PASS

- [ ] **Step 6: Run everything**

Run: `pytest -v && pytest -m browser -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add static/display.html static/display.js static/common.css tests/ui/display_tests.js
git commit -m "feat: projector wall renders independent flowing lanes"
```

---

### Task 6: Focus and auto-rotate on the wall

**Files:**
- Modify: `static/display.js`, `static/common.css`
- Test: `tests/ui/display_tests.js` (append)

**Interfaces:**
- Consumes: `state.display.focus`, `state.display.rotate` from Task 5
- Produces: `nextLane(lanes, current) -> string` exported from `display.js` for
  the browser suite; DOM contract `.wall.focused` when a single lane is shown

- [ ] **Step 1: Append the failing tests**

Add to `tests/ui/display_tests.js` before `finish(run())`, inside `run()`:

```js
  // --- focus -------------------------------------------------------------
  ws.deliver(display(["en", "ml", "te", "hi"], "ml", 0));
  check("focus renders exactly one lane", lanes().length === 1, lanes().length);
  check("the focused lane is the requested one",
        lanes()[0].dataset.lang === "ml", lanes()[0].dataset.lang);
  check("wall is marked focused",
        doc.getElementById("wall").classList.contains("focused"), "no .focused");

  const focusedFlow = doc.querySelector('.lane[data-lang="ml"] .lane-flow');
  check("focused lane still holds the backlog",
        focusedFlow.querySelectorAll("span").length >= 12,
        focusedFlow.querySelectorAll("span").length);

  // Leaving focus must restore lanes that are already full, not blank ones.
  ws.deliver(display(["en", "ml", "te", "hi"], null, 0));
  check("leaving focus restores every lane", lanes().length === 4, lanes().length);
  const restored = doc.querySelector('.lane[data-lang="te"] .lane-flow');
  check("restored lane is not blank",
        restored.textContent.includes("తెలుగు"), restored.textContent.slice(0, 30));

  // --- rotation ----------------------------------------------------------
  const { nextLane } = win;
  check("rotation advances through the lanes",
        nextLane(["ml", "te", "hi"], "ml") === "te", nextLane(["ml", "te", "hi"], "ml"));
  check("rotation wraps",
        nextLane(["ml", "te", "hi"], "hi") === "ml", nextLane(["ml", "te", "hi"], "hi"));
  check("rotation starts at the first lane when nothing is current",
        nextLane(["ml", "te", "hi"], null) === "ml", nextLane(["ml", "te", "hi"], null));
  check("rotation survives the current lane being disabled",
        nextLane(["ml", "hi"], "te") === "ml", nextLane(["ml", "hi"], "te"));
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest -m browser -v -k display`
Expected: FAIL — focus still renders four lanes; `win.nextLane` is undefined.

- [ ] **Step 3: Add focused styling**

Append to `static/common.css`:

```css
/* A single lane owning the whole wall spends the extra height on MORE HISTORY,
   not larger text: the operator pins a language so someone who looked away can
   read back, which bigger type would defeat. */
.wall.focused .lane-tag { flex-basis: 3.2em; font-size: 0.45em; }
```

- [ ] **Step 4: Implement focus and rotation**

In `static/display.js`, replace `visibleLanes()` and add rotation:

```js
/* Which lanes to paint right now. Focus and rotation are mutually exclusive
   modes — see the spec's state model. Rotation is advanced by THIS page on its
   own timer, so the server needs no timer and broadcasts nothing per step. */
function visibleLanes() {
  const { lanes, focus, rotate } = state.display;
  if (rotate > 0) return [rotationLane];
  if (focus && lanes.includes(focus)) return [focus];
  return lanes;
}

/* Exported onto window for the browser suite; pure, so it is worth testing. */
export function nextLane(lanes, current) {
  if (!lanes.length) return null;
  const i = lanes.indexOf(current);
  // indexOf -1 (nothing current, or the current lane was just disabled) gives
  // 0 here, which is the right recovery: restart at the first lane.
  return lanes[(i + 1) % lanes.length];
}
window.nextLane = nextLane;

let rotationLane = null;
let rotationTimer = null;

function applyRotation() {
  clearInterval(rotationTimer);
  rotationTimer = null;
  const { lanes, rotate } = state.display;
  if (rotate <= 0) {
    rotationLane = null;
    return;
  }
  if (!lanes.includes(rotationLane)) rotationLane = nextLane(lanes, null);
  rotationTimer = setInterval(() => {
    rotationLane = nextLane(state.display.lanes, rotationLane);
    renderLanes();
  }, rotate * 1000);
}
```

Declare `rotationLane` and `rotationTimer` above `visibleLanes()` so they are
initialised before first use.

In `renderLanes()`, set the focused class after rebuilding:

```js
  els.wall.classList.toggle("focused", wanted.length === 1);
```

Place that line at the top of `renderLanes()`, before the early return, so the
class tracks the mode even when the lane set is unchanged.

In the `display` message branch of `ws.onmessage`, call `applyRotation()` before
`renderLanes()`:

```js
    } else if (msg.type === "display") {
      const { type, ...display } = msg;
      state.display = display;
      applyRotation();
      renderLanes();
    }
```

- [ ] **Step 5: Run the browser suite**

Run: `pytest -m browser -v -k display`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add static/display.js static/common.css tests/ui/display_tests.js
git commit -m "feat: pin one language full-wall, or rotate through them"
```

---

### Task 7: The operator page

**Files:**
- Create: `static/control.html`, `static/control.js`
- Test: `tests/ui/control_tests.js`, `tests/ui/test_view_ui.py` (add to parametrize), `tests/ui/server.py`

**Interfaces:**
- Consumes: `display` message shape from Task 2
- Produces: a page that sends complete `{"type":"display", lanes, focus, rotate}` messages

- [ ] **Step 1: Let the test server serve the page**

In `tests/ui/server.py`, extend `translate_path`:

```python
        if path in ("/view", "/display", "/control"):
            return str(ROOT / "static" / f"{path.lstrip('/')}.html")
```

In `tests/ui/test_view_ui.py`, add the suite:

```python
@pytest.mark.parametrize("suite", ["view", "display", "control"])
```

- [ ] **Step 2: Write the failing tests**

Create `tests/ui/control_tests.js`:

```js
/* The operator page. It holds no authoritative state: every interaction sends a
   COMPLETE display object, and the page re-renders from what the server echoes
   back. That is what stops two operator phones from disagreeing. */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const sent = (ws) => ws.__sent.map((raw) => JSON.parse(raw));

async function run() {
  const { win, doc } = await loadPage("/control", { width: 390, height: 720 });
  const ws = win.__sockets[0];
  check("control opened a caption socket", !!ws, ws && ws.url);
  if (!ws) return;
  ws.__sent = [];
  ws.send = (raw) => ws.__sent.push(raw);
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });
  ws.deliver({ type: "display", lanes: ["en", "ml", "te", "hi"], focus: null, rotate: 0 });

  const laneBtn = (lang) => doc.querySelector(`#lanes button[data-lang="${lang}"]`);
  check("a toggle exists per language",
        ["en", "ml", "te", "hi"].every(laneBtn), "missing lane toggle");
  check("all lanes start enabled",
        ["en", "ml", "te", "hi"].every((l) => laneBtn(l).classList.contains("on")),
        "a lane started off");

  laneBtn("en").click();
  const off = sent(ws).pop();
  check("turning english off sends a complete state",
        off && off.type === "display" &&
        JSON.stringify(off.lanes) === JSON.stringify(["ml", "te", "hi"]) &&
        off.focus === null && off.rotate === 0,
        JSON.stringify(off));

  // The page must not assume its own click succeeded — it renders the echo.
  ws.deliver({ type: "display", lanes: ["ml", "te", "hi"], focus: null, rotate: 0 });
  check("english toggle reflects the echoed state",
        !laneBtn("en").classList.contains("on"), "en still marked on");

  const focusBtn = (v) => doc.querySelector(`#focus button[data-focus="${v}"]`);
  focusBtn("ml").click();
  const pinned = sent(ws).pop();
  check("pinning a language sends focus and clears rotate",
        pinned.focus === "ml" && pinned.rotate === 0, JSON.stringify(pinned));

  doc.getElementById("rotate-on").click();
  const rotating = sent(ws).pop();
  check("enabling rotation clears the pin",
        rotating.focus === null && rotating.rotate > 0, JSON.stringify(rotating));

  // Disabling every lane would blank the wall; the page must refuse locally.
  ws.deliver({ type: "display", lanes: ["ml"], focus: null, rotate: 0 });
  const before = sent(ws).length;
  laneBtn("ml").click();
  check("refuses to disable the last lane", sent(ws).length === before,
        `sent ${sent(ws).length - before} messages`);
}

finish(run());
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest -m browser -v -k control`
Expected: FAIL — the harness cannot fetch `/control`.

- [ ] **Step 4: Build the page**

Create `static/control.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Live Translation — Operator</title>
  <link rel="stylesheet" href="/static/common.css">
  <style>
    main { gap: 1.6rem; }
    h2 { font-size: 0.72rem; letter-spacing: 0.09em; text-transform: uppercase;
         color: #7d8fa3; margin-bottom: 0.6rem; font-weight: 700; }
    .row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.5rem; }
    .row.wide { grid-template-columns: repeat(2, 1fr); }
    button {
      padding: 0.9rem 0.3rem; font-size: 0.95rem; font-family: inherit;
      background: #16202b; color: #e8eef5; border: 1px solid #2b3948;
      border-radius: 0.5rem; cursor: pointer; min-width: 0;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    button.on, button.active { background: #1d4ed8; border-color: #3b82f6; }
    #mirror { color: #7d8fa3; font-size: 0.9rem; line-height: 1.6; }
    #mirror b { color: #e8eef5; }
  </style>
</head>
<body>
  <header>
    <span><span id="status-dot"></span><span id="status-text">connecting…</span></span>
    <span>Operator</span>
  </header>
  <main>
    <section>
      <h2>Languages on the wall</h2>
      <div class="row" id="lanes">
        <button type="button" data-lang="en">English</button>
        <button type="button" data-lang="ml">മലയാളം</button>
        <button type="button" data-lang="te">తెలుగు</button>
        <button type="button" data-lang="hi">हिन्दी</button>
      </div>
    </section>
    <section>
      <h2>Full wall</h2>
      <div class="row" id="focus">
        <button type="button" data-focus="">All</button>
        <button type="button" data-focus="ml">മലയാളം</button>
        <button type="button" data-focus="te">తెలుగు</button>
        <button type="button" data-focus="hi">हिन्दी</button>
      </div>
    </section>
    <section>
      <h2>Auto-rotate</h2>
      <div class="row wide">
        <button type="button" id="rotate-off">Off</button>
        <button type="button" id="rotate-on">Every 20s</button>
      </div>
    </section>
    <section>
      <h2>On the wall now</h2>
      <p id="mirror">…</p>
    </section>
  </main>
  <script type="module" src="/static/control.js"></script>
</body>
</html>
```

Create `static/control.js`:

```js
/* Operator page. Sends COMPLETE wall states and renders only what the server
   echoes back — never its own optimistic guess. Two operator phones therefore
   converge on the same view instead of drifting apart. */
const ROTATE_S = 20;
const NAMES = { en: "English", ml: "മലയാളം", te: "తెలుగు", hi: "हिन्दी" };

let socket = null;
let wall = { lanes: ["en", "ml", "te", "hi"], focus: null, rotate: 0 };

const els = {
  lanes: document.getElementById("lanes"),
  focus: document.getElementById("focus"),
  rotateOn: document.getElementById("rotate-on"),
  rotateOff: document.getElementById("rotate-off"),
  mirror: document.getElementById("mirror"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
};

function send(next) {
  if (socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ type: "display", ...next }));
  }
}

function render() {
  for (const btn of els.lanes.querySelectorAll("button")) {
    btn.classList.toggle("on", wall.lanes.includes(btn.dataset.lang));
  }
  for (const btn of els.focus.querySelectorAll("button")) {
    const value = btn.dataset.focus || null;
    btn.classList.toggle("active", !wall.rotate && wall.focus === value);
    // Cannot pin a language that is not on the wall.
    btn.disabled = value !== null && !wall.lanes.includes(value);
  }
  els.rotateOn.classList.toggle("active", wall.rotate > 0);
  els.rotateOff.classList.toggle("active", wall.rotate === 0);

  const shown = wall.lanes.map((l) => NAMES[l] || l).join(", ");
  if (wall.rotate) {
    els.mirror.innerHTML = `Rotating every <b>${wall.rotate}s</b> through <b>${shown}</b>`;
  } else if (wall.focus) {
    els.mirror.innerHTML = `<b>${NAMES[wall.focus]}</b> alone, full wall`;
  } else {
    els.mirror.innerHTML = `<b>${shown}</b>`;
  }
}

els.lanes.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-lang]");
  if (!btn) return;
  const lang = btn.dataset.lang;
  const on = wall.lanes.includes(lang);
  // Refuse locally rather than letting the server reject it: an empty wall is
  // never what the operator meant, and a silent no-op is confusing.
  if (on && wall.lanes.length === 1) return;
  const lanes = on
    ? wall.lanes.filter((l) => l !== lang)
    : ["en", "ml", "te", "hi"].filter((l) => wall.lanes.includes(l) || l === lang);
  send({ ...wall, lanes });
});

els.focus.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-focus]");
  if (!btn || btn.disabled) return;
  // Pinning and rotating are mutually exclusive modes.
  send({ ...wall, focus: btn.dataset.focus || null, rotate: 0 });
});

els.rotateOn.addEventListener("click", () => send({ ...wall, focus: null, rotate: ROTATE_S }));
els.rotateOff.addEventListener("click", () => send({ ...wall, rotate: 0 }));

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${proto}//${location.host}/ws/captions`);

  socket.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
  };
  socket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type !== "display") return;
    const { type, ...state } = msg;
    wall = state;
    render();
  };
  socket.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

render();
connect();
```

- [ ] **Step 5: Run the browser suite**

Run: `pytest -m browser -v -k control`
Expected: PASS

- [ ] **Step 6: Run everything**

Run: `pytest -v && pytest -m browser -v`
Expected: PASS across `view`, `display`, `control`

- [ ] **Step 7: Update the README pages table**

In `README.md`, add to the pages table:

```markdown
| `/control/<token>` | the operator's phone | turn languages on and off, pin one full-wall, auto-rotate. The token is printed by `start.sh` and is new every run |
```

- [ ] **Step 8: Commit**

```bash
git add static/control.html static/control.js tests/ui/control_tests.js \
        tests/ui/test_view_ui.py tests/ui/server.py README.md
git commit -m "feat: operator page for lanes, focus and auto-rotate"
```

---

## Manual verification

After Task 7, before calling stage 1 done:

1. `./start.sh` — confirm the banner prints a `/control/<token>` line with a
   fresh token, and that the token differs on a second run.
2. Open `/display` on the projector and `/control/<token>` on a phone.
3. Speak. Confirm each lane flows as continuous prose rather than blocks, and
   that an untranslated sentence shows grey English rather than `…`.
4. Turn English off. Three lanes should remain and grow taller; no gap.
5. Pin Malayalam. Confirm it fills the wall and shows several past lines.
6. Un-pin. Confirm Telugu and Hindi come back already full of text, not blank.
7. Enable auto-rotate; confirm it cycles and that pinning cancels it.
8. Kill the projector's WiFi and restore it. Confirm it reconnects to the same
   lanes rather than reverting to all four.
9. Open `/control/wrongtoken` — must 404 identically to `/control/`.

## Self-review notes

- **Spec coverage.** Protocol `final` → Task 2. Protocol `display` + retention →
  Task 2. Lane rendering + grey-English-not-ellipsis → Task 5. `flow.js`
  extraction → Task 4. Focus with more history → Task 6. Auto-rotate → Task 6.
  `/control` → Task 7. Secret path + `start.sh` → Task 3. Config values → Task 1.
  Testing section → covered per task plus the manual list above.
- **Spec correction applied.** The spec previously said rotate is
  "ignored when `focus` is non-null". It now documents the mutually exclusive
  model implemented here: `rotate > 0` forces `focus` to null server-side, and
  the display advances rotation locally. No action needed during execution.

- **Out of scope here.** Stage 2 (sentence as the unit), stage 2b (grace hold),
  stage 3 (repair pass), stage 4 (streaming STT). No task in this plan touches
  `stt.py`, `translator.py`, or `audio_buffer.py`.
