#!/usr/bin/env bash
set -euo pipefail

# E2E validation for conditional requests (ETag + If-None-Match + If-Modified-Since).
#
# Validates critical conditional-request paths:
#  1) Converted response includes ETag header
#  2) ETag differs from upstream's original ETag
#  3) If-None-Match with matching ETag returns 304 Not Modified
#  4) If-None-Match with non-matching ETag returns 200 with body
#  5) If-Modified-Since with future date returns 304
#  6) If-Modified-Since with past date returns 200 with body
#  7) Weak ETag matching (W/"...") returns 304
#  8) Wildcard If-None-Match: * returns 304
#  9) Vary: Accept present in 304 response
#  10) HEAD request returns same ETag as GET

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18099}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19099}"
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
readonly PATTERN_HTTP_200='HTTP/1.1 200'
readonly PATTERN_HTTP_304='HTTP/1.1 304'

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]

Build local NGINX with the markdown module and run conditional-request E2E checks.

Checks:
  1) Converted response includes ETag
  2) ETag differs from upstream ETag
  3) If-None-Match matching ETag returns 304
  4) If-None-Match non-matching ETag returns 200
  5) If-Modified-Since future returns 304
  6) If-Modified-Since past returns 200
  7) Weak ETag matching returns 304
  8) Wildcard If-None-Match returns 304
  9) Vary: Accept in 304 response
  10) HEAD returns same ETag as GET

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
    echo "Conditional-request E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Conditional-request E2E succeeded. Artifacts kept at: ${BUILDROOT}"
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
BUILDROOT="$(mktemp -d /tmp/nginx-conditional-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/conditional_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream server with ETag and Last-Modified ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from email.utils import formatdate
from time import time

HTML_BODY = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>Conditional Test</title></head>
<body><h1>Conditional Request Test</h1><p>Content for ETag validation.</p>
<a href="/link">Click here</a></body></html>
"""

UPSTREAM_ETAG = '"upstream-original-etag-12345"'
LAST_MODIFIED = "Mon, 01 Jan 2024 00:00:00 GMT"

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
            self.send_header("ETag", UPSTREAM_ETAG)
            self.send_header("Last-Modified", LAST_MODIFIED)
            self.send_header("Cache-Control", "public, max-age=3600")
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
    parser.add_argument("--port", type=int, default=19099)
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
echo "==> Starting conditional-request upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" "Upstream on ${UPSTREAM_PORT}" || exit 1

# --- NGINX config with conditional requests enabled ---
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
            markdown_etag on;
            markdown_conditional_requests full_support;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/html" "NGINX on ${PORT}" || exit 1

# --- Case 1: Converted response includes ETag header ---
echo "==> Case 1: Converted response includes ETag header"
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 1" "${RAW_DIR}/case1.hdr" "${PATTERN_HTTP_200}"
markdown_expect_header "Case 1" "${RAW_DIR}/case1.hdr" '^ETag:'
echo "  PASS: Converted response includes ETag header"

# Extract the ETag value for subsequent tests
RESPONSE_ETAG="$(markdown_extract_header "${RAW_DIR}/case1.hdr" "ETag")"

# --- Case 2: ETag differs from upstream's original ETag ---
echo "==> Case 2: ETag differs from upstream original"
[[ "${RESPONSE_ETAG}" != '"upstream-original-etag-12345"' ]] || {
  echo "FAIL: Case 2 - markdown ETag should differ from upstream ETag, got: ${RESPONSE_ETAG}" >&2
  exit 1
}
echo "  PASS: Markdown ETag differs from upstream (upstream=upstream-original-etag-12345, got=${RESPONSE_ETAG})"

# --- Case 3: If-None-Match with matching ETag returns 304 ---
echo "==> Case 3: If-None-Match matching ETag returns 304"
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H "If-None-Match: ${RESPONSE_ETAG}" \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 3" "${RAW_DIR}/case3.hdr" "${PATTERN_HTTP_304}"
echo "  PASS: If-None-Match with matching ETag returns 304"

# --- Case 4: If-None-Match with non-matching ETag returns 200 ---
echo "==> Case 4: If-None-Match non-matching ETag returns 200"
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'If-None-Match: "non-matching-etag-99999"' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 4" "${RAW_DIR}/case4.hdr" "${PATTERN_HTTP_200}"
echo "  PASS: If-None-Match with non-matching ETag returns 200"

# --- Case 5: If-Modified-Since with future date returns 304 ---
echo "==> Case 5: If-Modified-Since with future date returns 304"
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'If-Modified-Since: Mon, 01 Jan 2030 00:00:00 GMT' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 5" "${RAW_DIR}/case5.hdr" "${PATTERN_HTTP_304}"
echo "  PASS: If-Modified-Since with future date returns 304"

# --- Case 6: If-Modified-Since with past date returns 200 ---
echo "==> Case 6: If-Modified-Since with past date returns 200"
curl -sS -D "${RAW_DIR}/case6.hdr" -o "${RAW_DIR}/case6.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'If-Modified-Since: Mon, 01 Jan 2020 00:00:00 GMT' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 6" "${RAW_DIR}/case6.hdr" "${PATTERN_HTTP_200}"
echo "  PASS: If-Modified-Since with past date returns 200"

# --- Case 7: Weak ETag matching returns 304 ---
echo "==> Case 7: Weak ETag matching returns 304"
WEAK_ETAG="W/${RESPONSE_ETAG}"
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H "If-None-Match: ${WEAK_ETAG}" \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 7" "${RAW_DIR}/case7.hdr" "${PATTERN_HTTP_304}"
echo "  PASS: Weak ETag matching returns 304"

# --- Case 8: Wildcard If-None-Match: * returns 304 ---
echo "==> Case 8: Wildcard If-None-Match: * returns 304"
curl -sS -D "${RAW_DIR}/case8.hdr" -o "${RAW_DIR}/case8.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'If-None-Match: *' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 8" "${RAW_DIR}/case8.hdr" "${PATTERN_HTTP_304}"
echo "  PASS: Wildcard If-None-Match returns 304"

# --- Case 9: Vary: Accept present in 304 response ---
echo "==> Case 9: Vary: Accept present in 304 response"
markdown_expect_header "Case 9" "${RAW_DIR}/case3.hdr" '^Vary:.*Accept'
echo "  PASS: Vary: Accept present in 304 response"

# --- Case 10: HEAD returns same ETag as GET ---
echo "==> Case 10: HEAD returns same ETag as GET"
curl -sS -D "${RAW_DIR}/case10.hdr" -o "${RAW_DIR}/case10.body" \
  --head -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
markdown_expect_status "Case 10" "${RAW_DIR}/case10.hdr" "${PATTERN_HTTP_200}"
HEAD_ETAG="$(markdown_extract_header "${RAW_DIR}/case10.hdr" "ETag")"
[[ "${HEAD_ETAG}" == "${RESPONSE_ETAG}" ]] || {
  echo "FAIL: Case 10 - HEAD ETag (${HEAD_ETAG}) != GET ETag (${RESPONSE_ETAG})" >&2
  exit 1
}
echo "  PASS: HEAD and GET return identical ETag"

echo ""
echo "=============================================="
echo "All conditional-request E2E tests passed!"
echo "=============================================="
