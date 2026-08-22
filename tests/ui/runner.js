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

// Chrome (headless and headed, confirmed against the real binary this suite
// runs under) silently refuses to execute a <script type="module" src=...>
// that arrives through document.write() — classic <script src> tags run fine
// through the same call. Pull module-script tags out of the markup before
// writing it, then re-insert them as real nodes afterwards; a dynamically
// created script only stays in document order and blocks readyState
// "complete" (matching how a parser-inserted <script> behaves) if `.async`
// is explicitly set to false before it is attached.
const MODULE_SCRIPT_RE = /<script\s+type=["']module["']\s+src=["']([^"']+)["']\s*><\/script>/gi;

export async function loadPage(path, { width = 390, height = 720 } = {}) {
  const html = await fetch(path).then((r) => r.text());
  const moduleSrcs = [];
  const withoutModules = html.replace(MODULE_SCRIPT_RE, (_, src) => {
    moduleSrcs.push(src);
    return "";
  });

  const frame = document.createElement("iframe");
  frame.style.cssText = `width:${width}px;height:${height}px;border:0;display:block;`;
  document.body.appendChild(frame);

  const win = frame.contentWindow;
  installStubs(win);                    // must precede the page's own scripts

  const doc = frame.contentDocument;
  doc.open();
  doc.write(withoutModules);             // written <script src> tags do execute
  doc.close();

  await new Promise((resolve) => {
    if (doc.readyState === "complete") return resolve();
    const timer = setInterval(() => {
      if (doc.readyState === "complete") { clearInterval(timer); resolve(); }
    }, 20);
  });

  for (const src of moduleSrcs) {
    await new Promise((resolve, reject) => {
      const script = doc.createElement("script");
      script.type = "module";
      script.async = false;             // see comment above: keeps it blocking + ordered
      script.onload = () => resolve();
      script.onerror = (e) => reject(new Error(`module script failed to load: ${src}`));
      doc.body.appendChild(script);
      script.src = src;
    });
  }

  return { win, doc, frame };
}

export function finish(promise) {
  const write = () =>
    (document.getElementById("__results").textContent = JSON.stringify(results, null, 1));

  // A suite that HANGS — an await that never settles — otherwise leaves the
  // results block empty, and the pytest side reports an opaque JSON parse
  // error naming no check at all. Write whatever ran, so the last check in the
  // list points at the line that hung. The budget is virtual time, so this
  // costs no wall clock; it must stay under --virtual-time-budget.
  const watchdog = setTimeout(() => {
    check("suite finished without hanging", false,
          `timed out after ${results.length} checks`);
    write();
  }, 5000);

  return promise
    .catch((err) => check("suite ran without throwing", false, err && (err.stack || err.message)))
    .finally(() => {
      clearTimeout(watchdog);
      write();
    });
}
