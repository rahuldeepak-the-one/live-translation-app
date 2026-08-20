"""Tiny static server for the browser harness: serves the real /static assets,
the real /view page, and the harness under /tests/ui/."""
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # /qr.svg is generated, not a file — mirror the real server so the
        # display page renders a genuine QR in the browser tests.
        if self.path.split("?", 1)[0] == "/qr.svg":
            from qr import svg_for
            body = svg_for(f"http://{self.headers.get('Host', 'localhost')}/view").encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path in ("/view", "/display"):
            return str(ROOT / "static" / f"{path.lstrip('/')}.html")
        return str(ROOT / path.lstrip("/"))

    def log_message(self, *args):
        pass


def serve():
    """Start on an ephemeral port; returns (base_url, shutdown_callable)."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{httpd.server_port}", httpd.shutdown
