/* Projector wall: one card per enabled target language.
 *
 * Cards are rebuilt only when the SET of visible languages changes. A caption
 * arriving never rebuilds anything — it is handed to each card, which revises
 * in place. Rebuilding per caption would restart every card's fade and throw
 * away the append-only guarantee readers depend on.
 *
 * English is not a card. It has the header line, and printing the same
 * sentence twice would spend wall space a reader at the back needs.
 */
import { LanguageCard, createCard } from "/static/card.js";
import { initTheme, toggleTheme } from "/static/theme.js";

const MAX_SENTENCES = 400;        // bounds the cache over a long service

// Native script, because the person who needs the label cannot read the other
// three. English only ever labels the degenerate single-card case below.
const LANG_LABELS = { en: "English", te: "తెలుగు", ml: "മലയാളം", hi: "हिन्दी" };

const state = {
  display: { lanes: ["en", "ml", "te", "hi"], focus: null, rotate: 0 },
  sentences: new Map(),           // id -> {id, en, translations, final}
  order: [],
};
const cards = new Map();          // lang -> LanguageCard

const els = {
  grid: document.getElementById("grid"),
  live: document.getElementById("live"),
  liveLabel: document.getElementById("live-label"),
  enBar: document.getElementById("en-bar"),
  enText: document.getElementById("en-text"),
  themeToggle: document.getElementById("theme-toggle"),
};

/* ------------------------------------------------------------------ theme */

initTheme(window);
labelToggle();

function labelToggle() {
  const dark = document.documentElement.dataset.theme === "dark";
  els.themeToggle.setAttribute(
    "aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}

els.themeToggle.addEventListener("click", () => {
  toggleTheme(window);
  labelToggle();
});

/* ------------------------------------------------------------------ cards */

// rotationLane/rotationTimer must be declared before visibleLanes() — it reads
// rotationLane, and renderCards() (which calls visibleLanes()) runs at module
// load, below. A `let` declared after that first call would still be in its
// temporal dead zone and throw a ReferenceError.
let rotationLane = null;
let rotationTimer = null;

/* Which lanes the operator is asking for right now. Focus and rotation are
   mutually exclusive modes; rotation is advanced by THIS page on its own
   timer, so the server needs no timer and broadcasts nothing per step. */
function visibleLanes() {
  const { lanes, focus, rotate } = state.display;
  if (rotate > 0) return rotationLane ? [rotationLane] : [];
  if (focus && lanes.includes(focus)) return [focus];
  return lanes;
}

/* The cards to paint: the visible lanes minus English.
 *
 * If that leaves nothing — the operator pinned English, or rotation landed on
 * it — English becomes the card rather than the wall going blank. */
function cardLangs() {
  const targets = visibleLanes().filter((l) => l && l !== "en");
  return targets.length ? targets : ["en"];
}

/* The header line duplicates an English card, so it yields to one — and it
   stays out of the way until there is a sentence to put on it, rather than
   showing a bare "English" label against nothing. */
function enBarVisible() {
  return state.display.lanes.includes("en")
    && !cardLangs().includes("en")
    && state.order.length > 0;
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
    renderCards();
    paintCards();
  }, rotate * 1000);
}

/* Returns whether it actually rebuilt the DOM. The history handler needs to
   know: when this returns true every card it now holds was just built and
   painted from the current cache, so painting again would be repeated work. */
function renderCards() {
  const wanted = cardLangs();
  els.grid.dataset.cards = String(wanted.length);
  els.enBar.hidden = !enBarVisible();

  const current = [...cards.keys()];
  if (current.join(",") === wanted.join(",")) return false;

  els.grid.textContent = "";
  cards.clear();
  for (const lang of wanted) {
    const section = createCard(document, lang, LANG_LABELS[lang] || lang);
    els.grid.appendChild(section);
    cards.set(lang, new LanguageCard(section, lang));
  }
  paintCards();
  return true;
}

function rows() {
  return state.order.map((id) => state.sentences.get(id));
}

function paintCards() {
  const all = rows();
  for (const card of cards.values()) card.render(all);
  paintEnBar(all);
}

function paintEnBar(all) {
  const newest = all[all.length - 1];
  els.enText.textContent = newest ? newest.en : "";
  // Also set here, not only in renderCards(): the first caption of a service
  // does not change the card SET, so renderCards() early-returns and this is
  // the only place that learns the line now has something to show.
  els.enBar.hidden = !enBarVisible();
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

/* ------------------------------------------------------------- connection */

function setLive(live, label) {
  els.live.dataset.state = live ? "live" : "down";
  els.liveLabel.textContent = label;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/captions`);

  // Events received on THIS connection before its history snapshot arrives.
  // Replayed on top of history: hub.py adds a client to _clients before
  // awaiting the history send, so a `sentence`/`translation` can legitimately
  // arrive first. Mirrors view.js's connSentences/connTranslations — without
  // this, that utterance paints once and then vanishes the instant history
  // (which never saw it) arrives and wipes the cache.
  const connSentences = [];
  const connTranslations = [];
  let gotHistory = false;

  ws.onopen = () => setLive(true, "LIVE");

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "history") {
      state.sentences.clear();
      state.order = [];
      for (const s of msg.sentences) remember(s);
      connSentences.forEach((s) => remember(s));
      connTranslations.forEach(([id, translations]) => remember({ id, translations }));
      gotHistory = true;
      // `history` is a full-snapshot event (registration replay, or a fresh
      // reconnect where stale state must be discarded), so every card needs
      // bringing up to date with the cache just rebuilt above — UNLESS
      // renderCards() just built and painted them from that same cache. The
      // common case is a fresh load mid-service, where the card SET happens
      // not to have changed, so it early-returns and the cards are stale.
      if (!renderCards()) paintCards();
    } else if (msg.type === "display") {
      const { type, ...display } = msg;
      state.display = display;
      applyRotation();
      renderCards();
      paintCards();
    } else if (msg.type === "sentence") {
      if (!gotHistory) connSentences.push({ id: msg.id, en: msg.en, final: msg.final });
      remember({ id: msg.id, en: msg.en, final: msg.final });
      paintCards();
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      if (!gotHistory) connTranslations.push([id, translations]);
      remember({ id, translations });
      paintCards();
    }
  };

  // "RECONNECTING", not a green LIVE over a dead socket: the operator needs to
  // know the wall has stopped updating, and at 13px letter-spaced this reads
  // as a status light rather than as text competing with the captions.
  ws.onclose = () => {
    setLive(false, "RECONNECTING");
    setTimeout(connect, 2000);
  };
}

renderCards();
connect();
