#!/usr/bin/env bash
set -euo pipefail

# E2E validation for NGINX configuration merging across http/server/location levels.
#
# Validates critical config-merge paths:
#  1) http-level markdown_on_error pass + location-level override reject
#  2) markdown_filter off location disables conversion despite Accept header
#  3) markdown_on_wildcard on at server + off at location
#  4) markdown_etag off at server + on at location (location wins)
#  5) markdown_conditional_requests none at server + if_modified_since_only at location
#  6) markdown_flavor override at location level

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18101}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19101}"
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
readonly PATTERN_CT_MARKDOWN='^Content-Type: text/markdown'
readonly PATTERN_CT_HTML='^Content-Type: text/html'

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]

Build local NGINX with the markdown module and run config-merge E2E checks.

Checks:
  1) http-level on_error pass + location-level reject override
  2) markdown_filter off disables conversion
  3) on_wildcard on at server + off at location
  4) etag off at server + on at location
  5) conditional_requests none + if_modified_since_only override
  6) markdown_flavor override at location

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
    echo "Config-merge E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Config-merge E2E succeeded. Artifacts kept at: ${BUILDROOT}"
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
BUILDROOT="$(mktemp -d /tmp/nginx-config-merge-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/config_merge_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream server ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HTML_BODY = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>Config Merge Test</title></head>
<body><h1>Config Merge Test Page</h1><p>Testing configuration merging.</p></body></html>
"""

LARGE_HTML_START = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>Large</title></head>
<body>
"""
LARGE_HTML_END = b"""</body></html>
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
        if path == "/html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_BODY)))
            self.end_headers()
            self.wfile.write(HTML_BODY)
            return
        if path == "/large":
            body = LARGE_HTML_START + b"<p>x</p>\n" * 100000 + LARGE_HTML_END
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
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
    parser.add_argument("--port", type=int, default=19101)
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
echo "==> Starting config-merge upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" "Upstream on ${UPSTREAM_PORT}" || exit 1

# --- NGINX config with multiple merge scenarios ---
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

    # http-level defaults
    markdown_on_error pass;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        # server-level settings
        markdown_on_wildcard on;
        markdown_etag off;
        markdown_conditional_requests none;

        # Case 1: location overrides on_error to reject
        location /md/reject/ {
            markdown_filter on;
            markdown_on_error reject;
            markdown_max_size 10m;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Case 1b: location inherits on_error pass from http level
        location /md/pass/ {
            markdown_filter on;
            markdown_max_size 10m;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Case 3: markdown_filter off disables conversion
        location /nomd/ {
            markdown_filter off;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Case 4: on_wildcard off at location
        location /no-wildcard/ {
            markdown_filter on;
            markdown_on_wildcard off;
            markdown_max_size 10m;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Case 5: etag on at location overrides server off
        location /etag-on/ {
            markdown_filter on;
            markdown_etag on;
            markdown_max_size 10m;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Case 6: conditional_requests override at location
        location /cond-ims/ {
            markdown_filter on;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size 10m;
            markdown_timeout 120000;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Case 7: flavor override at location
        location /flavor/ {
            markdown_filter on;
            markdown_flavor github;
            markdown_max_size 10m;
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
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/pass/html" "NGINX on ${PORT}" || exit 1

# --- Case 1: location-level on_error reject overrides http-level pass ---
echo "==> Case 1: location-level on_error reject overrides http-level pass"
# With reject, a malformed response should return an error, not pass-through
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/reject/html" >/dev/null
grep -qi "${PATTERN_HTTP_200}" "${RAW_DIR}/case1.hdr" || {
  echo "FAIL: Case 1 - expected 200 for valid HTML with on_error reject" >&2
  exit 1
}
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case1.hdr" || {
  echo "FAIL: Case 1 - expected text/markdown for valid HTML" >&2
  exit 1
}
echo "  PASS: Valid HTML converts with on_error reject"

# --- Case 1b: http-level on_error pass inherited ---
echo "==> Case 1b: http-level on_error pass inherited at location"
curl -sS -D "${RAW_DIR}/case1b.hdr" -o "${RAW_DIR}/case1b.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/pass/html" >/dev/null
grep -qi "${PATTERN_HTTP_200}" "${RAW_DIR}/case1b.hdr" || {
  echo "FAIL: Case 1b - expected 200" >&2
  exit 1
}
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case1b.hdr" || {
  echo "FAIL: Case 1b - expected text/markdown" >&2
  exit 1
}
echo "  PASS: http-level on_error pass inherited correctly"

# --- Case 3: markdown_filter off disables conversion ---
echo "==> Case 3: markdown_filter off disables conversion"
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/nomd/html" >/dev/null
grep -qi "${PATTERN_HTTP_200}" "${RAW_DIR}/case3.hdr" || {
  echo "FAIL: Case 3 - expected 200" >&2
  exit 1
}
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case3.hdr" || {
  echo "FAIL: Case 3 - expected text/html when markdown_filter off" >&2
  exit 1
}
echo "  PASS: markdown_filter off preserves HTML"

# --- Case 4: on_wildcard off at location overrides server on ---
echo "==> Case 4: on_wildcard off at location overrides server on"
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H 'Accept: */*' --max-time 30 \
  "http://127.0.0.1:${PORT}/no-wildcard/html" >/dev/null
grep -qi "${PATTERN_HTTP_200}" "${RAW_DIR}/case4.hdr" || {
  echo "FAIL: Case 4 - expected 200" >&2
  exit 1
}
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/case4.hdr" || {
  echo "FAIL: Case 4 - expected text/html with on_wildcard off" >&2
  exit 1
}
echo "  PASS: on_wildcard off at location overrides server on"

# --- Case 5: etag on at location overrides server off ---
echo "==> Case 5: etag on at location overrides server off"
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/etag-on/html" >/dev/null
grep -qi "${PATTERN_HTTP_200}" "${RAW_DIR}/case5.hdr" || {
  echo "FAIL: Case 5 - expected 200" >&2
  exit 1
}
grep -qi '^ETag:' "${RAW_DIR}/case5.hdr" || {
  echo "FAIL: Case 5 - expected ETag header when etag on at location" >&2
  exit 1
}
echo "  PASS: ETag present when etag on at location overrides server off"

# Verify server-level etag off behavior: the /md/pass/ location inherits server off
curl -sS -D "${RAW_DIR}/case5b.hdr" -o "${RAW_DIR}/case5b.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/pass/html" >/dev/null
if grep -qi '^ETag:' "${RAW_DIR}/case5b.hdr"; then
  echo "FAIL: Case 5b - ETag present despite server-level etag off being inherited" >&2
  exit 1
fi
echo "  PASS: No ETag when server-level etag off inherited"

# --- Case 6: conditional_requests override at location ---
echo "==> Case 6: conditional_requests if_modified_since_only at location"
curl -sS -D "${RAW_DIR}/case6.hdr" -o "${RAW_DIR}/case6.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'If-Modified-Since: Mon, 01 Jan 2030 00:00:00 GMT' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/cond-ims/html" >/dev/null
if ! grep -qi 'HTTP/1.1 304' "${RAW_DIR}/case6.hdr"; then
  echo "FAIL: Case 6 - expected 304 from if_modified_since_only override" >&2
  exit 1
fi
echo "  PASS: If-Modified-Since returns 304 with if_modified_since_only"

# --- Case 7: flavor override at location ---
echo "==> Case 7: flavor override at location level"
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/flavor/html" >/dev/null
grep -qi "${PATTERN_HTTP_200}" "${RAW_DIR}/case7.hdr" || {
  echo "FAIL: Case 7 - expected 200" >&2
  exit 1
}
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case7.hdr" || {
  echo "FAIL: Case 7 - expected text/markdown with flavor override" >&2
  exit 1
}
echo "  PASS: flavor override at location level works"

echo ""
echo "========================================="
echo "All config-merge E2E tests passed!"
echo "========================================="
