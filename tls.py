"""TLS material for the LAN server.

WHY THIS EXISTS, AND WHY IT IS NOW OPTIONAL. It used to be load-bearing:
Chrome grants getUserMedia only in a "secure context", `http://localhost`
counts as one and `http://192.168.1.110` does not, so the phone that was
supposed to BE the microphone never saw a permission prompt and /mic silently
sat there. TLS was the only way capture worked at all.

Capture is no longer in a browser — ./feed-mic.py reads the mixer through
parec on this machine — so nothing in this app requires a secure context any
more. What is left is smaller but real: /view and /ws/captions carry the
sermon, in the clear, across whatever WiFi the venue runs. Serving them over
TLS is now a privacy choice rather than a precondition, and a certless boot is
a complete, working service.

THE CERTIFICATE IS SELF-SIGNED, and there is no way around that. No public CA
will issue for a private IP, so the first phone to open a page gets Chrome's
"Your connection is not private" interstitial and has to tap through it. That
is a one-time cost per device, and it does not prevent getUserMedia: once the
override is accepted the origin is a secure context like any other.

Absent or unreadable certificate files are NOT an error. The server falls back
to plain HTTP and everything except the phone microphone keeps working, which
is the right behaviour for a Sunday morning — a missing cert must not be the
reason there are no captions at all.
"""
from pathlib import Path

from config import SSL_CERTFILE, SSL_KEYFILE


def paths(certfile=SSL_CERTFILE, keyfile=SSL_KEYFILE):
    """(cert, key) as strings if BOTH exist, else (None, None).

    Both or neither: uvicorn given one half of a pair fails to boot, and a
    server that refuses to start is a worse outcome than one without TLS.
    """
    cert, key = Path(certfile), Path(keyfile)
    if cert.is_file() and key.is_file():
        return str(cert), str(key)
    return None, None


def enabled(certfile=SSL_CERTFILE, keyfile=SSL_KEYFILE):
    return paths(certfile, keyfile) != (None, None)


def scheme(certfile=SSL_CERTFILE, keyfile=SSL_KEYFILE):
    """The scheme every printed URL and QR code should carry."""
    return "https" if enabled(certfile, keyfile) else "http"
