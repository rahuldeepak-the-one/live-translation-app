/* The projector wall: one card per enabled target language, English on the
 * header line, and a trust-marked split between confirmed and in-progress
 * text. Card-internal behaviour is covered by card_tests.js; this suite is
 * about the wall — which cards exist, what the operator's controls do to it,
 * and what must never appear on it.
 */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const sentence = (id, en, final = true) => ({ type: "sentence", id, en, final });
const translation = (id) => ({
  type: "translation", id,
  ml: `മലയാളം വാക്യം ${id}.`,
  te: `తెలుగు వాక్యం ${id}.`,
  hi: `हिन्दी वाक्य ${id}।`,
});
const display = (lanes, focus = null, rotate = 0) =>
  ({ type: "display", lanes, focus, rotate });

async function run() {
  const { win, doc } = await loadPage("/display", { width: 1280, height: 720 });
  const ws = win.__sockets[0];
  check("display.js opened a caption socket", !!ws, ws && ws.url);
  if (!ws) return;

  const cards = () => [...doc.querySelectorAll(".card")];
  const langs = () => cards().map((c) => c.dataset.lang).join(",");
  const grid = () => doc.getElementById("grid");
  const cardFor = (lang) => doc.querySelector(`.card[data-lang="${lang}"]`);

  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });
  ws.deliver(display(["en", "ml", "te", "hi"]));

  // --- cards are the lanes minus English ---------------------------------
  // English has its own header line; printing the same sentence twice would
  // spend wall space a reader at the back needs.
  check("english does not get a card of its own", langs() === "ml,te,hi", langs());
  check("the grid is told how many cards it holds",
        grid().dataset.cards === "3", grid().dataset.cards);
  // Before the first caption there is nothing to put on the line, and a bare
  // "English" label against blank space reads as a rendering fault.
  check("the english header line stays out of the way until there is a caption",
        doc.getElementById("en-bar").hidden);

  for (let i = 1; i <= 6; i++) {
    ws.deliver(sentence(i, `Projector caption number ${i} is fairly long.`));
    ws.deliver(translation(i));
  }

  check("the english header line appears once captions start",
        !doc.getElementById("en-bar").hidden);
  check("the malayalam card shows malayalam",
        cardFor("ml").textContent.includes("മലയാളം വാക്യം"),
        cardFor("ml").textContent.slice(0, 40));
  check("the telugu card shows telugu",
        cardFor("te").textContent.includes("తెలుగు వాక్యం"),
        cardFor("te").textContent.slice(0, 40));
  check("the english header line carries the newest english",
        doc.getElementById("en-text").textContent.includes("number 6"),
        doc.getElementById("en-text").textContent);

  // The header line is one line, always — a wrapping English paragraph up
  // there competes with the captions it exists to caption.
  {
    const enText = doc.getElementById("en-text");
    const style = win.getComputedStyle(enText);
    check("the english header line never wraps",
          style.whiteSpace === "nowrap" && style.textOverflow === "ellipsis",
          `${style.whiteSpace}/${style.textOverflow}`);
  }

  // --- nothing technical may reach the wall ------------------------------
  // The QR carries the address. An IP or URL printed as text puts a technical
  // string in front of the whole congregation.
  {
    const text = doc.body.textContent;
    check("no IP address appears on the wall",
          !/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/.test(text), text.slice(0, 200));
    check("no URL appears on the wall",
          !/https?:\/\//.test(text) && !/\/view\b/.test(text), text.slice(0, 200));
    check("the QR is still there for phones", !!doc.querySelector("img.qr"));
  }

  // --- the trust split ----------------------------------------------------
  ws.deliver(sentence(99, "Just spoken, not yet translated.", false));
  {
    const te = cardFor("te");
    const pending = te.querySelector(".card-pending").textContent;
    check("a held sentence shows grey english on the pending line",
          pending.startsWith("Just spoken, not yet translated"), pending);
    check("the pending line is marked unfinished",
          pending.endsWith("…"), pending);
    check("the held sentence is not in the confirmed block",
          !te.querySelector(".card-confirmed").textContent.includes("Just spoken"),
          te.querySelector(".card-confirmed").textContent.slice(-60));
  }
  ws.deliver(sentence(99, "Just spoken, now finished.", true));
  ws.deliver(translation(99));
  {
    const te = cardFor("te");
    check("confirming moves the sentence into the confirmed block",
          te.querySelector(".card-confirmed").textContent.includes("వాక్యం 99"),
          te.querySelector(".card-confirmed").textContent.slice(-60));
    check("the pending line clears once the sentence is confirmed",
          te.querySelector(".card-pending").textContent === "",
          te.querySelector(".card-pending").textContent);
  }

  // --- theme --------------------------------------------------------------
  check("the wall opens light — most services are in a bright hall",
        doc.documentElement.dataset.theme === "light",
        doc.documentElement.dataset.theme);
  {
    const toggle = doc.getElementById("theme-toggle");
    check("the theme toggle is reachable by assistive tech",
          toggle && toggle.hasAttribute("aria-label"),
          toggle && toggle.getAttribute("aria-label"));
    toggle.click();
    check("clicking the toggle switches the wall to dark",
          doc.documentElement.dataset.theme === "dark",
          doc.documentElement.dataset.theme);
    check("the choice is remembered for the next service",
          win.localStorage.getItem("theme") === "dark",
          win.localStorage.getItem("theme"));
    check("the toggle relabels itself for the new state",
          /light/i.test(toggle.getAttribute("aria-label")),
          toggle.getAttribute("aria-label"));
    toggle.click();
    check("clicking again returns to light",
          doc.documentElement.dataset.theme === "light",
          doc.documentElement.dataset.theme);
  }

  // --- connection is reported honestly ------------------------------------
  check("a live socket shows LIVE",
        doc.getElementById("live").dataset.state === "live"
          && /LIVE/i.test(doc.getElementById("live-label").textContent),
        doc.getElementById("live-label").textContent);

  // --- the lane set drives the grid ---------------------------------------
  ws.deliver(display(["ml", "te", "hi"]));
  check("turning english off leaves the three cards", langs() === "ml,te,hi", langs());
  check("turning english off hides the header line",
        doc.getElementById("en-bar").hidden);

  ws.deliver(display(["en", "ml", "te"]));
  check("two target languages give two cards", langs() === "ml,te", langs());
  check("the grid follows the count", grid().dataset.cards === "2", grid().dataset.cards);

  // English alone must not blank the wall: it becomes the card, and the header
  // line steps aside so the same sentence is not printed twice.
  ws.deliver(display(["en"]));
  check("english alone becomes the card rather than an empty wall",
        langs() === "en", langs());
  check("the header line steps aside when english is the card",
        doc.getElementById("en-bar").hidden);

  ws.deliver(display(["en", "ml", "te", "hi"]));

  // --- reconnect -----------------------------------------------------------
  ws.onclose();
  check("a dropped socket says so rather than showing a green LIVE",
        doc.getElementById("live").dataset.state === "down",
        doc.getElementById("live").dataset.state);
  await new Promise((r) => win.setTimeout(r, 2100));
  const ws2 = win.__sockets[win.__sockets.length - 1];
  check("the wall reconnected", ws2 !== ws, `${win.__sockets.length} sockets`);

  // `ws2 !== ws` only proves a new socket object exists. The replayed backlog
  // must be DISTINCT from the pre-close one: it used to be byte-identical,
  // which made the assertion below unfalsifiable — a wall that ignored the
  // replayed `history` and kept rendering its pre-close cache still had cards
  // full of spans and still passed. Assert on content only the NEW history
  // could have produced.
  const reconnectSentences = Array.from({ length: 6 }, (_, i) => {
    const id = 101 + i;
    return {
      id,
      en: `Reconnect caption ${id}.`,
      translations: {
        ml: `പുനഃസംയോജനം ${id}.`,
        te: `మళ్లీ కనెక్ట్ ${id}.`,
        hi: `पुनः जुड़ाव ${id}।`,
      },
      final: true,
    };
  });
  ws2.onopen();
  ws2.deliver({ type: "history", sentences: reconnectSentences });
  ws2.deliver(display(["ml"]));
  check("reconnect restores the narrowed lane set on the new socket",
        langs() === "ml", langs());
  const reconnectText = cardFor("ml") ? cardFor("ml").textContent : "";
  check("the restored card renders the backlog replayed on the NEW socket",
        reconnectText.includes("പുനഃസംയോജനം 101")
        && reconnectText.includes("പുനഃസംയോജനം 106"),
        reconnectText.slice(0, 80));
  // history REPLACES state; a wall that appended would still be showing the
  // pre-close captions underneath, so a reconnect mid-service would print the
  // passage twice. Nothing else in this suite covers that.
  check("reconnect history replaces the pre-close cache rather than appending",
        !reconnectText.includes("മലയാളം വാക്യം"),
        reconnectText.slice(0, 80));

  // --- focus ---------------------------------------------------------------
  ws2.deliver(display(["en", "ml", "te", "hi"], "ml", 0));
  check("focus renders exactly one card", langs() === "ml", langs());
  check("the focused card fills the wall", grid().dataset.cards === "1", grid().dataset.cards);
  check("the focused card still holds the backlog",
        cardFor("ml").querySelectorAll(".card-confirmed span").length > 0,
        cardFor("ml").querySelectorAll(".card-confirmed span").length);

  ws2.deliver(display(["en", "ml", "te", "hi"], null, 0));
  check("leaving focus restores every card", langs() === "ml,te,hi", langs());
  // The reconnect above replaced the backlog, so this card now holds the
  // REPLAYED captions. Asserting the pre-close text would pass only if the
  // wall had failed to apply that history.
  check("a restored card is not blank",
        cardFor("te").textContent.includes("మళ్లీ కనెక్ట్"),
        cardFor("te").textContent.slice(0, 40));

  // --- rotation -------------------------------------------------------------
  const { nextLane } = win;
  check("rotation advances through the lanes",
        nextLane(["ml", "te", "hi"], "ml") === "te");
  check("rotation wraps", nextLane(["ml", "te", "hi"], "hi") === "ml");
  check("rotation starts at the first lane when nothing is current",
        nextLane(["ml", "te", "hi"], null) === "ml");
  check("rotation survives the current lane being disabled",
        nextLane(["ml", "hi"], "te") === "ml");

  // Every `display` message must clear the previous interval before setting a
  // new one. Without that each message stacks another timer and the wall
  // flickers at compounding speed — invisible in a short test, ruinous over a
  // 90-minute service.
  let setCalls = 0, clearCalls = 0;
  const realSet = win.setInterval, realClear = win.clearInterval;
  win.setInterval = (...a) => { setCalls++; return realSet.apply(win, a); };
  win.clearInterval = (...a) => { clearCalls++; return realClear.apply(win, a); };

  ws2.deliver(display(["ml", "te", "hi"], null, 20));
  ws2.deliver(display(["ml", "te", "hi"], null, 30));
  ws2.deliver(display(["ml", "te", "hi"], null, 20));
  check("every rotating display message clears before it sets",
        clearCalls === 3 && setCalls === 3, `clear=${clearCalls} set=${setCalls}`);

  ws2.deliver(display(["ml", "te", "hi"], "ml", 0));
  check("pinning clears the rotation timer and starts no new one",
        clearCalls === 4 && setCalls === 3, `clear=${clearCalls} set=${setCalls}`);

  win.setInterval = realSet;
  win.clearInterval = realClear;

  // A tick must actually repaint the wall. nextLane being right proves nothing
  // about the interval callback being wired to the DOM.
  let tick = null;
  const savedSet = win.setInterval;
  win.setInterval = (fn, ms) => { tick = fn; return savedSet.apply(win, [fn, ms]); };

  ws2.deliver(display(["ml", "te", "hi"], null, 20));
  check("rotating shows exactly one card", cards().length === 1, cards().length);
  check("a rotating display message installed a tick callback", typeof tick === "function");

  const seen = [];
  for (let i = 0; i < 4; i++) {
    seen.push(cards()[0].dataset.lang);
    tick();
  }
  check("each rotation tick advances the wall to a different language",
        seen[0] !== seen[1] && seen[1] !== seen[2] && seen[2] !== seen[3],
        seen.join(" -> "));
  check("rotation only ever shows enabled lanes",
        seen.every((l) => ["ml", "te", "hi"].includes(l)), seen.join(","));
  check("rotation cycles rather than running off the end",
        seen[3] === seen[0], seen.join(" -> "));

  win.setInterval = savedSet;

  // --- an utterance published during registration must not be lost ---------
  // hub.py adds a client to _clients BEFORE awaiting the history send, so a
  // `sentence` can legitimately arrive on this connection before its own
  // `history` snapshot. Without buffering, the empty history that follows
  // wipes the cache and the utterance vanishes from the wall while /view
  // still shows it.
  {
    const { win: freshWin, doc: freshDoc } = await loadPage("/display", { width: 1280, height: 720 });
    const freshWs = freshWin.__sockets[0];
    check("fresh display.js connection opened a caption socket", !!freshWs);
    if (freshWs) {
      freshWs.onopen();
      freshWs.deliver(sentence(99, "Mid-join utterance.", true));
      freshWs.deliver({ type: "history", sentences: [] });
      freshWs.deliver(display(["en", "ml", "te", "hi"]));
      const te = freshDoc.querySelector('.card[data-lang="te"]');
      check("an utterance published during registration survives history replay",
            te && te.textContent.includes("Mid-join utterance."),
            te && te.textContent.trim().slice(-60));
    }
  }

  // A fresh /display load mid-service: the page's own module-load render has
  // already built the default cards before any socket message arrives, so when
  // `history` lands with a backlog and the lane SET is unchanged, the rebuild
  // early-returns and the per-card repaint after it is what loads the backlog.
  {
    const { win: backlogWin, doc: backlogDoc } = await loadPage("/display", { width: 1280, height: 720 });
    const backlogWs = backlogWin.__sockets[0];
    check("fresh display.js connection (backlog case) opened a caption socket", !!backlogWs);
    if (backlogWs) {
      backlogWs.onopen();
      backlogWs.deliver({
        type: "history",
        sentences: [{
          id: 1, en: "Pre-existing backlog sentence.",
          translations: { ml: "പഴയത്.", te: "పాతది.", hi: "पुराना।" }, final: true,
        }],
      });
      backlogWs.deliver(display(["en", "ml", "te", "hi"]));
      const te = backlogDoc.querySelector('.card[data-lang="te"]');
      check("a fresh connection's non-empty history renders even when the card set is unchanged",
            te && te.textContent.includes("పాతది."),
            te && te.textContent.trim().slice(-60));
    }
  }

  // --- legibility of the live line -----------------------------------------
  // The pending line IS the live translation — the one a reader at the back is
  // following. It is deliberately subordinate to the confirmed text because it
  // may still change, but subordinate is not the same as invisible: the light
  // theme shipped at 1.98:1, below even the AA-large floor, on the default
  // theme, on a projector, in a hall chosen bright enough to need light mode.
  // Projector text is large, so 3:1 (AA-large) is the right bar.
  {
    const rel = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const lum = ([r, g, b]) => 0.2126 * rel(r) + 0.7152 * rel(g) + 0.0722 * rel(b);
    const parse = (v) => v.match(/[\d.]+/g).slice(0, 3).map(Number);
    const ratio = (fg, bg) => {
      const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
      return (hi + 0.05) / (lo + 0.05);
    };
    const root = doc.documentElement;
    // A probe element, not getPropertyValue: a custom property returns its
    // literal token text ("#fbfcfd"), whereas `color` on a real element is
    // resolved to rgb() — which is also what actually renders.
    const probe = doc.createElement("span");
    probe.style.display = "none";
    doc.body.appendChild(probe);
    const resolved = (token) => {
      probe.style.color = `var(${token})`;
      return parse(doc.defaultView.getComputedStyle(probe).color);
    };
    const measure = () => {
      const bg = resolved("--card-bg");
      return {
        pending: ratio(resolved("--text-pending"), bg),
        confirmed: ratio(resolved("--text-confirmed"), bg),
      };
    };
    for (const theme of ["light", "dark"]) {
      root.dataset.theme = theme;
      const { pending, confirmed } = measure();
      check(`${theme}: the live (pending) line clears the AA-large floor`,
            pending >= 3.0, `${pending.toFixed(2)}:1`);
      check(`${theme}: confirmed text is comfortably readable`,
            confirmed >= 4.5, `${confirmed.toFixed(2)}:1`);
      // Still subordinate — the trust marking is meaningless if they match.
      check(`${theme}: pending stays visibly subordinate to confirmed`,
            pending < confirmed / 2,
            `pending ${pending.toFixed(2)} vs confirmed ${confirmed.toFixed(2)}`);
    }
    probe.remove();
    root.dataset.theme = "light";
  }
}

finish(run());
