/* The operator page. It holds no authoritative state: every interaction sends a
   COMPLETE display object, and the page re-renders from what the server echoes
   back. That is what stops two operator phones from disagreeing. */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const sent = (ws) => ws.__sent.map((raw) => JSON.parse(raw));

async function run() {
  const { win, doc } = await loadPage("/control", { width: 390, height: 720 });
  const ws = win.__sockets[0];
  check("control opened a caption socket", !!ws, ws && ws.url);
  if (!ws) return;
  ws.__sent = [];
  ws.send = (raw) => ws.__sent.push(raw);
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });
  ws.deliver({ type: "display", lanes: ["en", "ml", "te", "hi"], focus: null, rotate: 0 });

  const laneBtn = (lang) => doc.querySelector(`#lanes button[data-lang="${lang}"]`);
  const focusBtn = (v) => doc.querySelector(`#focus button[data-focus="${v}"]`);
  check("a toggle exists per language",
        ["en", "ml", "te", "hi"].every(laneBtn), "missing lane toggle");
  check("all lanes start enabled",
        ["en", "ml", "te", "hi"].every((l) => laneBtn(l).classList.contains("on")),
        "a lane started off");

  // Correction: English is a fully toggleable lane, so it must be pinnable
  // too — the focus row must not omit it while every other lane gets a button.
  check("english has a focus (pin) button and it is enabled while on the wall",
        !!focusBtn("en") && !focusBtn("en").disabled,
        "missing or disabled english focus button");

  laneBtn("en").click();
  const off = sent(ws).pop();
  check("turning english off sends a complete state",
        off && off.type === "display" &&
        JSON.stringify(off.lanes) === JSON.stringify(["ml", "te", "hi"]) &&
        off.focus === null && off.rotate === 0,
        JSON.stringify(off));

  // The page must not assume its own click succeeded — it renders the echo.
  ws.deliver({ type: "display", lanes: ["ml", "te", "hi"], focus: null, rotate: 0 });
  check("english toggle reflects the echoed state",
        !laneBtn("en").classList.contains("on"), "en still marked on");
  check("a focus button for a disabled lane is itself disabled",
        focusBtn("en").disabled, "english focus button not disabled while english is off");

  // Correction: the lane order is derived from the buttons already in the
  // page, not a second hard-coded array — so re-adding a language slots it
  // back into its canonical position instead of appending it at the end.
  laneBtn("en").click();
  const reAdded = sent(ws).pop();
  check("re-enabling a lane restores canonical button order, not append order",
        reAdded && JSON.stringify(reAdded.lanes) === JSON.stringify(["en", "ml", "te", "hi"]),
        JSON.stringify(reAdded));

  // Roll back to the three-lane state the rest of the suite assumes.
  ws.deliver({ type: "display", lanes: ["ml", "te", "hi"], focus: null, rotate: 0 });

  focusBtn("ml").click();
  const pinned = sent(ws).pop();
  check("pinning a language sends focus and clears rotate",
        pinned.focus === "ml" && pinned.rotate === 0, JSON.stringify(pinned));

  doc.getElementById("rotate-on").click();
  const rotating = sent(ws).pop();
  check("enabling rotation clears the pin",
        rotating.focus === null && rotating.rotate > 0, JSON.stringify(rotating));

  // Disabling every lane would blank the wall; the page must refuse locally.
  ws.deliver({ type: "display", lanes: ["ml"], focus: null, rotate: 0 });
  const before = sent(ws).length;
  laneBtn("ml").click();
  check("refuses to disable the last lane", sent(ws).length === before,
        `sent ${sent(ws).length - before} messages`);


  // --- language names must never be clipped -------------------------------
  // A reader picks their language by the shape of its own script. "മലയാ…ളം"
  // was what the 3-column row actually rendered.
  const clipped = [...doc.querySelectorAll("#lanes button, #focus button")]
    .filter((b) => b.scrollWidth > b.clientWidth + 1)
    .map((b) => `${b.textContent.trim()} (${b.scrollWidth}>${b.clientWidth})`);
  check("no language label is truncated", clipped.length === 0, clipped.join(", ") || "none");

  // --- clearing the wall takes two taps -----------------------------------
  // The one irreversible control on the page: it wipes the scrollback from
  // every phone in the building. A pocket tap must not be enough.
  const clear = doc.getElementById("clear-wall");
  check("clear control exists", !!clear);

  const wallMsgs = () => sent(ws).filter((m) => m.type === "wall");
  clear.click();
  check("one tap sends nothing", wallMsgs().length === 0,
        JSON.stringify(sent(ws).slice(-1)));
  check("one tap arms and says so",
        clear.classList.contains("armed") && /again/i.test(clear.textContent),
        clear.textContent.trim());

  clear.click();
  check("the second tap clears",
        wallMsgs().length === 1 && wallMsgs()[0].action === "clear",
        JSON.stringify(wallMsgs()));
  check("it disarms after firing", !clear.classList.contains("armed"),
        clear.textContent.trim());

  // Back to a resting state, so the next tap starts the two-step over again
  // rather than firing immediately.
  clear.click();
  check("a later tap re-arms rather than clearing again",
        clear.classList.contains("armed") && wallMsgs().length === 1,
        `${wallMsgs().length} clears sent`);
}

finish(run());