/* Light or dark for the projector wall.
 *
 * Light is the default: most services are in the morning, in a bright hall.
 * The operator toggles from the wall itself and the choice persists, so the
 * next service opens the way the last one ended.
 *
 * `?theme=` is a per-URL override for kiosk setups and is deliberately NOT
 * persisted. A projector permanently pinned to `?theme=dark` must not change
 * what the operator's own browser shows on the plain /display URL.
 *
 * Every entry point takes its window explicitly. That is what lets the whole
 * decision be tested against a plain object, with no page and no DOM — see
 * tests/ui/theme_tests.js.
 */
const THEMES = ["light", "dark"];
const STORAGE_KEY = "theme";
const DEFAULT = "light";

/* Pure: the two inputs that can decide the theme, in priority order.
 *
 * An unrecognised value falls through to the next source rather than being
 * honoured. `?theme=purple` must not reach the DOM as a data-theme attribute
 * that matches no CSS rule and leaves the wall half-styled. */
export function resolveTheme(queryTheme, storedTheme) {
  if (THEMES.includes(queryTheme)) return queryTheme;
  if (THEMES.includes(storedTheme)) return storedTheme;
  return DEFAULT;
}

export function applyTheme(theme, root) {
  root.dataset.theme = theme;
}

// A kiosk browser can run with storage blocked, and a remembered theme is not
// worth a wall that fails to boot. Both directions swallow the throw.
function readStored(win) {
  try {
    return win.localStorage.getItem(STORAGE_KEY);
  } catch (e) {
    return null;
  }
}

function writeStored(win, theme) {
  try {
    win.localStorage.setItem(STORAGE_KEY, theme);
  } catch (e) {
    /* see readStored */
  }
}

/* Resolve and apply. Returns the theme actually applied. */
export function initTheme(win) {
  const query = new URLSearchParams(win.location.search).get(STORAGE_KEY);
  const theme = resolveTheme(query, readStored(win));
  applyTheme(theme, win.document.documentElement);
  return theme;
}

/* Flip, apply and persist. Returns the new theme.
 *
 * Reads the current theme off the root element rather than from storage, so a
 * kiosk running under `?theme=dark` toggles to light from what is on screen
 * rather than from a stored value nobody can see. */
export function toggleTheme(win) {
  const root = win.document.documentElement;
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next, root);
  writeStored(win, next);
  return next;
}
