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
    // Settle the 120ms colour fade before reading it. Under
    // --virtual-time-budget the headless renderer can decline to give a CSS
    // transition its first frame at all (startTime stays null, playState
    // "running"), and getComputedStyle then reports the FROM colour forever —
    // so a plain wait is a race that the page's own weight decides. This check
    // is about the colour the reader ends up looking at, not the ramp there.
    el.getAnimations().forEach((a) => a.finish());
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

  // --- unknown id guard ----------------------------------------------------
  // A translation can legitimately arrive for an id this Flow has never seen
  // (e.g. /display builds a Flow fresh per lane). It must be a no-op, not a
  // phantom row painting the literal string "undefined" into the lane.
  // No language keys at all, so whichever language is selected finds nothing
  // to show and would fall back to the (missing) English — reproducing the
  // defect regardless of the current picker state.
  ws.deliver({ type: "translation", id: 999 });
  check("translation for an unseen id does not create a span", !spanFor(999));
  check("translation for an unseen id does not paint literal 'undefined'",
        !$("#transcript").textContent.includes("undefined"),
        $("#transcript").textContent.slice(-80));

  // --- provisional / final ------------------------------------------------
  // `final` drives `.provisional` independently of `.awaiting` — see the CSS
  // comment in view.html distinguishing the two.
  ws.deliver({ type: "sentence", id: 400, en: "Speaker is still mid-sentence.", final: false });
  check("final:false marks the span provisional",
        spanFor(400) && spanFor(400).classList.contains("provisional"));

  ws.deliver({ type: "sentence", id: 400, en: "Speaker is still mid-sentence.", final: true });
  check("final:true clears the provisional class",
        spanFor(400) && !spanFor(400).classList.contains("provisional"));

  ws.deliver({ type: "sentence", id: 401, en: "No final flag was sent at all." });
  check("a sentence with no final flag is not marked provisional",
        spanFor(401) && !spanFor(401).classList.contains("provisional"));

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

  // --- recap mode ---------------------------------------------------------
  // The language picker was just switched to Telugu, so the recap must render
  // Telugu too — it reuses the live lane's language resolution, not its own.
  const modeLive = doc.getElementById("mode-live");
  const modeRecap = doc.getElementById("mode-recap");
  const recapPanel = doc.getElementById("recap-panel");
  const refresh = doc.getElementById("recap-refresh");
  check("mode switch exists", !!modeLive && !!modeRecap);

  // Not offsetTop equality — the three zones are different heights and sit
  // centred, so equal tops was never the question. Sharing a horizontal band is.
  const headerRects = ["status", "mode-switch", "size-controls"]
    .map((id) => doc.getElementById(id).getBoundingClientRect());
  check("header keeps state, mode and size on one row",
        Math.max(...headerRects.map((r) => r.top)) < Math.min(...headerRects.map((r) => r.bottom)),
        headerRects.map((r) => `${r.top.toFixed(0)}-${r.bottom.toFixed(0)}`).join(" "));
  check("header does not overflow the phone width",
        doc.documentElement.scrollWidth <= win.innerWidth,
        `${doc.documentElement.scrollWidth} > ${win.innerWidth}`);

  // The 40 boilerplate sentences above are near-identical; a recap of them is
  // noise and is SUPPOSED to be empty. Deliver a corpus with actual themes.
  const CORPUS = [
    [501, "The harvest is plentiful but the labourers are few in this field"],
    [502, "A farmer went out to scatter seed across the whole field"],
    [503, "Some seed fell on rocky ground where the soil was shallow"],
    [504, "Other seed fell among thorns and the thorns choked the young plants"],
    [505, "But seed that fell on good soil produced a harvest beyond measure"],
    [506, "Amen"],
    [507, "The soil is the heart and the seed is the word that is sown"],
    [508, "A shallow heart receives the word gladly and loses it by Tuesday"],
    [509, "Thorns are the worries of this life crowding out the word"],
    [510, "Good soil is a heart that keeps the word and lets it work slowly"],
    [511, "Let us sing"],
    [512, "The harvest is plentiful but the labourers are few in this field"],
  ];
  for (const [id, en] of CORPUS) {
    ws.deliver(sentence(id, en));
    ws.deliver(translation(id));
  }

  modeRecap.click();
  check("recap hides the live transcript", !visible(captions));
  check("recap panel is shown", visible(recapPanel));
  check("mode switch marks recap selected",
        modeRecap.getAttribute("aria-selected") === "true"
        && modeLive.getAttribute("aria-selected") === "false");
  check("mode thumb slides rather than snapping",
        win.getComputedStyle(doc.getElementById("mode-thumb")).transform !== "none",
        win.getComputedStyle(doc.getElementById("mode-thumb")).transform);

  const cards = $$("#recap-list .recap-card");
  check("recap produced cards", cards.length >= 3, `cards=${cards.length}`);
  check("recap is a selection, not the whole transcript",
        cards.length < CORPUS.length, `cards=${cards.length} corpus=${CORPUS.length}`);
  check("recap empty-state is not showing alongside cards",
        !visible(doc.getElementById("recap-empty")));
  check("recap says what it covered", $("#recap-meta").textContent.includes("key moment"),
        $("#recap-meta").textContent);

  const ids = cards.map((c) => Number(c.dataset.sid));
  check("recap cards run in spoken order",
        ids.every((v, i, a) => i === 0 || a[i - 1] < v), ids.join(","));
  check("one-word filler is not a key moment", !ids.includes(506) && !ids.includes(511),
        ids.join(","));
  check("a restated sentence is not picked twice",
        !(ids.includes(501) && ids.includes(512)), ids.join(","));
  check("recap renders the chosen language",
        $(".recap-text").textContent.includes("తెలుగు"), $(".recap-text").textContent);
  check("recap cards are nested shell + core (not flat on the background)",
        !!$(".recap-card > .recap-shell > .recap-core"));
  check("recap reads from the top, not pinned to the bottom",
        recapPanel.scrollTop === 0, recapPanel.scrollTop);
  check("jump-to-live stays out of recap mode", !visible(pill));

  await settle();
  check("recap cards animate in rather than appearing statically",
        cards[0].classList.contains("in"), cards[0].className);

  // --- new sentences while reading the recap -------------------------------
  check("no refresh offered when nothing is new", !visible(refresh));
  ws.deliver(sentence(900, "A new thought arrives while you are reading back."));
  check("refresh pill offers the new sentences", visible(refresh), refresh.textContent.trim());
  check("recap did not reshuffle under the reader",
        $$("#recap-list .recap-card").map((c) => c.dataset.sid).join(",") === ids.join(","));

  // --- tap through to the live transcript ----------------------------------
  const firstId = ids[0];
  $$("#recap-list .recap-shell")[0].click();
  check("tapping a recap card returns to live",
        visible(captions) && !visible(recapPanel));
  const landed = spanFor(firstId);
  check("tapped sentence is marked where it landed",
        landed && landed.classList.contains("flash"), landed && landed.className);

  // A recap built from a short transcript is worse than none at all.
  ws.deliver({ type: "history", sentences: [] });
  for (let i = 1; i <= 3; i++) {
    ws.deliver(sentence(700 + i, `Only a few words were said here number ${i}`));
  }
  modeRecap.click();
  check("a barely-started service shows the empty state, not a fake recap",
        visible(doc.getElementById("recap-empty"))
        && $$("#recap-list .recap-card").length === 0);

  check("screen wake lock requested", win.__wakeLockCalls.includes("screen"),
        JSON.stringify(win.__wakeLockCalls));
}

finish(run());
