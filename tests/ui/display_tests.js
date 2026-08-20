/* Regression cover for the projector page — it shares common.css with /view,
   so the scroll and viewport-height fixes must not disturb it. */
import { check, loadPage, finish } from "/tests/ui/runner.js";

const LANGS = ["EN", "ML", "TE", "HI"];

const sentence = (id, en) => ({ type: "sentence", id, en });
const translation = (id) => ({
  type: "translation", id,
  ml: `മലയാളം വാക്യം ${id}.`,
  te: `తెలుగు వాక్యం ${id}.`,
  hi: `हिन्दी वाक्य ${id}।`,
});

async function run() {
  const { win, doc } = await loadPage("/display", { width: 1280, height: 720 });
  const ws = win.__sockets[0];
  check("display.js opened a caption socket", !!ws, ws && ws.url);
  if (!ws) return;
  ws.onopen();
  ws.deliver({ type: "history", sentences: [] });

  for (let i = 1; i <= 12; i++) {
    ws.deliver(sentence(i, `This is a fairly long projector caption number ${i}.`));
    ws.deliver(translation(i));
  }

  const main = doc.getElementById("captions");
  const rows = doc.querySelectorAll(".caption-row");
  check("captions still render as rows", rows.length > 0, rows.length);

  const lines = rows[rows.length - 1].querySelectorAll(".lang-line");
  check("newest caption shows all four languages", lines.length === LANGS.length, lines.length);

  // The whole point of the projector: the newest caption must be on screen.
  const mainBox = main.getBoundingClientRect();
  const newestBox = rows[rows.length - 1].getBoundingClientRect();
  check("newest caption is inside the visible area",
        newestBox.bottom <= mainBox.bottom + 2 && newestBox.top >= mainBox.top - 2,
        `main=[${Math.round(mainBox.top)},${Math.round(mainBox.bottom)}] ` +
        `newest=[${Math.round(newestBox.top)},${Math.round(newestBox.bottom)}]`);

  // The footer carries the /view URL people type into their phones.
  const footer = doc.querySelector("footer");
  const footerBox = footer.getBoundingClientRect();
  check("footer with the /view URL stays on screen",
        footerBox.bottom <= win.innerHeight + 2 && footerBox.height > 0,
        `bottom=${Math.round(footerBox.bottom)} viewport=${win.innerHeight}`);

  // The QR is how people get to /view without typing an IP — if it fails to
  // render there is no fallback anyone will actually use.
  const qr = doc.getElementById("qr");
  check("QR code element is present", !!qr);
  await new Promise((r) => win.setTimeout(r, 400));   // let the image load
  check("QR code image actually loaded", qr && qr.complete && qr.naturalWidth > 0,
        qr && `complete=${qr.complete} naturalWidth=${qr.naturalWidth}`);
  const qrBox = qr && qr.getBoundingClientRect();
  check("QR code is big enough to scan", qrBox && qrBox.width >= 80 && qrBox.height >= 80,
        qrBox && `${Math.round(qrBox.width)}x${Math.round(qrBox.height)}`);

  check("page itself does not scroll", doc.body.scrollHeight <= win.innerHeight + 2,
        `body=${doc.body.scrollHeight} viewport=${win.innerHeight}`);
}

finish(run());
