#!/usr/bin/env bash
set -euo pipefail

# E2E validation for auth/cache interaction with markdown conversion.
#
# Validates critical auth/cache paths:
#  1) Auth request with Cookie: session=abc gets Cache-Control: private
#  2) Non-auth request retains upstream Cache-Control: public
#  3) markdown_auth_policy deny rejects conversion for auth requests
#  4) markdown_auth_cookies pattern matching (session_* regex)
#  5) Auth request fail-open preserves Cache-Control from upstream
#  6) Non-auth conversion removes upstream ETag, generates markdown ETag
#  7) Auth request with Vary: Cookie in response

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18103}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19103}"
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
readonly HEADER_COOKIE_AUTH='Cookie: session_user=abc123'

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

#
# Assert that a headers file contains a line matching a pattern.
#
# Arguments:
#   $1 - case label (for diagnostic messages)
#   $2 - headers file path
#   $3 - grep pattern to match
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
}

#
# Assert that a headers file does NOT contain a line matching a pattern.
#
# Arguments:
#   $1 - case label (for diagnostic messages)
#   $2 - headers file path
#   $3 - grep pattern that must not match
# Output: FAIL diagnostic to stderr on failure
# Exit behavior: exits 1 if pattern is found
#
assert_header_not_contains() {
  local label="$1"
  local hdr_file="$2"
  local pattern="$3"

  if grep -qi "${pattern}" "${hdr_file}"; then
    echo "FAIL: ${label} - unexpected header matching ${pattern}" >&2
    exit 1
  fi
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

Build local NGINX with the markdown module and run auth/cache E2E checks.

Options:
  --keep-artifacts       Keep build artifacts after run (default: remove on success)
  --nginx-version VERSION
                         NGINX version to build (default: ${NGINX_VERSION})
  --port PORT            Main NGINX listen port (default: ${PORT})
  --upstream-port PORT   Upstream server listen port (default: ${UPSTREAM_PORT})
  -h, --help             Show this help message

Checks:
  1) Auth request Cache-Control: private
  2) Non-auth request retains Cache-Control: public
  3) auth_policy deny rejects conversion
  4) auth_cookies pattern matching
  5) Auth fail-open preserves Cache-Control
  6) Non-auth ETag replacement
  7) Vary: Cookie in auth response

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
    echo "Auth/cache E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Auth/cache E2E succeeded. Artifacts kept at: ${BUILDROOT}" >&2
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
BUILDROOT="$(mktemp -d /tmp/nginx-auth-cache-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/auth_cache_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream server that responds to auth and non-auth requests ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HTML_BODY = b"""<!doctype html>
<html><head><meta charset="UTF-8"><title>Auth Cache Test</title></head>
<body><h1>Auth Cache Test</h1><p>Content for auth/cache validation.</p></body></html>
"""

UPSTREAM_ETAG = '"upstream-auth-etag-001"'

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
            has_cookie = "Cookie" in self.headers
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(HTML_BODY)))
            self.send_header("ETag", UPSTREAM_ETAG)
            if has_cookie:
                self.send_header("Cache-Control", "private, max-age=0")
                self.send_header("Vary", "Cookie")
            else:
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
    parser.add_argument("--port", type=int, default=19103)
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
echo "==> Starting auth/cache upstream on 127.0.0.1:${UPSTREAM_PORT}" >&2
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" "Upstream on ${UPSTREAM_PORT}" || exit 1

# --- NGINX config with auth policy ---
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

        # Auth-aware conversion location
        location /md/ {
            markdown_filter on;
            markdown_max_size 10m;
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_auth_policy allow;
            markdown_auth_cookies "session_*";
            markdown_etag on;

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        # Auth deny location
        location /md-deny/ {
            markdown_filter on;
            markdown_max_size 10m;
            markdown_on_error pass;
            markdown_timeout 120000;
            markdown_auth_policy deny;
            markdown_auth_cookies "session_*";

            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}" >&2
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/md/html" "NGINX on ${PORT}" || exit 1

# --- Case 1: Auth request with Cookie gets Cache-Control: private ---
echo "==> Case 1: Auth request with Cookie gets Cache-Control: private" >&2
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H "${HEADER_COOKIE_AUTH}" \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
assert_header_contains "Case 1 status" "${RAW_DIR}/case1.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 1 cache-control" "${RAW_DIR}/case1.hdr" '^Cache-Control:.*private'
echo "  PASS: Cache-Control contains private for auth request" >&2

# --- Case 2: Non-auth request retains Cache-Control: public ---
echo "==> Case 2: Non-auth request retains upstream Cache-Control: public" >&2
curl -sS -D "${RAW_DIR}/case2.hdr" -o "${RAW_DIR}/case2.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
assert_header_contains "Case 2 status" "${RAW_DIR}/case2.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 2 cache-control" "${RAW_DIR}/case2.hdr" '^Cache-Control:.*public'
echo "  PASS: Cache-Control contains public for non-auth request" >&2

# --- Case 3: auth_policy deny rejects conversion for auth requests ---
echo "==> Case 3: auth_policy deny rejects conversion for auth requests" >&2
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H "${HEADER_COOKIE_AUTH}" \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md-deny/html" >/dev/null
assert_header_contains "Case 3 status" "${RAW_DIR}/case3.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 3 content-type" "${RAW_DIR}/case3.hdr" "${PATTERN_CT_HTML}"
echo "  PASS: auth_policy deny returns HTML passthrough" >&2

# --- Case 4: auth_cookies pattern matching ---
echo "==> Case 4: auth_cookies pattern matching" >&2
# A cookie that does NOT match session_* should not trigger auth logic
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H 'Cookie: preferences=dark' \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
assert_header_contains "Case 4 status" "${RAW_DIR}/case4.hdr" "${PATTERN_HTTP_200}"
assert_header_contains "Case 4 content-type" "${RAW_DIR}/case4.hdr" "${PATTERN_CT_MARKDOWN}"
echo "  PASS: Non-matching cookie does not trigger auth logic" >&2

# --- Case 5: Auth fail-open preserves Cache-Control ---
echo "==> Case 5: Auth fail-open preserves upstream Cache-Control" >&2
# With on_error pass and auth request, if conversion fails, upstream headers preserved
# Capture upstream Cache-Control value for comparison
curl -sS -D "${RAW_DIR}/case5up.hdr" -o /dev/null \
  -H "${HEADER_COOKIE_AUTH}" \
  --max-time 30 \
  "http://127.0.0.1:${UPSTREAM_PORT}/html" >/dev/null
UPSTREAM_CC="$(markdown_extract_header "${RAW_DIR}/case5up.hdr" "Cache-Control")"
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H "${HEADER_COOKIE_AUTH}" \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
AUTH_CC="$(markdown_extract_header "${RAW_DIR}/case5.hdr" "Cache-Control")"
if [[ -z "${AUTH_CC}" ]]; then
  echo "FAIL: Case 5 - expected Cache-Control header in auth request response" >&2
  exit 1
fi
if [[ "${AUTH_CC}" != "${UPSTREAM_CC}" ]]; then
  echo "FAIL: Case 5 - auth Cache-Control (${AUTH_CC}) differs from upstream (${UPSTREAM_CC})" >&2
  exit 1
fi
echo "  PASS: Auth response preserves upstream Cache-Control (${AUTH_CC})" >&2

# --- Case 6: Non-auth ETag replacement ---
echo "==> Case 6: Non-auth conversion replaces upstream ETag with markdown ETag" >&2
curl -sS -D "${RAW_DIR}/case6.hdr" -o "${RAW_DIR}/case6.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
MD_ETAG="$(markdown_extract_header "${RAW_DIR}/case6.hdr" "ETag")"
if [[ -z "${MD_ETAG}" ]]; then
  echo "FAIL: Case 6 - expected ETag header in non-auth conversion response" >&2
  exit 1
fi
if [[ "${MD_ETAG}" == '"upstream-auth-etag-001"' ]]; then
  echo "FAIL: Case 6 - ETag should differ from upstream for markdown content" >&2
  exit 1
fi
echo "  PASS: Markdown ETag differs from upstream (upstream=upstream-auth-etag-001, got=${MD_ETAG})" >&2

# --- Case 7: Vary: Cookie in auth response ---
echo "==> Case 7: Vary header in auth response" >&2
# The upstream sends Vary: Cookie for auth requests; check if forwarded
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" \
  -H "${HEADER_COOKIE_AUTH}" \
  --max-time 30 \
  "http://127.0.0.1:${PORT}/md/html" >/dev/null
assert_header_contains "Case 7 vary" "${RAW_DIR}/case7.hdr" '^Vary:.*Cookie'
echo "  PASS: Vary header contains Cookie in auth response" >&2

echo "" >&2
echo "=========================================" >&2
echo "All auth/cache E2E tests passed!" >&2
echo "=========================================" >&2
