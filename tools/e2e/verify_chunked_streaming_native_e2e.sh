#!/bin/bash
set -euo pipefail

# Native-only E2E validation for chunked/streaming upstream responses.
#
# Validates six critical paths:
#  1) Chunked body below markdown_max_size converts successfully to Markdown.
#  2) Chunked body above markdown_max_size triggers fail-open without truncation.
#  3) Streaming + gzip decompression converts to Markdown and strips
#     Content-Encoding.
#  4) Streaming + deflate decompression converts to Markdown and strips
#     Content-Encoding.
#  5) Truncated gzip stream triggers decomp finalize failure and fail-open.
#  6) Truncated deflate stream triggers decomp finalize failure and fail-open.

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18094}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19094}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
MARKDOWN_MAX_SIZE="${MARKDOWN_MAX_SIZE:-10m}"
PROFILE="${PROFILE:-smoke}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
UPSTREAM_PID=""
ORIG_ARGS=("$@")
ACCEPT_MARKDOWN_HEADER='Accept: text/markdown'
readonly CURL_METRICS_FMT='http=%{http_code} size=%{size_download} total=%{time_total}\n'
readonly PATTERN_HTTP_200='http=200 '
readonly PATTERN_CT_MARKDOWN='^Content-Type: text/markdown; charset=utf-8'
readonly PATTERN_CT_HTML='^Content-Type: text/html'
readonly PATTERN_TRANSFER_CHUNKED='^Transfer-Encoding:.*chunked'
readonly PATTERN_CONTENT_LENGTH='^Content-Length:'
readonly PATTERN_CONTENT_ENCODING='^Content-Encoding:'
# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT] [--markdown-max-size SIZE]
                          [--profile smoke|stress]

Build local NGINX with the markdown module and run chunked/streaming E2E checks:
  - chunked below max_size -> Markdown conversion success
  - chunked above max_size -> fail-open pass-through without truncation

This script auto-reexecs under native arm64 on Apple Silicon if launched under Rosetta.
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

#
# Validate a streaming markdown HTTP response contract.
# Args:
#   case_name: logical case label used in diagnostics
#   hdr_file: response header artifact path
#   body_file: response body artifact path
#   heading: required markdown heading token in body
#   end_token: optional tail token expected in body
# Output/exit:
#   emits diagnostics to stderr and exits non-zero on assertion failure;
#   returns 0 when all assertions pass.
#
assert_streaming_markdown_response() {
  local case_name="$1"
  local hdr_file="$2"
  local body_file="$3"
  local heading="$4"
  local end_token="${5:-}"

  grep -qi "${PATTERN_CT_MARKDOWN}" "${hdr_file}" || {
    echo "${case_name} expected markdown Content-Type" >&2
    exit 1
  }
  grep -qi "${PATTERN_TRANSFER_CHUNKED}" "${hdr_file}" || {
    echo "${case_name} missing chunked transfer encoding header" >&2
    exit 1
  }
  grep -qi "${PATTERN_CONTENT_LENGTH}" "${hdr_file}" && {
    echo "${case_name} unexpected Content-Length in streaming response" >&2
    exit 1
  }
  grep -qi "${PATTERN_CONTENT_ENCODING}" "${hdr_file}" && {
    echo "${case_name} response leaked Content-Encoding header" >&2
    exit 1
  }
  grep -q "${heading}" "${body_file}" || {
    echo "${case_name} missing converted heading marker" >&2
    exit 1
  }
  if [[ -n "${end_token}" ]]; then
    grep -q "${end_token}" "${body_file}" || {
      echo "${case_name} missing tail marker after conversion" >&2
      exit 1
    }
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
    echo "Chunked/streaming E2E validation failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Chunked/streaming E2E validation succeeded. Artifacts kept at: ${BUILDROOT}"
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
    --profile)
      require_flag_value "$1" "${2-}"
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
  markdown_ensure_native_apple_silicon "$0" "${ORIG_ARGS[@]}"
else
  markdown_ensure_native_apple_silicon "$0"
fi

for cmd in curl python3 awk grep sed wc; do
  markdown_need_cmd "$cmd"
done
if [[ "${PROFILE}" == "stress" ]]; then
  markdown_need_cmd ab
fi
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-chunked-native.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
UPSTREAM_SCRIPT="${BUILDROOT}/chunked_upstream.py"
mkdir -p "${RAW_DIR}"

cat > "${UPSTREAM_SCRIPT}" <<'PY'
#!/usr/bin/env python3
import argparse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SMALL_END_TOKEN = "SMALL_STREAM_END_TOKEN"
OVERSIZE_END_TOKEN = "OVERSIZE_STREAM_END_TOKEN"
GZIP_END_TOKEN = "GZIP_STREAM_END_TOKEN"
DEFLATE_END_TOKEN = "DEFLATE_STREAM_END_TOKEN"
SMALL_TARGET = 2 * 1024 * 1024
COMPRESSED_TARGET = 64 * 1024
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


def compress_payload(body: bytes, mode: str) -> bytes:
    if mode == "gzip":
        wbits = zlib.MAX_WBITS | 16
    elif mode == "deflate":
        wbits = -zlib.MAX_WBITS
    else:
        raise ValueError(f"unsupported mode: {mode}")

    compressor = zlib.compressobj(level=6, wbits=wbits)
    return compressor.compress(body) + compressor.flush()


SMALL_BODY = build_payload("Chunked Small", SMALL_TARGET, SMALL_END_TOKEN)
OVERSIZE_BODY = build_payload("Chunked Oversize", OVERSIZE_TARGET, OVERSIZE_END_TOKEN)
GZIP_SOURCE_BODY = build_payload(
    "Chunked Gzip", COMPRESSED_TARGET, GZIP_END_TOKEN
)
DEFLATE_SOURCE_BODY = build_payload(
    "Chunked Deflate", COMPRESSED_TARGET, DEFLATE_END_TOKEN
)
GZIP_BODY = compress_payload(GZIP_SOURCE_BODY, "gzip")
DEFLATE_BODY = compress_payload(DEFLATE_SOURCE_BODY, "deflate")
TRUNCATED_GZIP_BODY = GZIP_BODY[:-8] if len(GZIP_BODY) > 8 else GZIP_BODY
TRUNCATED_DEFLATE_BODY = (
    DEFLATE_BODY[:-4] if len(DEFLATE_BODY) > 4 else DEFLATE_BODY
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        return

    def _write_chunked(self, body: bytes, content_encoding=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
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
        if path == "/small-gzip":
            self._write_chunked(GZIP_BODY, content_encoding="gzip")
            return
        if path == "/small-deflate":
            self._write_chunked(DEFLATE_BODY, content_encoding="deflate")
            return
        if path == "/truncated-gzip":
            self._write_chunked(TRUNCATED_GZIP_BODY, content_encoding="gzip")
            return
        if path == "/truncated-deflate":
            self._write_chunked(
                TRUNCATED_DEFLATE_BODY, content_encoding="deflate"
            )
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
        if path == "/small-gzip":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            return
        if path == "/small-deflate":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Encoding", "deflate")
            self.end_headers()
            return
        if path == "/truncated-gzip":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            return
        if path == "/truncated-deflate":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Encoding", "deflate")
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
        print(f"GZIP_SOURCE_LEN={len(GZIP_SOURCE_BODY)}")
        print(f"GZIP_COMPRESSED_LEN={len(GZIP_BODY)}")
        print(f"DEFLATE_SOURCE_LEN={len(DEFLATE_SOURCE_BODY)}")
        print(f"DEFLATE_COMPRESSED_LEN={len(DEFLATE_BODY)}")
        print(f"TRUNCATED_GZIP_COMPRESSED_LEN={len(TRUNCATED_GZIP_BODY)}")
        print(
            f"TRUNCATED_DEFLATE_COMPRESSED_LEN="
            f"{len(TRUNCATED_DEFLATE_BODY)}"
        )
        print(f"SMALL_END_TOKEN={SMALL_END_TOKEN}")
        print(f"OVERSIZE_END_TOKEN={OVERSIZE_END_TOKEN}")
        print(f"GZIP_END_TOKEN={GZIP_END_TOKEN}")
        print(f"DEFLATE_END_TOKEN={DEFLATE_END_TOKEN}")
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
if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
  LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
  NGINX_EXECUTABLE="${NGINX_BIN}"
else
  echo "==> Building Rust converter (${RUST_TARGET})"
  markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}" >/dev/null

  echo "==> Downloading/building NGINX ${NGINX_VERSION}"
  curl --proto '=https' --tlsv1.2 -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
  mkdir -p "${BUILDROOT}/src"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
  (
    cd "${BUILDROOT}/src"
    ./configure --without-http_rewrite_module --prefix="${RUNTIME}" --add-module="${WORKSPACE_ROOT}/components/nginx-module" >/dev/null
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
    make install >/dev/null
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs"

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

        location /streaming/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_streaming_engine on;
            markdown_streaming_on_error pass;
            markdown_conditional_requests if_modified_since_only;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_streaming_budget 64m;
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
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Case 1: chunked below max_size should convert to Markdown"
small_line="$(curl -sS -D "${RAW_DIR}/small.hdr" -o "${RAW_DIR}/small.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/stream/small-valid" \
  -w "${CURL_METRICS_FMT}")"
echo "${small_line}" | tee "${RAW_DIR}/small.metrics" >/dev/null
echo "${small_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "small-valid failed: ${small_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/small.hdr" || {
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
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 240 \
  "http://127.0.0.1:${PORT}/stream/oversize" \
  -w "${CURL_METRICS_FMT}")"
echo "${oversize_line}" | tee "${RAW_DIR}/oversize.metrics" >/dev/null
echo "${oversize_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "oversize failed: ${oversize_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/oversize.hdr" || {
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

echo "==> Case 3: streaming gzip decompression should convert to Markdown"
gzip_line="$(curl -sS -D "${RAW_DIR}/gzip.hdr" -o "${RAW_DIR}/gzip.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/small-gzip" \
  -w "${CURL_METRICS_FMT}")"
echo "${gzip_line}" | tee "${RAW_DIR}/gzip.metrics" >/dev/null
echo "${gzip_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "small-gzip failed: ${gzip_line}" >&2; exit 1; }
assert_streaming_markdown_response \
  "small-gzip" "${RAW_DIR}/gzip.hdr" "${RAW_DIR}/gzip.body" \
  "# Chunked Gzip" "${GZIP_END_TOKEN}"

echo "==> Case 4: streaming deflate decompression should convert to Markdown"
deflate_line="$(curl -sS -D "${RAW_DIR}/deflate.hdr" -o "${RAW_DIR}/deflate.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/small-deflate" \
  -w "${CURL_METRICS_FMT}")"
echo "${deflate_line}" | tee "${RAW_DIR}/deflate.metrics" >/dev/null
echo "${deflate_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "small-deflate failed: ${deflate_line}" >&2; exit 1; }
assert_streaming_markdown_response \
  "small-deflate" "${RAW_DIR}/deflate.hdr" "${RAW_DIR}/deflate.body" \
  "# Chunked Deflate" "${DEFLATE_END_TOKEN}"

echo "==> Case 5: truncated gzip should trigger post-commit failure"
trunc_gzip_line="$(curl -sS -D "${RAW_DIR}/trunc_gzip.hdr" -o "${RAW_DIR}/trunc_gzip.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/truncated-gzip" \
  -w "${CURL_METRICS_FMT}" || true)"
echo "${trunc_gzip_line}" | tee "${RAW_DIR}/trunc_gzip.metrics" >/dev/null
echo "${trunc_gzip_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "truncated-gzip failed: ${trunc_gzip_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/trunc_gzip.hdr" || {
  echo "truncated-gzip expected markdown Content-Type (post-commit path)" >&2
  exit 1
}
grep -q '# Chunked Gzip' "${RAW_DIR}/trunc_gzip.body" || {
  echo "truncated-gzip missing converted heading marker" >&2
  exit 1
}

echo "==> Case 6: truncated deflate should trigger post-commit failure"
trunc_deflate_line="$(curl -sS -D "${RAW_DIR}/trunc_deflate.hdr" -o "${RAW_DIR}/trunc_deflate.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/truncated-deflate" \
  -w "${CURL_METRICS_FMT}" || true)"
echo "${trunc_deflate_line}" | tee "${RAW_DIR}/trunc_deflate.metrics" >/dev/null
echo "${trunc_deflate_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "truncated-deflate failed: ${trunc_deflate_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_MARKDOWN}" "${RAW_DIR}/trunc_deflate.hdr" || {
  echo "truncated-deflate expected markdown Content-Type (post-commit path)" >&2
  exit 1
}
grep -q '# Chunked Deflate' "${RAW_DIR}/trunc_deflate.body" || {
  echo "truncated-deflate missing converted heading marker" >&2
  exit 1
}

echo "==> Log sanity checks"
grep -q 'response size exceeds limit' "${RUNTIME}/logs/error.log" || {
  echo "missing size-limit log for chunked oversize case" >&2
  exit 1
}
grep -q 'decomp_finish failed in finalize' "${RUNTIME}/logs/error.log" || {
  echo "missing decomp finalize failure log for truncated compressed cases" >&2
  exit 1
}
grep -q 'reason=STREAMING_FAIL_POSTCOMMIT' "${RUNTIME}/logs/error.log" || {
  echo "missing STREAMING_FAIL_POSTCOMMIT decision log for truncated cases" >&2
  exit 1
}

if [[ "${PROFILE}" == "stress" ]]; then
  echo "==> Stress profile: ApacheBench for chunked conversion and fail-open paths"

  ab -H "${ACCEPT_MARKDOWN_HEADER}" -n 60 -c 6 \
    "http://127.0.0.1:${PORT}/stream/small-valid" > "${RAW_DIR}/ab_small.txt" 2>&1
  ab -H "${ACCEPT_MARKDOWN_HEADER}" -n 20 -c 2 \
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
echo "  gzip_source_html_bytes=${GZIP_SOURCE_LEN}"
echo "  gzip_compressed_bytes=${GZIP_COMPRESSED_LEN}"
echo "  gzip_result=$(cat "${RAW_DIR}/gzip.metrics")"
echo "  deflate_source_html_bytes=${DEFLATE_SOURCE_LEN}"
echo "  deflate_compressed_bytes=${DEFLATE_COMPRESSED_LEN}"
echo "  deflate_result=$(cat "${RAW_DIR}/deflate.metrics")"
echo "  truncated_gzip_compressed_bytes=${TRUNCATED_GZIP_COMPRESSED_LEN}"
echo "  truncated_gzip_result=$(cat "${RAW_DIR}/trunc_gzip.metrics")"
echo "  truncated_deflate_compressed_bytes=${TRUNCATED_DEFLATE_COMPRESSED_LEN}"
echo "  truncated_deflate_result=$(cat "${RAW_DIR}/trunc_deflate.metrics")"
if [[ "${PROFILE}" == "stress" ]]; then
  echo "  stress_small_ab_rps=${small_rps:-unknown}"
  echo "  stress_oversize_ab_rps=${oversize_rps:-unknown}"
fi
echo "  artifacts=${BUILDROOT}"
