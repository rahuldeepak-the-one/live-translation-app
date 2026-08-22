/* Operator page. Sends COMPLETE wall states and renders only what the server
 * echoes back — never its own optimistic guess. Two operator phones therefore
 * converge on the same view instead of drifting apart.
 */
const ROTATE_S = 20;
const NAMES = { en: "English", ml: "മലയാളം", te: "తెలుగు", hi: "हिन्दी" };

let socket = null;
let wall = { lanes: ["en", "ml", "te", "hi"], focus: null, rotate: 0 };

const els = {
  lanes: document.getElementById("lanes"),
  focus: document.getElementById("focus"),
  rotateOn: document.getElementById("rotate-on"),
  rotateOff: document.getElementById("rotate-off"),
  mirror: document.getElementById("mirror"),
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
};

// The canonical language order comes from the buttons already in the page —
// not a second hard-coded array — so there is exactly one source of truth
// for wall order, and re-enabling a language slots it back where its button
// sits rather than at the end.
const LANE_ORDER = [...els.lanes.querySelectorAll("button")].map((b) => b.dataset.lang);

function send(next) {
  if (socket && socket.readyState === 1) {
    socket.send(JSON.stringify({ type: "display", ...next }));
  }
}

function render() {
  for (const btn of els.lanes.querySelectorAll("button")) {
    btn.classList.toggle("on", wall.lanes.includes(btn.dataset.lang));
  }
  for (const btn of els.focus.querySelectorAll("button")) {
    const value = btn.dataset.focus || null;
    btn.classList.toggle("active", !wall.rotate && wall.focus === value);
    // Cannot pin a language that is not on the wall.
    btn.disabled = value !== null && !wall.lanes.includes(value);
  }
  els.rotateOn.classList.toggle("active", wall.rotate > 0);
  els.rotateOff.classList.toggle("active", wall.rotate === 0);

  const shown = wall.lanes.map((l) => NAMES[l] || l).join(", ");
  if (wall.rotate) {
    els.mirror.innerHTML = `Rotating every <b>${wall.rotate}s</b> through <b>${shown}</b>`;
  } else if (wall.focus) {
    els.mirror.innerHTML = `<b>${NAMES[wall.focus]}</b> alone, full wall`;
  } else {
    els.mirror.innerHTML = `<b>${shown}</b>`;
  }
}

els.lanes.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-lang]");
  if (!btn) return;
  const lang = btn.dataset.lang;
  const on = wall.lanes.includes(lang);
  // Refuse locally rather than letting the server reject it: an empty wall is
  // never what the operator meant, and a silent no-op is confusing.
  if (on && wall.lanes.length === 1) return;
  const lanes = on
    ? wall.lanes.filter((l) => l !== lang)
    : LANE_ORDER.filter((l) => wall.lanes.includes(l) || l === lang);
  send({ ...wall, lanes });
});

els.focus.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-focus]");
  if (!btn || btn.disabled) return;
  // Pinning and rotating are mutually exclusive modes.
  send({ ...wall, focus: btn.dataset.focus || null, rotate: 0 });
});

els.rotateOn.addEventListener("click", () => send({ ...wall, focus: null, rotate: ROTATE_S }));
els.rotateOff.addEventListener("click", () => send({ ...wall, rotate: 0 }));

/* This page is served at /control/<token>, so it already holds the one secret
   it needs — no need to embed it separately, which would risk it reaching a
   page the congregation can load. The socket carries it because reading the
   caption feed is open to every screen but CHANGING the wall is not. Empty on
   the test harness, which serves /control with no token and stubs the socket. */
function controlToken() {
  return location.pathname.split("/").filter(Boolean)[1] || "";
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const t = controlToken();
  socket = new WebSocket(
    `${proto}//${location.host}/ws/captions${t ? `?t=${encodeURIComponent(t)}` : ""}`);

  socket.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
  };
  socket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type !== "display") return;
    const { type, ...state } = msg;
    wall = state;
    render();
  };
  socket.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

render();
connect();
