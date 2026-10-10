/* Personal phone view: one chosen language, in two modes.
 *
 *   Live  — the flowing transcript, pinned to the speaker.
 *   Recap — the sentences that carried the message, as a scrollable reading
 *           list. Tap one to land on it in the live transcript.
 *
 * The recap is computed ON THE PHONE from sentences it already holds (see
 * recap.js). No endpoint, no model, no round trip — which is also why it is
 * extractive rather than a written summary.
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
import { selectRecap } from "/static/recap.js";

const MAX_SENTENCES = 2000;      // whole service; guard against unbounded DOM
const LIVE_EDGE_PX = 80;         // "close enough to the bottom" for autoscroll
const SIZE_STEPS = ["1.1rem", "1.3rem", "1.5rem", "1.8rem", "2.2rem", "2.7rem"];
const FLASH_MS = 1600;           // landing marker after a tap through from recap

const state = {
  lang: localStorage.getItem("lang") || "ml",
  sizeStep: Number(localStorage.getItem("sizeStep") ?? 2),
  // Deliberately NOT persisted. You unlock the phone mid-service to hear what
  // is being said now; reopening into a stale recap would be wrong every time.
  mode: "live",
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
  modeButtons: document.querySelectorAll("#mode-switch button"),
  recapPanel: document.getElementById("recap-panel"),
  recapList: document.getElementById("recap-list"),
  recapMeta: document.getElementById("recap-meta"),
  recapEmpty: document.getElementById("recap-empty"),
  recapRefresh: document.getElementById("recap-refresh"),
  recapRefreshLabel: document.getElementById("recap-refresh-label"),
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
  const atEdge = state.mode === "live" && atLiveEdge();
  state.sizeStep += delta;
  applySize();
  if (atEdge) scrollToLive();       // resizing reflows; keep the live edge visible
}

els.smaller.addEventListener("click", () => stepSize(-1));
els.bigger.addEventListener("click", () => stepSize(+1));

/* ---------------------------------------------------------------- scrolling */

// Remembered across a trip into recap mode. A hidden container reports
// scrollHeight 0, so nothing about the live edge can be measured while the
// transcript is off screen — it has to be recorded on the way out.
let liveStick = true;
let liveScroll = 0;
let recapScroll = 0;

function atLiveEdge() {
  const c = els.captions;
  return c.scrollHeight - c.clientHeight - c.scrollTop <= LIVE_EDGE_PX;
}

function scrollToLive() {
  els.captions.scrollTop = els.captions.scrollHeight;
}

function updateJumpLive() {
  els.jumpLive.hidden = state.mode !== "live" || atLiveEdge();
}

els.captions.addEventListener("scroll", updateJumpLive);
els.jumpLive.addEventListener("click", () => {
  scrollToLive();
  updateJumpLive();
});

/* -------------------------------------------------------------------- mode */

els.modeButtons.forEach((btn) => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});

function setMode(mode) {
  if (mode === state.mode) return;

  if (state.mode === "live") {
    liveStick = atLiveEdge();       // must be read BEFORE the container hides
    liveScroll = els.captions.scrollTop;
  } else {
    recapScroll = els.recapPanel.scrollTop;
  }

  state.mode = mode;
  document.body.classList.toggle("mode-recap", mode === "recap");
  document.body.classList.toggle("mode-live", mode === "live");
  els.modeButtons.forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.mode === mode)));

  // display:none drops scroll position, so both panels are restored by hand.
  els.captions.hidden = mode !== "live";
  els.recapPanel.hidden = mode !== "recap";

  if (mode === "live") {
    if (liveStick) scrollToLive();
    else els.captions.scrollTop = liveScroll;
  } else {
    // Only rebuild when something was actually said since the recap on screen
    // was built — otherwise flipping back and forth reshuffles your reading
    // position for nothing. The refresh pill covers the rest.
    if (recapBuiltAt !== flow.order.length) buildRecap();
    else els.recapPanel.scrollTop = recapScroll;
  }

  updateJumpLive();
  updateRefresh();
}

/* ------------------------------------------------------------------- recap */

let recapBuiltAt = -1;            // flow.order.length when the list was built

// Cards fade up as they arrive. Root is the panel, not the viewport: the panel
// is the scroller, and cards below its fold are still inside the window rect.
const reveal = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("in");
      reveal.unobserve(e.target);
    }
  },
  { root: els.recapPanel, rootMargin: "0px 0px -6% 0px" },
);

function recapCard(s, i) {
  const { text, awaiting } = flow.textFor(s);

  const li = document.createElement("li");
  li.className = "recap-card";
  li.style.setProperty("--i", String(i));      // drives the stagger delay
  li.dataset.sid = String(s.id);

  const shell = document.createElement("button");
  shell.type = "button";
  shell.className = "recap-shell";

  const core = document.createElement("span");
  core.className = "recap-core";

  const ord = document.createElement("span");
  ord.className = "recap-ord";
  ord.textContent = String(i + 1).padStart(2, "0");

  const body = document.createElement("span");
  body.className = "recap-text";
  body.textContent = text;
  body.classList.toggle("awaiting", awaiting);

  core.append(ord, body);
  shell.append(core);
  li.append(shell);
  shell.addEventListener("click", () => jumpToSentence(s.id));
  reveal.observe(li);
  return li;
}

function buildRecap() {
  const all = flow.all;
  const picked = selectRecap(all);

  recapBuiltAt = all.length;
  els.recapList.textContent = "";
  els.recapEmpty.hidden = picked.length > 0;
  els.recapMeta.textContent = picked.length
    ? `${picked.length} key ${picked.length === 1 ? "moment" : "moments"} `
      + `from ${all.length} sentences. Tap one to read it in place.`
    : "";
  picked.forEach((s, i) => els.recapList.appendChild(recapCard(s, i)));
  els.recapPanel.scrollTop = 0;
  recapScroll = 0;
  updateRefresh();
}

function updateRefresh() {
  const pending = state.mode === "recap" && recapBuiltAt >= 0
    ? flow.order.length - recapBuiltAt
    : 0;
  els.recapRefresh.hidden = pending <= 0;
  if (pending > 0) {
    els.recapRefreshLabel.textContent =
      `${pending} new sentence${pending === 1 ? "" : "s"}`;
  }
}

els.recapRefresh.addEventListener("click", buildRecap);

function jumpToSentence(id) {
  setMode("live");
  const span = flow.spanFor(id);
  if (!span) return;
  liveStick = false;                   // you asked for a specific line, stay on it
  // The transcript was display:none one statement ago, so it has no scroll
  // geometry yet. scrollIntoView forces the layout flush itself — waiting a
  // frame first is not needed, and rAF does not fire in the headless renderer
  // the UI suite runs under, so depending on it would make this untestable.
  span.scrollIntoView({ block: "center", behavior: "smooth" });
  span.classList.add("flash");
  setTimeout(() => span.classList.remove("flash"), FLASH_MS);
  updateJumpLive();
}

/* ---------------------------------------------------------------- rendering */

function rebuild() {
  flow.setLang(state.lang);
  els.empty.style.display = flow.isEmpty ? "" : "none";
  if (state.mode === "recap") {
    // Selection scores the English, so a language switch picks the same
    // sentences and simply repaints them in the new script.
    buildRecap();
  } else {
    scrollToLive();
  }
  updateJumpLive();
}

/* Apply a change, then autoscroll only if we were already at the live edge.
   The check must happen BEFORE the DOM grows, or everything looks "at bottom". */
function update(mutate) {
  const stick = state.mode === "live" ? atLiveEdge() : liveStick;
  mutate();
  flow.trim(MAX_SENTENCES);
  els.empty.style.display = flow.isEmpty ? "" : "none";
  if (state.mode === "live") {
    if (stick) scrollToLive();
    updateJumpLive();
  } else {
    liveStick = stick;              // carried until live mode comes back
    updateRefresh();                // "N new sentences" on the refresh pill
  }
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
