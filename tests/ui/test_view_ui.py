"""Browser tests for the caption pages. Needs Chrome; run with `pytest -m browser`."""
import json
import re
import shutil
import subprocess

import pytest

from tests.ui.server import serve

pytestmark = pytest.mark.browser

CHROME = next(
    (c for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)),
    None,
)


def run_harness(suite):
    base, shutdown = serve()
    try:
        out = subprocess.run(
            # --headless=new honours --window-size for the DOM viewport; the old
            # headless mode reports a fixed ~500px width regardless, which would
            # let phone-width layout regressions pass unnoticed.
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--window-size=390,720", "--virtual-time-budget=8000",
             "--dump-dom", f"{base}/tests/ui/harness.html?suite={suite}"],
            capture_output=True, text=True, timeout=90,
        ).stdout
    finally:
        shutdown()
    match = re.search(r'<pre id="__results" hidden="">(.*?)</pre>', out, re.S)
    assert match, f"harness produced no results block:\n{out[:2000]}"
    raw = match.group(1)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        raw = raw.replace(entity, char)
    return json.loads(raw)


@pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium on PATH")
@pytest.mark.parametrize("suite", ["view", "display", "control", "theme"])
def test_page_behaviour(suite):
    checks = run_harness(suite)
    assert checks, "harness ran no checks"
    failed = [c for c in checks if not c["pass"]]
    report = "\n".join(
        f"  {'PASS' if c['pass'] else 'FAIL'}  {c['name']}"
        + (f"  [{c['detail']}]" if c["detail"] else "")
        for c in checks
    )
    assert not failed, f"[{suite}] {len(failed)}/{len(checks)} browser checks failed:\n{report}"
