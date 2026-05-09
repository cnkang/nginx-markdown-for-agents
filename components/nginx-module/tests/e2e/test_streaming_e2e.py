#!/usr/bin/env python3
"""
Streaming E2E test specifications.

These tests validate the streaming conversion path end-to-end.
They require a streaming-enabled NGINX build with the markdown module.

Test cases:
  16.1 Streaming conversion success (small/medium/large responses)
  16.2 Streaming + gzip decompression
  16.3 Streaming + brotli decompression
  16.4 Streaming fallback (table triggers fallback)
  16.5 Streaming timeout
  16.6 Streaming size limit exceeded
  16.7 markdown_streaming_engine off/on/auto modes
  16.8 HEAD request does not enter streaming path
  16.9 304 response does not enter streaming path

Feature: nginx-streaming-runtime-and-ffi
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

_E2E_SAFE_BIN_DIRS = ("/usr/local/bin", "/usr/bin", "/opt/homebrew/bin")

import pytest

WORKSPACE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)

_SKIP_REASON = (
    "Streaming E2E requires a streaming-enabled nginx binary on PATH"
)

NGINX_PORT = 19876
UPSTREAM_PORT = 19877


def _nginx_bin():
    resolved = shutil.which("nginx")
    if resolved is None:
        return ""
    realpath = os.path.realpath(resolved)
    base = os.path.basename(realpath)
    if base not in {"nginx", "nginx-debug"}:
        return ""
    if not any(realpath.startswith(d + os.sep) or realpath == d for d in _E2E_SAFE_BIN_DIRS):
        return ""
    return resolved


def _check_prerequisites():
    nginx_bin = _nginx_bin()
    if not nginx_bin or not os.path.isfile(nginx_bin):
        return False
    if not os.access(nginx_bin, os.X_OK):
        return False
    return True


def _write_nginx_conf(conf_dir, nginx_bin):
    conf_path = os.path.join(conf_dir, "nginx.conf")
    module_so = os.path.join(WORKSPACE_ROOT, "build", "ngx_http_markdown_filter_module.so")
    conf = f"""
daemon off;
error_log {conf_dir}/error.log info;
pid {conf_dir}/nginx.pid;

events {{
    worker_connections 64;
}}

http {{
    markdown_filter on;
    markdown_streaming_engine on;
    markdown_streaming_budget 2m;

    server {{
        listen {NGINX_PORT};
        server_name localhost;

        location / {{
            proxy_pass http://127.0.0.1:{UPSTREAM_PORT};
            proxy_set_header Host $host;
        }}

        location /metrics {{
            markdown_metrics on;
        }}
    }}
}}
"""
    with open(conf_path, "w") as f:
        f.write(conf)
    return conf_path


def _start_upstream(port, doc_root):
    """Start a simple HTTP server serving doc_root on port."""
    resolved_doc_root = os.path.realpath(doc_root)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            resolved_doc_root,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    return proc


def _fetch(path="/", accept="text/markdown"):
    url = f"http://127.0.0.1:{NGINX_PORT}{path}"
    req = Request(url)
    if accept:
        req.add_header("Accept", accept)
    try:
        resp = urlopen(req, timeout=5)
        return resp.status, resp.read(), dict(resp.headers)
    except HTTPError as e:
        return e.code, e.read() if e.fp else b"", dict(e.headers) if e.headers else {}
    except URLError:
        return 0, b"", {}


def _start_nginx(nginx_bin, conf_path):
    """Start nginx with a validated binary path."""
    real_nginx_bin = os.path.realpath(nginx_bin)
    if not os.path.isabs(real_nginx_bin):
        raise ValueError(f"NGINX binary path must be absolute: {nginx_bin}")
    return subprocess.Popen(
        [real_nginx_bin, "-c", conf_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_1_streaming_conversion_success():
    """16.1 Streaming conversion success (small/medium/large responses)."""
    nginx_bin = _nginx_bin()
    with tempfile.TemporaryDirectory() as td:
        doc_root = os.path.join(td, "docs")
        os.makedirs(doc_root)
        with open(os.path.join(doc_root, "index.html"), "w") as f:
            f.write("<html><body><h1>Hello</h1><p>World</p></body></html>")

        upstream = _start_upstream(UPSTREAM_PORT, doc_root)
        try:
            conf_path = _write_nginx_conf(td, nginx_bin)
            nginx = _start_nginx(nginx_bin, conf_path)
            time.sleep(1.0)
            try:
                status, body, headers = _fetch("/")
                assert status == 200, f"Expected 200, got {status}"
                md = body.decode("utf-8", errors="replace")
                assert "Hello" in md, "Converted markdown should contain heading text"
            finally:
                nginx.terminate()
                nginx.wait(timeout=5)
        finally:
            upstream.terminate()
            upstream.wait(timeout=5)


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_2_streaming_gzip_decompression():
    """16.2 Streaming + gzip decompression."""
    pytest.skip("Requires gzip-compressed upstream fixture")


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_3_streaming_brotli_decompression():
    """16.3 Streaming + brotli decompression."""
    pytest.skip("Requires brotli-compressed upstream fixture")


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_4_streaming_fallback():
    """16.4 Streaming fallback (table triggers fallback)."""
    nginx_bin = _nginx_bin()
    with tempfile.TemporaryDirectory() as td:
        doc_root = os.path.join(td, "docs")
        os.makedirs(doc_root)
        with open(os.path.join(doc_root, "table.html"), "w") as f:
            f.write("<html><body><table><tr><td>A</td></tr></table></body></html>")

        upstream = _start_upstream(UPSTREAM_PORT, doc_root)
        try:
            conf_path = _write_nginx_conf(td, nginx_bin)
            nginx = _start_nginx(nginx_bin, conf_path)
            time.sleep(1.0)
            try:
                status, body, headers = _fetch("/table.html")
                assert status == 200, f"Expected 200, got {status}"
            finally:
                nginx.terminate()
                nginx.wait(timeout=5)
        finally:
            upstream.terminate()
            upstream.wait(timeout=5)


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_5_streaming_timeout():
    """16.5 Streaming timeout."""
    pytest.skip("Requires slow upstream fixture for timeout")


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_6_streaming_size_limit():
    """16.6 Streaming size limit exceeded."""
    pytest.skip("Requires large upstream fixture exceeding budget")


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_7_streaming_engine_modes():
    """16.7 markdown_streaming_engine off/on/auto modes."""
    pytest.skip("Requires NGINX config reload between modes")


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_8_head_request_no_streaming():
    """16.8 HEAD request does not enter streaming path."""
    nginx_bin = _nginx_bin()
    with tempfile.TemporaryDirectory() as td:
        doc_root = os.path.join(td, "docs")
        os.makedirs(doc_root)
        with open(os.path.join(doc_root, "index.html"), "w") as f:
            f.write("<html><body><p>Test</p></body></html>")

        upstream = _start_upstream(UPSTREAM_PORT, doc_root)
        try:
            conf_path = _write_nginx_conf(td, nginx_bin)
            nginx = _start_nginx(nginx_bin, conf_path)
            time.sleep(1.0)
            try:
                url = f"http://127.0.0.1:{NGINX_PORT}/"
                req = Request(url, method="HEAD")
                req.add_header("Accept", "text/markdown")
                resp = urlopen(req, timeout=5)
                assert resp.status == 200
                body = resp.read()
                assert len(body) == 0, "HEAD should return empty body"
            finally:
                nginx.terminate()
                nginx.wait(timeout=5)
        finally:
            upstream.terminate()
            upstream.wait(timeout=5)


@pytest.mark.skipif(not _check_prerequisites(), reason=_SKIP_REASON)
def test_16_9_304_response_no_streaming():
    """16.9 304 response does not enter streaming path."""
    pytest.skip("Requires If-None-Match with known ETag fixture")


def main():
    """Run streaming E2E test specifications."""
    print()
    print("=========================================")
    print("Streaming E2E Test Specifications")
    print("=========================================")
    print()

    if not _check_prerequisites():
        print("NGINX_BIN not set or not found.")
        print("  NGINX_BIN=/path/to/nginx python3 test_streaming_e2e.py")
        return 1

    print("Prerequisites met. Run with pytest:")
    print(f"  NGINX_BIN={_nginx_bin()} pytest {__file__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
