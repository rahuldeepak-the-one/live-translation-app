/* Control room. Read-only about the wall, hands-on about the microphone.
 *
 * The capture device belongs to ./feed-mic.py's parec child, not to the
 * server, so nothing here changes a device directly: every button sends an
 * instruction up the operator socket, the server relays it to the running
 * feed, and this page renders only what comes back. Same discipline as
 * /control — never an optimistic guess, so two operator phones agree.
 */
const NAMES = { en: "English", ml: "മലയാളം", te: "తెలుగు", hi: "हिन्दी", pa: "ਪੰਜਾਬੀ" };

// The server calls anything quieter than this silence (config.SILENCE_THRESHOLD,
// int16). Drawn on the meter so "is it hearing the room?" is one glance.
const SILENCE_THRESHOLD = 300;
const METER_FLOOR_DB = -60;

let socket = null;
let mic = { connected: false, capturing: false, devices: [], level: 0 };

const els = {
  dot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  device: document.getElementById("mic-device"),
  sub: document.getElementById("mic-sub"),
  meter: document.getElementById("meter"),
  meterRead: document.getElementById("meter-read"),
  threshold: document.getElementById("threshold"),
  start: document.getElementById("btn-start"),
  stop: document.getElementById("btn-stop"),
  devices: document.getElementById("devices"),
  devicesEmpty: document.getElementById("devices-empty"),
  chips: document.getElementById("chips"),
  pipeline: document.getElementById("d-pipeline"),
  screens: document.getElementById("d-screens"),
  wall: document.getElementById("d-wall"),
  urls: document.getElementById("urls"),
};

/* Served at /status/<token>, so the page already holds the one secret it
   needs — exactly as /control does. Empty under the test harness. */
function token() {
  return location.pathname.split("/").filter(Boolean)[1] || "";
}

function send(msg) {
  if (socket && socket.readyState === 1) socket.send(JSON.stringify(msg));
}

/* ------------------------------------------------------------------ meter */

function dbfs(level) {
  return level > 0 ? 20 * Math.log10(level) : -Infinity;
}

/* Linear scale is useless here: the silence threshold sits at 0.9% of full
   scale, i.e. pinned to the left edge, so every reading looks like nothing.
   On a dB scale it lands around a third of the way across, where the eye can
   actually judge it. */
function meterFraction(level) {
  const db = dbfs(level);
  if (!isFinite(db)) return 0;
  return Math.max(0, Math.min(1, (db - METER_FLOOR_DB) / -METER_FLOOR_DB));
}

els.threshold.style.left = `${meterFraction(SILENCE_THRESHOLD / 32768) * 100}%`;

function renderMeter() {
  const live = mic.connected && mic.capturing;
  const fraction = live ? meterFraction(mic.level) : 0;
  els.meter.style.transform = `scaleX(${fraction})`;
  const audible = mic.level * 32768 >= SILENCE_THRESHOLD;
  els.meter.classList.toggle("quiet", !audible);
  if (!live) {
    els.meterRead.textContent = "not listening";
  } else if (!isFinite(dbfs(mic.level))) {
    els.meterRead.textContent = "silent";
  } else {
    els.meterRead.textContent =
      `${dbfs(mic.level).toFixed(0)} dB · peak ${Math.round(mic.level * 32768)}`;
  }
}

/* ------------------------------------------------------------------- mic */

function renderMic() {
  const named = mic.deviceLabel || mic.deviceId || "Unnamed input";
  els.device.textContent = mic.connected ? named : "No capture feed connected";
  els.device.classList.toggle("muted", !mic.connected);

  if (!mic.connected) {
    els.sub.textContent = "Run ./feed-mic.py on the machine with the mixer.";
  } else if (!mic.capturing) {
    els.sub.textContent = "Connected but not listening. Press Start.";
  } else if (mic.level * 32768 < SILENCE_THRESHOLD) {
    // The failure this whole page exists for: capture running, nothing heard.
    els.sub.textContent = "Listening — but nothing above the silence threshold. "
      + "Check the cable, the mixer fader, and that this is the right input.";
  } else {
    els.sub.textContent = "Listening, and hearing the room.";
  }

  els.start.disabled = !mic.connected || mic.capturing;
  els.stop.disabled = !mic.connected || !mic.capturing;
  els.start.classList.toggle("armed", mic.connected && !mic.capturing);
  els.stop.classList.toggle("armed", mic.connected && mic.capturing);

  renderDevices();
  renderMeter();
  renderChips();
}

function deviceRow(id, label, role, current) {
  const li = document.createElement("li");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `device${current ? " current" : ""}`;

  const pip = document.createElement("span");
  pip.className = "pip";

  const name = document.createElement("span");
  name.className = "name";
  name.textContent = label;

  const tag = document.createElement("span");
  tag.className = "role";
  tag.textContent = current ? "in use" : role;

  btn.append(pip, name, tag);
  btn.addEventListener("click", () => send({ type: "mic", action: "set_device", deviceId: id }));
  li.append(btn);
  return li;
}

function renderDevices() {
  els.devices.textContent = "";
  // No "system default" row. It existed because getUserMedia had a null-device
  // mode; feed-mic.py names a pactl source instead, so a 'default' id would
  // match nothing and the button would silently do nothing. Every row here is
  // a real device the feed reported it can open.
  for (const d of mic.devices) {
    els.devices.append(deviceRow(d.id, d.label, "input", mic.deviceId === d.id));
  }
  els.devicesEmpty.hidden = mic.devices.length > 0;
}

function chip(text, cls) {
  const span = document.createElement("span");
  span.className = `chip ${cls}`;
  const pip = document.createElement("span");
  pip.className = "pip";
  span.append(pip, document.createTextNode(text));
  return span;
}

let pipeline = "—";

function renderChips() {
  els.chips.textContent = "";
  els.chips.append(chip(mic.connected ? "Mic feed connected" : "No mic feed",
                        mic.connected ? "on" : "off"));
  els.chips.append(chip(mic.capturing ? "Capturing" : "Stopped",
                        mic.capturing ? "on" : "warn"));
  const audible = mic.connected && mic.capturing && mic.level * 32768 >= SILENCE_THRESHOLD;
  els.chips.append(chip(audible ? "Signal" : "No signal", audible ? "on" : "off"));
  els.pipeline.textContent = pipeline;
}

/* ------------------------------------------------------------------- wire */

els.start.addEventListener("click", () => send({ type: "mic", action: "start" }));
els.stop.addEventListener("click", () => send({ type: "mic", action: "stop" }));

function renderWall(state) {
  const shown = state.lanes.map((l) => NAMES[l] || l).join(", ");
  if (state.rotate) els.wall.textContent = `rotating ${state.rotate}s — ${shown}`;
  else if (state.focus) els.wall.textContent = `${NAMES[state.focus]} alone`;
  else els.wall.textContent = shown;
}

function seconds(total) {
  const s = Math.round(total);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`;
}

async function loadServerFacts() {
  try {
    const t = token();
    const r = await fetch(`/api/status${t ? `?t=${encodeURIComponent(t)}` : ""}`);
    if (!r.ok) return;                     // harness serves no API; leave the dashes
    const s = await r.json();
    const set = (id, value) => { document.getElementById(id).textContent = value; };
    set("d-whisper", s.whisper);
    set("d-translator", `${s.translator} · ${s.translatorModel}`);
    set("d-langs", s.targetLangs.map((l) => NAMES[l] || l).join(", "));
    set("d-rate", `${s.sampleRate} Hz`);
    set("d-silence", `rms < ${s.silenceThreshold}`);
    set("d-buffer", `${s.maxBufferS}s`);
    set("d-history", `${s.historySize} sentences`);
    set("d-uptime", seconds(s.uptimeS));
    els.urls.textContent = "";
    for (const url of s.viewUrls || []) {
      const li = document.createElement("li");
      li.textContent = url;
      els.urls.append(li);
    }
  } catch (err) {
    /* The console is still useful without the static facts. */
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const t = token();
  socket = new WebSocket(
    `${proto}//${location.host}/ws/captions${t ? `?t=${encodeURIComponent(t)}` : ""}`);

  socket.onopen = () => {
    els.dot.classList.add("connected");
    els.statusText.textContent = "live";
    loadServerFacts();
  };

  socket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "mic") {
      const { type, ...state } = msg;
      mic = state;
      renderMic();
    } else if (msg.type === "presence") {
      els.screens.textContent = String(msg.screens);
    } else if (msg.type === "status") {
      pipeline = msg.state;
      renderChips();
    } else if (msg.type === "display") {
      const { type, ...state } = msg;
      renderWall(state);
    }
  };

  socket.onclose = () => {
    els.dot.classList.remove("connected");
    els.statusText.textContent = "reconnecting…";
    setTimeout(connect, 2000);
  };
}

renderMic();
connect();
