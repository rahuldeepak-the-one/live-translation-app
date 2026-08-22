/* One language's card on the projector wall.
 *
 * Not flow.js. /view wants an unbounded, scrollable transcript; a card is a
 * FIXED WINDOW showing the newest few sentences and dropping the rest. Nothing
 * on the wall scrolls, so a reader 25 metres back never has to find their
 * place again — which is also why updates here are strictly append-only.
 *
 * The trust contract, which is the point of the whole redesign:
 *
 *   confirmed  dark, in the target script   the server will not revise this
 *   pending    grey, English standing in    still being spoken, or translating
 *   failed     "Waiting for translation…"   the translator gave up on this one
 *
 * A sentence crosses pending -> confirmed exactly once, with a 300ms fade, and
 * only if a viewer was actually there to see it happen.
 */

// How many of the cache's sentences are ever built into DOM. The fit pass
// drops from here; anything older could not fit a card at any font size, so
// building it would be work for the garbage collector and nothing else.
const RENDER_WINDOW = 8;

// 46 -> 40 -> 36 for Telugu, and proportionally for every other script.
// Dropping old text comes first; this is the last resort for one sentence that
// is simply too long for its card.
const SCALE_LADDER = [1, 0.87, 0.78];

export const LISTENING = "Listening…";
export const AWAITING_TRANSLATION = "Waiting for translation…";

/* Which of the three states a sentence is in FOR THIS LANGUAGE.
 *
 * The `""` case is load-bearing: translator.py returns {lang: ""} when it
 * gives up on one language, and flow.js cannot tell that from a key that has
 * not arrived yet because "" is falsy. One means "this will never come", the
 * other means "wait a second" — a reader deserves to know which. */
export function classify(sentence, lang) {
  const text = lang === "en" ? sentence.en : sentence.translations?.[lang];
  if (text === "") return "failed";
  if (sentence.final && typeof text === "string") return "confirmed";
  return "pending";
}

/* The markup for one card. Lives here rather than in display.html so the card
   and its renderer cannot drift apart, and so tests can mount one alone. */
export function createCard(doc, lang, label) {
  const section = doc.createElement("section");
  section.className = "card";
  section.dataset.lang = lang;

  const labelEl = doc.createElement("span");
  labelEl.className = "card-label";
  labelEl.textContent = label;

  // .card-content is the fixed box the fit pass measures against; the two
  // paragraphs inside it are the confirmed block and the pending line.
  const content = doc.createElement("div");
  content.className = "card-content";

  const confirmed = doc.createElement("p");
  confirmed.className = "card-confirmed";
  const pending = doc.createElement("p");
  pending.className = "card-pending";

  content.append(confirmed, pending);
  section.append(labelEl, content);
  return section;
}

export class LanguageCard {
  constructor(section, lang) {
    this.lang = lang;
    this.section = section;
    this.doc = section.ownerDocument;
    this.win = this.doc.defaultView;
    this.content = section.querySelector(".card-content");
    this.confirmedEl = section.querySelector(".card-confirmed");
    this.pendingEl = section.querySelector(".card-pending");
    this.spans = new Map();      // sentence id -> span, confirmed only
    this.everPending = new Set(); // ids this card has shown as pending
    this.faded = new Set();       // ids that have already run the fade
  }

  /* `sentences` is the wall's whole ordered cache, oldest first. The card
     takes what it can use and ignores the rest. */
  render(sentences) {
    const window_ = sentences.slice(-RENDER_WINDOW);
    const confirmed = [];
    const tail = [];
    for (const s of window_) {
      if (classify(s, this.lang) === "confirmed") confirmed.push(s);
      else tail.push(s);
    }

    this._paintPending(tail);
    this._paintConfirmed(confirmed);
    this.fit();
  }

  /* Make the card's content fit its box, and return the scale it settled on.
   *
   * Drop before shrink, deliberately: a reader 25 metres back needs the type
   * big far more than they need the sentence from a minute ago. The ladder is
   * only reached when there is nothing left to drop — one sentence too long
   * for its own card. */
  fit() {
    this._setScale(1);
    while (this._overflows() && this._droppable()) this._dropOldest();
    if (!this._overflows()) return 1;

    for (const scale of SCALE_LADDER.slice(1)) {
      this._setScale(scale);
      if (!this._overflows()) return scale;
    }
    // Bottom of the ladder with a single sentence still too tall. Accepted:
    // overflow:hidden clips it rather than the card growing over its
    // neighbours. Needs one translated sentence beyond ~5 lines at 36px.
    return SCALE_LADDER[SCALE_LADDER.length - 1];
  }

  _setScale(scale) {
    this.section.style.setProperty("--card-scale", String(scale));
  }

  _overflows() {
    // 1px of tolerance: sub-pixel line heights routinely make scrollHeight
    // exceed clientHeight by a fraction on content that visually fits.
    return this.content.scrollHeight > this.content.clientHeight + 1;
  }

  /* The newest confirmed sentence is never dropped.
   *
   * It is tempting to drop it when the pending line is long — that line is the
   * live edge, after all. It is the wrong trade: the pending line is grey
   * ENGLISH, worth little to the Telugu-only reader this card exists for,
   * while the confirmed line is the translation they came for. The 2-line
   * clamp on the pending line is what keeps it from crowding this out; if the
   * confirmed sentence alone still will not fit, the scale ladder handles it. */
  _droppable() {
    return this.spans.size > 1;
  }

  // Oldest is first in the DOM, which is the authority on order here: the
  // spans Map can be reordered by a later render restoring a dropped span.
  _dropOldest() {
    const first = this.confirmedEl.firstElementChild;
    if (!first) return;
    this.spans.delete(Number(first.dataset.sid));
    first.remove();
  }

  _paintPending(tail) {
    // Only the newest unconfirmed sentence can be a translation failure worth
    // announcing; anything older is about to scroll out of relevance anyway.
    const newest = tail[tail.length - 1];
    let text = "";
    if (newest && classify(newest, this.lang) === "failed") {
      text = AWAITING_TRANSLATION;
    } else if (tail.length) {
      // The trailing ellipsis is the sentence saying it is not finished. It is
      // part of the text so that a clamped line replaces it with the browser's
      // own ellipsis rather than showing two.
      text = `${tail.map((s) => s.en).join(" ")}…`;
    }
    for (const s of tail) this.everPending.add(s.id);

    this.pendingEl.textContent = text;
    this.pendingEl.classList.toggle("card-waiting", text === AWAITING_TRANSLATION);
  }

  _paintConfirmed(confirmed) {
    if (!confirmed.length) {
      // An empty panel reads as broken from the back of a hall. Say something.
      const listening = this.pendingEl.textContent === "";
      this.confirmedEl.textContent = listening ? LISTENING : "";
      this.confirmedEl.classList.toggle("card-waiting", listening);
      for (const span of this.spans.values()) span.remove();
      this.spans.clear();
      return;
    }
    if (this.confirmedEl.classList.contains("card-waiting")) {
      this.confirmedEl.textContent = "";
      this.confirmedEl.classList.remove("card-waiting");
    }

    const wanted = new Set(confirmed.map((s) => s.id));
    for (const [id, span] of this.spans) {
      if (!wanted.has(id)) {
        span.remove();
        this.spans.delete(id);
      }
    }

    // A keyed reconcile rather than a rebuild: an existing span keeps its
    // identity, so the browser does not reflow text the reader is mid-way
    // through, and only genuinely new spans can animate. insertBefore also
    // repairs order after the fit pass has dropped from the front and a later
    // render restores it.
    let cursor = null;
    for (const s of confirmed) {
      let span = this.spans.get(s.id);
      if (!span) {
        span = this.doc.createElement("span");
        span.dataset.sid = String(s.id);
        this.spans.set(s.id, span);
        this._maybeFade(s.id, span);
      }
      // Trailing space so consecutive sentences read as prose and wrap.
      const text = this.lang === "en" ? s.en : s.translations[this.lang];
      const next = `${text} `;
      if (span.textContent !== next) span.textContent = next;

      const expected = cursor ? cursor.nextSibling : this.confirmedEl.firstChild;
      if (span !== expected) this.confirmedEl.insertBefore(span, expected);
      cursor = span;
    }
  }

  /* Fade only a crossing somebody actually watched.
   *
   * A backlog replayed from history never showed its pending state to anyone,
   * so fading it in would announce a transition that did not happen — and on a
   * reconnect the whole wall would flash grey at once. */
  _maybeFade(id, span) {
    if (!this.everPending.has(id) || this.faded.has(id)) return;
    this.faded.add(id);
    span.classList.add("fading");
    // Next frame, not this one: the pending colour has to be painted once
    // before the transition has anything to animate away from.
    this.win.requestAnimationFrame(() => span.classList.remove("fading"));
  }
}
