#!/usr/bin/env python3
"""Lightweight mock upstream server for performance benchmarking.

Supports serving test corpus fixtures with configurable compression (gzip, deflate)
and transfer encodings (identity with Content-Length, or chunked).
"""

from __future__ import annotations

import gzip
import http.server
import os
import sys
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.lib.path_validation import validate_read_path


class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler with dynamic chunking, gzip, and deflate encoding support."""

    def log_message(self, format: str, *args: object) -> None:
        """Silence standard request logging to clean terminal output."""
        pass

    def do_GET(self) -> None:
        """Handle GET requests dynamically serving corpus files."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path_str = parsed.path.lstrip("/")

        file_path = self._resolve_and_verify_path(path_str)
        if file_path is None or not file_path.is_file():
            self.send_error(404, "File not found")
            return

        body = file_path.read_bytes()

        # Determine transport settings
        is_gzip = "gzip" in path_str or "gzip" in query
        is_deflate = "deflate" in path_str or "deflate" in query
        is_chunked = "chunked" in path_str or "chunked" in query

        # Apply compression if requested
        body, content_encoding = self._apply_compression(body, is_gzip, is_deflate)

        self.protocol_version = "HTTP/1.1"

        if is_chunked:
            self._send_chunked_response(body, content_encoding)
        else:
            self._send_identity_response(body, content_encoding)

    def _resolve_and_verify_path(self, path_str: str) -> Path | None:
        """Resolve and securely validate request path."""
        corpus_dir = Path(os.environ.get("CORPUS_DIR", "tests/corpus")).resolve()
        try:
            file_path = (corpus_dir / path_str).resolve()
            validate_read_path(str(file_path), purpose="corpus file")
            if corpus_dir not in file_path.parents and file_path != corpus_dir:
                return None
            return file_path
        except Exception:
            return None

    def _apply_compression(
        self, body: bytes, is_gzip: bool, is_deflate: bool
    ) -> tuple[bytes, str | None]:
        """Apply requested compression codec to response body."""
        if is_gzip:
            return gzip.compress(body), "gzip"
        if is_deflate:
            compressor = zlib.compressobj(wbits=-15)
            compressed = compressor.compress(body) + compressor.flush()
            return compressed, "deflate"
        return body, None

    def _send_chunked_response(self, body: bytes, content_encoding: str | None) -> None:
        """Send chunked HTTP response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        if body:
            self.wfile.write(f"{len(body):x}\r\n".encode())
            self.wfile.write(body)
            self.wfile.write(b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def _send_identity_response(
        self, body: bytes, content_encoding: str | None
    ) -> None:
        """Send standard identity HTTP response with Content-Length."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Start the mock upstream server on the designated port."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 19100
    server_address = ("127.0.0.1", port)
    httpd = http.server.HTTPServer(server_address, MockUpstreamHandler)
    sys.stdout.write(f"Mock upstream starting on port {port}\n")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
