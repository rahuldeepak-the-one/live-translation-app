/* Personal phone view: one chosen language, remembered across visits. */
const KEEP_LAST = 10;

const state = {
  lang: localStorage.getItem("lang") || "ml",
  sentences: [],  // [{id, en, translations|null}]
};
const els = {
  captions: document.getElementById("captions"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  buttons: document.querySelectorAll("#picker button"),
};

els.buttons.forEach((btn) => {
  btn.addEventListener("click", () => {
    state.lang = btn.dataset.lang;
    localStorage.setItem("lang", state.lang);
    updatePicker();
    render();
  });
});

function updatePicker() {
  els.buttons.forEach((b) => b.classList.toggle("active", b.dataset.lang === state.lang));
}

function textFor(s) {
  if (state.lang === "en") return s.en;
  return s.translations?.[state.lang] || null;  // null -> still translating
}

function render() {
  els.empty.style.display = state.sentences.length ? "none" : "";
  els.captions.querySelectorAll(".caption-row").forEach((n) => n.remove());
  for (const s of state.sentences) {
    const text = textFor(s);
    const row = document.createElement("div");
    row.className = "caption-row";
    const line = document.createElement("div");
    line.className = "lang-line" + (text ? "" : " pending");
    const textEl = document.createElement("span");
    textEl.className = "lang-text";
    textEl.textContent = text || "…";
    line.appendChild(textEl);
    row.appendChild(line);
    els.captions.appendChild(row);
  }
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
    state.sentences = state.sentences.slice(-KEEP_LAST);
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

updatePicker();
connect();
