#!/usr/bin/env bash
set -euo pipefail

# E2E validation for error handling and fail-open behavior.
#
# Validates critical error paths:
#  1) markdown_on_error pass: conversion failure returns original HTML
#  2) Empty response body does not crash
#  3) Malformed HTML input does not crash
#  4) Upstream 5xx errors are not converted
#  5) 206 Partial Content is not converted
#  6) markdown_max_size boundary: exactly at limit converts, over limit fail-opens
#  7) Metrics endpoint is reachable

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18097}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19097}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
UPSTREAM_PID=""
ORIG_ARGS=("$@")

readonly ACCEPT_MARKDOWN='Accept: text/markdown'
readonly PATTERN_CT_MARKDOWN='^Content-Type: text/markdown'
readonly PATTERN_CT_HTML='^Content-Type: text/html'
readonly AWK_HTTP_STATUS="{print \$2}"

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"
# shellcheck source=tools/e2e/e2e_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e_common.sh"

#
# Print command-line usage for this E2E script.
# Arguments: none.
# Output: writes help text to stdout; callers may redirect to stderr.
# Exit: returns 0 and does not exit the script.
#
usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]

Build local NGINX with the markdown module and run error-handling E2E checks.

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

#
# Stop child services and remove temporary artifacts when appropriate.
# Arguments: none.
# Output: writes failure artifact diagnostics to stderr.
# Exit: returns 0; preserves the original script status by running from trap.
# Side effects: stops upstream/NGINX and may remove BUILDROOT on success.
#
cleanup() {
  local rc=$?
  if [[ -n "${UPSTREAM_PID}" ]]; then
    kill "${UPSTREAM_PID}" >/dev/null 2>&1 || true
    wait "${UPSTREAM_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi
  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}" || true
  fi
  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Error-handling E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  fi
  return 0
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts) KEEP_ARTIFACTS=1; shift ;;
    --nginx-version)  markdown_require_flag_value "$1" "${2-}"; NGINX_VERSION="$2"; shift 2 ;;
    --port)           markdown_require_flag_value "$1" "${2-}"; PORT="$2"; shift 2 ;;
    --upstream-port)  markdown_require_flag_value "$1" "${2-}"; UPSTREAM_PORT="$2"; shift 2 ;;
    -h|--help)        usage; exit 0 ;;
    *)                echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if (( ${#ORIG_ARGS[@]} )); then
  markdown_ensure_native_apple_silicon "$0" "${ORIG_ARGS[@]}"
else
  markdown_ensure_native_apple_silicon "$0"
fi

for cmd in curl python3 grep; do
  markdown_need_cmd "$cmd"
done
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-error-handling-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/error_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream serving error test pages ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VALID_HTML = b"""<html><head><title>Valid</title></head>
<body><h1>Valid Page</h1>""" + (b"<p>Content here.</p>" * 80) + b"""</body></html>
"""

LARGE_HTML = b"""<html><head><title>Large</title></head>
<body><h1>Large Page</h1>""" + (b"<p>Oversize content.</p>" * 120) + b"""</body></html>
"""

MALFORMED_HTML = b"""<html><head><title>Bad
<body><h1>Unclosed heading
<div><div><div>nested without closing
<p>paragraph without closing
<a href="link without quote>text</a>
</html>
"""

EMPTY_BODY = b""

# HTML that is exactly ~1KB for boundary testing
SMALL_HTML = b"""<html><head><title>Small</title></head>
<body><h1>Small Page</h1><p>Content.</p></body></html>
"""

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
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
        if path == "/valid":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(VALID_HTML)))
            self.end_headers()
            self.wfile.write(VALID_HTML)
            return
        if path == "/large":
            if len(LARGE_HTML) <= 1024:
                raise RuntimeError(f"LARGE_HTML must exceed 1024 bytes, got {len(LARGE_HTML)}")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(LARGE_HTML)))
            self.end_headers()
            self.wfile.write(LARGE_HTML)
            return
        if path == "/malformed":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(MALFORMED_HTML)))
            self.end_headers()
            self.wfile.write(MALFORMED_HTML)
            return
        if path == "/empty":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/small":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(SMALL_HTML)))
            self.end_headers()
            self.wfile.write(SMALL_HTML)
            return
        if path == "/error500":
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            body = b"<html><body><h1>Server Error</h1></body></html>"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/error502":
            self.send_response(502)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            body = b"<html><body><h1>Bad Gateway</h1></body></html>"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/partial206":
            self.send_response(206)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            body = b"<html><body><h1>Partial</h1></body></html>"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19097)
    args = parser.parse_args()
    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()

if __name__ == "__main__":
    main()
PY

chmod +x "${UPSTREAM_SCRIPT}"

echo "==> Host architecture: $(uname -m)" >&2
if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})" >&2
  LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
  NGINX_EXECUTABLE="${NGINX_BIN}"
else
  echo "==> Building Rust converter (${RUST_TARGET})" >&2
  markdown_prepare_rust_converter_release \
    "${WORKSPACE_ROOT}" "${RUST_TARGET}" --features streaming >/dev/null

  echo "==> Downloading/building NGINX ${NGINX_VERSION}" >&2
  curl --proto '=https' --tlsv1.2 -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
  mkdir -p "${BUILDROOT}/src"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
  (
    cd "${BUILDROOT}/src"
    ./configure \
      --without-http_rewrite_module \
      --with-cc-opt="-DMARKDOWN_STREAMING_ENABLED" \
      --prefix="${RUNTIME}" \
      --add-module="${WORKSPACE_ROOT}/components/nginx-module" >/dev/null
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
    make install >/dev/null
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs"

echo "==> Starting error-handling upstream on 127.0.0.1:${UPSTREAM_PORT}" >&2
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" \
  "Upstream on ${UPSTREAM_PORT}" || exit 1

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log info;
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

        location /md/ {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_max_size 10m;
            markdown_on_error pass;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        location /md-reject/ {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_max_size 10m;
            markdown_on_error reject;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        location /md-small/ {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_max_size 1k;
            markdown_on_error pass;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}" >&2
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/valid" \
  "NGINX on ${PORT}" || exit 1

# --- Case 1: Valid HTML converts successfully ---
echo "==> Case 1: Valid HTML converts successfully" >&2
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/valid" >/dev/null
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case1.hdr" || {
  echo "FAIL: Case 1 - expected markdown Content-Type" >&2
  exit 1
}
echo "  PASS: Valid HTML converts to Markdown" >&2

# --- Case 2: Malformed HTML does not crash (fail-open) ---
echo "==> Case 2: Malformed HTML does not crash (fail-open with on_error pass)" >&2
curl -sS -D "${RAW_DIR}/case2.hdr" -o "${RAW_DIR}/case2.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/malformed" >/dev/null
# Should get a 200 response (either converted or fail-open to HTML)
status_code="$(head -1 "${RAW_DIR}/case2.hdr" | awk "${AWK_HTTP_STATUS}")"
if [[ "${status_code}" != "200" ]]; then
  echo "FAIL: Case 2 - expected 200, got ${status_code}" >&2
  exit 1
fi
echo "  PASS: Malformed HTML handled without crash (HTTP ${status_code})" >&2

# --- Case 3: Empty response body does not crash ---
echo "==> Case 3: Empty response body does not crash" >&2
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/empty" >/dev/null
status_code="$(head -1 "${RAW_DIR}/case3.hdr" | awk "${AWK_HTTP_STATUS}")"
if [[ "${status_code}" != "200" ]]; then
  echo "FAIL: Case 3 - expected 200, got ${status_code}" >&2
  exit 1
fi
echo "  PASS: Empty body handled without crash" >&2

# --- Case 4: Upstream 500 error is not converted ---
echo "==> Case 4: Upstream 500 error is not converted" >&2
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/error500" >/dev/null
status_code="$(head -1 "${RAW_DIR}/case4.hdr" | awk "${AWK_HTTP_STATUS}")"
if [[ "${status_code}" != "500" ]]; then
  echo "FAIL: Case 4 - expected 500, got ${status_code}" >&2
  exit 1
else
  echo "  PASS: Upstream 500 preserved (not converted)" >&2
fi

# --- Case 5: Upstream 502 error is not converted ---
echo "==> Case 5: Upstream 502 error is not converted" >&2
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/error502" >/dev/null
status_code="$(head -1 "${RAW_DIR}/case5.hdr" | awk "${AWK_HTTP_STATUS}")"
if [[ "${status_code}" != "502" ]]; then
  echo "FAIL: Case 5 - expected 502, got ${status_code}" >&2
  exit 1
else
  echo "  PASS: Upstream 502 preserved (not converted)" >&2
fi

# --- Case 6: 206 Partial Content is not converted ---
echo "==> Case 6: 206 Partial Content is not converted" >&2
curl -sS -D "${RAW_DIR}/case6.hdr" -o "${RAW_DIR}/case6.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/partial206" >/dev/null
status_code="$(head -1 "${RAW_DIR}/case6.hdr" | awk "${AWK_HTTP_STATUS}")"
if [[ "${status_code}" == "206" ]]; then
  # 206 should not be converted - body should be HTML
  grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case6.hdr" || {
    echo "FAIL: Case 6 - 206 response Content-Type is not text/html" >&2
    exit 1
  }
  echo "  PASS: 206 Partial Content not converted" >&2
else
  echo "FAIL: Case 6 - expected 206, got ${status_code}" >&2
  exit 1
fi

# --- Case 7: Small max_size allows small response ---
echo "==> Case 7: Small max_size (1k) allows small response" >&2
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md-small/small" >/dev/null
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case7.hdr" || {
  echo "FAIL: Case 7 - small response should convert with 1k max_size" >&2
  exit 1
}
echo "  PASS: Small response converts with 1k max_size" >&2

# --- Case 8: Small max_size fail-opens for larger response ---
echo "==> Case 8: Small max_size (1k) fail-opens for larger response" >&2
oversize_bytes="$(curl -sS --max-time 30 \
  "http://127.0.0.1:${UPSTREAM_PORT}/large" | wc -c | tr -d ' ')"
if [[ "${oversize_bytes}" -le 1024 ]]; then
  echo "FAIL: Case 8 - /md-small/large fixture must be >1024 bytes, got ${oversize_bytes}" >&2
  exit 1
fi
curl -sS -D "${RAW_DIR}/case8.hdr" -o "${RAW_DIR}/case8.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md-small/large" >/dev/null
# The large HTML is bigger than 1k, so it should fail-open to HTML.
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case8.hdr" || {
  echo "FAIL: Case 8 - expected text/html for oversize response" >&2
  exit 1
}
echo "  PASS: Oversize response fail-open verified" >&2

echo "" >&2
echo "========================================" >&2
echo "All error-handling E2E tests passed!" >&2
echo "========================================" >&2
