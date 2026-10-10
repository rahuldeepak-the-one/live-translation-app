"""The phone pages and the projector wall must not drift into two palettes.

static/display.css owns the wall and switches light/dark with the hall.
static/common.css dresses the screens people hold, which are always dark. The
dark values are therefore COPIED rather than shared through one file — sharing
would let a change to the wall's theme flip the phones with it, which is
exactly what splitting display.css out was meant to prevent.

Copying is only safe if something notices when the copies disagree. That is
this file. If it fails, the two stylesheets have diverged: decide which value
is right and change BOTH, or move the token out of the shared set below.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"

# Tokens that must mean the same thing on the wall and in the hand. Tokens
# outside this set are free to differ (--accent has no meaning on a wall with
# nothing to press; --qr-border has none on a phone).
SHARED = (
    "--page-bg", "--card-bg", "--card-border", "--text-confirmed",
    "--text-pending", "--text-muted", "--text-secondary", "--live-dot",
    "--warn-dot",
)


def declarations(css, block_re):
    """The `--name: value` pairs inside the first block matching block_re."""
    match = re.search(block_re, css, re.S)
    assert match, f"no block matching {block_re!r}"
    return dict(re.findall(r"(--[a-z-]+)\s*:\s*([^;]+);", match.group(1)))


@pytest.fixture(scope="module")
def palettes():
    display = (STATIC / "display.css").read_text()
    common = (STATIC / "common.css").read_text()
    return (
        declarations(display, r':root\[data-theme="dark"\]\s*\{(.*?)\}'),
        declarations(common, r":root\s*\{(.*?)\}"),
    )


@pytest.mark.parametrize("token", SHARED)
def test_phone_and_wall_agree_on(token, palettes):
    wall, phone = palettes
    assert token in wall, f"{token} vanished from display.css's dark theme"
    assert token in phone, f"{token} vanished from common.css"
    assert wall[token].strip() == phone[token].strip(), (
        f"{token} differs: wall={wall[token].strip()} phone={phone[token].strip()}. "
        "Change both, or drop it from SHARED if they are meant to differ."
    )


def test_the_font_stack_matches_exactly():
    """Not just the same families — the same ORDER.

    common.css used to lead with system-ui, so the same English sentence
    rendered in the OS UI font on a phone and in Noto Sans on the wall.
    """
    def stack(name):
        css = (STATIC / name).read_text()
        raw = re.search(r"font-family:\s*([^;]+);", css).group(1)
        return [f.strip().strip('"\'') for f in raw.split(",")]

    assert stack("common.css") == stack("display.css")


def test_no_stylesheet_reaches_the_internet():
    """The hall has no internet. A remote @import or webfont <link> is
    render-blocking until it fails, on the pages that must work to start the
    service. Every Noto family is installed on the host instead."""
    offenders = []
    for path in sorted(STATIC.glob("*.css")) + sorted(STATIC.glob("*.html")):
        text = path.read_text()
        for pattern in (r"@import\s+url\(['\"]?https?://",
                        r"<link[^>]+href=['\"]https?://"):
            if re.search(pattern, text):
                offenders.append(path.name)
    assert not offenders, f"these fetch from the network: {sorted(set(offenders))}"
