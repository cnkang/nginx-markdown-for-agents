#!/usr/bin/env bash
set -euo pipefail

# E2E validation for upstream status code passthrough.
#
# Validates that non-2xx status codes from upstream are properly handled:
#  1) 403 Forbidden - no conversion, passthrough HTML
#  2) 404 Not Found - no conversion, passthrough HTML
#  3) 500 Internal Server Error - no conversion, passthrough
#  4) 502 Bad Gateway - no conversion, passthrough
#  5) 503 Service Unavailable - no conversion, passthrough
#  6) 301 Redirect - not converted, follows redirect
#  7) 302 Redirect - not converted, follows redirect
#  8) 410 Gone - no conversion, passthrough

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18105}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19105}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
UPSTREAM_PID=""
ORIG_ARGS=("$@")

readonly ACCEPT_MARKDOWN='Accept: text/markdown'
readonly PATTERN_CT_HTML='^Content-Type: text/html'

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]

Build local NGINX with the markdown module and run upstream status-code E2E checks.

Checks:
  1) 403 passthrough
  2) 404 passthrough
  3) 500 passthrough
  4) 502 passthrough
  5) 503 passthrough
  6) 410 passthrough
  7) 301 redirect handling
  8) 302 redirect handling

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

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
    echo "Status-code E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Status-code E2E succeeded. Artifacts kept at: ${BUILDROOT}"
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
    --nginx-version)
      markdown_require_flag_value "$1" "${2:-}"
      NGINX_VERSION="$2"
      shift 2
      ;;
    --port)
      markdown_require_flag_value "$1" "${2:-}"
      PORT="$2"
      shift 2
      ;;
    --upstream-port)
      markdown_require_flag_value "$1" "${2:-}"
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
BUILDROOT="$(mktemp -d /tmp/nginx-status-code-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/status_code_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream server that returns various status codes ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HTML_ERROR = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>Error</title></head>
<body><h1>Error Page</h1></body></html>
"""

HTML_OK = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>OK</title></head>
<body><h1>OK Page</h1></body></html>
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
        if path == "/ok":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_OK)))
            self.end_headers()
            self.wfile.write(HTML_OK)
            return
        if path == "/403":
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_ERROR)))
            self.end_headers()
            self.wfile.write(HTML_ERROR)
            return
        if path == "/404":
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_ERROR)))
            self.end_headers()
            self.wfile.write(HTML_ERROR)
            return
        if path == "/500":
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_ERROR)))
            self.end_headers()
            self.wfile.write(HTML_ERROR)
            return
        if path == "/502":
            self.send_response(502)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_ERROR)))
            self.end_headers()
            self.wfile.write(HTML_ERROR)
            return
        if path == "/503":
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_ERROR)))
            self.end_headers()
            self.wfile.write(HTML_ERROR)
            return
        if path == "/410":
            self.send_response(410)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_ERROR)))
            self.end_headers()
            self.wfile.write(HTML_ERROR)
            return
        if path == "/301":
            self.send_response(301)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/302":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19105)
    args = parser.parse_args()
    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()

if __name__ == "__main__":
    main()
PY

chmod +x "${UPSTREAM_SCRIPT}"

# --- Build or reuse NGINX ---
echo "==> Host architecture: $(uname -m)"
if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
  LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
  NGINX_EXECUTABLE="${NGINX_BIN}"
else
  echo "==> Building Rust converter (${RUST_TARGET})"
  markdown_prepare_rust_converter_release \
    "${WORKSPACE_ROOT}" "${RUST_TARGET}" --features streaming >/dev/null

  echo "==> Downloading/building NGINX ${NGINX_VERSION}"
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

# --- Start upstream ---
echo "==> Starting status-code upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" "Upstream on ${UPSTREAM_PORT}" || exit 1

# --- NGINX config ---
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
            markdown_max_size 10m;
            markdown_on_error pass;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/ok" "NGINX on ${PORT}" || exit 1

# Verify upstream HTTP status passthrough without markdown conversion.
#
# Args:
#   $1 - case number for diagnostics
#   $2 - expected upstream status code (e.g. 403, 502)
#   $3 - upstream path (relative to /md/)
# Globals:
#   RAW_DIR, ACCEPT_MARKDOWN, PORT, PATTERN_CT_HTML
# Output:
#   PASS message on stdout; FAIL message on stderr.
# Exits:
#   1 when status line or Content-Type assertion fails.
#   Returns 0 on success.
check_status_passthrough() {
  local case_num="$1"
  local status_code="$2"
  local path="$3"
  local hdr="${RAW_DIR}/status_${status_code}.hdr"
  local body="${RAW_DIR}/status_${status_code}.body"

  echo "==> Case ${case_num}: ${status_code} passthrough"
  curl -sS -D "${hdr}" -o "${body}" \
    -H "${ACCEPT_MARKDOWN}" --max-time 30 \
    "http://127.0.0.1:${PORT}/md/${path}" || true

  grep -qi "HTTP/1.1 ${status_code}" "${hdr}" || {
    echo "FAIL: Case ${case_num} - expected HTTP ${status_code}" >&2
    exit 1
  }
  grep -qi "${PATTERN_CT_HTML}" "${hdr}" || {
    echo "FAIL: Case ${case_num} - expected text/html (no conversion for ${status_code})" >&2
    exit 1
  }
  echo "  PASS: ${status_code} passthrough with HTML Content-Type"
  return 0
}

# --- Cases 1-6: Error status codes ---
check_status_passthrough 1 403 "403"
check_status_passthrough 2 404 "404"
check_status_passthrough 3 500 "500"
check_status_passthrough 4 502 "502"
check_status_passthrough 5 503 "503"
check_status_passthrough 6 410 "410"

# --- Case 7: 301 redirect ---
echo "==> Case 7: 301 redirect handling"
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" -L --max-time 30 \
  "http://127.0.0.1:${PORT}/md/301" >/dev/null
# After following redirect, should reach /ok which gets converted
grep -qi 'HTTP/1.1 200' "${RAW_DIR}/case7.hdr" || {
  echo "FAIL: Case 7 - expected 200 after following 301 redirect" >&2
  exit 1
}
echo "  PASS: 301 redirect followed successfully"

# --- Case 8: 302 redirect ---
echo "==> Case 8: 302 redirect handling"
curl -sS -D "${RAW_DIR}/case8.hdr" -o "${RAW_DIR}/case8.body" \
  -H "${ACCEPT_MARKDOWN}" -L --max-time 30 \
  "http://127.0.0.1:${PORT}/md/302" >/dev/null
grep -qi 'HTTP/1.1 200' "${RAW_DIR}/case8.hdr" || {
  echo "FAIL: Case 8 - expected 200 after following 302 redirect" >&2
  exit 1
}
echo "  PASS: 302 redirect followed successfully"

echo ""
echo "=============================================="
echo "All status-code passthrough E2E tests passed!"
echo "=============================================="
