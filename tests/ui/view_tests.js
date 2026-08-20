/* Drives the REAL static/view.js at phone size and asserts on the DOM. */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const sentence = (id, en) => ({ type: "sentence", id, en });
const translation = (id) => ({
  type: "translation", id,
  ml: `മലയാളം വാക്യം ${id} ഇവിടെ ഉണ്ട്.`,
  te: `తెలుగు వాక్యం ${id} ఇక్కడ ఉంది.`,
  hi: `हिन्दी वाक्य ${id} यहाँ है।`,
});

async function run() {
  const { win, doc } = await loadPage("/view", { width: 390, height: 720 });
  const $ = (sel) => doc.querySelector(sel);
  const $$ = (sel) => [...doc.querySelectorAll(sel)];
  const spanFor = (id) => doc.querySelector(`[data-sid="${id}"]`);
  const captions = doc.getElementById("captions");
  const brightness = (el) => {
    const [r, g, b] = win.getComputedStyle(el).color.match(/\d+/g).map(Number);
    return (r + g + b) / 3;
  };
  const visible = (el) => el && !el.hidden && win.getComputedStyle(el).display !== "none";
  const scrollTo = (el, top) => { el.scrollTop = top; el.dispatchEvent(new win.Event("scroll")); };
  // #transcript animates colour changes, and getComputedStyle reports the
  // in-flight value — wait the transition out before reading colours.
  const settle = () => new Promise((r) => win.setTimeout(r, 300));

  const ws = win.__sockets[0];
  check("view.js opened a caption socket", !!ws, ws && ws.url);
  check("harness viewport is phone width", win.innerWidth === 390, win.innerWidth);
  if (!ws) return;
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });

  // --- layout ------------------------------------------------------------
  for (let i = 1; i <= 40; i++) {
    ws.deliver(sentence(i, `This is sentence number ${i} of the message.`));
    ws.deliver(translation(i));
  }
  check("sentences render as inline spans", $$("#captions span[data-sid]").length > 0,
        $$("#captions span[data-sid]").length);
  check("no per-sentence block rows", $$(".caption-row").length === 0, $$(".caption-row").length);
  check("keeps more than 10 sentences of scrollback", $$("[data-sid]").length > 10,
        $$("[data-sid]").length);

  const langButtons = $$("#picker button");
  check("language picker fits on one row", new Set(langButtons.map((b) => b.offsetTop)).size === 1,
        `tops=${langButtons.map((b) => b.offsetTop).join(",")} viewport=${win.innerWidth}`);

  // --- scrolling ---------------------------------------------------------
  const maxScroll = captions.scrollHeight - captions.clientHeight;
  check("scrollback is reachable", maxScroll > 0, `maxScroll=${maxScroll}`);

  scrollTo(captions, captions.scrollHeight);
  ws.deliver(sentence(41, "A brand new sentence arrives."));
  ws.deliver(translation(41));
  const gap = captions.scrollHeight - captions.clientHeight - captions.scrollTop;
  check("sticks to the bottom when already live", gap < 5, `gap=${gap}`);

  scrollTo(captions, 0);
  const before = captions.scrollTop;
  ws.deliver(sentence(42, "Another one while reading back."));
  ws.deliver(translation(42));
  check("does not yank you down when scrolled up", captions.scrollTop === before,
        `before=${before} after=${captions.scrollTop}`);

  const pill = doc.getElementById("jump-live");
  check("jump-to-live control exists", !!pill);
  check("jump-to-live shows when scrolled up", visible(pill));
  if (pill) {
    pill.click();
    const g2 = captions.scrollHeight - captions.clientHeight - captions.scrollTop;
    check("jump-to-live returns to the live edge", g2 < 5, `gap=${g2}`);
    check("jump-to-live hides at the live edge", !visible(pill));
  }

  // --- awaiting translation ---------------------------------------------
  ws.deliver(sentence(100, "This sentence is still being spoken"));
  const held = spanFor(100);
  check("unfinished sentence appears immediately", !!held);
  check("shows grey English while awaiting", held && held.textContent.includes("still being spoken"),
        held && held.textContent.trim());
  check("marked as awaiting translation", held && held.classList.contains("awaiting"));

  ws.deliver(sentence(100, "This sentence is still being spoken and now it ends."));
  check("held sentence grows in place (no duplicate)", $$('[data-sid="100"]').length === 1,
        $$('[data-sid="100"]').length);
  check("held sentence text updates", spanFor(100).textContent.includes("now it ends"));

  ws.deliver(translation(100));
  check("swaps to the translation", spanFor(100).textContent.includes("മലയാളം"),
        spanFor(100).textContent.trim());
  check("awaiting marker cleared after swap", !spanFor(100).classList.contains("awaiting"));

  // The awaiting state must be visible, not merely a class name: the newest
  // sentence is always the untranslated one, so a "highlight the newest" rule
  // can silently cancel the dimming.
  ws.deliver(sentence(300, "This newest sentence has no translation yet"));
  await settle();
  check("awaiting text is visibly dimmer than translated text",
        brightness(spanFor(300)) < brightness(spanFor(100)) - 20,
        `awaiting=${brightness(spanFor(300)).toFixed(0)} translated=${brightness(spanFor(100)).toFixed(0)}`);

  // --- controls ----------------------------------------------------------
  const bigger = doc.getElementById("text-bigger");
  check("text size control exists", !!bigger);
  if (bigger) {
    const sizeBefore = win.getComputedStyle(doc.documentElement).getPropertyValue("--caption-size");
    bigger.click();
    const sizeAfter = win.getComputedStyle(doc.documentElement).getPropertyValue("--caption-size");
    check("A+ increases caption size", sizeBefore !== sizeAfter, `${sizeBefore} -> ${sizeAfter}`);
  }

  const teButton = $('#picker button[data-lang="te"]');
  check("language picker present", !!teButton);
  if (teButton) {
    teButton.click();
    check("switching language re-renders existing text",
          spanFor(100).textContent.includes("తెలుగు"), spanFor(100).textContent.trim());
  }

  check("screen wake lock requested", win.__wakeLockCalls.includes("screen"),
        JSON.stringify(win.__wakeLockCalls));
}

finish(run());
