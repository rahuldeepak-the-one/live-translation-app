/* Drives the REAL static/status.js at phone size.
 *
 * The page is deliberately opinion-free about the microphone: it renders what
 * the socket says and sends instructions back. So the checks come in pairs —
 * what a given state LOOKS like, and what a given tap SENDS.
 */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const DEVICES = [
  { id: "usb-1", label: "Yamaha MG10XU Analog Stereo" },
  { id: "pci-1", label: "Built-in Audio Analog Stereo" },
];
const micState = (over = {}) => ({
  type: "mic", connected: true, capturing: false, deviceId: null,
  deviceLabel: null, devices: DEVICES, level: 0, ...over,
});

async function run() {
  const { win, doc } = await loadPage("/status", { width: 390, height: 844 });
  const $ = (sel) => doc.querySelector(sel);
  const $$ = (sel) => [...doc.querySelectorAll(sel)];
  const text = (sel) => ($(sel) ? $(sel).textContent.trim() : "");
  const scaleX = (el) => {
    // Settle the meter's 120ms ease first. Under --virtual-time-budget the
    // headless renderer can decline to start a CSS transition at all, and
    // getComputedStyle then reports the pre-transition value forever — which
    // reads as "the meter never moved" no matter what the level is.
    el.getAnimations().forEach((a) => a.finish());
    const m = win.getComputedStyle(el).transform.match(/matrix\(([-\d.]+)/);
    return m ? Number(m[1]) : 1;
  };

  const ws = win.__sockets[0];
  check("status.js opened an operator socket", !!ws, ws && ws.url);
  if (!ws) return;

  // FakeWS swallows send(); capture it instead so taps can be asserted on.
  const sent = [];
  ws.send = (data) => sent.push(JSON.parse(data));
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });
  ws.deliver({ type: "display", lanes: ["en", "ml"], focus: null, rotate: 0 });

  // --- nothing connected yet ----------------------------------------------
  check("says plainly when no capture feed is connected",
        text("#mic-device").includes("No capture feed"), text("#mic-device"));
  check("start is not offered with no mic feed", $("#btn-start").disabled);
  check("stop is not offered with no mic feed", $("#btn-stop").disabled);
  check("the wall is mirrored from the display state",
        text("#d-wall").includes("English") && text("#d-wall").includes("മലയാളം"),
        text("#d-wall"));

  // --- connected, idle, nothing pinned yet ---------------------------------
  ws.deliver(micState());
  check("lists every input the feed can see",
        $$("#devices .device .name").some((n) => n.textContent.includes("Yamaha"))
        && $$("#devices .device .name").some((n) => n.textContent.includes("Built-in")),
        $$("#devices .device .name").map((n) => n.textContent).join(" | "));
  // No 'System default' row any more: feed-mic.py names a pactl source, so
  // such a row would send an id that matches nothing and do nothing.
  check("offers no System default row",
        !$$("#devices .device .name").some((n) => n.textContent.includes("System default")),
        $$("#devices .device .name").map((n) => n.textContent).join(" | "));
  check("nothing is marked current until the feed names a device",
        $("#devices .device.current") === null);
  check("start is armed once a feed is there",
        !$("#btn-start").disabled && $("#btn-start").classList.contains("armed"));
  check("stop stays disabled while not capturing", $("#btn-stop").disabled);
  check("device list replaces the placeholder", $("#devices-empty").hidden);

  // --- switching input ------------------------------------------------------
  const yamaha = $$("#devices .device").find((b) => b.textContent.includes("Yamaha"));
  yamaha.click();
  const switchMsg = sent.find((m) => m.action === "set_device");
  check("tapping an input asks the feed to switch to it",
        switchMsg && switchMsg.type === "mic" && switchMsg.deviceId === "usb-1",
        JSON.stringify(switchMsg));
  check("the page does not mark the switch done on its own",
        $("#devices .device.current") === null,
        "must wait for the server to echo the new state");

  // The echo is what moves the marker.
  ws.deliver(micState({ deviceId: "usb-1", deviceLabel: DEVICES[0].label }));
  check("the echoed state moves the current marker",
        $("#devices .device.current .name").textContent.includes("Yamaha"),
        text("#devices .device.current"));
  check("the headline names the live input",
        text("#mic-device").includes("Yamaha"), text("#mic-device"));

  // --- capturing, but hearing nothing --------------------------------------
  ws.deliver(micState({ deviceId: "usb-1", deviceLabel: DEVICES[0].label,
                        capturing: true, level: 0.005 }));   // peak 164, under 300
  check("stop is armed while capturing",
        !$("#btn-stop").disabled && $("#btn-stop").classList.contains("armed"));
  check("start is disabled while already capturing", $("#btn-start").disabled);
  check("a silent live mic is called out, not left to the operator to notice",
        text("#mic-sub").includes("nothing above the silence threshold"), text("#mic-sub"));
  check("the meter reads quiet below the silence threshold",
        $("#meter").classList.contains("quiet"), $("#meter").className);
  check("no-signal chip is showing",
        $$(".chip").some((c) => c.textContent.includes("No signal")),
        $$(".chip").map((c) => c.textContent).join(" | "));

  const quietWidth = scaleX($("#meter"));
  check("a quiet-but-present signal still registers on the meter",
        quietWidth > 0 && quietWidth < 0.5, String(quietWidth));

  // --- capturing and hearing the room --------------------------------------
  ws.deliver(micState({ deviceId: "usb-1", deviceLabel: DEVICES[0].label,
                        capturing: true, level: 0.25 }));
  check("the meter grows with the level", scaleX($("#meter")) > quietWidth,
        `${quietWidth.toFixed(3)} -> ${scaleX($("#meter")).toFixed(3)}`);
  check("the meter stops reading quiet once above the threshold",
        !$("#meter").classList.contains("quiet"));
  check("signal chip replaces no-signal",
        $$(".chip").some((c) => c.textContent.trim() === "Signal"),
        $$(".chip").map((c) => c.textContent).join(" | "));
  check("says it is hearing the room", text("#mic-sub").includes("hearing the room"),
        text("#mic-sub"));
  check("the silence threshold is drawn on the meter, not just described",
        parseFloat($("#threshold").style.left) > 0
        && parseFloat($("#threshold").style.left) < 100, $("#threshold").style.left);

  // --- stop -----------------------------------------------------------------
  $("#btn-stop").click();
  check("stop asks the feed to stop",
        sent.some((m) => m.type === "mic" && m.action === "stop"),
        JSON.stringify(sent[sent.length - 1]));

  // --- the capture feed goes away ------------------------------------------
  ws.deliver(micState({ connected: false, capturing: false,
                        deviceId: "usb-1", deviceLabel: DEVICES[0].label, level: 0 }));
  check("a dropped capture feed disables both controls",
        $("#btn-start").disabled && $("#btn-stop").disabled);
  check("the meter empties when the mic feed drops", scaleX($("#meter")) === 0,
        String(scaleX($("#meter"))));

  // --- the rest of the console ---------------------------------------------
  ws.deliver({ type: "presence", screens: 12 });
  check("counts the screens connected", text("#d-screens") === "12", text("#d-screens"));
  ws.deliver({ type: "status", state: "processing" });
  check("shows what the pipeline is doing", text("#d-pipeline") === "processing",
        text("#d-pipeline"));

  check("console does not overflow the phone width",
        doc.documentElement.scrollWidth <= win.innerWidth,
        `${doc.documentElement.scrollWidth} > ${win.innerWidth}`);
  check("console reads from the top, not pinned to the bottom",
        $("#grid").getBoundingClientRect().top < win.innerHeight / 2,
        String($("#grid").getBoundingClientRect().top));
}

finish(run());
