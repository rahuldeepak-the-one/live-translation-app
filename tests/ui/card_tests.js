/* One language's card on the projector wall.
 *
 * The trust contract is the whole point of the redesign, so most of this suite
 * is about which of the three states a sentence lands in and what the reader
 * sees for each.
 */
import { check, finish } from "/tests/ui/runner.js";
import {
  LanguageCard, createCard, classify, LISTENING, AWAITING_TRANSLATION,
} from "/static/card.js";

const raf = () => new Promise((r) => requestAnimationFrame(() => r()));

/* Sentences as the wall's cache holds them (display.js `remember()` shape). */
const s = (id, en, { final = true, translations = null } = {}) =>
  ({ id, en, final, translations });

/* The real stylesheet, once: the fit pass measures a box whose height comes
   entirely from display.css, so an unstyled card would report no overflow and
   every fit check would pass vacuously. */
let cssLoaded = null;
function loadCss() {
  // Fetched and inlined rather than <link rel=stylesheet>: under Chrome's
  // --virtual-time-budget a link element's onload never fires, so awaiting it
  // hangs the suite and the harness dumps an empty results block. An inserted
  // <style> applies synchronously. runner.js already relies on fetch working
  // here, so this costs no new assumption.
  if (!cssLoaded) {
    cssLoaded = fetch("/static/display.css")
      .then((r) => {
        if (!r.ok) throw new Error(`display.css: HTTP ${r.status}`);
        return r.text();
      })
      .then((css) => {
        const style = document.createElement("style");
        style.textContent = css;
        document.head.appendChild(style);
      });
  }
  return cssLoaded;
}

/* A grid host so the card stretches to an exact size, the way the wall's own
   grid stretches it. */
function mount(lang, label, { width = 900, height = 400 } = {}) {
  const host = document.createElement("div");
  host.style.cssText = `width:${width}px;height:${height}px;display:grid;`;
  document.body.appendChild(host);
  const section = createCard(document, lang, label);
  host.appendChild(section);
  return { card: new LanguageCard(section, lang), section, host };
}

const overflows = (section) => {
  const c = section.querySelector(".card-content");
  return c.scrollHeight > c.clientHeight + 1;
};
const scaleOf = (section) =>
  Number(section.style.getPropertyValue("--card-scale") || 1);
const LONG_TE = "యేసు ఈ గుణాన్ని హైలైట్ చేస్తాడు, ఎందుకంటే అది ఆధ్యాత్మిక జ్ఞానానికి చాలా అవసరం.";

const confirmedSpans = (section) =>
  [...section.querySelectorAll(".card-confirmed span")];
const confirmedText = (section) =>
  section.querySelector(".card-confirmed").textContent.trim();
const pendingText = (section) =>
  section.querySelector(".card-pending").textContent.trim();

async function run() {
  await loadCss();

  // --- classification ----------------------------------------------------
  check("english is confirmed once final",
        classify(s(1, "Hello."), "en") === "confirmed");
  check("english is pending while unfinalised",
        classify(s(1, "Hello", { final: false }), "en") === "pending");
  check("a final sentence with a translation is confirmed",
        classify(s(1, "Hello.", { translations: { te: "హలో" } }), "te") === "confirmed");
  check("an unfinalised sentence is pending even with a translation",
        classify(s(1, "Hello", { final: false, translations: { te: "హలో" } }), "te")
          === "pending");
  check("a final sentence whose translation has not arrived is pending",
        classify(s(1, "Hello."), "te") === "pending");
  check("a language missing from the translations is pending",
        classify(s(1, "Hello.", { translations: { te: "హలో" } }), "ml") === "pending");

  // translator.py returns {lang: ""} when it gives up on a language. That is a
  // FAILURE for this card and must not read as "not yet" — the two look
  // identical to flow.js, which treats "" as falsy.
  check("an empty translation is a failure, not 'not yet'",
        classify(s(1, "Hello.", { translations: { te: "" } }), "te") === "failed");

  // --- waiting states ----------------------------------------------------
  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([]);
    check("an empty card says Listening, never nothing",
          confirmedText(section) === LISTENING, confirmedText(section));
    check("the waiting line is marked so it can be styled apart from captions",
          section.querySelector(".card-confirmed").classList.contains("card-waiting"));
    check("nothing is pending on an empty card", pendingText(section) === "");
  }

  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([s(1, "Good intentions.", { translations: { te: "" } })]);
    check("a failed translation says so in plain words",
          pendingText(section) === AWAITING_TRANSLATION, pendingText(section));
    check("a failed translation never shows a technical error",
          !/error|null|undefined|\bexception\b/i.test(section.textContent),
          section.textContent);
  }

  // --- pending: grey English stands in -----------------------------------
  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([s(1, "Good intentions without action", { final: false })]);
    check("a held sentence puts English on the pending line",
          pendingText(section).startsWith("Good intentions without action"),
          pendingText(section));
    check("the pending line carries a trailing ellipsis",
          pendingText(section).endsWith("…"), pendingText(section));
    check("nothing is confirmed yet", confirmedSpans(section).length === 0);
    check("Listening is gone once there is something to show",
          !section.textContent.includes(LISTENING));
  }

  // --- confirmation: the translation replaces the English ----------------
  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([s(1, "Good intentions.", { final: false })]);
    card.render([s(1, "Good intentions.", { translations: { te: "మంచి ఉద్దేశాలు." } })]);
    check("the confirmed block holds the translation, not the English",
          confirmedText(section) === "మంచి ఉద్దేశాలు.", confirmedText(section));
    check("the pending line empties on confirmation",
          pendingText(section) === "", pendingText(section));
  }

  // --- the grey -> dark fade runs once, and only when witnessed ----------
  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([s(1, "One.", { final: false })]);
    card.render([s(1, "One.", { translations: { te: "ఒకటి." } })]);
    const span = confirmedSpans(section)[0];
    check("a witnessed confirmation starts at the pending colour",
          span.classList.contains("fading"), span.className);
    await raf();
    check("the fade is released on the next frame",
          !span.classList.contains("fading"), span.className);

    // Re-rendering must not replay the fade — the wall repaints on every
    // caption, and a sentence that keeps flashing grey would read as
    // permanently unsettled.
    card.render([
      s(1, "One.", { translations: { te: "ఒకటి." } }),
      s(2, "Two.", { translations: { te: "రెండు." } }),
    ]);
    check("an already-confirmed sentence does not fade again",
          !confirmedSpans(section)[0].classList.contains("fading"));
  }

  {
    // History replay: nobody watched these cross over, so nothing should fade.
    const { card, section } = mount("te", "తెలుగు");
    card.render([
      s(1, "One.", { translations: { te: "ఒకటి." } }),
      s(2, "Two.", { translations: { te: "రెండు." } }),
    ]);
    check("a backlog rendered from history never fades in",
          confirmedSpans(section).every((sp) => !sp.classList.contains("fading")),
          confirmedSpans(section).map((sp) => sp.className).join("|"));
  }

  // --- append-only: a reader must not lose their place -------------------
  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([s(1, "One.", { translations: { te: "ఒకటి." } })]);
    const first = confirmedSpans(section)[0];
    card.render([
      s(1, "One.", { translations: { te: "ఒకటి." } }),
      s(2, "Two.", { translations: { te: "రెండు." } }),
    ]);
    check("an existing sentence keeps its node across an update",
          confirmedSpans(section)[0] === first);
    check("the new sentence is appended after it",
          confirmedSpans(section).length === 2
            && confirmedSpans(section)[1].dataset.sid === "2",
          confirmedSpans(section).map((sp) => sp.dataset.sid).join(","));
    check("sentences render in spoken order",
          confirmedText(section) === "ఒకటి. రెండు.", confirmedText(section));
  }

  // --- the card reads as prose, not a stack of blocks --------------------
  {
    const { card, section } = mount("te", "తెలుగు");
    card.render([1, 2, 3].map((i) =>
      s(i, `Sentence ${i}.`, { translations: { te: `వాక్యం ${i}.` } })));
    const tops = new Set(confirmedSpans(section).map((sp) =>
      sp.getBoundingClientRect().top));
    check("short sentences share a line rather than stacking",
          tops.size < 3, `distinct tops=${tops.size}`);
  }

  // --- English as a card (the degenerate lanes === ['en'] case) ----------
  {
    const { card, section } = mount("en", "English");
    card.render([
      s(1, "Confirmed line.", {}),
      s(2, "Still being spoken", { final: false }),
    ]);
    check("an english card confirms from `en`, not from a translation",
          confirmedText(section) === "Confirmed line.", confirmedText(section));
    check("an english card still shows its pending line",
          pendingText(section) === "Still being spoken…", pendingText(section));
  }

  // --- fitting the window ------------------------------------------------
  // A card is a fixed window, not a scroller. Whatever else happens, the
  // newest text must be on screen and nothing may spill out of the box.
  {
    const { card, section } = mount("te", "తెలుగు");
    const many = Array.from({ length: 8 }, (_, i) =>
      s(i + 1, `Sentence ${i + 1}.`, { translations: { te: `${LONG_TE} ${i + 1}` } }));
    card.render(many);
    check("a long run of sentences does not overflow the card",
          !overflows(section),
          `scroll=${section.querySelector(".card-content").scrollHeight} ` +
          `client=${section.querySelector(".card-content").clientHeight}`);
    check("something is still on screen after the drop pass",
          confirmedSpans(section).length > 0, confirmedSpans(section).length);
    check("the oldest sentences are the ones dropped",
          !confirmedText(section).includes(" 1"), confirmedText(section).slice(0, 40));
    check("the newest sentence survives",
          confirmedText(section).includes(" 8"), confirmedText(section).slice(-40));
  }

  {
    // Dropping comes before shrinking: a reader at the back needs the type big
    // more than they need the sentence from a minute ago.
    const { card, section } = mount("te", "తెలుగు");
    card.render([1, 2, 3, 4].map((i) =>
      s(i, `S${i}.`, { translations: { te: `${LONG_TE} ${i}` } })));
    check("type stays at full size while there is old text to drop",
          scaleOf(section) === 1, scaleOf(section));
  }

  {
    // ...but one sentence too big for the card has nothing left to drop, so
    // the ladder is the only way to fit it.
    const { card, section } = mount("te", "తెలుగు", { width: 520, height: 260 });
    card.render([s(1, "Long.", { translations: { te: `${LONG_TE} ${LONG_TE}` } })]);
    check("a single oversized sentence steps the type down instead",
          scaleOf(section) < 1, scaleOf(section));
    check("the ladder bottoms out at the handoff's smallest step",
          scaleOf(section) >= 0.78, scaleOf(section));
  }

  {
    // The pending line is capped so a held sentence cannot crowd out the
    // translation the card exists to show.
    const { card, section } = mount("te", "తెలుగు");
    const held = "Good intentions without action accomplish nothing and moral "
      + "values without strategy become ineffective in every situation we meet";
    card.render([
      s(1, "Confirmed.", { translations: { te: LONG_TE } }),
      s(2, held, { final: false }),
    ]);
    check("a long held sentence still leaves the translation on screen",
          confirmedSpans(section).length === 1, confirmedSpans(section).length);
    check("the card does not overflow with a long pending line",
          !overflows(section));
    const pending = section.querySelector(".card-pending");
    const lines = pending.clientHeight
      / parseFloat(getComputedStyle(pending).lineHeight);
    check("the pending line is clamped to two lines", lines <= 2.1, lines.toFixed(2));
  }

  {
    // Re-rendering after a drop must bring the dropped text back in the right
    // place, not append it after the newer sentences.
    const { card, section } = mount("te", "తెలుగు", { width: 520, height: 300 });
    const rows = [1, 2, 3, 4].map((i) =>
      s(i, `S${i}.`, { translations: { te: `${LONG_TE} ${i}` } }));
    card.render(rows);
    const dropped = confirmedSpans(section).length;
    const { card: roomy, section: bigSection } = mount("te", "తెలుగు", { width: 1400, height: 900 });
    roomy.render(rows);
    check("a bigger card shows more of the same backlog",
          confirmedSpans(bigSection).length > dropped,
          `${confirmedSpans(bigSection).length} > ${dropped}`);
    check("restored sentences stay in spoken order",
          confirmedSpans(bigSection).map((sp) => sp.dataset.sid).join(",")
            === confirmedSpans(bigSection).map((sp) => sp.dataset.sid)
                 .slice().sort((a, b) => a - b).join(","),
          confirmedSpans(bigSection).map((sp) => sp.dataset.sid).join(","));
  }
}

finish(run());
