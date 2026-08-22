# Projector Display Redesign — Design

Date: 2026-08-23
Source handoff: `design_handoff_projector_display/` (README.md + `Display Wall - Final.dc.html`)

## Purpose

Replace the `/display` projector wall with the handed-off card design and its
trust-marking system: confirmed sentences render dark, in-progress text renders
grey on its own line and turns dark when the sentence is finalized. Adds a
light/dark theme with an operator toggle; light is the default.

This is a presentation-layer change. The SSE/WebSocket protocol, `hub.py`,
`display_state.py`, `control.js` and `/view` are untouched.

## Decisions taken against the handoff

The handoff and the running system disagree in four places. Resolutions, agreed
with the client 2026-08-23:

### 1. The grey in-progress line shows English

The Final mockup shows the pending line in the target script. The pipeline
cannot produce that: translating an unfinished sentence is precisely the P0
failure the same handoff mandates fixing, so a pending sentence has no
translation to show.

Resolution: the pending line shows the **English** source, greyed, with a
trailing `…` — the behaviour `flow.js` already has, and the behaviour the
handoff's own `1b Repair quietly — recommended` frame illustrates. On
confirmation the text swaps to the target script as it fades grey → dark.

### 2. `en` drives the header bar, not a card

`lanes` already includes `en` as an operator-togglable lane. Cards are
`lanes − 'en'`; the header English line is shown iff `en ∈ lanes` (the
mockup's `showEnBar`). The same sentence is therefore never printed twice.

Degenerate case: if `lanes − 'en'` is empty (`lanes === ['en']`, or focus is
`en`), English is rendered as the single card so the wall is never blank.

The handoff's "4 languages → 2×2" rule is retained in CSS and is reachable if a
fourth target language is ever configured; with today's `TARGET_LANGS` the
maximum is three cards.

### 3. Scrollback is removed from the projector

The card is a fixed top-anchored window of roughly 2–4 lines. Older confirmed
text is dropped, not scrolled. This deliberately supersedes the current
behaviour documented at `common.css:106`, where focus mode spends its extra
height on more history. Full history remains available to congregants on
`/view`.

### 4. The P0 segmentation fix is already closed

Measured over the repo's own transcripts:

| | 2026-08-21 (pre-fix) | 2026-08-22 (current code) |
|---|---|---|
| flushed sentences | 37 | 5 |
| fragments reaching the translator | 14/37 | 0/5 |
| inter-flush gap | median 7.9s, p90 12.8s, max 19.4s | median 5.9s |

`MAX_SENTENCE_HOLD_S` is 16.0s, above the ~8s inter-chunk gap the handoff
cites, and `flush_if_stale()` already flushes on silence rather than on
sentence age.

Separately, `pipeline.py:132-134` computes `held_for` immediately after
`touched = self._clock()`, so it is always ~0 and the
`held_for >= MAX_SENTENCE_HOLD_S` disjunct can never fire. That dead branch is
removed. No behaviour change; the flush rule already reads
`looks_complete(joined) or len(joined) >= MAX_PENDING_CHARS`.

### 5. Fonts are loaded locally, not from Google Fonts

The handoff specifies a Google Fonts `<link>`. `qr.py` documents that the venue
has no internet, so that link fails closed and causes a fallback flash on the
projector. All four Noto families are present on the host
(`/usr/share/fonts/truetype/noto/`), so the existing local font stack is kept
and no network fetch is added.

Follow-up (out of scope): if a hall machine lacks the Noto families, self-host
woff2 under `static/fonts/` with `@font-face`. Do not reintroduce the CDN link.

## Architecture

### Module boundaries

| File | Status | Responsibility |
|---|---|---|
| `static/display.html` | rewritten | Wall markup: header, card grid, QR footer |
| `static/display.css` | new | Wall layout + both theme token sets |
| `static/theme.js` | new | Resolve, apply, persist and toggle the theme |
| `static/card.js` | new | `LanguageCard`: confirmed/pending split + fit window |
| `static/display.js` | rewritten | Socket, wall state, card grid, header English line |
| `static/common.css` | trimmed | Display-only `.wall`/`.lane*` block removed |
| `static/flow.js` | **untouched** | Still `/view`'s unbounded scrolling transcript |
| `pipeline.py` | trimmed | Dead `held_for` branch removed |

`card.js` is a sibling of `flow.js`, not an extension of it. The handoff
suggests extending `flow.js` with a `confirmed | pending` flag, but `flow.js`
already carries `final`; the genuinely new work is a *windowed* renderer that
splits confirmed from pending and drops content to fit. `/view` needs none of
that and still wants unbounded scrollback. Giving `Flow` two personalities
would put `/view` at risk for every `/display` change.

`display.html` stops linking `common.css`. `common.css` is shared with `/view`
and `/control`, which are dark by design — a phone in a dark church should stay
dark, and the wall no longer shares the header/main/footer pattern. Verified:
`control.html` uses `id="lanes"` and the word "wall" in headings only, never
the `.wall`/`.lane*` classes, so moving that block to `display.css` disturbs
nothing.

### Per-card sentence classification

A sentence is **confirmed in language L** iff `final === true` **and**
(`L === 'en'` or `translations[L]` is a non-empty string). Otherwise it is
pending. Three states fall out of data the protocol already carries:

| `translations[L]` | meaning | renders as |
|---|---|---|
| absent / `null` | not translated yet (held, or in flight) | grey English on the pending line, trailing `…` |
| `""` | translator returned empty for this language | `Waiting for translation…` |
| non-empty string | confirmed | dark, in-script, in the confirmed block |

The empty-string case is real: `translator.py:148` returns `{lang: ""}`.
`flow.js:90` currently cannot distinguish it from "not yet" because `""` is
falsy; `card.js` distinguishes the two explicitly.

### Card DOM

```
<section class="card" data-lang="te">
  <span class="card-label">తెలుగు</span>
  <div class="card-content">
    <p class="card-confirmed"><span data-sid="41">…</span><span data-sid="42">…</span></p>
    <p class="card-pending">Good intentions without action accomplish nothing…</p>
  </div>
</section>
```

Confirmed sentences are inline `<span>`s keyed by sentence id inside one
paragraph, so the card reads as continuous prose and wraps. Updates are
append-only and keyed: an existing span is never replaced or reordered, so a
reader never loses their place.

### Grey → dark confirmation

When a sentence becomes confirmed in L, its text is written into a span
appended to `.card-confirmed` that starts at `--text-pending`; on the next
animation frame a class flip lets `transition: color 300ms ease` carry it to
`--text-confirmed`. No movement, no flash.

Because the pending line held English, the text also swaps script at that
instant. The fade reads as "this just became trustworthy".

Each sentence id animates at most once. `card.js` keeps a `Set` of already
animated ids, so spans re-created by a re-fit or a lane rebuild appear at their
final colour instead of re-running the fade.

### Fit algorithm

Runs after every update to a card.

1. The pending line is capped at 2 lines by CSS (`line-clamp: 2` plus the
   `-webkit-box` / `-webkit-line-clamp` form the projector's Chrome needs).
   The trailing `…` is part of the text, so a clamped line shows the browser's
   ellipsis in its place and never doubles.
2. Render at most the newest 8 sentences, so a 400-sentence model never builds
   396 spans only to drop them.
3. For `scale` in `1 → 0.87 → 0.78`: set `--card-scale`, then while
   `.card-content` overflows and more than one confirmed span remains, remove
   the oldest confirmed span. Stop at the first scale that fits.
4. Telugu therefore steps 46 → 40 → 36 px, as the handoff specifies; the other
   scripts step proportionally from their own base.

Both text sizes derive from the same two properties, so the pending line always
matches the confirmed text's size and weight as the handoff requires:

```css
.card-content { font-size: calc(var(--card-font-px) * var(--card-scale)); }
```

`--card-font-px` is set per language on the card; `--card-scale` is set by the
fit loop. The 2-line clamp is expressed in `em`, so it tracks the scale.

Dropping is a **view** concern only. `state.sentences` keeps its 400-sentence
cache, so re-focusing or re-enabling a language re-renders from full data.

Accepted limitation: a single confirmed sentence longer than the card at 0.78
scale is clipped by `overflow: hidden`. This needs one translated sentence
beyond roughly five lines at the smallest size and is left to clip rather than
adding a truncation path that would fire nowhere else.

### Base type per script

| Card | Size at scale 1 | Line height |
|---|---|---|
| Telugu | 46px | 1.45 |
| Malayalam | 42px | 1.5 |
| Hindi | 54px | 1.45 |
| English (degenerate card only) | 44px | 1.4 |

All caption text is weight 600. English's size is not in the handoff; 44px sits
between the others and suits Latin's tighter fit.

### Grid

`data-cards="N"` on the grid container drives layout: 1 → full wall, 2 → one
row of two, 3 → a row of two plus a full-width row, 4 → 2×2. Cards are rebuilt
only when the visible language *set* changes, matching the existing
`renderLanes()` contract; a caption never rebuilds anything.

Focus and rotation are unchanged, including the exported `nextLane()` and the
clear-before-set discipline in `applyRotation()`.

### Waiting states

Precedence per card:

1. Confirmed content renders if present.
2. Pending line: `Waiting for translation…` if the pending sentence's
   translation is `""`; otherwise grey English + `…`; otherwise empty.
3. If both are empty, `Listening…` renders in the confirmed block's position.

Waiting is therefore always per card. The wall-level `#empty`
(`Waiting for the speaker…`) element is removed: with cards top-anchored and
independent, a single global placeholder would either cover live cards or
contradict them.

Both waiting strings use `--text-muted` at a fixed 24px weight 400 — visibly
not caption content, and unaffected by `--card-scale`.

### Header and footer

- LIVE indicator keeps its connection semantics: 9px dot, `--live-dot` green
  with the label `LIVE` when connected; `--warn-dot` amber with
  `RECONNECTING` when the socket is down. At 13px letter-spaced this is an
  operator signal, not congregation-facing text, and it stays honest rather
  than showing a green `LIVE` on a dead socket.
- English line: the newest sentence's English, `white-space: nowrap` with
  ellipsis, shown only when `en ∈ lanes`.
- Theme toggle: 28px sun/moon icon button, far right, with an `aria-label`.
- Footer: `Scan for phone captions` + `<img src="/qr.svg">` at 58×58. The
  existing `#view-url` element is removed entirely — no IP, URL, or server
  info appears on the wall.

### Theme

`theme.js` resolves in order: `?theme=dark|light` → `localStorage.theme` →
`light`. It sets `data-theme` on `<html>` and exports `toggleTheme()`, which
persists to `localStorage` and applies without reload.

`?theme` is a per-URL override for kiosk setups and is **not** written to
`localStorage` — a kiosk pinned to `?theme=dark` must not silently change what
an operator's browser does on the plain `/display` URL. Toggling always
persists, and a persisted choice then applies on the plain URL.

Tokens are defined on `:root` and overridden under `:root[data-theme="dark"]`.

| Token | Light | Dark |
|---|---|---|
| `--page-bg` | `#f6f8fa` | `#0b0f14` |
| `--card-bg` | `#fbfcfd` | `#0e141b` |
| `--card-border` | `#e3e8ee` | `#1c2733` |
| `--text-confirmed` | `#10151b` | `#e8eef5` |
| `--text-pending` | `#aeb7c1` | `#6b7c8f` |
| `--text-muted` | `#8a94a0` | `#5b6e83` |
| `--text-secondary` | `#66717e` | `#7d8fa3` |
| `--live-dot` | `#16a34a` | `#22c55e` |
| `--warn-dot` | `#d97706` | `#f59e0b` |
| `--qr-border` | `#d7dee6` | `#1c2733` |
| `--link` | `#3b82f6` | `#3b82f6` |

`--warn-dot` and `--qr-border` are additions; the handoff's table covers
neither the disconnected state nor the QR frame in dark. The QR image keeps a
white background in both themes — it must stay scannable.

Spacing, radius and the no-shadow rule follow the handoff: card padding
26/40/30, grid gap 20px, label→text 18px, confirmed→pending 10px, card radius
10px, QR radius 6px.

Long words wrap via `overflow-wrap: break-word` on card text.

## Testing

TDD through the existing Chrome harness (`tests/ui/`, `pytest -m browser`).
`tests/ui/display_tests.js` is rewritten for the new DOM; the harness, runner
and `test_view_ui.py` are unchanged.

Behaviour to cover:

- Cards are `lanes − 'en'`; the header English line appears iff `en ∈ lanes`;
  `lanes === ['en']` yields a single English card.
- `data-cards` is 1/2/3 for the corresponding lane sets.
- Classification: an unfinalised sentence lands on the pending line as grey
  English with `…`; once final with a translation it moves into the confirmed
  block in the target script; `translations[L] === ""` yields
  `Waiting for translation…`; an empty card shows `Listening…`.
- Append-only: a span's node identity survives a later caption.
- Each sentence fades grey → dark at most once.
- Fit: after a run of long sentences `.card-content` does not overflow.
- Theme: defaults to light, `toggleTheme()` flips `data-theme` and writes
  `localStorage`, `?theme=dark` is honoured.
- No IP, hostname or URL appears anywhere in the wall's DOM.
- Focus, rotation, the clear-before-set timer discipline, history replay and
  the registration race carry over from the existing suite.

Python side: the existing `pytest` suite must stay green, in particular
`tests/test_pipeline.py` across the `held_for` removal.
