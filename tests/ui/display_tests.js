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
  // `scrollHeight >= clientHeight` is true by definition (scrollHeight can
  // never be smaller) and cannot fail; assert real overflow exists instead,
  // the same shape tests/ui/view_tests.js uses for its `maxScroll` check.
  for (const lane of lanes()) {
    const flow = lane.querySelector(".lane-flow");
    const maxScroll = flow.scrollHeight - flow.clientHeight;
    check(`lane ${lane.dataset.lang} is not scroll-trapped`, maxScroll > 0,
          `maxScroll=${maxScroll} scrollHeight=${flow.scrollHeight} clientHeight=${flow.clientHeight}`);
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

  // `ws2 !== ws` only proves a new socket object exists. Prove state is
  // actually RESTORED on it: deliver history (replaying the same backlog a
  // real server would still have) and a NARROWED lane set on ws2 itself, and
  // assert the wall renders exactly that — not all four, and not by having
  // silently kept running on the old (closed) `ws`.
  // The replayed backlog must be DISTINCT from the pre-close one. It used to be
  // byte-identical, which made the assertion below unfalsifiable: a page that
  // ignored the replayed `history` entirely and kept rendering its pre-close
  // cache still had >= 12 spans and still passed. The content has to be
  // something only the NEW history could have produced.
  const reconnectSentences = Array.from({ length: 12 }, (_, i) => {
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
        lanes().length === 1 && lanes()[0].dataset.lang === "ml",
        lanes().map((l) => l.dataset.lang).join(","));

  const mlAfterReconnect = doc.querySelector('.lane[data-lang="ml"] .lane-flow');
  const reconnectText = mlAfterReconnect ? mlAfterReconnect.textContent : "";
  check("reconnect's lane renders the backlog replayed on the NEW socket",
        reconnectText.includes("പുനഃസംയോജനം 101")
        && reconnectText.includes("പുനഃസംയോജനം 112"),
        reconnectText.slice(0, 80));
  // history REPLACES state; a page that appended would still be showing the
  // pre-close captions underneath, and a reconnect mid-service would then show
  // the passage twice.
  check("reconnect history replaces the pre-close cache rather than appending",
        !reconnectText.includes("മലയാളം വാക്യം"),
        reconnectText.slice(0, 80));

  // --- focus -------------------------------------------------------------
  ws2.deliver(display(["en", "ml", "te", "hi"], "ml", 0));
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
  ws2.deliver(display(["en", "ml", "te", "hi"], null, 0));
  check("leaving focus restores every lane", lanes().length === 4, lanes().length);
  const restored = doc.querySelector('.lane[data-lang="te"] .lane-flow');
  // The reconnect above replaced the backlog, so the Telugu lane now holds the
  // REPLAYED captions. Asserting the pre-close text here would pass only
  // because the page failed to apply that history.
  check("restored lane is not blank",
        restored.textContent.includes("మళ్లీ కనెక్ట్"),
        restored.textContent.slice(0, 30));

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

  // --- rotation must not leak timers ------------------------------------
  // Every `display` message must clear the previous interval before setting a
  // new one. Without that, each message stacks another timer and the wall
  // flickers between languages at compounding speed — invisible in a short
  // test, ruinous over a 90-minute service. Counting calls needs no real ticks:
  // display.js resolves setInterval from the iframe global at call time.
  let setCalls = 0, clearCalls = 0;
  const realSet = win.setInterval, realClear = win.clearInterval;
  win.setInterval = (...a) => { setCalls++; return realSet.apply(win, a); };
  win.clearInterval = (...a) => { clearCalls++; return realClear.apply(win, a); };

  ws2.deliver(display(["ml", "te", "hi"], null, 20));
  ws2.deliver(display(["ml", "te", "hi"], null, 30));
  ws2.deliver(display(["ml", "te", "hi"], null, 20));
  check("every rotating display message clears before it sets",
        clearCalls === 3 && setCalls === 3, `clear=${clearCalls} set=${setCalls}`);

  // Pinning must clear without setting, so a rotation timer cannot survive it.
  ws2.deliver(display(["ml", "te", "hi"], "ml", 0));
  check("pinning clears the rotation timer and starts no new one",
        clearCalls === 4 && setCalls === 3, `clear=${clearCalls} set=${setCalls}`);

  win.setInterval = realSet;
  win.clearInterval = realClear;

  // --- a rotation tick must actually move the wall -----------------------
  // nextLane is covered above as a pure function, but nothing verified the
  // setInterval callback wires it to the DOM. A broken callback leaves all four
  // nextLane checks green while the wall silently never rotates. Capturing the
  // callback and calling it is synchronous, so no real timer is needed.
  let tick = null;
  const savedSet = win.setInterval;
  win.setInterval = (fn, ms) => { tick = fn; return savedSet.apply(win, [fn, ms]); };

  ws2.deliver(display(["ml", "te", "hi"], null, 20));
  check("rotating shows exactly one lane",
        doc.querySelectorAll(".lane").length === 1,
        doc.querySelectorAll(".lane").length);
  check("a rotating display message installed a tick callback", typeof tick === "function");

  const seen = [];
  for (let i = 0; i < 4; i++) {
    seen.push(doc.querySelector(".lane").dataset.lang);
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

  // --- an utterance published during registration must not be lost -------
  // hub.py adds a client to _clients BEFORE awaiting the history send (see
  // hub.py:21-22 and tests/test_hub.py's pinned ordering test), so a
  // `sentence` can legitimately arrive on THIS connection before its own
  // `history` snapshot. display.js must buffer such events and replay them
  // on top of history, exactly like view.js already does — otherwise the
  // empty history that follows wipes every lane's cache and the utterance
  // vanishes from the wall while /view still shows it.
  {
    const { win: freshWin, doc: freshDoc } = await loadPage("/display", { width: 1280, height: 720 });
    const freshWs = freshWin.__sockets[0];
    check("fresh display.js connection opened a caption socket", !!freshWs);
    if (freshWs) {
      freshWs.onopen();
      freshWs.deliver(sentence(99, "Mid-join utterance.", true));
      freshWs.deliver({ type: "history", sentences: [] });
      freshWs.deliver(display(["en", "ml", "te", "hi"]));
      const enFlow = freshDoc.querySelector('.lane[data-lang="en"] .lane-flow');
      check("an utterance published during registration survives history replay",
            enFlow && enFlow.textContent.includes("Mid-join utterance."),
            enFlow && enFlow.textContent.trim().slice(-60));
    }
  }

  // A fresh /display load mid-service is the common case this guards: the
  // page's own module-load renderLanes() already built the default four
  // lanes before any socket message arrives, so when `history` arrives with
  // a non-empty backlog and the lane SET is unchanged, renderLanes() takes
  // its "nothing structural changed" early return — the per-flow reset right
  // after it is what actually loads the backlog into the (already-built,
  // still-empty) lanes.
  {
    const { win: backlogWin, doc: backlogDoc } = await loadPage("/display", { width: 1280, height: 720 });
    const backlogWs = backlogWin.__sockets[0];
    check("fresh display.js connection (backlog case) opened a caption socket", !!backlogWs);
    if (backlogWs) {
      backlogWs.onopen();
      backlogWs.deliver({
        type: "history",
        sentences: [{ id: 1, en: "Pre-existing backlog sentence.", translations: null, final: true }],
      });
      backlogWs.deliver(display(["en", "ml", "te", "hi"]));
      const enFlow = backlogDoc.querySelector('.lane[data-lang="en"] .lane-flow');
      check("a fresh connection's non-empty history renders even when the lane set is unchanged",
            enFlow && enFlow.textContent.includes("Pre-existing backlog sentence."),
            enFlow && enFlow.textContent.trim().slice(-60));
    }
  }
}

finish(run());
