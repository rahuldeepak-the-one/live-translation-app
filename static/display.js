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
  // Pin to the newest caption. The projector never scrolls by hand, and the
  // container is a real scrolling box now (see common.css), so without this a
  // caption taller than the remaining space would run off the bottom edge.
  els.captions.scrollTop = els.captions.scrollHeight;
}

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
    const i = state.sentences.findIndex((x) => x.id === s.id);
    if (i >= 0) state.sentences[i].en = s.en;
    else state.sentences.push({ id: s.id, en: s.en, translations: null });
  }

  function applyTranslation(id, translations) {
    const row = state.sentences.find((x) => x.id === id);
    if (row) row.translations = translations;
  }

  function finalize() {
    state.sentences = state.sentences.slice(-SHOW_LAST);
    render();
  }

  ws.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "history") {
      state.sentences = msg.sentences.map((s) => ({ ...s }));
      connSentences.forEach(applySentence);
      connTranslations.forEach(([id, t]) => applyTranslation(id, t));
      gotHistory = true;
      finalize();
    } else if (msg.type === "sentence") {
      if (!gotHistory) connSentences.push({ id: msg.id, en: msg.en });
      applySentence({ id: msg.id, en: msg.en });
      finalize();
    } else if (msg.type === "translation") {
      const { type, id, ...translations } = msg;
      if (!gotHistory) connTranslations.push([id, translations]);
      applyTranslation(id, translations);
      finalize();
    }
  };

  ws.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}
connect();
