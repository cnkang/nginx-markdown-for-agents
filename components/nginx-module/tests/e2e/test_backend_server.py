#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIMPLE_HTML = """<html><head><title>Simple</title></head><body><h1>Simple Test Page</h1><p>Visit <a href=\"https://example.com\">Example</a>.</p></body></html>"""

COMPLEX_HTML = """<html><head><title>Complex</title></head><body>
<h1>Main Heading</h1>
<h2>Subheading</h2>
<p>This is <strong>complex</strong> content with <em>various</em> elements.</p>
<pre><code>let x = 1;</code></pre>
<script>console.log('remove me')</script>
</body></html>"""

CHUNKED_HTML_PARTS = [
    "<html><body>",
    "<h1>Chunked Response</h1>",
    "<p>This response is sent in chunks.</p>",
    "</body></html>",
]

LARGE_HTML = "<html><body><h1>Large Document</h1><p>" + ("content " * 40000) + "</p></body></html>"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        return

    def _send_html(self, status: int, body: str, extra_headers: dict[str, str] | None = None):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("ETag", '"simple-v1"')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/simple":
            self._send_html(200, SIMPLE_HTML)
            return

        if self.path == "/complex":
            self._send_html(200, COMPLEX_HTML)
            return

        if self.path == "/error":
            self._send_html(500, "<html><body><h1>Backend Error</h1></body></html>")
            return

        if self.path == "/large":
            self._send_html(200, LARGE_HTML)
            return

        if self.path == "/chunked":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            if self.command != "HEAD":
                for part in CHUNKED_HTML_PARTS:
                    chunk = part.encode("utf-8")
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(b"0\r\n\r\n")
            return

        self._send_html(404, "<html><body><h1>Not Found</h1></body></html>")

    def do_HEAD(self):
        self.do_GET()


def main():
    parser = argparse.ArgumentParser(description="Test backend for NGINX markdown E2E")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
