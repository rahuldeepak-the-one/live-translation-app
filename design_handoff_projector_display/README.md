# Handoff: Church Live Translation — Projector Display Redesign

## Overview
Redesign of the `/display` projector wall for the Church Live Translation app (one English speaker, live translated captions in Telugu, Malayalam, Hindi). The redesign adds a **trust-marking system**: only complete sentences are shown as confirmed (dark) text; in-progress translation appears as lighter grey text on its own line, turning dark when the sentence is finalized. This fixes the app's worst failure mode (mid-sentence fragments translating into confident wrong meaning — see `transcripts/2026-08-21.jsonl` and `docs/translation-audit.html` in the app repo).

Implement **both a light and a dark theme with an operator toggle** (persist choice in localStorage). The light theme is the default (morning services, bright hall).

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing intended look and behavior, not production code to copy directly. Recreate them in the existing codebase (`live_translation_app/` — vanilla JS: `static/display.html`, `static/display.js`, `static/flow.js`, `static/common.css`) using its established patterns. Keep the existing SSE/data flow; this is a presentation-layer change plus one segmentation fix (below).

## Fidelity
**High-fidelity.** `Display Wall - Final.dc.html` is the pixel reference for the light theme at 1920×1080. Recreate it exactly, then derive the dark theme from the token table below.

## Screens / Views

### Projector wall (replaces current `/display`)
1920×1080, fills viewport, `overflow: hidden`. Column layout:

**1. Header row** — padding `14px 36px 12px`, flex row, gap 22px, vertically centered:
- LIVE indicator: 9px circle, `#16a34a` (light) / `#22c55e` (dark), then `LIVE` — 13px, weight 600, letter-spacing 0.14em, muted color. Top-left.
- `English` label — 16px, muted.
- Current English source sentence — 24px, line-height 1.3, one line, `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`, secondary color `#66717e`. Show ONLY the current sentence/short chunk, never a scrolling paragraph.
- **Theme toggle** (new): small unobtrusive control in this row, far right — e.g. a 28px icon button (sun/moon). Must not compete with captions.

**2. Language cards area** — `flex: 1`, column, gap 20px, padding `8px 24px 20px`:
- Row 1 (flex 1): **తెలుగు** and **മലയാളം** cards side by side, gap 20px, equal width.
- Row 2 (flex 1): **हिन्दी** card full width.
- Layout must adapt to the enabled-language set (the app already rebuilds lanes on settings change): 2 languages → one row of 2; 3 → 2+1 as above; 4 → 2×2 grid.

**Card** (identical structure for all languages):
- Background `#fbfcfd`, border `1px solid #e3e8ee`, radius 10px, padding `26px 40px 30px`.
- Label at top: native-script language name, 19px, muted `#8a94a0`, margin-bottom 18px. Identical position in every card.
- Content is **top-anchored** under the label (not vertically centered/bottom-aligned) — consistent text position across all cards.
- **Confirmed text**: weight 600, color `#10151b`. Per-script sizes: Telugu 46px / lh 1.45; Malayalam 42px / lh 1.5; Hindi 54px / lh 1.45.
- **In-progress text**: same size/weight, color `#aeb7c1`, **always on a new line** below the confirmed text, margin-top 10px, trailing ellipsis (`…`).
- Keep the active translation ~2–4 lines. If it grows longer, drop the oldest confirmed chunk at a sentence boundary (never clip glyphs mid-line) and/or step font size down (46→40→36).

**3. Footer QR row** — right-aligned, gap 12px, padding `0 24px 16px`:
- `Scan for phone captions` — 14px, muted.
- QR image 58×58, white bg, `1px solid #d7dee6`, radius 6px, padding 3px. Generate the real QR for the `/view` URL server-side or with a QR lib; the design uses a placeholder.
- **Never show the raw IP/URL, server info, or any technical text on the projector.**

## Interactions & Behavior
- **Grey → dark confirmation**: when a sentence completes, transition the in-progress line's color to the confirmed color over ~300ms ease, then merge it into the confirmed block. No sliding, bouncing, or flashy transitions — subtle fade only.
- **Stability**: never re-render/replace already-confirmed text while the speaker continues; append only. Readers must not lose their place.
- **Complete-thought priority (segmentation fix, P0)**: do not flush/translate mid-sentence fragments. In the pipeline, raise `MAX_SENTENCE_HOLD_S` above the observed inter-chunk gap (~8s) or flush on silence rather than a timer; hold fragments until the sentence completes. While holding, the card simply keeps showing grey in-progress text.
- **Waiting states**: empty card shows a subtle muted `Listening…` (never an empty broken-looking panel). Translation failure for one language shows `Waiting for translation…` in that card — never a technical error.
- **Theme toggle**: switches all tokens (table below), persists in `localStorage`, applies without reload. Also honor `?theme=dark|light` query param for kiosk setups if easy.
- **Responsive**: 16:9 projector is primary; the grid should also hold on TV/laptop widths. Long Telugu/Malayalam/Hindi words must wrap (`overflow-wrap: break-word`) without breaking the layout.

## State Management
- Existing per-language flow state (confirmed sentences list + current in-progress fragment) — `flow.js` already models this; extend it with a `confirmed | pending` flag per chunk.
- `theme: 'light' | 'dark'` (localStorage-persisted).
- Enabled-language set (existing settings SSE) drives the card grid layout.

## Design Tokens

Light (default) / Dark:
- Page background: `#f6f8fa` / `#0b0f14`
- Card background: `#fbfcfd` / `#0e141b`
- Card border: `#e3e8ee` / `#1c2733`
- Confirmed text: `#10151b` / `#e8eef5`
- In-progress (grey) text: `#aeb7c1` / `#6b7c8f`
- Muted labels (language names, EN label, QR caption): `#8a94a0` / `#5b6e83`
- Secondary text (English source line): `#66717e` / `#7d8fa3`
- LIVE dot: `#16a34a` / `#22c55e`
- Link color (if any links appear): `#3b82f6`

Type: Google Noto Sans family — `'Noto Sans'`, `'Noto Sans Telugu'`, `'Noto Sans Malayalam'`, `'Noto Sans Devanagari'`; weights 400/600/700. Weight 600 for all caption text (never thin weights — projector readability).
Spacing: card padding 26/40/30; grid gap 20px; label→text 18px; confirmed→pending 10px.
Radius: cards 10px, QR 6px. No shadows.

## Assets
- `qr-placeholder.png` — placeholder only; replace with a generated QR encoding the phone captions URL.
- Fonts loaded from Google Fonts (see link tag in the design file); self-host if the hall's network is unreliable.

## Design principle (from the client)
Design for a person sitting 20–30 meters away in a church, reading a translation while listening to a sermon. Readability, stability, and a clear confirmed-vs-in-progress distinction beat decorative UI. Do not redesign the grey→dark behavior.

## Files
- `Display Wall - Final.dc.html` — final light-theme reference (open in a browser; the visual source of truth).
- `Display Wall - Trust Marking.dc.html` — earlier explorations, incl. dark-theme frames (turn-1 sections) useful as the dark-mode reference.
- `qr-placeholder.png` — used by both.
