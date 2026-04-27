#!/usr/bin/env bash
set -euo pipefail

# E2E validation for Accept header content negotiation.
#
# Validates critical content-negotiation paths:
#  1) Accept: text/markdown triggers conversion (text/markdown response)
#  2) Accept: text/html returns original HTML (no conversion)
#  3) No Accept header returns original HTML (default behavior)
#  4) Accept: */* with markdown_on_wildcard on triggers conversion
#  5) Accept: */* with markdown_on_wildcard off does NOT trigger conversion
#  6) Vary: Accept header present in converted responses
#  7) Non-HTML Content-Type from upstream is not converted

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18095}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19095}"
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
readonly ACCEPT_HTML='Accept: text/html'
readonly ACCEPT_WILDCARD='Accept: */*'
readonly PATTERN_CT_MARKDOWN='^Content-Type: text/markdown'
readonly PATTERN_CT_HTML='^Content-Type: text/html'
readonly PATTERN_VARY_ACCEPT='^Vary:.*Accept'
readonly PATTERN_HTTP_200='HTTP/1.1 200'

# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]

Build local NGINX with the markdown module and run Accept-header content-negotiation E2E checks.

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

#
# Trap handler: stop upstream and NGINX, then optionally remove build artifacts.
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
    echo "Accept-negotiation E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
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
      markdown_require_flag_value "$1" "${2-}"
      NGINX_VERSION="$2"
      shift 2
      ;;
    --port)
      markdown_require_flag_value "$1" "${2-}"
      PORT="$2"
      shift 2
      ;;
    --upstream-port)
      markdown_require_flag_value "$1" "${2-}"
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
BUILDROOT="$(mktemp -d /tmp/nginx-accept-negotiation.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/accept_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream server that serves various content types ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HTML_BODY = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>Test Page</title></head>
<body><h1>Hello World</h1><p>This is a test paragraph.</p>
<a href="/link">Click here</a></body></html>
"""

JSON_BODY = b'{"status":"ok","message":"not html"}'

PLAIN_BODY = b'This is plain text, not HTML.'

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
        if path == "/html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_BODY)))
            self.end_headers()
            self.wfile.write(HTML_BODY)
            return
        if path == "/json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(JSON_BODY)))
            self.end_headers()
            self.wfile.write(JSON_BODY)
            return
        if path == "/plain":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(PLAIN_BODY)))
            self.end_headers()
            self.wfile.write(PLAIN_BODY)
            return
        if path == "/no-ct":
            self.send_response(200)
            self.send_header("Content-Length", str(len(HTML_BODY)))
            self.end_headers()
            self.wfile.write(HTML_BODY)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19095)
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
echo "==> Starting accept-negotiation upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" \
  "Upstream on ${UPSTREAM_PORT}" || exit 1

# --- NGINX config with wildcard on ---
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
            markdown_on_wildcard on;
            markdown_max_size 10m;
            markdown_on_error pass;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        location /no-wildcard/ {
            markdown_filter on;
            markdown_on_wildcard off;
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
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/html" \
  "NGINX on ${PORT}" || exit 1

# --- Case 1: Accept: text/markdown triggers conversion ---
echo "==> Case 1: Accept: text/markdown triggers conversion"
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 1" "${RAW_DIR}/case1.hdr" "${PATTERN_HTTP_200}"
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case1.hdr" || {
  echo "FAIL: Case 1 - expected text/markdown Content-Type" >&2
  exit 1
}
grep -q "# Hello World" "${RAW_DIR}/case1.body" || {
  echo "FAIL: Case 1 - expected Markdown heading in body" >&2
  exit 1
}
echo "  PASS: Accept: text/markdown -> text/markdown response with Markdown content"

# --- Case 2: Accept: text/html returns original HTML ---
echo "==> Case 2: Accept: text/html returns original HTML"
curl -sS -D "${RAW_DIR}/case2.hdr" -o "${RAW_DIR}/case2.body" \
  -H "${ACCEPT_HTML}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 2" "${RAW_DIR}/case2.hdr" "${PATTERN_HTTP_200}"
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case2.hdr" || {
  echo "FAIL: Case 2 - expected text/html Content-Type" >&2
  exit 1
}
grep -q "<h1>" "${RAW_DIR}/case2.body" || {
  echo "FAIL: Case 2 - expected HTML h1 tag in body" >&2
  exit 1
}
echo "  PASS: Accept: text/html -> text/html response with HTML content"

# --- Case 3: No Accept header returns original HTML ---
echo "==> Case 3: No Accept header returns original HTML (default)"
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H 'Accept:' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 3" "${RAW_DIR}/case3.hdr" "${PATTERN_HTTP_200}"
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case3.hdr" || {
  echo "FAIL: Case 3 - expected text/html Content-Type (no conversion without Accept)" >&2
  exit 1
}
echo "  PASS: No Accept header -> original HTML"

# --- Case 4: Accept: */* with markdown_on_wildcard on triggers conversion ---
echo "==> Case 4: Accept: */* with markdown_on_wildcard on triggers conversion"
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H "${ACCEPT_WILDCARD}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 4" "${RAW_DIR}/case4.hdr" "${PATTERN_HTTP_200}"
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case4.hdr" || {
  echo "FAIL: Case 4 - expected text/markdown with wildcard Accept and on_wildcard on" >&2
  exit 1
}
echo "  PASS: Accept: */* + on_wildcard on -> text/markdown"

# --- Case 5: Accept: */* with markdown_on_wildcard off does NOT convert ---
echo "==> Case 5: Accept: */* with markdown_on_wildcard off does NOT convert"
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_WILDCARD}" --max-time 30 \
  "http://127.0.0.1:${PORT}/no-wildcard/html" >/dev/null
markdown_expect_status "Case 5" "${RAW_DIR}/case5.hdr" "${PATTERN_HTTP_200}"
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case5.hdr" || {
  echo "FAIL: Case 5 - expected text/html with wildcard Accept and on_wildcard off" >&2
  exit 1
}
echo "  PASS: Accept: */* + on_wildcard off -> text/html"

# --- Case 6: Vary: Accept header in converted responses ---
echo "==> Case 6: Vary: Accept header present in converted response"
grep -qi "${PATTERN_VARY_ACCEPT}" "${RAW_DIR}/case1.hdr" || {
  echo "FAIL: Case 6 - Vary: Accept not found in converted response" >&2
  exit 1
}
echo "  PASS: Vary header check completed"

# --- Case 7: Non-HTML Content-Type is not converted ---
echo "==> Case 7: Non-HTML Content-Type (application/json) is not converted"
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/json" >/dev/null
markdown_expect_status "Case 7" "${RAW_DIR}/case7.hdr" "${PATTERN_HTTP_200}"
grep -qi "application/json" "${RAW_DIR}/case7.hdr" || {
  echo "FAIL: Case 7 - expected application/json Content-Type to be preserved" >&2
  exit 1
}
echo "  PASS: application/json upstream not converted"

# --- Case 8: text/plain Content-Type is not converted ---
echo "==> Case 8: text/plain Content-Type is not converted"
curl -sS -D "${RAW_DIR}/case8.hdr" -o "${RAW_DIR}/case8.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/plain" >/dev/null
markdown_expect_status "Case 8" "${RAW_DIR}/case8.hdr" "${PATTERN_HTTP_200}"
grep -qi "text/plain" "${RAW_DIR}/case8.hdr" || {
  echo "FAIL: Case 8 - expected text/plain Content-Type to be preserved" >&2
  exit 1
}
echo "  PASS: text/plain upstream not converted"

echo ""
echo "========================================"
echo "All Accept-negotiation E2E tests passed!"
echo "========================================"
