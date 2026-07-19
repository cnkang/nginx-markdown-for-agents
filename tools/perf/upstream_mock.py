#!/usr/bin/env python3
"""Lightweight mock upstream server for performance benchmarking.

Supports serving test corpus fixtures with configurable compression (gzip, deflate,
brotli) and transfer encodings (identity with Content-Length, or chunked).
"""


from __future__ import annotations

import contextlib
import gzip
import http.server
import os
import subprocess
import sys
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.lib.path_validation import validate_read_path

# Brotli compression support: prefer the Python module, fall back to CLI.
_brotli_mod = None
_brotli_cli: str | None = None
try:
    import brotli as _brotli_mod  # type: ignore[no-redef]
except ImportError:
    import shutil

    _brotli_cli = shutil.which("brotli")


def _compress_brotli(data: bytes) -> bytes | None:
    """Compress data with Brotli, using Python module or CLI fallback.

    Returns None if neither the Python brotli module nor the brotli CLI
    is available.
    """
    if _brotli_mod is not None:
        return _brotli_mod.compress(data, quality=6)  # type: ignore[union-attr]
    if _brotli_cli is not None:
        result = subprocess.run(
            [_brotli_cli, "--quality=6", "-"],
            input=data,
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    return None


def _iter_brotli_chunks(data: bytes, chunk_size: int) -> list[bytes]:
    """Compress one Brotli stream with bounded decompression bursts."""
    if _brotli_mod is None:
        raise RuntimeError(
            "streaming Brotli benchmarks require the Brotli Python package"
        )

    compressor = _brotli_mod.Compressor(quality=6)
    chunks = []
    for offset in range(0, len(data), chunk_size):
        encoded = compressor.process(data[offset : offset + chunk_size])
        encoded += compressor.flush()
        if encoded:
            chunks.append(encoded)
    tail = compressor.finish()
    if tail:
        chunks.append(tail)
    return chunks


class MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler with dynamic chunking, gzip, deflate, and Brotli encoding support."""

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
        is_brotli = "brotli" in path_str or "brotli" in query
        is_chunked = "chunked" in path_str or "chunked" in query

        self.protocol_version = "HTTP/1.1"

        if is_chunked:
            if is_gzip:
                content_encoding = "gzip"
            elif is_deflate:
                content_encoding = "deflate"
            elif is_brotli:
                content_encoding = "br"
            else:
                content_encoding = None
            self._send_chunked_response(body, content_encoding)
        else:
            body, content_encoding = self._apply_compression(
                body, is_gzip, is_deflate, is_brotli
            )
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
        self, body: bytes, is_gzip: bool, is_deflate: bool, is_brotli: bool
    ) -> tuple[bytes, str | None]:
        """Apply requested compression codec to response body.

        For Content-Encoding: deflate, RFC 9110 defines the format as
        zlib-wrapped deflate (RFC 1950).  The streaming decompressor
        sniffs the first 2 bytes to distinguish zlib-wrapped from raw
        deflate, so we send the standard-compliant zlib-wrapped form
        here.

        For Content-Encoding: br, RFC 7932 Brotli is used.  Compression
        uses the Python brotli module when available; otherwise falls back
        to the brotli CLI.
        """
        if is_gzip:
            return gzip.compress(body), "gzip"
        if is_deflate:
            # zlib-wrapped deflate (RFC 1950, RFC 9110-compliant)
            compressor = zlib.compressobj(wbits=zlib.MAX_WBITS)
            compressed = compressor.compress(body) + compressor.flush()
            return compressed, "deflate"
        if is_brotli:
            compressed = _compress_brotli(body)
            if compressed is not None:
                return compressed, "br"
            # Brotli unavailable — fall through to identity
        return body, None

    def _send_chunked_response(self, body: bytes, content_encoding: str | None) -> None:
        """Send chunked HTTP response."""
        self._send_common_headers(content_encoding)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        for chunk in self._iter_chunked_body(body, content_encoding):
            self.wfile.write(f"{len(chunk):x}\r\n".encode())
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")

    @staticmethod
    def _iter_chunked_body(
        body: bytes, content_encoding: str | None
    ) -> list[bytes]:
        """Return wire chunks with bounded decompressed production bursts."""
        chunk_size = 16 * 1024
        if content_encoding is None:
            return MockUpstreamHandler._split_chunks(body, chunk_size)

        if content_encoding == "br":
            return _iter_brotli_chunks(body, chunk_size)

        return MockUpstreamHandler._iter_zlib_chunks(
            body, content_encoding, chunk_size
        )

    @staticmethod
    def _split_chunks(body: bytes, chunk_size: int) -> list[bytes]:
        """Split bytes into fixed-size wire chunks."""
        return [
            body[offset : offset + chunk_size]
            for offset in range(0, len(body), chunk_size)
        ]

    @staticmethod
    def _iter_zlib_chunks(
        body: bytes, content_encoding: str, chunk_size: int
    ) -> list[bytes]:
        """Compress gzip/deflate input while preserving streaming bursts."""

        wbits = (
            zlib.MAX_WBITS | 16
            if content_encoding == "gzip"
            else zlib.MAX_WBITS
        )
        compressor = zlib.compressobj(wbits=wbits)
        chunks = []
        for offset in range(0, len(body), chunk_size):
            encoded = compressor.compress(body[offset : offset + chunk_size])
            encoded += compressor.flush(zlib.Z_SYNC_FLUSH)
            if encoded:
                chunks.append(encoded)
        tail = compressor.flush(zlib.Z_FINISH)
        if tail:
            chunks.append(tail)
        return chunks

    def _send_identity_response(
        self, body: bytes, content_encoding: str | None
    ) -> None:
        """Send standard identity HTTP response with Content-Length."""
        self._send_common_headers(content_encoding)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_common_headers(self, content_encoding: str | None) -> None:
        """Send status line and shared headers common to all response modes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)


def main() -> None:
    """Start the mock upstream server on the designated port."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 19100
    server_address = ("127.0.0.1", port)
    httpd = http.server.ThreadingHTTPServer(server_address, MockUpstreamHandler)
    sys.stdout.write(f"Mock upstream starting on port {port}\n")
    sys.stdout.flush()
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()  # NOSONAR(S5332) local benchmark fixture binds 127.0.0.1


if __name__ == "__main__":
    main()
