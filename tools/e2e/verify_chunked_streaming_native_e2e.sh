#!/bin/bash
set -euo pipefail

# Native-only E2E validation for chunked/streaming upstream responses.
#
# Validates two critical paths:
#  1) Chunked body below markdown_max_size converts successfully to Markdown.
#  2) Chunked body above markdown_max_size triggers fail-open without truncation.

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18094}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19094}"
KEEP_ARTIFACTS=0
MARKDOWN_MAX_SIZE="${MARKDOWN_MAX_SIZE:-10m}"
PROFILE="${PROFILE:-smoke}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
UPSTREAM_PID=""
ORIG_ARGS=("$@")

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT] [--markdown-max-size SIZE]
                          [--profile smoke|stress]

Build local NGINX with the markdown module and run chunked/streaming E2E checks:
  - chunked below max_size -> Markdown conversion success
  - chunked above max_size -> fail-open pass-through without truncation

This script auto-reexecs under native arm64 on Apple Silicon if launched under Rosetta.
EOF
}

ensure_native_apple_silicon() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  local translated
  translated="$(sysctl -in sysctl.proc_translated 2>/dev/null || echo 0)"
  if [[ "${translated}" == "1" ]]; then
    echo "Re-executing under native arm64 (/bin/bash) to avoid Rosetta..." >&2
    exec arch -arm64 /bin/bash "$0" "$@"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

detect_rust_target() {
  case "$(uname -s):$(uname -m)" in
    Darwin:arm64) echo "aarch64-apple-darwin" ;;
    Darwin:x86_64) echo "x86_64-apple-darwin" ;;
    Linux:x86_64) echo "x86_64-unknown-linux-gnu" ;;
    Linux:aarch64) echo "aarch64-unknown-linux-gnu" ;;
    *)
      echo "Unsupported host for Rust target detection: $(uname -s)/$(uname -m)" >&2
      exit 1
      ;;
  esac
}

cleanup() {
  local rc=$?

  if [[ -n "${UPSTREAM_PID}" ]]; then
    kill "${UPSTREAM_PID}" >/dev/null 2>&1 || true
    wait "${UPSTREAM_PID}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${RUNTIME}" && -x "${RUNTIME}/sbin/nginx" ]]; then
    "${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}" || true
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Chunked/streaming E2E validation failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Chunked/streaming E2E validation succeeded. Artifacts kept at: ${BUILDROOT}"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
      ;;
    --nginx-version)
      NGINX_VERSION="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --upstream-port)
      UPSTREAM_PORT="$2"
      shift 2
      ;;
    --markdown-max-size)
      MARKDOWN_MAX_SIZE="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
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

case "${PROFILE}" in
  smoke|stress) ;;
  *)
    echo "Invalid profile: ${PROFILE} (expected: smoke|stress)" >&2
    exit 2
    ;;
esac

if (( ${#ORIG_ARGS[@]} )); then
  ensure_native_apple_silicon "${ORIG_ARGS[@]}"
else
  ensure_native_apple_silicon
fi

for cmd in curl tar make cargo python3 awk grep sed wc; do
  need_cmd "$cmd"
done
if [[ "${PROFILE}" == "stress" ]]; then
  need_cmd ab
fi

RUST_TARGET="$(detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-chunked-native.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/chunked_upstream.py"
mkdir -p "${RAW_DIR}"

cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SMALL_END_TOKEN = "SMALL_STREAM_END_TOKEN"
OVERSIZE_END_TOKEN = "OVERSIZE_STREAM_END_TOKEN"
SMALL_TARGET = 2 * 1024 * 1024
OVERSIZE_TARGET = 12 * 1024 * 1024
CHUNK_SIZE = 16 * 1024


def build_payload(title: str, target_size: int, end_token: str) -> bytes:
    prefix = (
        f'<!doctype html><html><head><meta charset="UTF-8"><title>{title}</title></head>'
        f'<body><h1>{title}</h1><pre>\n'
    ).encode("utf-8")
    suffix = f"\n</pre><p>{end_token}</p></body></html>\n".encode("utf-8")
    fill = (b"chunked-stream-data-0123456789abcdef\n" * 512)

    out = bytearray(prefix)
    remaining = target_size - len(prefix) - len(suffix)
    while remaining > 0:
        piece = fill if remaining >= len(fill) else fill[:remaining]
        out.extend(piece)
        remaining -= len(piece)
    out.extend(suffix)
    return bytes(out)


SMALL_BODY = build_payload("Chunked Small", SMALL_TARGET, SMALL_END_TOKEN)
OVERSIZE_BODY = build_payload("Chunked Oversize", OVERSIZE_TARGET, OVERSIZE_END_TOKEN)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        return

    def _write_chunked(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        for i in range(0, len(body), CHUNK_SIZE):
            chunk = body[i : i + CHUNK_SIZE]
            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

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
        if path == "/small-valid":
            self._write_chunked(SMALL_BODY)
            return
        if path == "/oversize":
            self._write_chunked(OVERSIZE_BODY)
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
        if path in ("/small-valid", "/oversize"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19094)
    parser.add_argument("--print-metrics", action="store_true")
    args = parser.parse_args()

    if args.print_metrics:
        print(f"SMALL_LEN={len(SMALL_BODY)}")
        print(f"OVERSIZE_LEN={len(OVERSIZE_BODY)}")
        print(f"SMALL_END_TOKEN={SMALL_END_TOKEN}")
        print(f"OVERSIZE_END_TOKEN={OVERSIZE_END_TOKEN}")
        return

    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()


if __name__ == "__main__":
    main()
PY

chmod +x "${UPSTREAM_SCRIPT}"
eval "$(python3 "${UPSTREAM_SCRIPT}" --print-metrics)"

echo "==> Host architecture: $(uname -m)"
echo "==> Building Rust converter (${RUST_TARGET})"
(
  cd "${WORKSPACE_ROOT}/components/rust-converter"
  cargo build --target "${RUST_TARGET}" --release >/dev/null
  mkdir -p target/release
  src_lib="target/${RUST_TARGET}/release/libnginx_markdown_converter.a"
  dst_lib="target/release/libnginx_markdown_converter.a"
  if [[ "$(cd "$(dirname "$src_lib")" && pwd)/$(basename "$src_lib")" != "$(cd "$(dirname "$dst_lib")" && pwd)/$(basename "$dst_lib")" ]]; then
    cp "$src_lib" "$dst_lib" 2>/dev/null || true
  fi
)

echo "==> Downloading/building NGINX ${NGINX_VERSION}"
curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
mkdir -p "${BUILDROOT}/src"
tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
(
  cd "${BUILDROOT}/src"
  ./configure --without-http_rewrite_module --prefix="${RUNTIME}" --add-module="${WORKSPACE_ROOT}/components/nginx-module" >/dev/null
  make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
  make install >/dev/null
)

mkdir -p "${RUNTIME}/conf"

echo "==> Starting chunked upstream on 127.0.0.1:${UPSTREAM_PORT}"
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
worker_processes 1;
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

        location /stream/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
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

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Case 1: chunked below max_size should convert to Markdown"
small_line="$(curl -sS -D "${RAW_DIR}/small.hdr" -o "${RAW_DIR}/small.body" \
  -H 'Accept: text/markdown' --max-time 180 \
  "http://127.0.0.1:${PORT}/stream/small-valid" \
  -w 'http=%{http_code} size=%{size_download} total=%{time_total}\n')"
echo "${small_line}" | tee "${RAW_DIR}/small.metrics" >/dev/null
echo "${small_line}" | grep -q 'http=200 ' || { echo "small-valid failed: ${small_line}" >&2; exit 1; }
grep -qi '^Content-Type: text/markdown; charset=utf-8' "${RAW_DIR}/small.hdr" || {
  echo "small-valid expected markdown Content-Type" >&2
  exit 1
}
grep -q '# Chunked Small' "${RAW_DIR}/small.body" || {
  echo "small-valid missing converted heading marker" >&2
  exit 1
}
grep -q "${SMALL_END_TOKEN}" "${RAW_DIR}/small.body" || {
  echo "small-valid missing tail marker after conversion" >&2
  exit 1
}

echo "==> Case 2: chunked above max_size should fail-open without truncation"
oversize_line="$(curl -sS -D "${RAW_DIR}/oversize.hdr" -o "${RAW_DIR}/oversize.body" \
  -H 'Accept: text/markdown' --max-time 240 \
  "http://127.0.0.1:${PORT}/stream/oversize" \
  -w 'http=%{http_code} size=%{size_download} total=%{time_total}\n')"
echo "${oversize_line}" | tee "${RAW_DIR}/oversize.metrics" >/dev/null
echo "${oversize_line}" | grep -q 'http=200 ' || { echo "oversize failed: ${oversize_line}" >&2; exit 1; }
grep -qi '^Content-Type: text/html' "${RAW_DIR}/oversize.hdr" || {
  echo "oversize expected fail-open pass-through Content-Type text/html" >&2
  exit 1
}

actual_oversize_bytes="$(wc -c < "${RAW_DIR}/oversize.body" | tr -d ' ')"
[[ "${actual_oversize_bytes}" == "${OVERSIZE_LEN}" ]] || {
  echo "oversize body length mismatch: expected ${OVERSIZE_LEN}, got ${actual_oversize_bytes}" >&2
  exit 1
}
grep -q "${OVERSIZE_END_TOKEN}" "${RAW_DIR}/oversize.body" || {
  echo "oversize missing final tail marker (possible truncation)" >&2
  exit 1
}

echo "==> Log sanity checks"
grep -q 'response size exceeds limit' "${RUNTIME}/logs/error.log" || {
  echo "missing size-limit log for chunked oversize case" >&2
  exit 1
}

if [[ "${PROFILE}" == "stress" ]]; then
  echo "==> Stress profile: ApacheBench for chunked conversion and fail-open paths"

  ab -H 'Accept: text/markdown' -n 60 -c 6 \
    "http://127.0.0.1:${PORT}/stream/small-valid" > "${RAW_DIR}/ab_small.txt" 2>&1
  ab -H 'Accept: text/markdown' -n 20 -c 2 \
    "http://127.0.0.1:${PORT}/stream/oversize" > "${RAW_DIR}/ab_oversize.txt" 2>&1

  small_failed="$(awk -F': *' '/Failed requests/ {print $2}' "${RAW_DIR}/ab_small.txt" | tr -d ' ')"
  small_non2xx="$(awk -F': *' '/Non-2xx responses/ {print $2}' "${RAW_DIR}/ab_small.txt" | tr -d ' ')"
  oversize_failed="$(awk -F': *' '/Failed requests/ {print $2}' "${RAW_DIR}/ab_oversize.txt" | tr -d ' ')"
  oversize_non2xx="$(awk -F': *' '/Non-2xx responses/ {print $2}' "${RAW_DIR}/ab_oversize.txt" | tr -d ' ')"

  [[ -n "${small_failed}" ]] || small_failed="0"
  [[ -n "${small_non2xx}" ]] || small_non2xx="0"
  [[ -n "${oversize_failed}" ]] || oversize_failed="0"
  [[ -n "${oversize_non2xx}" ]] || oversize_non2xx="0"

  [[ "${small_failed}" == "0" ]] || { echo "stress small-valid failed requests: ${small_failed}" >&2; exit 1; }
  [[ "${small_non2xx}" == "0" ]] || { echo "stress small-valid non-2xx: ${small_non2xx}" >&2; exit 1; }
  [[ "${oversize_failed}" == "0" ]] || { echo "stress oversize failed requests: ${oversize_failed}" >&2; exit 1; }
  [[ "${oversize_non2xx}" == "0" ]] || { echo "stress oversize non-2xx: ${oversize_non2xx}" >&2; exit 1; }

  small_rps="$(awk -F': *' '/Requests per second/ {print $2}' "${RAW_DIR}/ab_small.txt" | awk '{print $1}')"
  oversize_rps="$(awk -F': *' '/Requests per second/ {print $2}' "${RAW_DIR}/ab_oversize.txt" | awk '{print $1}')"
fi

echo "Chunked/streaming summary:"
echo "  nginx_version=${NGINX_VERSION}"
echo "  arch=$(uname -m)"
echo "  markdown_max_size=${MARKDOWN_MAX_SIZE}"
echo "  profile=${PROFILE}"
echo "  small_expected_html_bytes=${SMALL_LEN}"
echo "  small_result=$(cat "${RAW_DIR}/small.metrics")"
echo "  oversize_expected_html_bytes=${OVERSIZE_LEN}"
echo "  oversize_result=$(cat "${RAW_DIR}/oversize.metrics")"
if [[ "${PROFILE}" == "stress" ]]; then
  echo "  stress_small_ab_rps=${small_rps:-unknown}"
  echo "  stress_oversize_ab_rps=${oversize_rps:-unknown}"
fi
echo "  artifacts=${BUILDROOT}"
