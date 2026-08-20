/* Shared plumbing for the browser suites.
 *
 * Each suite loads the REAL page into an iframe sized like the target device.
 * The iframe matters: `--window-size` does not drive the DOM viewport in
 * headless dump-dom mode (it reports ~500px regardless), so asserting layout
 * against the top-level window would silently miss phone-width regressions.
 * An iframe gives a viewport we control exactly, and the page's own body-level
 * CSS applies inside it unchanged.
 */

export const results = [];
export const check = (name, pass, detail = "") =>
  results.push({ name, pass: !!pass, detail: String(detail) });

function installStubs(win) {
  win.__sockets = [];
  win.WebSocket = class FakeWS {
    constructor(url) {
      this.url = url;
      this.readyState = 1;
      win.__sockets.push(this);
    }
    send() {}
    close() {}
    deliver(msg) { this.onmessage({ data: JSON.stringify(msg) }); }
  };
  win.__wakeLockCalls = [];
  Object.defineProperty(win.navigator, "wakeLock", {
    configurable: true,
    value: {
      request(type) {
        win.__wakeLockCalls.push(type);
        return Promise.resolve({ release() {}, addEventListener() {} });
      },
    },
  });
  try { win.localStorage.clear(); } catch (e) { /* opaque origin */ }
}

export async function loadPage(path, { width = 390, height = 720 } = {}) {
  const html = await fetch(path).then((r) => r.text());
  const frame = document.createElement("iframe");
  frame.style.cssText = `width:${width}px;height:${height}px;border:0;display:block;`;
  document.body.appendChild(frame);

  const win = frame.contentWindow;
  installStubs(win);                    // must precede the page's own scripts

  const doc = frame.contentDocument;
  doc.open();
  doc.write(html);                      // written <script src> tags do execute
  doc.close();

  await new Promise((resolve) => {
    if (doc.readyState === "complete") return resolve();
    const timer = setInterval(() => {
      if (doc.readyState === "complete") { clearInterval(timer); resolve(); }
    }, 20);
  });
  return { win, doc, frame };
}

export function finish(promise) {
  return promise
    .catch((err) => check("suite ran without throwing", false, err && (err.stack || err.message)))
    .finally(() => {
      document.getElementById("__results").textContent = JSON.stringify(results, null, 1);
    });
}
