#!/usr/bin/env bash
set -euo pipefail

# E2E validation for security behavior of the markdown module.
#
# Validates critical security paths:
#  1) <script> content is stripped from Markdown output
#  2) javascript: URLs are removed/sanitized
#  3) data: URLs are removed/sanitized
#  4) Event handler attributes (onclick, etc.) are stripped
#  5) style attributes are stripped
#  6) Deeply nested HTML does not crash (memory bounded)
#  7) Control characters in URLs are rejected

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18096}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19096}"
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

# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]

Build local NGINX with the markdown module and run security E2E checks.

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
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
    echo "Security E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  fi
  return 0
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts) KEEP_ARTIFACTS=1; shift ;;
    --nginx-version)  require_flag_value "$1" "${2-}"; NGINX_VERSION="$2"; shift 2 ;;
    --port)           require_flag_value "$1" "${2-}"; PORT="$2"; shift 2 ;;
    --upstream-port)  require_flag_value "$1" "${2-}"; UPSTREAM_PORT="$2"; shift 2 ;;
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
BUILDROOT="$(mktemp -d /tmp/nginx-security-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/security_upstream.py"
mkdir -p "${RAW_DIR}"

# --- Upstream serving security test pages ---
cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SCRIPT_HTML = b"""<html><head><title>Script Test</title></head>
<body><h1>Safe Heading</h1>
<script>alert('XSS attack!')</script>
<p>Safe paragraph</p></body></html>
"""

JS_URL_HTML = b"""<html><head><title>JS URL Test</title></head>
<body><h1>JS URL Test</h1>
<a href="javascript:void(0)">Click me</a>
<p>Safe content</p></body></html>
"""

DATA_URL_HTML = b"""<html><head><title>Data URL Test</title></head>
<body><h1>Data URL Test</h1>
<img src="data:image/png;base64,AAAA" alt="data image">
<p>Safe content</p></body></html>
"""

EVENT_HANDLER_HTML = b"""<html><head><title>Event Handler Test</title></head>
<body><h1>Event Handler Test</h1>
<div onclick="alert('xss')" onmouseover="steal()">Hover me</div>
<p>Safe content</p></body></html>
"""

STYLE_ATTR_HTML = b"""<html><head><title>Style Test</title></head>
<body><h1>Style Test</h1>
<p style="color:red;font-size:20px">Styled text</p>
<p>Safe content</p></body></html>
"""

DEEP_NEST_HTML = b"""<html><head><title>Deep Nest Test</title></head>
<body><h1>Deep Nest</h1>""" + b"<div>" * 100 + b"deep content" + b"</div>" * 100 + b"""
<p>End</p></body></html>
"""

MIXED_DANGEROUS_HTML = b"""<html><head><title>Mixed Test</title></head>
<body><h1>Mixed Dangerous</h1>
<script>evil()</script>
<a href="javascript:alert(1)">JS link</a>
<img src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==" alt="data img">
<div onclick="bad()">click</div>
<p style="display:none">hidden</p>
<p>Safe paragraph with <a href="https://example.com">safe link</a></p>
</body></html>
"""

CONTROL_CHAR_URL_HTML = b"""<html><head><title>Control URL Test</title></head>
<body><h1>Control URL Test</h1>
<a href="https://example.com/%00evil">control char link</a>
<p>Safe content</p></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        return

    def do_GET(self):
        path = urlparse(self.path).path
        routes = {
            "/health": (b"ok\n", "text/plain"),
            "/script": (SCRIPT_HTML, "text/html; charset=UTF-8"),
            "/js-url": (JS_URL_HTML, "text/html; charset=UTF-8"),
            "/data-url": (DATA_URL_HTML, "text/html; charset=UTF-8"),
            "/event-handler": (EVENT_HANDLER_HTML, "text/html; charset=UTF-8"),
            "/style-attr": (STYLE_ATTR_HTML, "text/html; charset=UTF-8"),
            "/deep-nest": (DEEP_NEST_HTML, "text/html; charset=UTF-8"),
            "/mixed": (MIXED_DANGEROUS_HTML, "text/html; charset=UTF-8"),
            "/control-url": (CONTROL_CHAR_URL_HTML, "text/html; charset=UTF-8"),
        }
        if path in routes:
            body, ct = routes[path]
            self.send_response(200)
            self.send_header("Content-Type", ct)
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
    parser.add_argument("--port", type=int, default=19096)
    args = parser.parse_args()
    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()


if __name__ == "__main__":
    main()
PY

chmod +x "${UPSTREAM_SCRIPT}"

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

echo "==> Starting security upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
for _ in $(seq 1 50); do
  if curl -sS "http://127.0.0.1:${UPSTREAM_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -sS "http://127.0.0.1:${UPSTREAM_PORT}/health" >/dev/null

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
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/sec/script" "NGINX" || exit 1

# --- Case 1: <script> content is stripped ---
echo "==> Case 1: <script> content is stripped from Markdown output"
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/script" >/dev/null
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case1.hdr" || {
  echo "FAIL: Case 1 - expected markdown Content-Type" >&2
  exit 1
}
grep -q "alert" "${RAW_DIR}/case1.body" && {
  echo "FAIL: Case 1 - script content leaked into Markdown output" >&2
  exit 1
}
grep -q "# Safe Heading" "${RAW_DIR}/case1.body" || {
  echo "FAIL: Case 1 - safe heading missing from output" >&2
  exit 1
}
echo "  PASS: <script> content stripped"

# --- Case 2: javascript: URLs are sanitized ---
echo "==> Case 2: javascript: URLs are sanitized"
curl -sS -D "${RAW_DIR}/case2.hdr" -o "${RAW_DIR}/case2.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/js-url" >/dev/null
grep -q "javascript:" "${RAW_DIR}/case2.body" && {
  echo "FAIL: Case 2 - javascript: URL leaked into Markdown output" >&2
  exit 1
}
echo "  PASS: javascript: URL sanitized"

# --- Case 3: data: URLs are sanitized ---
echo "==> Case 3: data: URLs are sanitized"
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/data-url" >/dev/null
grep -q "data:image" "${RAW_DIR}/case3.body" && {
  echo "FAIL: Case 3 - data: URL leaked into Markdown output" >&2
  exit 1
}
echo "  PASS: data: URL sanitized"

# --- Case 4: Event handler attributes are stripped ---
echo "==> Case 4: Event handler attributes are stripped"
curl -sS -D "${RAW_DIR}/case4.hdr" -o "${RAW_DIR}/case4.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/event-handler" >/dev/null
grep -q "onclick" "${RAW_DIR}/case4.body" && {
  echo "FAIL: Case 4 - onclick attribute leaked into Markdown output" >&2
  exit 1
}
grep -q "onmouseover" "${RAW_DIR}/case4.body" && {
  echo "FAIL: Case 4 - onmouseover attribute leaked into Markdown output" >&2
  exit 1
}
echo "  PASS: Event handler attributes stripped"

# --- Case 5: style attributes are stripped ---
echo "==> Case 5: style attributes are stripped"
curl -sS -D "${RAW_DIR}/case5.hdr" -o "${RAW_DIR}/case5.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/style-attr" >/dev/null
grep -q "color:red" "${RAW_DIR}/case5.body" && {
  echo "FAIL: Case 5 - style attribute content leaked into Markdown output" >&2
  exit 1
}
echo "  PASS: style attributes stripped"

# --- Case 6: Deeply nested HTML does not crash ---
echo "==> Case 6: Deeply nested HTML does not crash"
curl -sS -D "${RAW_DIR}/case6.hdr" -o "${RAW_DIR}/case6.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/deep-nest" >/dev/null
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case6.hdr" || {
  echo "FAIL: Case 6 - expected markdown Content-Type (deep nesting should not crash)" >&2
  exit 1
}
grep -q "deep content" "${RAW_DIR}/case6.body" || {
  echo "  WARN: Case 6 - deep content may have been pruned (acceptable)"
}
echo "  PASS: Deep nesting handled without crash"

# --- Case 7: Mixed dangerous content all stripped, safe content preserved ---
echo "==> Case 7: Mixed dangerous content stripped, safe content preserved"
curl -sS -D "${RAW_DIR}/case7.hdr" -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/mixed" >/dev/null
grep -q "evil" "${RAW_DIR}/case7.body" && {
  echo "FAIL: Case 7 - script content leaked" >&2
  exit 1
}
grep -q "javascript:" "${RAW_DIR}/case7.body" && {
  echo "FAIL: Case 7 - javascript: URL leaked" >&2
  exit 1
}
grep -q "https://example.com" "${RAW_DIR}/case7.body" || {
  echo "FAIL: Case 7 - safe link was incorrectly stripped" >&2
  exit 1
}
echo "  PASS: Mixed dangerous content stripped, safe content preserved"

# --- Case 8: Control characters in URLs are neutralized ---
echo "==> Case 8: Control-character URL is neutralized"
curl -sS -D "${RAW_DIR}/case8.hdr" -o "${RAW_DIR}/case8.body" \
  -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/control-url" >/dev/null
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/case8.hdr" || {
  echo "FAIL: Case 8 - expected markdown Content-Type" >&2
  exit 1
}
grep -Eiq '%0[01]|https://example\.com/%00evil' "${RAW_DIR}/case8.body" && {
  echo "FAIL: Case 8 - control-character URL leaked into Markdown output" >&2
  exit 1
}
if [[ -n "$(LC_ALL=C tr -d '\011\012\015\040-\176' < "${RAW_DIR}/case8.body")" ]]; then
  echo "FAIL: Case 8 - raw control character leaked into Markdown output" >&2
  exit 1
fi
echo "  PASS: Control-character URL neutralized"

echo ""
echo "========================================"
echo "All security E2E tests passed!"
echo "========================================"
