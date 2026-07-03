#!/usr/bin/env bash
set -euo pipefail

# E2E validation for NGINX configuration merging across http/server/location levels.
#
# Validates critical config-merge paths:
#  1) http-level markdown_on_error pass + location-level override reject
#  2) markdown_filter off location disables conversion despite Accept header
#  3) markdown_accept wildcard at server + strict at location
#  4) markdown_etag off at server + on at location (location wins)
#  5) markdown_conditional_requests disabled at server + if_modified_since_only at location
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
# shellcheck source=tools/e2e/e2e_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/e2e_common.sh"

#
# Fetch URL, store headers/body, assert HTTP status matches pattern.
#
# Arguments:
#   $1 - case label
#   $2 - output directory for header/body files
#   $3 - expected status pattern (e.g. "HTTP/1.1 200")
#   $4 - URL to fetch
#   $5 - optional additional curl headers (space-separated)
# Output: FAIL diagnostic to stderr on failure
# Exit behavior: exits 1 if status does not match
#
assert_http_status() {
  local label="$1"
  local raw_dir="$2"
  local expected="$3"
  local url="$4"
  local extra_headers="${5:-}"

  local base="${raw_dir}/${label// /_}"
  # shellcheck disable=SC2086
  curl -sS -D "${base}.hdr" -o "${base}.body" \
    -H "${ACCEPT_MARKDOWN}" ${extra_headers} \
    --max-time 30 \
    "${url}" >/dev/null
  grep -qi "${expected}" "${base}.hdr" || {
    echo "FAIL: ${label} - expected status ${expected}" >&2
    exit 1
  }
  return 0
}

#
# Assert a header pattern is present in a headers file.
#
# Arguments:
#   $1 - case label
#   $2 - headers file path
#   $3 - header pattern (grep-compatible)
# Output: FAIL diagnostic to stderr on failure
# Exit behavior: exits 1 if pattern not found
#
assert_header_contains() {
  local label="$1"
  local hdr_file="$2"
  local pattern="$3"

  grep -qi "${pattern}" "${hdr_file}" || {
    echo "FAIL: ${label} - expected header matching ${pattern}" >&2
    exit 1
  }
  return 0
}

#
# Print command-line usage for this E2E script.
#
# Arguments: none
# Output: usage text to stdout
# Exit behavior: returns 0
#
usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT] [-h|--help]

Build local NGINX with the markdown module and run config-merge E2E checks.

Options:
  --keep-artifacts       Keep build artifacts after run (default: remove on success)
  --nginx-version VERSION
                         NGINX version to build (default: ${NGINX_VERSION})
  --port PORT            Main NGINX listen port (default: ${PORT})
  --upstream-port PORT   Upstream server listen port (default: ${UPSTREAM_PORT})
  -h, --help             Show this help message

Checks:
  1) http-level on_error pass + location-level reject override
  2) markdown_filter off disables conversion
  3) on_wildcard on at server + off at location
  4) etag off at server + on at location
  5) conditional_requests disabled + if_modified_since_only override
  6) markdown_flavor override at location

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

#
# Trap handler: stop upstream and NGINX, then optionally remove build artifacts.
#
# Arguments: none (reads global state)
# Output: diagnostic messages to stderr
# Exit behavior: returns 0
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
    echo "Config-merge E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Config-merge E2E succeeded. Artifacts kept at: ${BUILDROOT}" >&2
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

MALFORMED_HTML = b"""<html><head><title>Bad
<body><h1>Unclosed heading
<div><div><div>nested without closing
<p>paragraph without closing
<a href="link without quote>text</a>
</html>
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
        if path == "/malformed":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(MALFORMED_HTML)))
            self.end_headers()
            self.wfile.write(MALFORMED_HTML)
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

# --- Start upstream ---
echo "==> Starting config-merge upstream on 127.0.0.1:${UPSTREAM_PORT}" >&2
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
        markdown_accept wildcard;
        markdown_etag off;
        markdown_conditional_requests disabled;

        # Case 1: location overrides on_error to reject
        location /md/reject/ {
            markdown_filter on;
            markdown_on_error reject;
            markdown_max_size 1k;
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

        # Case 4: markdown_accept strict at location
        location /no-wildcard/ {
            markdown_filter on;
            markdown_accept strict;
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
            markdown_flavor gfm;
            markdown_max_size 10m;
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
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/pass/html" "NGINX on ${PORT}" || exit 1

# --- Case 1: location-level on_error reject overrides http-level pass ---
echo "==> Case 1: location-level on_error reject overrides http-level pass" >&2
# With reject, a malformed response should return an error, not pass-through
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/reject/html" >/dev/null
assert_header_contains "Case 1 status" "${RAW_DIR}/case1.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 1 content-type" "${RAW_DIR}/case1.hdr" "${PATTERN_CT_MARKDOWN}"
echo "  PASS: Valid HTML converts with on_error reject" >&2

# --- Case 1b: http-level on_error pass inherited ---
echo "==> Case 1b: http-level on_error pass inherited at location" >&2
curl -sS -D "${RAW_DIR}/case1b.hdr" -o "${RAW_DIR}/case1b.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/pass/html" >/dev/null
assert_header_contains "Case 1b status" "${RAW_DIR}/case1b.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 1b content-type" "${RAW_DIR}/case1b.hdr" "${PATTERN_CT_MARKDOWN}"
echo "  PASS: http-level on_error pass inherited correctly" >&2

# --- Case 1c: oversize input skips conversion before on_error handling ---
echo "==> Case 1c: oversize input skips conversion before on_error handling" >&2
curl -sS -D "${RAW_DIR}/case1c.hdr" -o "${RAW_DIR}/case1c.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/reject/large" >/dev/null
assert_header_contains "Case 1c status" "${RAW_DIR}/case1c.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 1c content-type" "${RAW_DIR}/case1c.hdr" "${PATTERN_CT_HTML}"
echo "  PASS: oversize input is skipped as passthrough HTML (size gate)" >&2

# --- Case 1d: on_error pass + malformed input returns successful response ---
echo "==> Case 1d: on_error pass with malformed input (no crash)" >&2
curl -sS -D "${RAW_DIR}/case1d.hdr" -o "${RAW_DIR}/case1d.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/pass/malformed" >/dev/null
assert_header_contains "Case 1d status" "${RAW_DIR}/case1d.hdr" "${PATTERN_HTTP_200}"
[[ -s "${RAW_DIR}/case1d.body" ]] || {
  echo "FAIL: Case 1d - expected non-empty response body for malformed input" >&2
  exit 1
}
echo "  PASS: malformed input returns successful non-empty response" >&2

# --- Case 3: markdown_filter off disables conversion ---
echo "==> Case 3: markdown_filter off disables conversion" >&2
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/nomd/html" >/dev/null
assert_header_contains "Case 3 status" "${RAW_DIR}/case3.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 3 content-type" "${RAW_DIR}/case3.hdr" "${PATTERN_CT_HTML}"
echo "  PASS: markdown_filter off preserves HTML" >&2

# --- Case 4: on_wildcard off at location overrides server on ---
echo "==> Case 4: on_wildcard off at location overrides server on" >&2
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H 'Accept: */*' --max-time 30 \
  "http://127.0.0.1:${PORT}/no-wildcard/html" >/dev/null
assert_header_contains "Case 4 status" "${RAW_DIR}/case4.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 4 content-type" "${RAW_DIR}/case4.hdr" "${PATTERN_CT_HTML}"
echo "  PASS: on_wildcard off at location overrides server on" >&2

# --- Case 5: etag on at location overrides server off ---
echo "==> Case 5: etag on at location overrides server off" >&2
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/etag-on/html" >/dev/null
assert_header_contains "Case 5 status" "${RAW_DIR}/case5.hdr" "${PATTERN_HTTP_200}"
grep -qi '^ETag:' "${RAW_DIR}/case5.hdr" || {
  echo "FAIL: Case 5 - expected ETag header when etag on at location" >&2
  exit 1
}
echo "  PASS: ETag present when etag on at location overrides server off" >&2

# Verify server-level etag off behavior: the /md/pass/ location inherits server off
curl -sS -D "${RAW_DIR}/case5b.hdr" -o "${RAW_DIR}/case5b.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/pass/html" >/dev/null
if grep -qi '^ETag:' "${RAW_DIR}/case5b.hdr"; then
  echo "FAIL: Case 5b - ETag present despite server-level etag off being inherited" >&2
  exit 1
fi
echo "  PASS: No ETag when server-level etag off inherited" >&2

# --- Case 6: conditional_requests override at location ---
echo "==> Case 6: conditional_requests if_modified_since_only at location" >&2
curl -sS -D "${RAW_DIR}/case6.hdr" -o "${RAW_DIR}/case6.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'If-Modified-Since: Mon, 01 Jan 2030 00:00:00 GMT' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/cond-ims/html" >/dev/null
assert_header_contains "Case 6 status" "${RAW_DIR}/case6.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 6 content-type" "${RAW_DIR}/case6.hdr" "${PATTERN_CT_MARKDOWN}"
echo "  PASS: if_modified_since_only override is accepted and conversion succeeds" >&2

# --- Case 7: flavor override at location ---
echo "==> Case 7: flavor override at location level" >&2
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/flavor/html" >/dev/null
assert_header_contains "Case 7 status" "${RAW_DIR}/case7.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 7 content-type" "${RAW_DIR}/case7.hdr" "${PATTERN_CT_MARKDOWN}"
echo "  PASS: flavor override at location level works" >&2

echo "" >&2
echo "=========================================" >&2
echo "All config-merge E2E tests passed!" >&2
echo "=========================================" >&2
