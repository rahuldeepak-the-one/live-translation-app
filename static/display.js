/* Projector display: shows the last N sentences in all four languages. */
const SHOW_LAST = 3;
const LANGS = [
  ["en", "EN"], ["ml", "ML"], ["te", "TE"], ["hi", "HI"],
];

const state = { sentences: [] };  // [{id, en, translations|null}]
const els = {
  captions: document.getElementById("captions"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  viewUrl: document.getElementById("view-url"),
};
els.viewUrl.textContent = `http://${window.location.host}/view`;

function upsert(sentence) {
  const i = state.sentences.findIndex((s) => s.id === sentence.id);
  if (i >= 0) state.sentences[i] = { ...state.sentences[i], ...sentence };
  else state.sentences.push(sentence);
  state.sentences = state.sentences.slice(-SHOW_LAST);
  render();
}

function render() {
  els.empty.style.display = state.sentences.length ? "none" : "";
  els.captions.querySelectorAll(".caption-row").forEach((n) => n.remove());
  for (const s of state.sentences) {
    const row = document.createElement("div");
    row.className = "caption-row";
    for (const [code, tag] of LANGS) {
      const text = code === "en" ? s.en : s.translations?.[code];
      const line = document.createElement("div");
      line.className = "lang-line" + (text ? "" : " pending");
      const tagEl = document.createElement("span");
      tagEl.className = "lang-tag";
      tagEl.textContent = tag;
      const textEl = document.createElement("span");
      textEl.className = "lang-text";
      textEl.textContent = text || "…";
      line.append(tagEl, textEl);
      row.appendChild(line);
    }
    els.captions.appendChild(row);
  }
}

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
      for (const s of msg.sentences) {
        const i = state.sentences.findIndex((x) => x.id === s.id);
        if (i >= 0) state.sentences[i] = { ...state.sentences[i], ...s };
        else state.sentences.push(s);
      }
      state.sentences.sort((a, b) => a.id - b.id);
      state.sentences = state.sentences.slice(-SHOW_LAST);
      render();
    } else if (msg.type === "sentence") {
      upsert({ id: msg.id, en: msg.en, translations: null });
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      upsert({ id, translations });
    }
  };
  ws.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}
connect();
