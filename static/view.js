/* Personal phone view: one chosen language, rendered as a flowing transcript.
 *
 * Three things this file is careful about:
 *   1. Rendering is incremental. A full rebuild would reset scrollTop on every
 *      message, which defeats reading back through the service.
 *   2. Autoscroll only happens if you were already at the live edge. Scrolled
 *      up to re-read something? You stay where you are.
 *   3. A sentence that isn't translated yet shows its English in grey rather
 *      than a placeholder, so the page never looks stalled while the server
 *      waits for the sentence to finish.
 */
import { Flow } from "/static/flow.js";

const MAX_SENTENCES = 2000;      // whole service; guard against unbounded DOM
const LIVE_EDGE_PX = 80;         // "close enough to the bottom" for autoscroll
const SIZE_STEPS = ["1.1rem", "1.3rem", "1.5rem", "1.8rem", "2.2rem", "2.7rem"];

const state = {
  lang: localStorage.getItem("lang") || "ml",
  sizeStep: Number(localStorage.getItem("sizeStep") ?? 2),
};

const els = {
  captions: document.getElementById("captions"),
  transcript: document.getElementById("transcript"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  jumpLive: document.getElementById("jump-live"),
  smaller: document.getElementById("text-smaller"),
  bigger: document.getElementById("text-bigger"),
  buttons: document.querySelectorAll("#picker button"),
};

const flow = new Flow(els.transcript, state.lang);

/* ---------------------------------------------------------------- language */

els.buttons.forEach((btn) => {
  btn.addEventListener("click", () => {
    state.lang = btn.dataset.lang;
    localStorage.setItem("lang", state.lang);
    updatePicker();
    rebuild();
  });
});

function updatePicker() {
  els.buttons.forEach((b) => b.classList.toggle("active", b.dataset.lang === state.lang));
}

/* -------------------------------------------------------------- text size */

function applySize() {
  state.sizeStep = Math.max(0, Math.min(SIZE_STEPS.length - 1, state.sizeStep));
  document.documentElement.style.setProperty("--caption-size", SIZE_STEPS[state.sizeStep]);
  localStorage.setItem("sizeStep", String(state.sizeStep));
  els.smaller.disabled = state.sizeStep === 0;
  els.bigger.disabled = state.sizeStep === SIZE_STEPS.length - 1;
}

function stepSize(delta) {
  const atEdge = atLiveEdge();
  state.sizeStep += delta;
  applySize();
  if (atEdge) scrollToLive();       // resizing reflows; keep the live edge visible
}

els.smaller.addEventListener("click", () => stepSize(-1));
els.bigger.addEventListener("click", () => stepSize(+1));

/* ---------------------------------------------------------------- scrolling */

function atLiveEdge() {
  const c = els.captions;
  return c.scrollHeight - c.clientHeight - c.scrollTop <= LIVE_EDGE_PX;
}

function scrollToLive() {
  els.captions.scrollTop = els.captions.scrollHeight;
}

function updateJumpLive() {
  els.jumpLive.hidden = atLiveEdge();
}

els.captions.addEventListener("scroll", updateJumpLive);
els.jumpLive.addEventListener("click", () => {
  scrollToLive();
  updateJumpLive();
});

/* ---------------------------------------------------------------- rendering */

function rebuild() {
  flow.setLang(state.lang);
  els.empty.style.display = flow.isEmpty ? "" : "none";
  scrollToLive();
  updateJumpLive();
}

/* Apply a change, then autoscroll only if we were already at the live edge.
   The check must happen BEFORE the DOM grows, or everything looks "at bottom". */
function update(mutate) {
  const stick = atLiveEdge();
  mutate();
  flow.trim(MAX_SENTENCES);
  els.empty.style.display = flow.isEmpty ? "" : "none";
  if (stick) scrollToLive();
  updateJumpLive();
}

/* ------------------------------------------------------------- wake lock */

let wakeLock = null;

async function keepScreenAwake() {
  if (!("wakeLock" in navigator)) return;   // older iOS Safari — nothing to do
  try {
    wakeLock = await navigator.wakeLock.request("screen");
  } catch (err) {
    /* Denied (often: tab not visible). Retried on the next visibility change. */
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") keepScreenAwake();
});

/* ------------------------------------------------------------- connection */

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/captions`);

  // Events received on THIS connection before its history snapshot arrives.
  // Replayed on top of history: handles both the registration race (live
  // event before history) and server restarts (stale pre-reconnect state
  // must be discarded, and ids from a previous server session mean nothing).
  const connSentences = [];
  const connTranslations = [];
  let gotHistory = false;

  function applySentence(s) {
    flow.apply({ id: s.id, en: s.en, final: s.final });
  }

  function applyTranslation(id, translations) {
    flow.apply({ id, translations });
  }

  ws.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "history") {
      flow.reset(msg.sentences);
      connSentences.forEach(applySentence);
      connTranslations.forEach(([id, t]) => applyTranslation(id, t));
      gotHistory = true;
      flow.trim(MAX_SENTENCES);
      rebuild();
    } else if (msg.type === "sentence") {
      if (!gotHistory) connSentences.push({ id: msg.id, en: msg.en, final: msg.final });
      update(() => applySentence({ id: msg.id, en: msg.en, final: msg.final }));
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      if (!gotHistory) connTranslations.push([id, translations]);
      update(() => applyTranslation(id, translations));
    }
  };

  ws.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

updatePicker();
applySize();
updateJumpLive();
keepScreenAwake();
connect();
