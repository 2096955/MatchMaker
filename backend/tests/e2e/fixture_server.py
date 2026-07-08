"""A tiny stdlib HTTP server serving one static HTML page, for the E2E
suite's website-URL-submission step. Never hits a real external host -
this IS the "website" the E2E test submits a URL for."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_FIXTURE_HTML = (
    b"<html><head><title>Global Equity Prices Feed</title></head>"
    b"<body><p>Real-time equity price feed for major exchanges, "
    b"published by the E2E fixture server.</p></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_FIXTURE_HTML)

    def log_message(self, format, *args):
        pass  # silence per-request stderr noise in test output


class FixtureServer:
    """Context-manager wrapping a background ThreadingHTTPServer on an
    ephemeral local port. `server.url` is the fully-qualified fixture URL
    to hand to the E2E test's URL-submission field."""

    def __enter__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
