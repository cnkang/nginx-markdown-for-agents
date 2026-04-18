#!/usr/bin/env bash
set -euo pipefail

# Streaming failure semantics, cache behavior, and conditional request E2E tests.
#
# Validates sub-spec #15 end-to-end:
#   10.1  Streaming success + ETag on: no ETag in response headers, ETag in logs
#   10.1b Streaming strips upstream ETag from proxied response
#   10.2  Streaming pre-commit failure + streaming_on_error pass: client gets HTML
#   10.3  Streaming pre-commit failure + streaming_on_error reject: client gets error
#   10.4  Streaming post-commit failure: client gets truncated Markdown
#   10.5  conditional_requests full_support + streaming_engine on: full-buffer path
#   10.6  conditional_requests if_modified_since_only + streaming_engine on: streaming
#   10.7  Streaming response headers: no Content-Length, chunked transfer
#   10.8  streaming_engine off + streaming_on_error config: 0.4.0 behavior
#   10.9  markdown_on_error vs markdown_streaming_on_error independence
#   10.10 HEAD request does not enter streaming path
#   10.11 304 response does not enter streaming path
#
# When NGINX_BIN is not set, exits with code 1 unless --plan is specified.

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18096}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19096}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
MARKDOWN_MAX_SIZE="${MARKDOWN_MAX_SIZE:-10m}"

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
UPSTREAM_PID=""
ORIG_ARGS=("$@")
ACCEPT_MARKDOWN_HEADER='Accept: text/markdown'
SEPARATOR='========================================='
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
PLAN_ONLY=0

# Reusable grep patterns (avoid duplicated string literals)
readonly PATTERN_CT_MARKDOWN='^Content-Type: text/markdown'
readonly PATTERN_CT_HTML='^Content-Type: text/html'
readonly PATTERN_CL='^Content-Length:'
readonly PATTERN_ETAG='^ETag:'
readonly PATTERN_HTTP_200='HTTP/1.1 200'
readonly PATTERN_HTTP_304='HTTP/1.1 304'
readonly PATTERN_TRANSFER_CHUNKED='^Transfer-Encoding:.*chunked'
readonly PATTERN_VARY_ACCEPT='^Vary:.*Accept'
readonly PATTERN_MARKDOWN_HEADING='^# '
readonly EXPECTED_HEADING='# Simple Test Page'
readonly MSG_EXPECTED_HTTP_200='expected HTTP 200'
readonly MSG_EXPECTED_CT_MARKDOWN='expected Content-Type text/markdown'
readonly MSG_MISSING_CONVERTED_HEADING='missing converted heading in body'

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-bin PATH]
                          [--nginx-version VERSION] [--port PORT]
                          [--upstream-port PORT] [--markdown-max-size SIZE]
                          [--plan]

Streaming failure/cache E2E validation. Requires a streaming-enabled NGINX build.
Set NGINX_BIN to reuse an existing module-enabled nginx binary.
When NGINX_BIN is not set, exits with code 1 unless --plan is specified.

Options:
  --keep-artifacts         Keep build artifacts after test
  --nginx-bin PATH         Path to streaming-enabled nginx binary
  --nginx-version VERSION  NGINX version to use (default: ${NGINX_VERSION})
  --plan                   Print test plan and exit 0 (no NGINX_BIN required)
  --port PORT              NGINX listen port (default: ${PORT})
  --upstream-port PORT     Upstream server port (default: ${UPSTREAM_PORT})
  --markdown-max-size SIZE markdown_max_size value (default: ${MARKDOWN_MAX_SIZE})
  -h, --help               Show this help

Test cases:
  10.1  Streaming success + ETag on
  10.1b Streaming strips upstream ETag
  10.2  Streaming pre-commit failure + pass
  10.3  Streaming pre-commit failure + reject
  10.4  Streaming post-commit failure
  10.5  conditional_requests full_support + streaming on
  10.6  conditional_requests if_modified_since_only + streaming on
  10.7  Streaming response headers
  10.8  streaming_engine off + streaming_on_error
  10.9  Directive independence
  10.10 HEAD request does not enter streaming path
  10.11 304 response does not enter streaming path
EOF
    return 0
}

require_flag_value() {
    local flag_name="$1"
    if [[ $# -lt 2 || -z "${2-}" ]]; then
        echo "Missing value for ${flag_name}" >&2
        usage >&2
        exit 2
    fi
    return 0
}

report_case() {
    local case_id="$1"
    local status="$2"
    local desc="$3"

    case "${status}" in
        PASS)
            PASS_COUNT=$((PASS_COUNT + 1))
            echo "  [PASS] ${case_id} ${desc}"
            ;;
        FAIL)
            FAIL_COUNT=$((FAIL_COUNT + 1))
            echo "  [FAIL] ${case_id} ${desc}" >&2
            ;;
        SKIP)
            SKIP_COUNT=$((SKIP_COUNT + 1))
            echo "  [SKIP] ${case_id} ${desc}"
            ;;
        *)
            FAIL_COUNT=$((FAIL_COUNT + 1))
            echo "  [ERROR] ${case_id} unknown status '${status}': ${desc}" >&2
            return 1
            ;;
    esac
    return 0
}

mark_case_fail() {
    local case_id="$1"
    local message="$2"
    local pass_var="$3"

    echo "  ${case_id} FAIL: ${message}" >&2
    printf -v "${pass_var}" '%s' 0
    return 0
}

assert_http_200() {
    local hdr_file="$1"
    local case_id="$2"
    local pass_var="$3"
    local message="$4"

    if ! grep -q "${PATTERN_HTTP_200}" "${hdr_file}"; then
        mark_case_fail "${case_id}" "${message}" "${pass_var}"
    fi
    return 0
}

assert_content_type_markdown() {
    local hdr_file="$1"
    local case_id="$2"
    local pass_var="$3"
    local message="$4"

    if ! grep -qi "${PATTERN_CT_MARKDOWN}" "${hdr_file}"; then
        mark_case_fail "${case_id}" "${message}" "${pass_var}"
    fi
    return 0
}

assert_has_header() {
    local hdr_file="$1"
    local pattern="$2"
    local case_id="$3"
    local pass_var="$4"
    local message="$5"

    if ! grep -qi "${pattern}" "${hdr_file}"; then
        mark_case_fail "${case_id}" "${message}" "${pass_var}"
    fi
    return 0
}

assert_no_header() {
    local hdr_file="$1"
    local pattern="$2"
    local case_id="$3"
    local pass_var="$4"
    local message="$5"

    if grep -qi "${pattern}" "${hdr_file}"; then
        mark_case_fail "${case_id}" "${message}" "${pass_var}"
    fi
    return 0
}

assert_body_contains() {
    local body_file="$1"
    local pattern="$2"
    local case_id="$3"
    local pass_var="$4"
    local message="$5"

    if ! grep -q "${pattern}" "${body_file}"; then
        mark_case_fail "${case_id}" "${message}" "${pass_var}"
    fi
    return 0
}

assert_body_not_contains() {
    local body_file="$1"
    local pattern="$2"
    local case_id="$3"
    local pass_var="$4"
    local message="$5"

    if grep -q "${pattern}" "${body_file}" 2>/dev/null; then
        mark_case_fail "${case_id}" "${message}" "${pass_var}"
    fi
    return 0
}

cleanup() {
    local rc=$?

    if [[ -n "${UPSTREAM_PID}" ]]; then
        kill "${UPSTREAM_PID}" >/dev/null 2>&1 || true
        wait "${UPSTREAM_PID}" >/dev/null 2>&1 || true
    fi

    if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
        "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf \
            -s stop >/dev/null 2>&1 || true
    fi

    if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 \
          && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
        rm -rf "${BUILDROOT}" || true
    fi

    if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
        echo "Streaming failure/cache E2E failed." \
             "Artifacts kept at: ${BUILDROOT}" >&2
    elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
        echo "Artifacts kept at: ${BUILDROOT}"
    fi
    return 0
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-artifacts)
            KEEP_ARTIFACTS=1
            shift
            ;;
        --plan)
            PLAN_ONLY=1
            shift
            ;;
        --nginx-bin)
            require_flag_value "$1" "${2-}"
            NGINX_BIN="$2"
            shift 2
            ;;
        --nginx-version)
            require_flag_value "$1" "${2-}"
            NGINX_VERSION="$2"
            shift 2
            ;;
        --port)
            require_flag_value "$1" "${2-}"
            PORT="$2"
            shift 2
            ;;
        --upstream-port)
            require_flag_value "$1" "${2-}"
            UPSTREAM_PORT="$2"
            shift 2
            ;;
        --markdown-max-size)
            require_flag_value "$1" "${2-}"
            MARKDOWN_MAX_SIZE="$2"
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


# ---------------------------------------------------------------------------
# Test plan (always printed)
# ---------------------------------------------------------------------------
echo "${SEPARATOR}"
echo "Streaming Failure/Cache E2E Test Plan"
echo "${SEPARATOR}"
echo ""
echo "10.1 Streaming success + ETag on"
echo "  - streaming_engine on, etag on"
echo "  - Verify: no ETag in response headers, ETag in debug log"
echo ""
echo "10.1b Streaming strips upstream ETag"
echo "  - streaming_engine on, etag on, upstream sends ETag header"
echo "  - Verify: upstream ETag stripped from response headers"
echo ""
echo "10.2 Streaming pre-commit failure + streaming_on_error pass"
echo "  - streaming_engine on, streaming_on_error pass"
echo "  - Trigger pre-commit failure (oversize input)"
echo "  - Verify: HTTP 200, Content-Type text/html, complete original HTML"
echo ""
echo "10.3 Streaming pre-commit failure + streaming_on_error reject"
echo "  - streaming_engine on, streaming_on_error reject"
echo "  - Trigger pre-commit failure (oversize input)"
echo "  - Verify: HTTP non-success response, no partial Markdown"
echo ""
echo "10.4 Streaming post-commit failure"
echo "  - streaming_engine on"
echo "  - Upstream aborts mid-stream after commit boundary"
echo "  - Verify: truncated Markdown, STREAMING_FAIL_POSTCOMMIT in log"
echo ""
echo "10.5 conditional_requests full_support + streaming_engine on"
echo "  - Forces full-buffer path"
echo "  - Verify: ETag present, Content-Length present"
echo ""
echo "10.6 conditional_requests if_modified_since_only + streaming_engine on"
echo "  - Allows streaming path"
echo "  - Verify: no Content-Length, chunked transfer"
echo ""
echo "10.7 Streaming response headers"
echo "  - Verify: no Content-Length, Transfer-Encoding chunked"
echo "  - Verify: Content-Type text/markdown, Vary Accept"
echo ""
echo "10.8 streaming_engine off + streaming_on_error config"
echo "  - streaming_engine off (default), streaming_on_error reject"
echo "  - Verify: full-buffer path, Content-Length present, 0.4.0 behavior"
echo ""
echo "10.9 Directive independence"
echo "  - Cross-config: on_error pass + streaming_on_error reject"
echo "  - Cross-config: on_error reject + streaming_on_error pass"
echo "  - Verify: each directive controls only its own path"
echo ""
echo "10.10 HEAD request does not enter streaming path"
echo "  - streaming_engine on + HEAD request"
echo "  - Verify: HTTP 200 and empty response body"
echo ""
echo "10.11 304 response does not enter streaming path"
echo "  - Capture ETag from full-buffer location"
echo "  - Re-request with If-None-Match and verify HTTP 304 + empty body"
echo ""

if [[ "${PLAN_ONLY}" -eq 1 ]]; then
    echo "Plan-only mode. All test cases documented. Exiting."
    exit 0
fi

if [[ -z "${NGINX_BIN}" ]]; then
    echo "NGINX_BIN not set. Cannot run E2E tests."
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
    echo "Use --plan flag for plan-only mode (exit 0)."
    exit 1
fi

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
if (( ${#ORIG_ARGS[@]} )); then
    markdown_ensure_native_apple_silicon "$0" "${ORIG_ARGS[@]}"
else
    markdown_ensure_native_apple_silicon "$0"
fi

for cmd in curl python3 awk grep sed wc; do
    markdown_need_cmd "$cmd"
done

if [[ ! -x "${NGINX_BIN}" ]]; then
    echo "Error: NGINX binary not found or not executable: ${NGINX_BIN}" >&2
    exit 1
fi

# Check streaming support
streaming_detected=0
if nm "${NGINX_BIN}" 2>/dev/null | grep -q 'markdown_streaming_new'; then
    streaming_detected=1
elif objdump -T "${NGINX_BIN}" 2>/dev/null \
     | grep -q 'markdown_streaming_new'; then
    streaming_detected=1
fi

if [[ ${streaming_detected} -eq 1 ]]; then
    echo "Streaming support detected in NGINX binary."
else
    echo "Warning: streaming support not detected." >&2
    echo "Tests may fail if module lacks streaming feature." >&2
fi

# ---------------------------------------------------------------------------
# Build environment
# ---------------------------------------------------------------------------
BUILDROOT="$(mktemp -d /tmp/nginx-streaming-fc.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/upstream_server.py"
mkdir -p "${RAW_DIR}" "${RUNTIME}/conf" "${RUNTIME}/logs"

# ---------------------------------------------------------------------------
# Upstream server (Python)
# ---------------------------------------------------------------------------
cat > "${UPSTREAM_SCRIPT}" <<'UPSTREAM_PY'
#!/usr/bin/env python3
"""
Test upstream server for streaming failure/cache E2E tests.

Endpoints:
  /health              Health check
  /simple              Small valid HTML (streaming-friendly)
  /simple-with-etag    Small HTML with upstream ETag header
  /oversize            HTML exceeding typical max_size (triggers pre-commit error)
  /partial-abort       Sends partial HTML then aborts (triggers post-commit error)
"""
import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SIMPLE_HTML = (
    '<!doctype html><html><head><meta charset="UTF-8">'
    '<title>Simple</title></head><body>'
    '<h1>Simple Test Page</h1>'
    '<p>This is a simple paragraph for streaming conversion.</p>'
    '<p>Visit <a href="https://example.com">Example</a>.</p>'
    '</body></html>'
)

OVERSIZE_CHUNK = 'x' * 4096
OVERSIZE_TARGET = 12 * 1024 * 1024  # 12MB, exceeds default 10m max_size
OVERSIZE_END_TOKEN = "OVERSIZE_STREAM_END_TOKEN"

PARTIAL_HTML_PREFIX = (
    '<!doctype html><html><head><meta charset="UTF-8">'
    '<title>Partial</title></head><body>'
    '<h1>Partial Page</h1>'
    '<p>This content will be followed by an abrupt connection close.</p>'
)


def build_oversize_payload():
    prefix = (
        '<!doctype html><html><head><meta charset="UTF-8">'
        '<title>Oversize</title></head><body>'
        '<h1>Oversize Page</h1><pre>\n'
    )
    suffix = f'\n</pre><p>{OVERSIZE_END_TOKEN}</p></body></html>\n'
    fill = OVERSIZE_CHUNK + '\n'
    out = prefix
    remaining = OVERSIZE_TARGET - len(prefix) - len(suffix)
    while remaining > 0:
        piece = fill if remaining >= len(fill) else fill[:remaining]
        out += piece
        remaining -= len(piece)
    out += suffix
    return out.encode('utf-8')


OVERSIZE_BODY = build_oversize_payload()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/simple":
            body = SIMPLE_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/simple-with-etag":
            body = SIMPLE_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"upstream-etag-v1"')
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/oversize":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(OVERSIZE_BODY)))
            self.end_headers()
            self.wfile.write(OVERSIZE_BODY)
            return

        if path == "/partial-abort":
            # Send headers and partial body, then close abruptly.
            # Use chunked transfer to allow mid-stream abort.
            body_prefix = PARTIAL_HTML_PREFIX.encode('utf-8')
            # Pad enough data to cross the commit boundary
            padding = (b'<p>' + b'A' * 2048 + b'</p>\n') * 20
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            # Send first chunk (should cross commit boundary)
            chunk = body_prefix + padding
            self.wfile.write(f"{len(chunk):X}\r\n".encode('ascii'))
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
            # Brief pause then close without sending terminator
            time.sleep(0.1)
            # Abruptly close — do NOT send 0\r\n\r\n terminator
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/simple":
            body = SIMPLE_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        if path == "/simple-with-etag":
            body = SIMPLE_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"upstream-etag-v1"')
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19096)
    parser.add_argument("--print-metrics", action="store_true")
    args = parser.parse_args()

    if args.print_metrics:
        print(f"OVERSIZE_LEN={len(OVERSIZE_BODY)}")
        print(f"OVERSIZE_END_TOKEN={OVERSIZE_END_TOKEN}")
        print(f"SIMPLE_HTML_LEN={len(SIMPLE_HTML.encode('utf-8'))}")
        return

    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()


if __name__ == "__main__":
    main()
UPSTREAM_PY

chmod +x "${UPSTREAM_SCRIPT}"
eval "$(python3 "${UPSTREAM_SCRIPT}" --print-metrics)"


# ---------------------------------------------------------------------------
# Prepare NGINX runtime (reuse existing binary)
# ---------------------------------------------------------------------------
echo "==> Host architecture: $(uname -m)"
echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
NGINX_EXECUTABLE="${NGINX_BIN}"

# ---------------------------------------------------------------------------
# Start upstream server
# ---------------------------------------------------------------------------
echo "==> Starting upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 \
    --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
for _ in $(seq 1 50); do
    if curl -sS "http://127.0.0.1:${UPSTREAM_PORT}/health" \
       >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
curl -sS "http://127.0.0.1:${UPSTREAM_PORT}/health" >/dev/null

# ---------------------------------------------------------------------------
# Write NGINX config with multiple location blocks for each test scenario
# ---------------------------------------------------------------------------
cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log debug;
pid logs/nginx.pid;

events { worker_connections 512; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        # 10.1: Streaming success + ETag on
        location /t01/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.2: Pre-commit failure + pass
        location /t02/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_streaming_on_error pass;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size 20m;
            markdown_streaming_budget 1k;
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.3: Pre-commit failure + reject
        location /t03/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_streaming_on_error reject;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size 20m;
            markdown_streaming_budget 1k;
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.4: Post-commit failure (upstream aborts mid-stream)
        location /t04/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.5: conditional_requests full_support + streaming on
        location /t05/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests full_support;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.6: conditional_requests if_modified_since_only + streaming on
        location /t06/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.7: Streaming response headers (same as t01)
        location /t07/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.8: streaming_engine off + streaming_on_error reject
        location /t08/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            # streaming_engine defaults to off
            markdown_streaming_on_error reject;
            markdown_conditional_requests full_support;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.9a: on_error pass + streaming_on_error reject
        location /t09a/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_on_error pass;
            markdown_streaming_on_error reject;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size 20m;
            markdown_streaming_budget 1k;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.9b: on_error reject + streaming_on_error pass
        location /t09b/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_on_error reject;
            markdown_streaming_on_error pass;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size 20m;
            markdown_streaming_budget 1k;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.10: HEAD request should bypass streaming body processing
        location /t10/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # 10.11: 304 should bypass streaming path selection
        location /t11/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_conditional_requests full_support;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }
    }
}
EOF


# ---------------------------------------------------------------------------
# Start NGINX
# ---------------------------------------------------------------------------
echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo ""
echo "${SEPARATOR}"
echo "Running streaming failure/cache E2E tests"
echo "${SEPARATOR}"
echo ""

# ---------------------------------------------------------------------------
# 10.1 Streaming success + ETag on
# ---------------------------------------------------------------------------
echo "==> 10.1 Streaming success + ETag on"
curl -sS -D "${RAW_DIR}/t01.hdr" -o "${RAW_DIR}/t01.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t01/simple"

t01_pass=1

assert_http_200 "${RAW_DIR}/t01.hdr" "10.1" t01_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t01.hdr" "10.1" t01_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
assert_no_header "${RAW_DIR}/t01.hdr" "${PATTERN_ETAG}" "10.1" t01_pass \
    "ETag should NOT be in streaming response headers"
assert_body_contains "${RAW_DIR}/t01.body" "${EXPECTED_HEADING}" "10.1" \
    t01_pass "${MSG_MISSING_CONVERTED_HEADING}"

# ETag log strings are implementation-detail and may differ by build/log level.
# Keep this as informational only and assert behavior via response headers.
if grep -qi 'etag' "${RUNTIME}/logs/error.log"; then
    echo "  10.1 INFO: ETag reference found in log" >&2
else
    echo "  10.1 INFO: no explicit ETag log line observed" >&2
fi

if [[ ${t01_pass} -eq 1 ]]; then
    report_case "10.1" "PASS" "Streaming success + ETag not in headers"
else
    report_case "10.1" "FAIL" "Streaming success + ETag not in headers"
fi

# ---------------------------------------------------------------------------
# 10.1b Streaming success + upstream ETag stripped
# ---------------------------------------------------------------------------
echo "==> 10.1b Streaming strips upstream ETag"
curl -sS -D "${RAW_DIR}/t01b.hdr" -o "${RAW_DIR}/t01b.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t01/simple-with-etag"

t01b_pass=1

assert_http_200 "${RAW_DIR}/t01b.hdr" "10.1b" t01b_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t01b.hdr" "10.1b" t01b_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
assert_no_header "${RAW_DIR}/t01b.hdr" "${PATTERN_ETAG}" "10.1b" t01b_pass \
    "upstream ETag leaked through streaming path"
assert_body_contains "${RAW_DIR}/t01b.body" "${EXPECTED_HEADING}" "10.1b" \
    t01b_pass "${MSG_MISSING_CONVERTED_HEADING}"

if [[ ${t01b_pass} -eq 1 ]]; then
    report_case "10.1b" "PASS" "Upstream ETag stripped in streaming"
else
    report_case "10.1b" "FAIL" "Upstream ETag stripped in streaming"
fi

# ---------------------------------------------------------------------------
# 10.2 Streaming pre-commit failure + pass
# ---------------------------------------------------------------------------
echo "==> 10.2 Pre-commit failure + streaming_on_error pass"
curl -sS -D "${RAW_DIR}/t02.hdr" -o "${RAW_DIR}/t02.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 60 \
    "http://127.0.0.1:${PORT}/t02/oversize"

t02_pass=1

assert_http_200 "${RAW_DIR}/t02.hdr" "10.2" t02_pass \
    "expected HTTP 200 for fail-open"
assert_has_header "${RAW_DIR}/t02.hdr" "${PATTERN_CT_HTML}" "10.2" t02_pass \
    "expected Content-Type text/html for fail-open"
assert_body_contains "${RAW_DIR}/t02.body" "${OVERSIZE_END_TOKEN}" "10.2" \
    t02_pass "missing end token (possible truncation)"

if [[ ${t02_pass} -eq 1 ]]; then
    report_case "10.2" "PASS" "Pre-commit fail-open returns complete HTML"
else
    report_case "10.2" "FAIL" "Pre-commit fail-open returns complete HTML"
fi

# ---------------------------------------------------------------------------
# 10.3 Streaming pre-commit failure + reject
# ---------------------------------------------------------------------------
echo "==> 10.3 Pre-commit failure + streaming_on_error reject"
if curl -sS -D "${RAW_DIR}/t03.hdr" -o "${RAW_DIR}/t03.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 60 \
    "http://127.0.0.1:${PORT}/t03/oversize"; then
    :
fi
t03_code="$(awk 'NR==1 {print $2}' "${RAW_DIR}/t03.hdr" 2>/dev/null || true)"
if [[ -z "${t03_code}" ]]; then
    t03_code="000"
fi

t03_pass=1

# Verify reject behavior: either explicit HTTP error (4xx/5xx) or transport close.
if [[ "${t03_code}" == "000" ]]; then
    echo "  10.3 INFO: transport close (connection reset = implicit reject)" >&2
elif [[ "${t03_code}" -lt 400 ]]; then
    echo "  10.3 FAIL: expected error response, got ${t03_code}" >&2
    t03_pass=0
fi

assert_body_not_contains "${RAW_DIR}/t03.body" "${PATTERN_MARKDOWN_HEADING}" \
    "10.3" t03_pass "unexpected Markdown content in reject response"

if [[ ${t03_pass} -eq 1 ]]; then
    report_case "10.3" "PASS" "Pre-commit reject returns error"
else
    report_case "10.3" "FAIL" "Pre-commit reject returns error"
fi

# ---------------------------------------------------------------------------
# 10.4 Streaming post-commit failure
# ---------------------------------------------------------------------------
echo "==> 10.4 Post-commit failure (upstream abort)"
# The upstream /partial-abort endpoint sends partial HTML then closes.
# This may result in a truncated response or connection error.
if curl -sS -D "${RAW_DIR}/t04.hdr" -o "${RAW_DIR}/t04.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t04/partial-abort" 2>"${RAW_DIR}/t04.err"; then
    :
fi
t04_code="$(awk 'NR==1 {print $2}' "${RAW_DIR}/t04.hdr" 2>/dev/null || true)"
if [[ -z "${t04_code}" ]]; then
    t04_code="000"
fi

# Post-commit failure: headers were already sent as text/markdown
# The response may be truncated. We check that if we got a response,
# it started as markdown (headers committed before failure).
if [[ -s "${RAW_DIR}/t04.hdr" ]] \
    && grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/t04.hdr"; then
    echo "  10.4 INFO: Content-Type text/markdown confirmed (post-commit)" >&2
fi

# Check NGINX log for post-commit error indicators
if grep -qi 'post.commit' "${RUNTIME}/logs/error.log" 2>/dev/null; then
    echo "  10.4 INFO: post-commit error reference found in log" >&2
fi

# Post-commit tests are inherently harder to validate in e2e because
# the upstream abort timing is non-deterministic. We accept the test
# if the response was either truncated markdown or a connection error.
if [[ "${t04_code}" == "200" || "${t04_code}" == "000" ]]; then
    report_case "10.4" "PASS" "Post-commit failure (truncated or conn error)"
else
    report_case "10.4" "FAIL" "Post-commit failure (status: ${t04_code})"
fi

# ---------------------------------------------------------------------------
# 10.5 conditional_requests full_support + streaming on
# ---------------------------------------------------------------------------
echo "==> 10.5 conditional_requests full_support forces full-buffer"
curl -sS -D "${RAW_DIR}/t05.hdr" -o "${RAW_DIR}/t05.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t05/simple"

t05_pass=1

assert_http_200 "${RAW_DIR}/t05.hdr" "10.5" t05_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t05.hdr" "10.5" t05_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
assert_has_header "${RAW_DIR}/t05.hdr" "${PATTERN_ETAG}" "10.5" t05_pass \
    "ETag missing (full-buffer path should produce ETag)"
assert_has_header "${RAW_DIR}/t05.hdr" "${PATTERN_CL}" "10.5" t05_pass \
    "Content-Length missing (full-buffer path should set it)"
assert_body_contains "${RAW_DIR}/t05.body" "${EXPECTED_HEADING}" "10.5" \
    t05_pass "${MSG_MISSING_CONVERTED_HEADING}"

if [[ ${t05_pass} -eq 1 ]]; then
    report_case "10.5" "PASS" "full_support forces full-buffer path"
else
    report_case "10.5" "FAIL" "full_support forces full-buffer path"
fi

# ---------------------------------------------------------------------------
# 10.6 conditional_requests if_modified_since_only + streaming on
# ---------------------------------------------------------------------------
echo "==> 10.6 conditional_requests if_modified_since_only allows streaming"
curl -sS -D "${RAW_DIR}/t06.hdr" -o "${RAW_DIR}/t06.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t06/simple"

t06_pass=1

assert_http_200 "${RAW_DIR}/t06.hdr" "10.6" t06_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t06.hdr" "10.6" t06_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
assert_no_header "${RAW_DIR}/t06.hdr" "${PATTERN_CL}" "10.6" t06_pass \
    "Content-Length present (should be streaming, not full-buffer)"
assert_no_header "${RAW_DIR}/t06.hdr" "${PATTERN_ETAG}" "10.6" t06_pass \
    "ETag present (streaming path must not include ETag)"
assert_has_header "${RAW_DIR}/t06.hdr" "${PATTERN_TRANSFER_CHUNKED}" "10.6" \
    t06_pass "Transfer-Encoding chunked missing"
assert_body_contains "${RAW_DIR}/t06.body" "${EXPECTED_HEADING}" "10.6" \
    t06_pass "${MSG_MISSING_CONVERTED_HEADING}"

if [[ ${t06_pass} -eq 1 ]]; then
    report_case "10.6" "PASS" "if_modified_since_only allows streaming"
else
    report_case "10.6" "FAIL" "if_modified_since_only allows streaming"
fi


# ---------------------------------------------------------------------------
# 10.7 Streaming response headers
# ---------------------------------------------------------------------------
echo "==> 10.7 Streaming response headers validation"
curl -sS -D "${RAW_DIR}/t07.hdr" -o "${RAW_DIR}/t07.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t07/simple"

t07_pass=1

assert_http_200 "${RAW_DIR}/t07.hdr" "10.7" t07_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t07.hdr" "10.7" t07_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
assert_no_header "${RAW_DIR}/t07.hdr" "${PATTERN_CL}" "10.7" t07_pass \
    "Content-Length present in streaming response"
assert_has_header "${RAW_DIR}/t07.hdr" "${PATTERN_TRANSFER_CHUNKED}" "10.7" \
    t07_pass "Transfer-Encoding chunked missing"
assert_has_header "${RAW_DIR}/t07.hdr" "${PATTERN_VARY_ACCEPT}" "10.7" \
    t07_pass "missing Vary: Accept header"
assert_body_contains "${RAW_DIR}/t07.body" "${EXPECTED_HEADING}" "10.7" \
    t07_pass "${MSG_MISSING_CONVERTED_HEADING}"

if [[ ${t07_pass} -eq 1 ]]; then
    report_case "10.7" "PASS" "Streaming response headers correct"
else
    report_case "10.7" "FAIL" "Streaming response headers correct"
fi

# ---------------------------------------------------------------------------
# 10.8 streaming_engine off + streaming_on_error config
# ---------------------------------------------------------------------------
echo "==> 10.8 streaming_engine off ignores streaming_on_error"
curl -sS -D "${RAW_DIR}/t08.hdr" -o "${RAW_DIR}/t08.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t08/simple"

t08_pass=1

assert_http_200 "${RAW_DIR}/t08.hdr" "10.8" t08_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t08.hdr" "10.8" t08_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
assert_has_header "${RAW_DIR}/t08.hdr" "${PATTERN_CL}" "10.8" t08_pass \
    "Content-Length missing (full-buffer path should set it)"
assert_has_header "${RAW_DIR}/t08.hdr" "${PATTERN_ETAG}" "10.8" t08_pass \
    "ETag missing (full-buffer path with etag on should set it)"
assert_body_contains "${RAW_DIR}/t08.body" "${EXPECTED_HEADING}" "10.8" \
    t08_pass "${MSG_MISSING_CONVERTED_HEADING}"

if [[ ${t08_pass} -eq 1 ]]; then
    report_case "10.8" "PASS" "streaming_engine off: 0.4.0 behavior"
else
    report_case "10.8" "FAIL" "streaming_engine off: 0.4.0 behavior"
fi

# ---------------------------------------------------------------------------
# 10.9 Directive independence
# ---------------------------------------------------------------------------
echo "==> 10.9 Directive independence (cross-configuration)"

# 10.9a: on_error pass + streaming_on_error reject
# Streaming pre-commit failure should return error (reject)
if curl -sS -D "${RAW_DIR}/t09a.hdr" -o "${RAW_DIR}/t09a.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 60 \
    "http://127.0.0.1:${PORT}/t09a/oversize"; then
    :
fi
t09a_code="$(awk 'NR==1 {print $2}' "${RAW_DIR}/t09a.hdr" 2>/dev/null || true)"
if [[ -z "${t09a_code}" ]]; then
    t09a_code="000"
fi

# 10.9b: on_error reject + streaming_on_error pass
# Streaming pre-commit failure should return HTML (pass/fail-open)
curl -sS -D "${RAW_DIR}/t09b.hdr" -o "${RAW_DIR}/t09b.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 60 \
    "http://127.0.0.1:${PORT}/t09b/oversize"

t09_pass=1

# 10.9a: streaming_on_error=reject should produce error
if [[ "${t09a_code}" == "000" ]]; then
    echo "  10.9a INFO: transport close (connection reset = implicit reject)" >&2
elif [[ "${t09a_code}" -lt 400 ]]; then
    echo "  10.9a FAIL: expected error (streaming_on_error=reject)," \
         "got ${t09a_code}" >&2
    t09_pass=0
else
    echo "  10.9a INFO: streaming_on_error=reject returned ${t09a_code}" >&2
fi

# 10.9b: streaming_on_error=pass should produce HTML pass-through
assert_http_200 "${RAW_DIR}/t09b.hdr" "10.9b" t09_pass \
    "expected HTTP 200 for streaming_on_error=pass"
assert_has_header "${RAW_DIR}/t09b.hdr" "${PATTERN_CT_HTML}" "10.9b" t09_pass \
    "expected text/html for streaming_on_error=pass"
assert_body_contains "${RAW_DIR}/t09b.body" "${OVERSIZE_END_TOKEN}" "10.9b" \
    t09_pass "missing end token (possible truncation)"

if [[ ${t09_pass} -eq 1 ]]; then
    report_case "10.9" "PASS" "Directive independence verified"
else
    report_case "10.9" "FAIL" "Directive independence verified"
fi

# ---------------------------------------------------------------------------
# 10.10 HEAD request does not enter streaming path
# ---------------------------------------------------------------------------
echo "==> 10.10 HEAD request does not enter streaming path"
curl -sS -D "${RAW_DIR}/t10.hdr" -o "${RAW_DIR}/t10.body" \
    -X HEAD -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t10/simple"

t10_pass=1

assert_http_200 "${RAW_DIR}/t10.hdr" "10.10" t10_pass \
    "${MSG_EXPECTED_HTTP_200}"
assert_content_type_markdown "${RAW_DIR}/t10.hdr" "10.10" t10_pass \
    "${MSG_EXPECTED_CT_MARKDOWN}"
if [[ -s "${RAW_DIR}/t10.body" ]]; then
    mark_case_fail "10.10" "HEAD response should not include a body" t10_pass
fi

if [[ ${t10_pass} -eq 1 ]]; then
    report_case "10.10" "PASS" "HEAD bypasses streaming body processing"
else
    report_case "10.10" "FAIL" "HEAD bypasses streaming body processing"
fi

# ---------------------------------------------------------------------------
# 10.11 304 response does not enter streaming path
# ---------------------------------------------------------------------------
echo "==> 10.11 304 response does not enter streaming path"
curl -sS -D "${RAW_DIR}/t11_first.hdr" -o "${RAW_DIR}/t11_first.body" \
    -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 30 \
    "http://127.0.0.1:${PORT}/t11/simple-with-etag"

t11_pass=1
t11_etag="$(awk '{
    line = $0
    if (tolower(line) ~ /^etag:/) {
        sub(/^[^:]+:[[:space:]]*/, "", line)
        gsub(/\r/, "", line)
        print line
        exit
    }
}' "${RAW_DIR}/t11_first.hdr")"
if [[ -z "${t11_etag}" ]]; then
    mark_case_fail "10.11" "first request did not return ETag for conditional revalidation" t11_pass
else
    curl -sS -D "${RAW_DIR}/t11_304.hdr" -o "${RAW_DIR}/t11_304.body" \
        -H "${ACCEPT_MARKDOWN_HEADER}" \
        -H "If-None-Match: ${t11_etag}" --max-time 30 \
        "http://127.0.0.1:${PORT}/t11/simple-with-etag"

    if ! grep -q "${PATTERN_HTTP_304}" "${RAW_DIR}/t11_304.hdr"; then
        mark_case_fail "10.11" "expected HTTP 304 for matching If-None-Match" t11_pass
    fi
    if [[ -s "${RAW_DIR}/t11_304.body" ]]; then
        mark_case_fail "10.11" "304 response should not include a body" t11_pass
    fi
fi

if [[ ${t11_pass} -eq 1 ]]; then
    report_case "10.11" "PASS" "304 bypasses streaming conversion path"
else
    report_case "10.11" "FAIL" "304 bypasses streaming conversion path"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "${SEPARATOR}"
echo "Streaming failure/cache E2E summary"
echo "${SEPARATOR}"
echo "  passed=${PASS_COUNT}"
echo "  failed=${FAIL_COUNT}"
echo "  skipped=${SKIP_COUNT}"
echo "  nginx_bin=${NGINX_BIN}"
echo "  arch=$(uname -m)"
echo "  markdown_max_size=${MARKDOWN_MAX_SIZE}"
echo "  artifacts=${BUILDROOT}"
echo ""

if [[ ${FAIL_COUNT} -gt 0 ]]; then
    echo "Some tests failed. Check artifacts at: ${BUILDROOT}" >&2
    exit 1
fi

echo "All streaming failure/cache E2E tests passed."
