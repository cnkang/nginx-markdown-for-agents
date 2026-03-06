#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socketserver
import ssl
import time

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

HTTP_STATUS_MESSAGES = {
    200: "OK",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


def status_line(status: int) -> bytes:
    message = HTTP_STATUS_MESSAGES.get(status, "Unknown")
    return f"HTTP/1.1 {status} {message}\r\n".encode("ascii")


class TLSBackendHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request_line = self.rfile.readline(65537)
        if not request_line:
            return

        try:
            method, target, _version = request_line.decode("iso-8859-1").strip().split(" ", 2)
        except ValueError:
            self._send_response(500, "text/plain; charset=utf-8", b"invalid request\n", "GET")
            return

        while True:
            header_line = self.rfile.readline(65537)
            if not header_line or header_line in {b"\r\n", b"\n"}:
                break

        path = target.split("?", 1)[0]
        if method not in {"GET", "HEAD"}:
            self._send_html(405, "<html><body><h1>Method Not Allowed</h1></body></html>", method)
            return

        if path == "/health":
            payload = json.dumps({"ok": True}).encode("utf-8")
            self._send_response(200, "application/json", payload, method)
            return

        if path == "/simple":
            self._send_html(200, SIMPLE_HTML, method)
            return

        if path == "/complex":
            self._send_html(200, COMPLEX_HTML, method)
            return

        if path == "/error":
            self._send_html(500, "<html><body><h1>Backend Error</h1></body></html>", method)
            return

        if path == "/large":
            self._send_html(200, LARGE_HTML, method)
            return

        if path == "/chunked":
            self._send_chunked(method)
            return

        self._send_html(404, "<html><body><h1>Not Found</h1></body></html>", method)

    def _send_headers(self, status: int, headers: dict[str, str]) -> None:
        self.wfile.write(status_line(status))
        for key, value in headers.items():
            self.wfile.write(f"{key}: {value}\r\n".encode("iso-8859-1"))
        self.wfile.write(b"\r\n")

    def _send_response(self, status: int, content_type: str, payload: bytes, method: str) -> None:
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
            "Connection": "close",
        }
        self._send_headers(status, headers)
        if method != "HEAD":
            self.wfile.write(payload)

    def _send_html(self, status: int, body: str, method: str) -> None:
        payload = body.encode("utf-8")
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(payload)),
            "Cache-Control": "public, max-age=3600",
            "ETag": '"simple-v1"',
            "Connection": "close",
        }
        self._send_headers(status, headers)
        if method != "HEAD":
            self.wfile.write(payload)

    def _send_chunked(self, method: str) -> None:
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Transfer-Encoding": "chunked",
            "Cache-Control": "public, max-age=3600",
            "Connection": "close",
        }
        self._send_headers(200, headers)
        if method == "HEAD":
            return

        for part in CHUNKED_HTML_PARTS:
            chunk = part.encode("utf-8")
            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
            self.wfile.write(chunk + b"\r\n")
            self.wfile.flush()
            time.sleep(0.05)

        self.wfile.write(b"0\r\n\r\n")


class ThreadingTLSServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], tls_ctx: ssl.SSLContext):
        self._tls_ctx = tls_ctx
        super().__init__(server_address, TLSBackendHandler)

    def get_request(self):
        sock, addr = super().get_request()
        tls_sock = self._tls_ctx.wrap_socket(sock, server_side=True)
        return tls_sock, addr


def main():
    parser = argparse.ArgumentParser(description="Test backend for NGINX markdown E2E")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--tls-cert", required=True, help="Path to TLS certificate file")
    parser.add_argument("--tls-key", required=True, help="Path to TLS private key file")
    args = parser.parse_args()

    tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    tls_ctx.load_cert_chain(certfile=args.tls_cert, keyfile=args.tls_key)

    server = ThreadingTLSServer(("127.0.0.1", args.port), tls_ctx)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
