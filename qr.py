"""QR code pointing phones at /view.

Typing `192.168.1.29:8080/view` on a phone is error-prone, and browsers like to
"helpfully" upgrade a bare host:port to https — which this server does not
speak. A scanned QR carries the scheme explicitly and sidesteps both problems.

Rendered as inline SVG so the page stays self-contained: the venue has no
internet, so nothing may be fetched from anywhere.
"""
import io
import re

import segno

# Host headers we are willing to encode: letters, digits, dots, hyphens, a
# port, and the brackets of an IPv6 literal. Anything else is a client sending
# us junk (or a header-injection attempt) and is refused.
_HOST = re.compile(r"^[A-Za-z0-9.\-]+(?::\d{1,5})?$|^\[[0-9A-Fa-f:]+\](?::\d{1,5})?$")


MAX_HOST_LEN = 253          # DNS limit; also caps how much a client can inject


def view_url_for(host):
    """`http://<host>/view`, or None if the host is not something we'd publish."""
    if not isinstance(host, str):
        return None
    host = host.strip()
    if not host or len(host) > MAX_HOST_LEN or not _HOST.match(host):
        return None
    return f"http://{host}/view"


def svg_for(url, scale=4, border=2, dark="#0b0f14", light="#ffffff"):
    """A standalone SVG document for `url`.

    NOT segno's svg_inline(): that omits the xmlns declaration, which is legal
    when the markup is pasted straight into HTML but makes the file invalid as
    a standalone document — an <img src="/qr.svg"> then loads with
    naturalWidth 0 and silently renders nothing.

    Error level M keeps it readable by a phone camera held at an angle.
    """
    buffer = io.BytesIO()          # segno's SVG writer emits bytes, not text
    segno.make(url, error="m").save(
        buffer, kind="svg", scale=scale, border=border,
        dark=dark, light=light, xmldecl=False,
    )
    return buffer.getvalue().decode("utf-8")
