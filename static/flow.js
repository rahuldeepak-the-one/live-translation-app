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
      // Revising a sentence already on screen never changes which one is
      // newest, so no _markLive() here — see _insert() for why a fresh
      // sentence is different.
      Object.assign(existing, s);
      this._paint(existing);
      return;
    }
    // A translation for a sentence we have never seen. The original
    // applyTranslation() no-op'd here; materialising a row would paint the
    // string "undefined" into the lane. /display constructs a Flow per lane
    // at runtime, so this is reachable, not theoretical.
    if (typeof s.en !== "string") return;
    this._insert(s);
    this._markLive();
  }

  // Shared by apply() (one new sentence) and reset() (many). Never calls
  // _markLive() itself: apply() needs it once, right after; reset() needs it
  // once, after the whole batch — either callsite doing it per-row makes
  // _markLive's full-span walk run once per sentence, i.e. O(n^2) overall.
  _insert(s) {
    const row = { translations: null, final: false, ...s };
    this.sentences.set(row.id, row);
    this.order.push(row.id);
    this._paint(row);
  }

  reset(sentences) {
    this.container.textContent = "";
    this.order = [];
    this.sentences.clear();
    this.spans.clear();
    // Same "no en, no row" guard as apply() — reset() bypasses apply() to
    // avoid its per-row _markLive(), but not the guard against a
    // translation-only row painting the literal string "undefined".
    for (const s of sentences) {
      if (typeof s.en === "string") this._insert(s);
    }
    this._markLive();
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
    // No _markLive() here on purpose: _paint() runs once per sentence inside
    // reset() and setLang(), and _markLive() itself walks every span, so a
    // call here makes both O(n^2). Callers that can change which sentence is
    // newest (apply()'s new-row branch, reset()) call it themselves, exactly
    // once, after the paint work is done.
  }

  _markLive() {
    const newest = this.order[this.order.length - 1];
    for (const [id, span] of this.spans) span.classList.toggle("live", id === newest);
  }
}
