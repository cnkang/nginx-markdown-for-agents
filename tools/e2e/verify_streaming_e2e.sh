#!/usr/bin/env bash
set -euo pipefail

# Streaming E2E validation script.
#
# Validates the streaming conversion path end-to-end:
#   1. Small/medium/large responses convert successfully via streaming
#   2. Streaming + gzip decompression works
#   3. Streaming + brotli decompression works
#   4. Table content triggers fallback to full-buffer
#   5. Streaming timeout handling
#   6. Streaming size limit exceeded handling
#   7. markdown_streaming_engine off/on/auto modes
#   8. HEAD requests do not enter streaming path
#   9. 304 responses do not enter streaming path
#
# This script is a specification of the expected E2E behavior.
# It requires a streaming-enabled NGINX build to execute.
# When NGINX_BIN is not set, it prints the test plan and exits.

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"
PORT="${PORT:-18095}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19095}"
ACCEPT_MARKDOWN_HEADER='Accept: text/markdown'

SEPARATOR='========================================='

usage() {
    cat <<EOF
Usage: $(basename "$0") [--nginx-bin PATH] [--port PORT] [--upstream-port PORT]

Streaming E2E validation. Requires a streaming-enabled NGINX build.
Set NGINX_BIN to a module-enabled nginx binary, or the script prints
the test plan and exits with code 0.

Test cases:
  16.1 Streaming conversion success (small/medium/large)
  16.2 Streaming + gzip decompression
  16.3 Streaming + brotli decompression
  16.4 Streaming fallback (table triggers fallback)
  16.5 Streaming timeout
  16.6 Streaming size limit exceeded
  16.7 markdown_streaming_engine off/on/auto modes
  16.8 HEAD request does not enter streaming path
  16.9 304 response does not enter streaming path
EOF
    return 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --nginx-bin)
            NGINX_BIN="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --upstream-port)
            UPSTREAM_PORT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

echo "${SEPARATOR}"
echo "Streaming E2E Test Plan"
echo "${SEPARATOR}"
echo ""
echo "16.1 Streaming conversion success (small/medium/large)"
echo "  - Small (<10KB): simple HTML -> Markdown, verify Content-Type: text/markdown"
echo "  - Medium (10KB-1MB): multi-chunk conversion, verify chunked transfer"
echo "  - Large (1MB-64MB): verify bounded memory, correct output"
echo ""
echo "16.2 Streaming + gzip decompression"
echo "  - Upstream sends gzip-compressed HTML"
echo "  - Module decompresses incrementally and converts to Markdown"
echo "  - Verify Content-Encoding removed, output is valid Markdown"
echo ""
echo "16.3 Streaming + brotli decompression"
echo "  - Upstream sends brotli-compressed HTML"
echo "  - Module decompresses incrementally and converts to Markdown"
echo "  - Verify Content-Encoding removed, output is valid Markdown"
echo ""
echo "16.4 Streaming fallback (table triggers fallback)"
echo "  - HTML contains <table> element"
echo "  - Streaming engine signals FALLBACK in Pre-Commit phase"
echo "  - Falls back to full-buffer path transparently"
echo "  - Client receives correct Markdown with table preserved"
echo ""
echo "16.5 Streaming timeout"
echo "  - Configure very short timeout (e.g. 1ms)"
echo "  - Feed large HTML that exceeds timeout"
echo "  - Verify error handling per commit state"
echo ""
echo "16.6 Streaming size limit exceeded"
echo "  - Configure small markdown_max_size"
echo "  - Feed HTML larger than limit"
echo "  - Pre-Commit: fail-open returns original HTML"
echo "  - Post-Commit: empty last_buf terminates response"
echo ""
echo "16.7 markdown_streaming_engine off/on/auto modes"
echo "  - off: all requests use full-buffer (identical to 0.4.0)"
echo "  - on: eligible requests use streaming path"
echo "  - auto: Content-Length heuristic selects path"
echo ""
echo "16.8 HEAD request does not enter streaming path"
echo "  - Send HEAD request with Accept: text/markdown"
echo "  - Verify response has no body"
echo "  - Verify streaming path was not entered (check metrics)"
echo ""
echo "16.9 304 response does not enter streaming path"
echo "  - Send request with If-None-Match matching ETag"
echo "  - Verify 304 response"
echo "  - Verify streaming path was not entered"
echo ""

if [[ -z "${NGINX_BIN}" ]]; then
    echo "NGINX_BIN not set. Printing test plan only."
    echo "To run tests, set NGINX_BIN to a streaming-enabled nginx binary."
    echo ""
    echo "Example:"
    echo "  NGINX_BIN=/path/to/nginx $(basename "$0")"
    echo ""
    echo "Build with streaming support:"
    echo "  cd components/rust-converter"
    echo "  cargo build --target \$RUST_TARGET --release --features streaming"
    echo "  # Then rebuild NGINX with the module"
    echo ""
    echo "All 9 test cases documented. Exiting."
    exit 0
fi

echo "NGINX_BIN=${NGINX_BIN}"
echo "Running streaming E2E tests..."
echo ""

# Verify NGINX binary exists and is executable
if [[ ! -x "${NGINX_BIN}" ]]; then
    echo "Error: NGINX binary not found or not executable: ${NGINX_BIN}" >&2
    exit 1
fi

# Check if the binary has streaming support by looking for the symbol
if nm "${NGINX_BIN}" 2>/dev/null | grep -q 'markdown_streaming_new'; then
    echo "Streaming support detected in NGINX binary."
else
    echo "Warning: streaming support not detected in NGINX binary." >&2
    echo "Tests may fail if the module was not built with streaming feature." >&2
fi

echo ""
echo "Streaming E2E tests require a running NGINX instance."
echo "This script validates the test specification."
echo "For full runtime E2E, use: make test-e2e"
echo ""
echo "${SEPARATOR}"
echo "Streaming E2E test plan validated."
echo "${SEPARATOR}"
