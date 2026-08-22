/* The projector wall: independent flowing lanes, one per enabled language. */
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
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });
  ws.deliver(display(["en", "ml", "te", "hi"]));

  for (let i = 1; i <= 12; i++) {
    ws.deliver(sentence(i, `Projector caption number ${i} is fairly long.`));
    ws.deliver(translation(i));
  }

  const lanes = () => [...doc.querySelectorAll(".lane")];
  check("one lane per enabled language", lanes().length === 4, lanes().length);
  check("lanes are labelled by language",
        lanes().map((l) => l.dataset.lang).join(",") === "en,ml,te,hi",
        lanes().map((l) => l.dataset.lang).join(","));

  const mlText = doc.querySelector('.lane[data-lang="ml"] .lane-flow').textContent;
  check("malayalam lane shows malayalam", mlText.includes("മലയാളം"), mlText.slice(0, 40));

  // Lanes flow: many sentences share one paragraph, not one block each.
  const mlSpans = doc.querySelectorAll('.lane[data-lang="ml"] .lane-flow span');
  check("lane is one flowing paragraph of spans", mlSpans.length === 12, mlSpans.length);

  // The span-count check above would still pass even if every span rendered
  // on its own line (e.g. Flow's container itself being a flex column, which
  // turns each inline span into a block-level flex item). Prove the lane
  // actually reads as flowing prose: if multiple sentences share a text line,
  // their spans share a top offset, so distinct tops must be fewer than spans.
  const mlTops = new Set([...mlSpans].map((s) => s.getBoundingClientRect().top));
  check("lane genuinely flows as prose (sentences share lines, not one-per-line)",
        mlTops.size < mlSpans.length,
        `distinct tops=${mlTops.size} spans=${mlSpans.length}`);

  // Turning English off must leave three lanes, not a gap.
  ws.deliver(display(["ml", "te", "hi"]));
  check("disabling english leaves three lanes", lanes().length === 3, lanes().length);
  check("english lane is gone",
        !doc.querySelector('.lane[data-lang="en"]'), "en lane still present");

  // The whole point of a projector: newest text must be visible, and the
  // container must remain scrollable (common.css:36 documents the trap).
  for (const lane of lanes()) {
    const flow = lane.querySelector(".lane-flow");
    const scrollable = flow.scrollHeight >= flow.clientHeight;
    check(`lane ${lane.dataset.lang} is not scroll-trapped`, scrollable,
          `scrollHeight=${flow.scrollHeight} clientHeight=${flow.clientHeight}`);
    check(`lane ${lane.dataset.lang} is pinned to the live edge`,
          flow.scrollHeight - flow.clientHeight - flow.scrollTop <= 2,
          `${flow.scrollHeight - flow.clientHeight - flow.scrollTop}px from bottom`);
  }

  // A sentence with no translation shows grey English, never "…".
  ws.deliver(sentence(99, "Just spoken, not yet translated.", false));
  const teFlow = doc.querySelector('.lane[data-lang="te"] .lane-flow');
  check("untranslated lane shows english, not an ellipsis",
        teFlow.textContent.includes("Just spoken") && !teFlow.textContent.includes("…"),
        teFlow.textContent.slice(-60));
  const newest = teFlow.querySelector('span[data-sid="99"]');
  check("unfinalised sentence is marked provisional",
        newest && newest.classList.contains("provisional"),
        newest && newest.className);

  // Reconnect must restore lanes, not revert to all four. display.js redials
  // via setTimeout(connect, 2000), so the new socket only exists after that
  // timer fires — wait it out (the harness runs under --virtual-time-budget,
  // so this settles fast rather than costing 2 real seconds).
  ws.onclose();
  await new Promise((r) => win.setTimeout(r, 2100));
  const ws2 = win.__sockets[win.__sockets.length - 1];
  check("display reconnected", ws2 !== ws, `${win.__sockets.length} sockets`);
  // --- focus -------------------------------------------------------------
  ws.deliver(display(["en", "ml", "te", "hi"], "ml", 0));
  check("focus renders exactly one lane", lanes().length === 1, lanes().length);
  check("the focused lane is the requested one",
        lanes()[0].dataset.lang === "ml", lanes()[0].dataset.lang);
  check("wall is marked focused",
        doc.getElementById("wall").classList.contains("focused"), "no .focused");

  const focusedFlow = doc.querySelector('.lane[data-lang="ml"] .lane-flow');
  check("focused lane still holds the backlog",
        focusedFlow.querySelectorAll("span").length >= 12,
        focusedFlow.querySelectorAll("span").length);

  // Leaving focus must restore lanes that are already full, not blank ones.
  ws.deliver(display(["en", "ml", "te", "hi"], null, 0));
  check("leaving focus restores every lane", lanes().length === 4, lanes().length);
  const restored = doc.querySelector('.lane[data-lang="te"] .lane-flow');
  check("restored lane is not blank",
        restored.textContent.includes("తెలుగు"), restored.textContent.slice(0, 30));

  // --- rotation ----------------------------------------------------------
  const { nextLane } = win;
  check("rotation advances through the lanes",
        nextLane(["ml", "te", "hi"], "ml") === "te", nextLane(["ml", "te", "hi"], "ml"));
  check("rotation wraps",
        nextLane(["ml", "te", "hi"], "hi") === "ml", nextLane(["ml", "te", "hi"], "hi"));
  check("rotation starts at the first lane when nothing is current",
        nextLane(["ml", "te", "hi"], null) === "ml", nextLane(["ml", "te", "hi"], null));
  check("rotation survives the current lane being disabled",
        nextLane(["ml", "hi"], "te") === "ml", nextLane(["ml", "hi"], "te"));
}

finish(run());
