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
const flows = new Map();          // lang -> Flow (built against .lane-text)
const scrollers = new Map();      // lang -> .lane-flow element (the scroll container)

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
  scrollers.clear();
  for (const lang of wanted) {
    const lane = document.createElement("div");
    lane.className = "lane";
    lane.dataset.lang = lang;

    const tag = document.createElement("span");
    tag.className = "lane-tag";
    tag.textContent = LANG_TAGS[lang] || lang.toUpperCase();

    // Two levels, deliberately: .lane-flow is the scroll container; .lane-text
    // is a plain block paragraph holding Flow's inline spans. See common.css
    // for why Flow must never be constructed against the flex-column scroll
    // container directly.
    const flowEl = document.createElement("div");
    flowEl.className = "lane-flow";

    const textEl = document.createElement("p");
    textEl.className = "lane-text";
    flowEl.appendChild(textEl);

    lane.append(tag, flowEl);
    els.wall.appendChild(lane);

    const flow = new Flow(textEl, lang);
    flow.reset(state.order.map((id) => state.sentences.get(id)));
    flow.trim(MAX_SENTENCES);
    flows.set(lang, flow);
    scrollers.set(lang, flowEl);
  }
  // Scroll only after every lane exists in the DOM. Each .lane shares equal
  // height with its siblings (flex: 1 inside .wall's flex column), so a lane
  // scrolled while it is still the only child measures a taller-than-final
  // clientHeight; once its later siblings are appended that height shrinks
  // and the earlier scrollTop falls short of the live edge.
  for (const flowEl of scrollers.values()) scrollLaneToLive(flowEl);
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
  for (const [lang, flow] of flows) {
    flow.apply(row);
    flow.trim(MAX_SENTENCES);
    scrollLaneToLive(scrollers.get(lang));
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
      for (const [lang, flow] of flows) {
        flow.reset(state.order.map((id) => state.sentences.get(id)));
        scrollLaneToLive(scrollers.get(lang));
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
