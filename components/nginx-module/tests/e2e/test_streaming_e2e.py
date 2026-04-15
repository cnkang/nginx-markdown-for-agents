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
import sys

import pytest

WORKSPACE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)

_SKIP_REASON = (
    "Streaming E2E harness not yet implemented — "
    "requires a streaming-enabled NGINX build"
)


def check_prerequisites():
    """Check if streaming E2E tests can run."""
    nginx_bin = os.environ.get("NGINX_BIN", "")
    if not nginx_bin or not os.path.isfile(nginx_bin):
        return False
    return True


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_1_streaming_conversion_success():
    """16.1 Streaming conversion success (small/medium/large responses)."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_2_streaming_gzip_decompression():
    """16.2 Streaming + gzip decompression."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_3_streaming_brotli_decompression():
    """16.3 Streaming + brotli decompression."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_4_streaming_fallback():
    """16.4 Streaming fallback (table triggers fallback)."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_5_streaming_timeout():
    """16.5 Streaming timeout."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_6_streaming_size_limit():
    """16.6 Streaming size limit exceeded."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_7_streaming_engine_modes():
    """16.7 markdown_streaming_engine off/on/auto modes."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_8_head_request_no_streaming():
    """16.8 HEAD request does not enter streaming path."""


@pytest.mark.skip(reason=_SKIP_REASON)
def test_16_9_304_response_no_streaming():
    """16.9 304 response does not enter streaming path."""


def main():
    """Run streaming E2E test specifications."""
    print()
    print("=========================================")
    print("Streaming E2E Test Specifications")
    print("=========================================")
    print()
    print("All 9 streaming E2E tests are marked as skipped (spec only).")
    print("To implement, build NGINX with streaming support and add assertions.")
    print()

    if not check_prerequisites():
        print("NGINX_BIN not set or not found.")
        print("  cargo build --features streaming --release")
        print("  NGINX_BIN=/path/to/nginx python3 test_streaming_e2e.py")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
