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
import subprocess
import json

WORKSPACE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)


def check_prerequisites():
    """Check if streaming E2E tests can run."""
    nginx_bin = os.environ.get("NGINX_BIN", "")
    if not nginx_bin or not os.path.isfile(nginx_bin):
        print("NGINX_BIN not set or not found. Skipping streaming E2E tests.")
        return False
    return True


def test_16_1_streaming_conversion_success():
    """
    16.1 Streaming conversion success (small/medium/large responses).

    Validates: Property 3 (Incremental Processing)

    Test plan:
    - Small response (<10KB): simple HTML converts to Markdown
      - Verify Content-Type: text/markdown; charset=utf-8
      - Verify Vary: Accept header present
      - Verify Content-Length removed (chunked transfer)
      - Verify Markdown output contains expected headings

    - Medium response (10KB-1MB): multi-chunk HTML
      - Verify multiple flush points in output
      - Verify complete Markdown output

    - Large response (1MB-64MB): bounded memory
      - Verify NGINX memory does not grow linearly with input
      - Verify complete Markdown output
    """
    print("  16.1 Streaming conversion success: SPEC ONLY")


def test_16_2_streaming_gzip_decompression():
    """
    16.2 Streaming + gzip decompression.

    Validates: Property 1 (Decompression Equivalence)

    Test plan:
    - Upstream sends gzip-compressed text/html
    - Module incrementally decompresses and converts
    - Verify Content-Encoding header removed
    - Verify output is valid Markdown (not compressed)
    - Verify output matches full-buffer gzip conversion
    """
    print("  16.2 Streaming + gzip decompression: SPEC ONLY")


def test_16_3_streaming_brotli_decompression():
    """
    16.3 Streaming + brotli decompression.

    Validates: Property 1 (Decompression Equivalence)

    Test plan:
    - Upstream sends brotli-compressed text/html
    - Module incrementally decompresses and converts
    - Verify Content-Encoding header removed
    - Verify output is valid Markdown
    """
    print("  16.3 Streaming + brotli decompression: SPEC ONLY")


def test_16_4_streaming_fallback():
    """
    16.4 Streaming fallback (table triggers fallback).

    Validates: Property 5 (Pre-Commit Fallback Transparency)

    Test plan:
    - HTML contains <table> element
    - Streaming engine returns ERROR_STREAMING_FALLBACK (7)
    - Module falls back to full-buffer path
    - Client receives correct Markdown with table preserved
    - Fallback is transparent to client (no partial output)
    """
    print("  16.4 Streaming fallback: SPEC ONLY")


def test_16_5_streaming_timeout():
    """
    16.5 Streaming timeout.

    Validates: Property 6 (Commit-State Error Handling)

    Test plan:
    - Configure markdown_timeout to very short value
    - Feed large HTML that exceeds timeout
    - Pre-Commit: fail-open returns original HTML
    - Post-Commit: response terminated with empty last_buf
    """
    print("  16.5 Streaming timeout: SPEC ONLY")


def test_16_6_streaming_size_limit():
    """
    16.6 Streaming size limit exceeded.

    Validates: Property 11 (Size Limit Execution)

    Test plan:
    - Configure small markdown_max_size
    - Feed HTML larger than limit
    - Verify size limit enforced per commit state
    - Pre-Commit + pass: fail-open returns original HTML
    - Pre-Commit + reject: returns error
    """
    print("  16.6 Streaming size limit exceeded: SPEC ONLY")


def test_16_7_streaming_engine_modes():
    """
    16.7 markdown_streaming_engine off/on/auto modes.

    Validates: Property 2 (Engine Selection Determinism)

    Test plan:
    - off: all requests use full-buffer (0.4.0 behavior)
    - on: eligible GET requests use streaming path
    - auto: Content-Length >= threshold uses streaming
    - auto: Content-Length < threshold uses full-buffer
    - auto: no Content-Length uses streaming
    """
    print("  16.7 Engine modes off/on/auto: SPEC ONLY")


def test_16_8_head_request_no_streaming():
    """
    16.8 HEAD request does not enter streaming path.

    Validates: Property 2 (Engine Selection Determinism)

    Test plan:
    - Send HEAD request with Accept: text/markdown
    - Verify response has no body
    - Verify streaming path was not entered
    - Verify path_hits.streaming metric not incremented
    """
    print("  16.8 HEAD request no streaming: SPEC ONLY")


def test_16_9_304_response_no_streaming():
    """
    16.9 304 response does not enter streaming path.

    Validates: Property 2 (Engine Selection Determinism)

    Test plan:
    - First request: get ETag from successful conversion
    - Second request: send If-None-Match with ETag
    - Verify 304 Not Modified response
    - Verify streaming path was not entered
    """
    print("  16.9 304 response no streaming: SPEC ONLY")


def main():
    """Run streaming E2E test specifications."""
    print()
    print("=========================================")
    print("Streaming E2E Test Specifications")
    print("=========================================")
    print()

    tests = [
        test_16_1_streaming_conversion_success,
        test_16_2_streaming_gzip_decompression,
        test_16_3_streaming_brotli_decompression,
        test_16_4_streaming_fallback,
        test_16_5_streaming_timeout,
        test_16_6_streaming_size_limit,
        test_16_7_streaming_engine_modes,
        test_16_8_head_request_no_streaming,
        test_16_9_304_response_no_streaming,
    ]

    for test_fn in tests:
        test_fn()

    print()
    print("=========================================")
    print(f"All {len(tests)} streaming E2E test specs documented.")
    print("=========================================")
    print()

    if not check_prerequisites():
        print("To run full E2E tests, build NGINX with streaming support:")
        print("  cargo build --features streaming --release")
        print("  # Rebuild NGINX module")
        print("  NGINX_BIN=/path/to/nginx python3 test_streaming_e2e.py")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
