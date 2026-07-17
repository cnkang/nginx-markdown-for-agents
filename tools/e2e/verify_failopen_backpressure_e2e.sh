#!/usr/bin/env bash
set -euo pipefail

# Fail-open passthrough + downstream backpressure (NGX_AGAIN) + resume E2E test.
#
# Validates that when streaming conversion fails and the module enters
# fail-open passthrough mode, the original HTML response is correctly
# delivered even when the downstream connection applies backpressure
# (causing NGX_AGAIN returns from the output filter chain).
#
# Test scenarios:
#   11.1  Fail-open passthrough delivers complete HTML body
#   11.2  Fail-open with large response (exercises pending_output chain)
#   11.3  Fail-open preserves original Content-Type and Content-Length
#   11.4  Fail-open with slow client (simulates backpressure via --limit-rate)
#   11.5  Fail-open byte-for-byte integrity under backpressure
#
# When NGINX_BIN is not set, exits with code 1 unless --plan is specified.

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18097}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19097}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
MARKDOWN_MAX_SIZE="${MARKDOWN_MAX_SIZE:-512}"

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
UPSTREAM_PID=""
ACCEPT_MARKDOWN_HEADER='Accept: text/markdown'
SEPARATOR='========================================='
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
PLAN_ONLY=0

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"
# shellcheck disable=SC1090
source "$(dirname "${BASH_SOURCE[0]}")/e2e_common.sh"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-bin PATH]
                          [--nginx-version VERSION] [--port PORT]
                          [--upstream-port PORT] [--markdown-max-size SIZE]
                          [--plan]

Fail-open backpressure E2E validation. Requires a streaming-enabled NGINX build.
Set NGINX_BIN to reuse an existing module-enabled nginx binary.
When NGINX_BIN is not set, exits with code 1 unless --plan is specified.

Options:
  --keep-artifacts         Keep build artifacts after test
  --nginx-bin PATH         Path to streaming-enabled nginx binary
  --nginx-version VERSION  NGINX version to use (default: ${NGINX_VERSION})
  --plan                   Print test plan and exit 0 (no NGINX_BIN required)
  --port PORT              NGINX listen port (default: ${PORT})
  --upstream-port PORT     Upstream server port (default: ${UPSTREAM_PORT})
  --markdown-max-size SIZE markdown_limits memory value (default: ${MARKDOWN_MAX_SIZE})
  -h, --help               Show this help
EOF
    return 0
}

print_plan() {
    cat >&2 <<EOF
Test Plan: Fail-open Backpressure E2E
  11.1  Fail-open passthrough delivers complete HTML body
  11.2  Fail-open with large response (exercises pending_output chain)
  11.3  Fail-open preserves original Content-Type and Content-Length
  11.4  Fail-open with slow client (simulates backpressure via --limit-rate)
  11.5  Fail-open byte-for-byte integrity under backpressure
EOF
    return 0
}

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS] %s\n' "$1" >&2
    return 0
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[FAIL] %s\n' "$1" >&2
    return 0
}

skip() {
    SKIP_COUNT=$((SKIP_COUNT + 1))
    printf '[SKIP] %s\n' "$1" >&2
    return 0
}

validate_numeric() {
    local name="$1" value="$2"
    if ! printf '%s' "${value}" | grep -qE '^[0-9]+$'; then
        echo "ERROR: ${name} must be a positive integer, got: '${value}'" >&2
        usage
        exit 1
    fi
    return 0
}

cleanup() {
    if [[ -n "${UPSTREAM_PID}" ]] && kill -0 "${UPSTREAM_PID}" 2>/dev/null; then
        kill "${UPSTREAM_PID}" 2>/dev/null || true
        wait "${UPSTREAM_PID}" 2>/dev/null || true
    fi
    if [[ -n "${NGINX_EXECUTABLE}" ]] && [[ -n "${RUNTIME}" ]]; then
        "${NGINX_EXECUTABLE}" -s stop -p "${RUNTIME}" 2>/dev/null || true
    fi
    if [[ "${KEEP_ARTIFACTS}" -eq 0 ]] && [[ -n "${RUNTIME}" ]] && [[ -d "${RUNTIME}" ]]; then
        rm -rf "${RUNTIME}"
    fi
    return 0
}
trap cleanup EXIT

# Parse arguments with validation
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-artifacts)   KEEP_ARTIFACTS=1; shift ;;
        --nginx-bin)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --nginx-bin requires an argument" >&2; exit 1
            fi
            NGINX_BIN="$2"; shift 2
            ;;
        --nginx-version)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --nginx-version requires an argument" >&2; exit 1
            fi
            NGINX_VERSION="$2"; shift 2
            ;;
        --plan)             PLAN_ONLY=1; shift ;;
        --port)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --port requires an argument" >&2; exit 1
            fi
            validate_numeric "--port" "$2"
            PORT="$2"; shift 2
            ;;
        --upstream-port)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --upstream-port requires an argument" >&2; exit 1
            fi
            validate_numeric "--upstream-port" "$2"
            UPSTREAM_PORT="$2"; shift 2
            ;;
        --markdown-max-size)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --markdown-max-size requires an argument" >&2; exit 1
            fi
            validate_numeric "--markdown-max-size" "$2"
            MARKDOWN_MAX_SIZE="$2"; shift 2
            ;;
        -h|--help)          usage; exit 0 ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ "${PLAN_ONLY}" -eq 1 ]]; then
    print_plan
    exit 0
fi

if [[ -z "${NGINX_BIN}" ]]; then
    echo "ERROR: NGINX_BIN is required (set via --nginx-bin or NGINX_BIN env)" >&2
    exit 1
fi

if [[ ! -x "${NGINX_BIN}" ]]; then
    echo "ERROR: NGINX_BIN is not executable: ${NGINX_BIN}" >&2
    exit 1
fi

NGINX_EXECUTABLE="${NGINX_BIN}"

# ─────────────────────────────────────────────────────────────────────────────
# Setup: Create runtime directory and upstream server
# ─────────────────────────────────────────────────────────────────────────────

RUNTIME="$(mktemp -d "${TMPDIR:-/tmp}/failopen-e2e.XXXXXXXX")"
mkdir -p "${RUNTIME}/logs" "${RUNTIME}/html" "${RUNTIME}/conf"

# Generate a large HTML file that exceeds markdown_limits memory to trigger fail-open
LARGE_HTML_SIZE=2048
python3 -c "
import sys
body = '<h1>Title</h1>' + '<p>' + 'x' * 200 + '</p>\n' * 20
while len(body) < ${LARGE_HTML_SIZE}:
    body += '<p>padding paragraph content here</p>\n'
sys.stdout.write(body)
" > "${RUNTIME}/html/large.html"

# Small HTML for basic fail-open test (still exceeds our tiny max_size of 512)
cat > "${RUNTIME}/html/failopen.html" <<'HTML'
<!DOCTYPE html>
<html>
<head><title>Fail-open Test</title></head>
<body>
<h1>Fail-open Test Page</h1>
<p>This page has enough content to exceed the configured markdown_limits memory
limit, which will trigger the fail-open passthrough path in the streaming
engine. The module should deliver the original HTML unchanged.</p>
<p>Additional paragraph to ensure we exceed the budget limit configured
for this test scenario. More content follows to pad the response.</p>
<p>Third paragraph with more padding content to ensure the response body
is large enough to trigger the budget-exceeded pre-commit error path.</p>
<p>Fourth paragraph. The streaming engine should detect that the response
exceeds markdown_limits memory and fall back to passthrough mode.</p>
<p>Fifth paragraph. After fail-open, the original HTML must be delivered
intact without any truncation or data corruption.</p>
</body>
</html>
HTML

FAILOPEN_SIZE="$(wc -c < "${RUNTIME}/html/failopen.html" | tr -d ' ')"

# Start a simple upstream HTTP server using Python
python3 -c "
import http.server, socketserver, os, sys

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory='${RUNTIME}/html', **kwargs)

    def log_message(self, format, *args):
        pass

PORT = ${UPSTREAM_PORT}
with socketserver.TCPServer(('127.0.0.1', PORT), Handler) as httpd:
    httpd.serve_forever()
" &
UPSTREAM_PID=$!
sleep 1

# Verify upstream is running
if ! kill -0 "${UPSTREAM_PID}" 2>/dev/null; then
    echo "ERROR: Upstream server failed to start" >&2
    exit 1
fi

# Determine module load directive — prefer well-known path, fall back to find
MODULE_PATH="$(dirname "${NGINX_BIN}")/../modules/ngx_http_markdown_filter_module.so"
if [[ ! -f "${MODULE_PATH}" ]]; then
    # Use null-delimited find and validate the result
    MODULE_PATH=""
    while IFS= read -r -d '' candidate; do
        if [[ -f "${candidate}" ]]; then
            MODULE_PATH="${candidate}"
            break
        fi
    done < <(find "$(dirname "${NGINX_BIN}")/.." -name 'ngx_http_markdown_filter_module.so' -print0 2>/dev/null)
fi

if [[ -z "${MODULE_PATH}" ]] || [[ ! -f "${MODULE_PATH}" ]]; then
    echo "ERROR: Cannot find ngx_http_markdown_filter_module.so" >&2
    exit 1
fi

LOAD_MODULE_LINE="load_module ${MODULE_PATH};"

# Write NGINX config with small markdown_limits memory to force fail-open
cat > "${RUNTIME}/conf/nginx.conf" <<EOF
worker_processes 1;
error_log ${RUNTIME}/logs/error.log debug;
pid ${RUNTIME}/logs/nginx.pid;

${LOAD_MODULE_LINE}

events {
    worker_connections 64;
}

http {
    access_log ${RUNTIME}/logs/access.log;

    server {
        listen ${PORT};
        server_name localhost;

        location / {
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT};
            proxy_http_version 1.1;

            markdown_filter on;
            markdown_limits memory=${MARKDOWN_MAX_SIZE} timeout=120s;
            markdown_error_policy pass;
            markdown_streaming force;
        }
    }
}
EOF

# Start NGINX
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c "${RUNTIME}/conf/nginx.conf" -t 2>/dev/null
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c "${RUNTIME}/conf/nginx.conf"
sleep 1

echo "${SEPARATOR}" >&2
echo "Fail-open Backpressure E2E Tests" >&2
echo "${SEPARATOR}" >&2

# ─────────────────────────────────────────────────────────────────────────────
# Test 11.1: Fail-open passthrough delivers complete HTML body
# ─────────────────────────────────────────────────────────────────────────────
echo "" >&2
echo "--- 11.1: Fail-open passthrough delivers complete HTML body ---" >&2

RESPONSE_FILE="${RUNTIME}/response_11_1.txt"
HEADERS_FILE="${RUNTIME}/headers_11_1.txt"

HTTP_CODE="$(curl -sS -o "${RESPONSE_FILE}" -D "${HEADERS_FILE}" \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    --max-time 10 \
    -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/failopen.html")"

if [[ "${HTTP_CODE}" == "200" ]]; then
    RESPONSE_SIZE="$(wc -c < "${RESPONSE_FILE}" | tr -d ' ')"
    if [[ "${RESPONSE_SIZE}" -ge "${FAILOPEN_SIZE}" ]] && grep -q '<h1>Fail-open Test Page</h1>' "${RESPONSE_FILE}"; then
        pass "11.1 Fail-open passthrough delivers complete HTML body (${RESPONSE_SIZE} bytes)"
    else
        fail "11.1 Response body incomplete or missing expected content (size: ${RESPONSE_SIZE})"
    fi
else
    fail "11.1 Expected HTTP 200, got ${HTTP_CODE}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 11.2: Fail-open with large response (exercises pending_output chain)
# ─────────────────────────────────────────────────────────────────────────────
echo "" >&2
echo "--- 11.2: Fail-open with large response ---" >&2

RESPONSE_FILE="${RUNTIME}/response_11_2.txt"
HEADERS_FILE="${RUNTIME}/headers_11_2.txt"

HTTP_CODE="$(curl -sS -o "${RESPONSE_FILE}" -D "${HEADERS_FILE}" \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    --max-time 10 \
    -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/large.html")"

if [[ "${HTTP_CODE}" == "200" ]]; then
    RESPONSE_SIZE="$(wc -c < "${RESPONSE_FILE}" | tr -d ' ')"
    if [[ "${RESPONSE_SIZE}" -ge "${LARGE_HTML_SIZE}" ]] && grep -q '<h1>Title</h1>' "${RESPONSE_FILE}"; then
        pass "11.2 Fail-open with large response (${RESPONSE_SIZE} bytes)"
    else
        fail "11.2 Large response incomplete: got ${RESPONSE_SIZE}, expected >= ${LARGE_HTML_SIZE}"
    fi
else
    fail "11.2 Expected HTTP 200, got ${HTTP_CODE}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 11.3: Fail-open preserves original Content-Type and Content-Length
# ─────────────────────────────────────────────────────────────────────────────
echo "" >&2
echo "--- 11.3: Fail-open preserves original Content-Type and Content-Length ---" >&2

# Verify Content-Type
if grep -qi '^Content-Type:.*text/html' "${HEADERS_FILE}"; then
    pass "11.3a Fail-open preserves Content-Type: text/html"
else
    CT_LINE="$(grep -i '^Content-Type:' "${HEADERS_FILE}" || echo "(none)")"
    fail "11.3a Expected Content-Type text/html, got: ${CT_LINE}"
fi

# Verify Content-Length matches actual body size
CL_LINE="$(grep -i '^Content-Length:' "${HEADERS_FILE}" | tr -d '\r' || true)"
if [[ -n "${CL_LINE}" ]]; then
    CL_VALUE="$(printf '%s' "${CL_LINE}" | sed 's/[^0-9]//g')"
    BODY_SIZE="$(wc -c < "${RESPONSE_FILE}" | tr -d ' ')"
    if [[ "${CL_VALUE}" == "${BODY_SIZE}" ]]; then
        pass "11.3b Fail-open preserves Content-Length (${CL_VALUE} == body size)"
    else
        fail "11.3b Content-Length mismatch: header=${CL_VALUE}, body=${BODY_SIZE}"
    fi
else
    # Content-Length may be absent if chunked transfer; not a failure
    skip "11.3b Content-Length header absent (chunked transfer)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 11.4: Fail-open with slow client (simulates backpressure)
# ─────────────────────────────────────────────────────────────────────────────
echo "" >&2
echo "--- 11.4: Fail-open with slow client (backpressure simulation) ---" >&2

RESPONSE_FILE="${RUNTIME}/response_11_4.txt"
HEADERS_FILE="${RUNTIME}/headers_11_4.txt"

# Use --limit-rate to simulate a slow client that causes backpressure
HTTP_CODE="$(curl -sS -o "${RESPONSE_FILE}" -D "${HEADERS_FILE}" \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    --limit-rate 256 \
    --max-time 30 \
    -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/large.html")"

if [[ "${HTTP_CODE}" == "200" ]]; then
    RESPONSE_SIZE="$(wc -c < "${RESPONSE_FILE}" | tr -d ' ')"
    if [[ "${RESPONSE_SIZE}" -ge "${LARGE_HTML_SIZE}" ]]; then
        pass "11.4 Fail-open with slow client delivers complete response (${RESPONSE_SIZE} bytes)"
    else
        fail "11.4 Slow client response truncated: got ${RESPONSE_SIZE}, expected >= ${LARGE_HTML_SIZE}"
    fi
else
    fail "11.4 Expected HTTP 200, got ${HTTP_CODE}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 11.5: Fail-open byte-for-byte integrity under backpressure
# Performs a single slow request and compares the response byte-for-byte
# against the upstream fixture to detect any data corruption or duplication.
# ─────────────────────────────────────────────────────────────────────────────
echo "" >&2
echo "--- 11.5: Fail-open byte-for-byte integrity under backpressure ---" >&2

RESPONSE_FILE="${RUNTIME}/response_11_5.txt"
EXPECTED_FILE="${RUNTIME}/html/large.html"

# Single slow request — forces NGINX to buffer and resume delivery
HTTP_CODE="$(curl -sS -o "${RESPONSE_FILE}" \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    --limit-rate 512 \
    --max-time 30 \
    -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/large.html")"

if [[ "${HTTP_CODE}" != "200" ]]; then
    fail "11.5 Expected HTTP 200, got ${HTTP_CODE}"
elif cmp -s "${EXPECTED_FILE}" "${RESPONSE_FILE}"; then
    pass "11.5 Fail-open response matches upstream fixture byte-for-byte"
else
    EXPECTED_SIZE="$(wc -c < "${EXPECTED_FILE}" | tr -d ' ')"
    ACTUAL_SIZE="$(wc -c < "${RESPONSE_FILE}" | tr -d ' ')"
    fail "11.5 Response differs from fixture (expected ${EXPECTED_SIZE} bytes, got ${ACTUAL_SIZE} bytes)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo "" >&2
echo "${SEPARATOR}" >&2
printf 'Results: %d passed, %d failed, %d skipped\n' \
    "${PASS_COUNT}" "${FAIL_COUNT}" "${SKIP_COUNT}" >&2
echo "${SEPARATOR}" >&2

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "FAIL: ${FAIL_COUNT} test(s) failed" >&2
    exit 1
fi

echo "OK: All fail-open backpressure tests passed" >&2
exit 0
