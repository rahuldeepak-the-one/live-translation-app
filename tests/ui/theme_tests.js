/* Theme resolution for the projector wall.
 *
 * No page is loaded here: theme.js takes its window explicitly so the whole
 * decision — query param, stored preference, default — is testable against a
 * plain object. The DOM wiring is covered end-to-end by display_tests.js.
 */
import { check, finish } from "/tests/ui/runner.js";
import { resolveTheme, initTheme, toggleTheme } from "/static/theme.js";

function fakeWin({ search = "", stored = null, blocked = false } = {}) {
  const store = new Map();
  if (stored !== null) store.set("theme", stored);
  return {
    location: { search },
    localStorage: {
      getItem(k) {
        if (blocked) throw new Error("storage disabled");
        return store.has(k) ? store.get(k) : null;
      },
      setItem(k, v) {
        if (blocked) throw new Error("storage disabled");
        store.set(k, v);
      },
    },
    document: { documentElement: { dataset: {} } },
    stored: () => (store.has("theme") ? store.get("theme") : null),
  };
}

async function run() {
  // --- pure resolution, in priority order --------------------------------
  check("query param wins", resolveTheme("dark", null) === "dark");
  check("query param beats a stored preference",
        resolveTheme("light", "dark") === "light");
  check("stored preference is used when there is no query param",
        resolveTheme(null, "dark") === "dark");
  check("light is the default", resolveTheme(null, null) === "light");

  // Garbage must not reach the DOM as a data-theme value; it falls through to
  // the next source rather than being honoured or throwing.
  check("a garbage query param falls through to stored",
        resolveTheme("purple", "dark") === "dark");
  check("a garbage query param with nothing stored gives light",
        resolveTheme("purple", null) === "light");
  check("a garbage stored value gives light",
        resolveTheme(null, "purple") === "light");

  // --- initTheme applies without persisting ------------------------------
  const kiosk = fakeWin({ search: "?theme=dark" });
  check("initTheme returns the resolved theme", initTheme(kiosk) === "dark");
  check("initTheme stamps the root element",
        kiosk.document.documentElement.dataset.theme === "dark",
        kiosk.document.documentElement.dataset.theme);
  // A kiosk pinned to ?theme=dark must not change what the operator's own
  // browser shows on the plain /display URL.
  check("a query param is NOT persisted", kiosk.stored() === null, kiosk.stored());

  const remembered = fakeWin({ stored: "dark" });
  check("initTheme honours a stored preference", initTheme(remembered) === "dark");

  const fresh = fakeWin();
  check("initTheme defaults to light", initTheme(fresh) === "light");

  // --- toggleTheme flips, applies and persists ---------------------------
  const win = fakeWin();
  initTheme(win);
  check("toggle goes light -> dark", toggleTheme(win) === "dark");
  check("toggle stamps the root element",
        win.document.documentElement.dataset.theme === "dark",
        win.document.documentElement.dataset.theme);
  check("toggle persists the choice", win.stored() === "dark", win.stored());
  check("toggle goes dark -> light", toggleTheme(win) === "light");
  check("toggle persists the second choice", win.stored() === "light", win.stored());

  // --- storage may be unavailable ----------------------------------------
  // A projector kiosk can run with storage blocked. A themed wall is not worth
  // a page that fails to boot, so both paths swallow the throw.
  const blocked = fakeWin({ blocked: true });
  let threw = null;
  try {
    check("initTheme survives blocked storage", initTheme(blocked) === "light");
    toggleTheme(blocked);
    check("toggle survives blocked storage",
          blocked.document.documentElement.dataset.theme === "dark",
          blocked.document.documentElement.dataset.theme);
  } catch (e) {
    threw = e;
  }
  check("blocked storage never throws", threw === null, threw && threw.message);
}

finish(run());
