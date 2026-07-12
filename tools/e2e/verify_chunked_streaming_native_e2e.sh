#!/bin/bash
set -euo pipefail

# Native-only E2E validation for chunked/streaming upstream responses.
#
# Validates six critical paths:
#  1) Chunked body below markdown_limits memory converts successfully to Markdown.
#  2) Chunked body above markdown_limits memory triggers fail-open without truncation.
#  3) Gzip under a streaming-enabled location routes through full-buffer
#     decompression and strips Content-Encoding.
#  4) Raw deflate streaming decompression converts to Markdown and strips
#     Content-Encoding.
#  5) Truncated gzip full-buffer decompression fails open.
#  6) Truncated raw deflate stream triggers decomp finalize failure and
#     fail-open.

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
# curl -w format string for capturing HTTP status, download size, and total
# time from each request; used to populate .metrics artifact files.
readonly CURL_METRICS_FMT='http=%{http_code} size=%{size_download} total=%{time_total}\n'
# Grep patterns for validating response headers and status codes.
readonly PATTERN_HTTP_200='http=200 '
readonly PATTERN_CT_MARKDOWN='^Content-Type: text/markdown; charset=utf-8'
readonly PATTERN_CT_HTML='^Content-Type: text/html'
readonly PATTERN_TRANSFER_CHUNKED='^Transfer-Encoding:.*chunked'
# Pattern matching Content-Length header; streaming responses must not
# include this header (they use chunked transfer instead).
readonly PATTERN_CONTENT_LENGTH='^Content-Length:'
# Pattern matching Content-Encoding header; used to verify that successful
# decompression strips the header from the downstream response.
readonly PATTERN_CONTENT_ENCODING='^Content-Encoding:'
# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"
# shellcheck disable=SC1090
source "$(dirname "${BASH_SOURCE[0]}")/e2e_common.sh"

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

#
# Validate a streaming markdown HTTP response contract.
# Args:
#   case_name: logical case label used in diagnostics
#   hdr_file: response header artifact path
#   body_file: response body artifact path
#   heading: required markdown heading token in body
#   end_token: optional tail token expected in body
#   require_chunked: optional, defaults to 1; set to 0 for decompression
#     cases that complete through the known-length full-body path.
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
  local require_chunked="${6:-1}"

  grep -qi "${PATTERN_CT_MARKDOWN}" "${hdr_file}" || {
    echo "${case_name} expected markdown Content-Type" >&2
    exit 1
  }
  if [[ "${require_chunked}" == "1" ]]; then
    grep -qi "${PATTERN_CONTENT_LENGTH}" "${hdr_file}" && {
      echo "${case_name} unexpected Content-Length in streaming response" >&2
      exit 1
    }
    grep -qi "${PATTERN_TRANSFER_CHUNKED}" "${hdr_file}" || {
      echo "${case_name} missing chunked transfer encoding header" >&2
      exit 1
    }
  fi
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

#
# Trap handler: stop upstream and NGINX, then optionally remove build artifacts.
# Arguments: none (reads global UPSTREAM_PID, RUNTIME, NGINX_EXECUTABLE, etc.)
# Output: diagnostic message to stderr on failure with artifact path
# Exit: returns the original trap exit code
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
    --markdown-max-size)
      markdown_require_flag_value "$1" "${2-}"
      MARKDOWN_MAX_SIZE="$2"
      shift 2
      ;;
    --profile)
      markdown_require_flag_value "$1" "${2-}"
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

for cmd in curl python3 awk grep pgrep sed wc; do
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
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SMALL_END_TOKEN = "SMALL_STREAM_END_TOKEN"
ZERO_COPY_END_TOKEN = "ZERO_COPY_STREAM_END_TOKEN"
OVERSIZE_END_TOKEN = "OVERSIZE_STREAM_END_TOKEN"
DELAYED_END_TOKEN = "DELAYED_OVERSIZE_STREAM_END_TOKEN"
GZIP_END_TOKEN = "GZIP_STREAM_END_TOKEN"
DEFLATE_END_TOKEN = "DEFLATE_STREAM_END_TOKEN"
DEFLATE_ZLIB_END_TOKEN = "DEFLATE_ZLIB_STREAM_END_TOKEN"
SMALL_TARGET = 2 * 1024 * 1024
ZERO_COPY_TARGET = 1024 * 1024
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

def build_streaming_payload(title: str, target_size: int, end_token: str) -> bytes:
    prefix = f"<!doctype html><html><body><h1>{title}</h1>\n".encode("utf-8")
    suffix = f"<p>{end_token}</p></body></html>\n".encode("utf-8")
    block = b"<p>zero-copy-stream-data-0123456789abcdef</p>\n"
    out = bytearray(prefix)
    while len(out) + len(block) + len(suffix) <= target_size:
        out.extend(block)
    out.extend(suffix)
    return bytes(out)

def compress_payload(body: bytes, mode: str) -> bytes:
    if mode == "gzip":
        wbits = zlib.MAX_WBITS | 16
    elif mode == "deflate":
        # raw deflate (RFC 1951, no zlib wrapper)
        wbits = -zlib.MAX_WBITS
    elif mode == "deflate-zlib":
        # zlib-wrapped deflate (RFC 1950, RFC 9110-compliant)
        wbits = zlib.MAX_WBITS
    else:
        raise ValueError(f"unsupported mode: {mode}")

    compressor = zlib.compressobj(level=6, wbits=wbits)
    return compressor.compress(body) + compressor.flush()

SMALL_BODY = build_payload("Chunked Small", SMALL_TARGET, SMALL_END_TOKEN)
ZERO_COPY_BODY = build_streaming_payload(
    "Zero Copy Streaming", ZERO_COPY_TARGET, ZERO_COPY_END_TOKEN
)
OVERSIZE_BODY = build_payload("Chunked Oversize", OVERSIZE_TARGET, OVERSIZE_END_TOKEN)
DELAYED_BODY = build_payload(
    "Delayed Chunked Oversize", OVERSIZE_TARGET, DELAYED_END_TOKEN
)
GZIP_SOURCE_BODY = build_payload(
    "Chunked Gzip", COMPRESSED_TARGET, GZIP_END_TOKEN
)
DEFLATE_SOURCE_BODY = build_payload(
    "Chunked Deflate", COMPRESSED_TARGET, DEFLATE_END_TOKEN
)
DEFLATE_ZLIB_SOURCE_BODY = build_payload(
    "Chunked Zlib Deflate", COMPRESSED_TARGET, DEFLATE_ZLIB_END_TOKEN
)
GZIP_BODY = compress_payload(GZIP_SOURCE_BODY, "gzip")
DEFLATE_BODY = compress_payload(DEFLATE_SOURCE_BODY, "deflate")
DEFLATE_ZLIB_BODY = compress_payload(DEFLATE_ZLIB_SOURCE_BODY, "deflate-zlib")
TRUNCATED_GZIP_BODY = GZIP_BODY[:-8] if len(GZIP_BODY) > 8 else GZIP_BODY
TRUNCATED_DEFLATE_BODY = DEFLATE_BODY[: max(1, len(DEFLATE_BODY) // 2)]
TRUNCATED_DEFLATE_ZLIB_BODY = DEFLATE_ZLIB_BODY[: max(1, len(DEFLATE_ZLIB_BODY) // 2)]

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

    def _write_delayed_oversize(self):
        split = 10 * 1024 * 1024
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        for part in (DELAYED_BODY[:split], DELAYED_BODY[split:]):
            self.wfile.write(f"{len(part):X}\r\n".encode("ascii"))
            self.wfile.write(part)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
            time.sleep(0.2)
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
        if path == "/zero-copy-valid":
            self._write_chunked(ZERO_COPY_BODY)
            return
        if path == "/oversize":
            self._write_chunked(OVERSIZE_BODY)
            return
        if path == "/delayed-oversize":
            self._write_delayed_oversize()
            return
        if path == "/small-gzip":
            self._write_chunked(GZIP_BODY, content_encoding="gzip")
            return
        if path == "/small-deflate":
            self._write_chunked(DEFLATE_BODY, content_encoding="deflate")
            return
        if path == "/small-deflate-zlib":
            self._write_chunked(DEFLATE_ZLIB_BODY, content_encoding="deflate")
            return
        if path == "/truncated-gzip":
            self._write_chunked(TRUNCATED_GZIP_BODY, content_encoding="gzip")
            return
        if path == "/truncated-deflate":
            self._write_chunked(
                TRUNCATED_DEFLATE_BODY, content_encoding="deflate"
            )
            return
        if path == "/truncated-deflate-zlib":
            self._write_chunked(
                TRUNCATED_DEFLATE_ZLIB_BODY, content_encoding="deflate"
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
        if path == "/small-deflate-zlib":
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
        if path == "/truncated-deflate-zlib":
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
        print(f"ZERO_COPY_LEN={len(ZERO_COPY_BODY)}")
        print(f"OVERSIZE_LEN={len(OVERSIZE_BODY)}")
        print(f"DELAYED_LEN={len(DELAYED_BODY)}")
        print(f"GZIP_SOURCE_LEN={len(GZIP_SOURCE_BODY)}")
        print(f"GZIP_COMPRESSED_LEN={len(GZIP_BODY)}")
        print(f"DEFLATE_SOURCE_LEN={len(DEFLATE_SOURCE_BODY)}")
        print(f"DEFLATE_COMPRESSED_LEN={len(DEFLATE_BODY)}")
        print(f"DEFLATE_ZLIB_SOURCE_LEN={len(DEFLATE_ZLIB_SOURCE_BODY)}")
        print(f"DEFLATE_ZLIB_COMPRESSED_LEN={len(DEFLATE_ZLIB_BODY)}")
        print(f"TRUNCATED_GZIP_COMPRESSED_LEN={len(TRUNCATED_GZIP_BODY)}")
        print(
            f"TRUNCATED_DEFLATE_COMPRESSED_LEN="
            f"{len(TRUNCATED_DEFLATE_BODY)}"
        )
        print(
            f"TRUNCATED_DEFLATE_ZLIB_COMPRESSED_LEN="
            f"{len(TRUNCATED_DEFLATE_ZLIB_BODY)}"
        )
        print(f"SMALL_END_TOKEN={SMALL_END_TOKEN}")
        print(f"ZERO_COPY_END_TOKEN={ZERO_COPY_END_TOKEN}")
        print(f"OVERSIZE_END_TOKEN={OVERSIZE_END_TOKEN}")
        print(f"DELAYED_END_TOKEN={DELAYED_END_TOKEN}")
        print(f"GZIP_END_TOKEN={GZIP_END_TOKEN}")
        print(f"DEFLATE_END_TOKEN={DEFLATE_END_TOKEN}")
        print(f"DEFLATE_ZLIB_END_TOKEN={DEFLATE_ZLIB_END_TOKEN}")
        return

    if args.serve:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.serve_forever()

if __name__ == "__main__":
    main()
PY

chmod +x "${UPSTREAM_SCRIPT}"

load_upstream_metrics() {
  local key
  local value

  while IFS='=' read -r key value; do
    case "${key}" in
      SMALL_LEN|ZERO_COPY_LEN|OVERSIZE_LEN|DELAYED_LEN|GZIP_SOURCE_LEN|GZIP_COMPRESSED_LEN|DEFLATE_SOURCE_LEN|DEFLATE_COMPRESSED_LEN|DEFLATE_ZLIB_SOURCE_LEN|DEFLATE_ZLIB_COMPRESSED_LEN|TRUNCATED_GZIP_COMPRESSED_LEN|TRUNCATED_DEFLATE_COMPRESSED_LEN|TRUNCATED_DEFLATE_ZLIB_COMPRESSED_LEN)
        [[ "${value}" =~ ^[0-9]+$ ]] || {
          echo "invalid numeric upstream metric: ${key}=${value}" >&2
          return 1
        }
        ;;
      SMALL_END_TOKEN|ZERO_COPY_END_TOKEN|OVERSIZE_END_TOKEN|DELAYED_END_TOKEN|GZIP_END_TOKEN|DEFLATE_END_TOKEN|DEFLATE_ZLIB_END_TOKEN)
        [[ "${value}" =~ ^[A-Z0-9_]+$ ]] || {
          echo "invalid upstream token metric: ${key}=${value}" >&2
          return 1
        }
        ;;
      "")
        continue
        ;;
      *)
        echo "unexpected upstream metric key: ${key}" >&2
        return 1
        ;;
    esac
    printf -v "${key}" '%s' "${value}"
  done < <(python3 "${UPSTREAM_SCRIPT}" --print-metrics)

  return 0
}

load_upstream_metrics

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
  curl --proto '=https' --tlsv1.2 --max-time 600 --connect-timeout 30 -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
  mkdir -p "${BUILDROOT}/src"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
  (
    cd "${BUILDROOT}/src"
    configure_arg=""
    markdown_export_nginx_dependency_env
    cc_opt="${NGINX_CC_OPT:-}"
    cc_opt="${cc_opt:+${cc_opt} }${CPPFLAGS:-}"
    cc_opt="${cc_opt:+${cc_opt} }-DMARKDOWN_STREAMING_ENABLED"
    ld_opt="${NGINX_LD_OPT:-}"
    ld_opt="${ld_opt:+${ld_opt} }${LDFLAGS:-}"
    configure_args=(
      --without-http_rewrite_module
      --prefix="${RUNTIME}"
      --add-module="${WORKSPACE_ROOT}/components/nginx-module"
    )
    while IFS= read -r configure_arg; do
      configure_args+=("${configure_arg}")
    done < <(markdown_emit_nginx_configure_env "${cc_opt}" "${ld_opt}")
    if ! ./configure "${configure_args[@]}" > "${RAW_DIR}/nginx-build.log" 2>&1 \
      || ! make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >> "${RAW_DIR}/nginx-build.log" 2>&1 \
      || ! make install >> "${RAW_DIR}/nginx-build.log" 2>&1
    then
      markdown_print_nginx_build_failure_diagnostics \
        "${BUILDROOT}" "${RAW_DIR}/nginx-build.log"
      exit 1
    fi
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs"

echo "==> Starting chunked upstream on 127.0.0.1:${UPSTREAM_PORT}"
python3 "${UPSTREAM_SCRIPT}" --serve --host 127.0.0.1 --port "${UPSTREAM_PORT}" > "${RAW_DIR}/upstream.log" 2>&1 &
UPSTREAM_PID=$!
markdown_wait_for_http "http://127.0.0.1:${UPSTREAM_PORT}/health" "Upstream on ${UPSTREAM_PORT}" || exit 1

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
            markdown_accept wildcard;
            markdown_cache_validation full;
            markdown_limits memory=${MARKDOWN_MAX_SIZE} timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        location /streaming/ {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming force;
            markdown_streaming_engine on;
            markdown_cache_validation ims_only;
            markdown_limits memory=${MARKDOWN_MAX_SIZE} streaming_buffer=64m timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        location /streaming-zero-copy/ {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming force;
            markdown_streaming_engine on;
            markdown_streaming_zero_copy on;
            markdown_stream_precommit_buffer 4m;
            markdown_cache_validation ims_only;
            markdown_limits memory=${MARKDOWN_MAX_SIZE} streaming_buffer=64m timeout=120s;
            markdown_error_policy pass;
            markdown_log_verbosity info;

            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_set_header Connection "";
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT}/;
        }

        location /markdown-metrics {
            markdown_metrics;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/stream/small-valid" "NGINX" || exit 1

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

echo "==> Case 2b: oversized fail-open must accept later upstream chunks"
delayed_line="$(curl -sS -D "${RAW_DIR}/delayed.hdr" \
  -o "${RAW_DIR}/delayed.body" -H "${ACCEPT_MARKDOWN_HEADER}" \
  --max-time 240 "http://127.0.0.1:${PORT}/stream/delayed-oversize" \
  -w "${CURL_METRICS_FMT}")"
echo "${delayed_line}" | grep -q "${PATTERN_HTTP_200}" || {
  echo "delayed oversize failed: ${delayed_line}" >&2
  exit 1
}
actual_delayed_bytes="$(wc -c < "${RAW_DIR}/delayed.body" | tr -d ' ')"
[[ "${actual_delayed_bytes}" == "${DELAYED_LEN}" ]] || {
  echo "delayed oversize body length mismatch: expected ${DELAYED_LEN}, got ${actual_delayed_bytes}" >&2
  exit 1
}
grep -q "${DELAYED_END_TOKEN}" "${RAW_DIR}/delayed.body" || {
  echo "delayed oversize missing later tail marker (terminal truncation)" >&2
  exit 1
}

echo "==> Case 3: gzip under streaming location uses full-buffer decompression"
gzip_line="$(curl -sS -D "${RAW_DIR}/gzip.hdr" -o "${RAW_DIR}/gzip.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/small-gzip" \
  -w "${CURL_METRICS_FMT}")"
echo "${gzip_line}" | tee "${RAW_DIR}/gzip.metrics" >/dev/null
echo "${gzip_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "small-gzip failed: ${gzip_line}" >&2; exit 1; }
assert_streaming_markdown_response \
  "small-gzip" "${RAW_DIR}/gzip.hdr" "${RAW_DIR}/gzip.body" \
  "# Chunked Gzip" "${GZIP_END_TOKEN}" 0

echo "==> Case 4: raw deflate streaming decompression should convert to Markdown"
deflate_line="$(curl -sS -D "${RAW_DIR}/deflate.hdr" -o "${RAW_DIR}/deflate.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/small-deflate" \
  -w "${CURL_METRICS_FMT}")"
echo "${deflate_line}" | tee "${RAW_DIR}/deflate.metrics" >/dev/null
echo "${deflate_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "small-deflate failed: ${deflate_line}" >&2; exit 1; }
assert_streaming_markdown_response \
  "small-deflate" "${RAW_DIR}/deflate.hdr" "${RAW_DIR}/deflate.body" \
  "# Chunked Deflate" "${DEFLATE_END_TOKEN}" 0

echo "==> Case 4b: zlib-wrapped deflate (RFC 9110) streaming should convert to Markdown"
deflate_zlib_line="$(curl -sS -D "${RAW_DIR}/deflate_zlib.hdr" -o "${RAW_DIR}/deflate_zlib.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/small-deflate-zlib" \
  -w "${CURL_METRICS_FMT}")"
echo "${deflate_zlib_line}" | tee "${RAW_DIR}/deflate_zlib.metrics" >/dev/null
echo "${deflate_zlib_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "small-deflate-zlib failed: ${deflate_zlib_line}" >&2; exit 1; }
assert_streaming_markdown_response \
  "small-deflate-zlib" "${RAW_DIR}/deflate_zlib.hdr" "${RAW_DIR}/deflate_zlib.body" \
  "# Chunked Zlib Deflate" "${DEFLATE_ZLIB_END_TOKEN}" 0

# The zero-copy path is a Stage 1 opt-in optimization in 0.9.1.
# When the test location explicitly configures:
#
#   markdown_streaming_zero_copy on;
#
# the E2E must strictly prove that path works.  A worker crash, response
# corruption, or absent zero-copy output is a release-blocking failure,
# NOT a skip.  Do not weaken assertions to work around a broken runtime —
# build a module-enabled NGINX that actually runs the zero-copy path.
#
# Hard failure conditions:
#   - zero-copy request returns non-200
#   - Markdown/tail corruption
#   - zero_copy_output_total does not increase
#   - worker PID changes during a zero-copy request (master respawned a
#     crashed worker)
#   - error log contains NGINX worker crash exit signatures

# Helper: get the current worker PID.
# nginx.pid stores the MASTER process PID, not the worker PID.
# With worker_processes 1, the single worker is the child of the master.
get_worker_pid() {
  local master_pid=""
  local wpid=""

  if [[ -f "${RUNTIME}/logs/nginx.pid" ]]; then
    master_pid="$(cat "${RUNTIME}/logs/nginx.pid" 2>/dev/null | tr -d '[:space:]')"
  fi
  if [[ -z "${master_pid}" || ! "${master_pid}" =~ ^[0-9]+$ ]]; then
    echo "FAIL: unable to resolve the NGINX master PID" >&2
    return 1
  fi
  # pgrep -P finds children of the master.  This harness configures exactly
  # one worker, so any other result is missing or ambiguous runtime evidence.
  wpid="$(pgrep -P "${master_pid}" 2>/dev/null)"
  if [[ -z "${wpid}" || ! "${wpid}" =~ ^[0-9]+$ ]] \
      || ! kill -0 "${wpid}" 2>/dev/null; then
    echo "FAIL: unable to resolve the single NGINX worker PID" >&2
    return 1
  fi
  echo "${wpid}"
}

# Helper: extract a numeric metric from the metrics endpoint.
# Args: $1 = metric name (e.g. "zero_copy_output_total")
get_perf_metric() {
  local metric_name="$1"
  local metrics_json

  metrics_json="$(curl -s -H 'Accept: application/json' \
    "http://127.0.0.1:${PORT}/markdown-metrics" 2>/dev/null || echo '{}')"
  echo "${metrics_json}" | python3 -c \
    "import sys, json; print(json.load(sys.stdin).get('perf', {}).get('${metric_name}', 0))" \
    2>/dev/null || echo 0
}

# Helper: check for NGINX worker crash signatures in the error log.
# Uses precise patterns matching NGINX source code exit logging:
#   "%s %P exited on signal %d"
#   "%s %P exited on signal %d (core dumped)"  (the "(core dumped)" is part of the format string in some builds)
#   "%s %P exited with fatal code %d and cannot be respawned"
#   "could not respawn worker process"
# Only scans log lines appended after the given byte offset.
# Args: $1 = byte offset to start scanning from
check_worker_crash_log() {
  local offset="$1"
  local error_log="${RUNTIME}/logs/error.log"
  local current_size
  local new_bytes

  if [[ ! -f "${error_log}" ]]; then
    return 0
  fi
  current_size="$(wc -c < "${error_log}" | tr -d '[:space:]')"
  if [[ "${current_size}" -le "${offset}" ]]; then
    return 0
  fi
  # Extract only the log lines appended after the offset.
  new_bytes=$((current_size - offset))
  tail -c "${new_bytes}" "${error_log}" 2>/dev/null \
    | grep -E -i \
      'exited on signal|core dumped|exited with fatal code|could not respawn worker' \
      > "${RAW_DIR}/zc_crash_matches" 2>/dev/null
  if [[ -s "${RAW_DIR}/zc_crash_matches" ]]; then
    return 1
  fi
  return 0
}

echo "==> Case 4c: zero-copy streaming should convert to Markdown with zero_copy_output_total > 0"

zc_output_total=0

# Record the error log byte offset before zero-copy tests so crash-log
# scanning only examines lines appended during zero-copy cases.
zc_log_offset_before=0
if [[ -f "${RUNTIME}/logs/error.log" ]]; then
  zc_log_offset_before="$(wc -c < "${RUNTIME}/logs/error.log" | tr -d '[:space:]')"
fi

# Capture the worker PID before the zero-copy request.
# nginx.pid stores the MASTER PID; the worker is its child (worker_processes 1).
zc_worker_pid_before="$(get_worker_pid)"

zc_line="$(curl -sS -D "${RAW_DIR}/zc.hdr" -o "${RAW_DIR}/zc.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming-zero-copy/zero-copy-valid" \
  -w "${CURL_METRICS_FMT}" || true)"
echo "${zc_line}" | tee "${RAW_DIR}/zc.metrics" >/dev/null

# curl may return non-zero on connection reset; the metrics line is still
# captured via -w.  Check the HTTP status from the metrics line first.
if ! echo "${zc_line}" | grep -q "${PATTERN_HTTP_200}"; then
  echo "FAIL: zero-copy path returned non-200: ${zc_line}" >&2
  exit 1
fi

assert_streaming_markdown_response \
  "zero-copy-small" "${RAW_DIR}/zc.hdr" "${RAW_DIR}/zc.body" \
  "# Zero Copy Streaming" "${ZERO_COPY_END_TOKEN}" 1

# Detect worker crash + respawn by comparing the worker PID before/after.
zc_worker_pid_after="$(get_worker_pid)"
if [[ -n "${zc_worker_pid_before}" && -n "${zc_worker_pid_after}" \
      && "${zc_worker_pid_before}" != "${zc_worker_pid_after}" ]]; then
  echo "FAIL: worker PID changed during zero-copy request" >&2
  echo "  before=${zc_worker_pid_before} after=${zc_worker_pid_after}" >&2
  echo "  worker crashed and master respawned it — zero-copy path is broken" >&2
  exit 1
fi

# Verify that zero_copy_output_total > 0 in the metrics endpoint
# after the zero-copy path was exercised.
zc_output_total="$(get_perf_metric 'zero_copy_output_total')"
if [[ "${zc_output_total}" -le 0 ]]; then
  echo "FAIL: zero-copy path did not produce zero-copy output (zero_copy_output_total=${zc_output_total})" >&2
  exit 1
fi
echo "  zero_copy_output_total=${zc_output_total} (verified > 0)"

# Record metrics before Case 4d for backpressure assertions.
zc_backpressure_before="$(get_perf_metric 'backpressure_total')"
zc_backpressure_resume_before="$(get_perf_metric 'backpressure_resume_total')"

echo "==> Case 4d: zero-copy with slow downstream client (backpressure/NGX_AGAIN resume)"
# To force the module to suspend (NGX_AGAIN) and resume, we need
# downstream write pressure — the client must read slowly enough that
# NGINX's downstream send buffer fills and the zero-copy output chain
# cannot be delivered immediately.  We use a Python slow-reader that:
#   1. Sends the request with Accept: text/markdown
#   2. Reads response headers
#   3. Throttles body reads below the loopback producer rate
#   4. Verifies the full Markdown content and tail token
#
# After the request, we assert:
#   - zero_copy_output_total increased (zero-copy path was used)
#   - backpressure_total increased (NGX_AGAIN occurred)
#   - backpressure_resume_total increased (resume path executed)
#   - worker PID unchanged (no crash)
if ! python3 - "${PORT}" "${RAW_DIR}/zc_slow.hdr" \
  "${RAW_DIR}/zc_slow.body" <<'PYEOF' \
  > "${RAW_DIR}/zc_slow_reader.log" 2>&1
import http.client
import socket
import sys
import time

port = int(sys.argv[1])
header_path = sys.argv[2]
body_path = sys.argv[3]
rate_bytes_per_second = 128 * 1024
deadline = time.monotonic() + 60

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
sock.settimeout(10)
sock.connect(("127.0.0.1", port))
request = (
    "GET /streaming-zero-copy/zero-copy-valid HTTP/1.1\r\n"
    "Host: localhost\r\n"
    "Accept: text/markdown\r\n"
    "Connection: close\r\n\r\n"
)
sock.sendall(request.encode("ascii"))

# Let the deliberately small receive window fill before consuming headers.
# This creates real downstream write pressure on fast loopback runners.
time.sleep(1)
response = http.client.HTTPResponse(sock)
response.begin()

with open(header_path, "w", encoding="iso-8859-1") as header_file:
    header_file.write(f"HTTP/1.1 {response.status} {response.reason}\r\n")
    for name, value in response.getheaders():
        header_file.write(f"{name}: {value}\r\n")
    header_file.write("\r\n")

with open(body_path, "wb") as body_file:
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("slow reader exceeded 60-second deadline")
        started = time.monotonic()
        chunk = response.read(16 * 1024)
        if not chunk:
            break
        body_file.write(chunk)
        delay = (len(chunk) / rate_bytes_per_second) \
            - (time.monotonic() - started)
        if delay > 0:
            time.sleep(delay)

response.close()
sock.close()
if response.status != 200:
    raise RuntimeError(f"unexpected HTTP status: {response.status}")
PYEOF
then
  echo "FAIL: zero-copy slow downstream reader failed:" >&2
  cat "${RAW_DIR}/zc_slow_reader.log" >&2
  exit 1
fi
assert_streaming_markdown_response \
  "zero-copy-slow" "${RAW_DIR}/zc_slow.hdr" "${RAW_DIR}/zc_slow.body" \
  "# Zero Copy Streaming" "${ZERO_COPY_END_TOKEN}" 1
echo "OK: zero-copy throttled reader received complete valid Markdown" \
  > "${RAW_DIR}/zc_slow_reader.log"
cat "${RAW_DIR}/zc_slow_reader.log"

# Assert backpressure metrics increased.
zc_backpressure_after="$(get_perf_metric 'backpressure_total')"
zc_backpressure_resume_after="$(get_perf_metric 'backpressure_resume_total')"
if [[ "${zc_backpressure_after}" -le "${zc_backpressure_before}" ]]; then
  echo "FAIL: backpressure_total did not increase during slow downstream (before=${zc_backpressure_before}, after=${zc_backpressure_after})" >&2
  echo "  NGX_AGAIN resume path was not exercised — zero-copy backpressure is unproven" >&2
  exit 1
fi
echo "  backpressure_total: ${zc_backpressure_before} -> ${zc_backpressure_after}"
if [[ "${zc_backpressure_resume_after}" -le "${zc_backpressure_resume_before}" ]]; then
  echo "FAIL: backpressure_resume_total did not increase (before=${zc_backpressure_resume_before}, after=${zc_backpressure_resume_after})" >&2
  echo "  resume path was not executed — zero-copy backpressure is unproven" >&2
  exit 1
fi
echo "  backpressure_resume_total: ${zc_backpressure_resume_before} -> ${zc_backpressure_resume_after}"

# Verify worker PID unchanged after slow downstream.
zc_slow_pid_after="$(get_worker_pid)"
if [[ -n "${zc_worker_pid_after}" && -n "${zc_slow_pid_after}" \
      && "${zc_worker_pid_after}" != "${zc_slow_pid_after}" ]]; then
  echo "FAIL: worker PID changed during zero-copy slow downstream" >&2
  exit 1
fi

echo "==> Case 4e: zero-copy client abort during streaming (pool cleanup)"
# Use a deterministic socket-level client that:
#   1. Sends the request
#   2. Waits for response headers and a small amount of body
#   3. Closes the socket immediately (simulating client disconnect)
# This guarantees the abort actually happens, unlike curl --max-time
# which may complete before the timeout on fast loopback.
zc_abort_worker_pid_before="$(get_worker_pid)"

if ! python3 - <<PYEOF > "${RAW_DIR}/zc_abort_client.log" 2>&1
import socket
import sys
import time

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
sock.connect(("127.0.0.1", ${PORT}))

request = (
    "GET /streaming-zero-copy/zero-copy-valid HTTP/1.1\r\n"
    "Host: localhost\r\n"
    "Accept: text/markdown\r\n"
    "Connection: close\r\n"
    "\r\n"
)
sock.sendall(request.encode())

# Read response headers and a small amount of body, then abort.
received = b""
try:
    while len(received) < 4096:
        chunk = sock.recv(4096)
        if not chunk:
            break
        received += chunk
        # Read just enough to confirm the response started, then close.
        if b"\r\n\r\n" in received and len(received) > 1024:
            break
except socket.timeout:
    pass

sock.close()

if len(received) == 0:
    print("FAIL: abort client received no data before close", file=sys.stderr)
    sys.exit(1)

if b"HTTP/1.1 200" not in received[:64]:
    print("FAIL: abort client did not receive 200 status", file=sys.stderr)
    sys.exit(1)

print(f"OK: abort client received {len(received)} bytes then closed socket")
PYEOF
then
  echo "FAIL: zero-copy deterministic client abort failed:" >&2
  cat "${RAW_DIR}/zc_abort_client.log" >&2
  exit 1
fi
cat "${RAW_DIR}/zc_abort_client.log"

# Wait briefly for NGINX to process the disconnect and run pool cleanup.
sleep 0.5

# The key assertion is that NGINX does not crash — verify by making a
# subsequent successful request to the same location.
zc_post_abort_line="$(curl -sS -D "${RAW_DIR}/zc_post_abort.hdr" \
  -o "${RAW_DIR}/zc_post_abort.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming-zero-copy/zero-copy-valid" \
  -w "${CURL_METRICS_FMT}" || true)"
echo "${zc_post_abort_line}" | tee "${RAW_DIR}/zc_post_abort.metrics" >/dev/null
if ! echo "${zc_post_abort_line}" | grep -q "${PATTERN_HTTP_200}"; then
  echo "FAIL: zero-copy post-abort request returned non-200: ${zc_post_abort_line}" >&2
  echo "  NGINX may have crashed after client abort on zero-copy path" >&2
  exit 1
fi
assert_streaming_markdown_response \
  "zero-copy-post-abort" "${RAW_DIR}/zc_post_abort.hdr" "${RAW_DIR}/zc_post_abort.body" \
  "# Zero Copy Streaming" "${ZERO_COPY_END_TOKEN}" 1
echo "  zero-copy post-abort: NGINX survived client abort, subsequent request OK"

# Verify worker PID unchanged after abort.
zc_abort_worker_pid_after="$(get_worker_pid)"
if [[ -n "${zc_abort_worker_pid_before}" && -n "${zc_abort_worker_pid_after}" \
      && "${zc_abort_worker_pid_before}" != "${zc_abort_worker_pid_after}" ]]; then
  echo "FAIL: worker PID changed during zero-copy client abort" >&2
  exit 1
fi

# Scan only the log lines appended during zero-copy tests for precise
# NGINX worker crash exit signatures.
if ! check_worker_crash_log "${zc_log_offset_before}"; then
  echo "FAIL: error log contains NGINX worker crash signature during zero-copy tests:" >&2
  cat "${RAW_DIR}/zc_crash_matches" >&2
  exit 1
fi

echo "==> Case 5: truncated gzip full-buffer path should fail open"
# The upstream sends a gzip stream with the final 8 bytes removed, so
# decompression fails before the compressed full-body path can commit
# Markdown headers. The module must fail open by preserving the upstream
# compressed HTML payload and Content-Encoding.
trunc_gzip_line="$(curl -sS -D "${RAW_DIR}/trunc_gzip.hdr" -o "${RAW_DIR}/trunc_gzip.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/truncated-gzip" \
  -w "${CURL_METRICS_FMT}" || true)"
# || true: under set -e, curl may return non-zero when the server closes
# the connection after a truncated stream; suppress so assertions can run.
echo "${trunc_gzip_line}" | tee "${RAW_DIR}/trunc_gzip.metrics" >/dev/null
echo "${trunc_gzip_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "truncated-gzip failed: ${trunc_gzip_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/trunc_gzip.hdr" || {
  echo "truncated-gzip expected HTML Content-Type after fail-open" >&2
  exit 1
}
grep -qi '^Content-Encoding: gzip' "${RAW_DIR}/trunc_gzip.hdr" || {
  echo "truncated-gzip expected preserved gzip Content-Encoding" >&2
  exit 1
}
actual_trunc_gzip_bytes="$(wc -c < "${RAW_DIR}/trunc_gzip.body" | tr -d '[:space:]')"
if [[ "${actual_trunc_gzip_bytes}" != "${TRUNCATED_GZIP_COMPRESSED_LEN}" ]]; then
  echo "truncated-gzip compressed length mismatch: expected ${TRUNCATED_GZIP_COMPRESSED_LEN}, got ${actual_trunc_gzip_bytes}" >&2
  exit 1
fi

echo "==> Case 6: truncated raw deflate streaming path should fail open"
# The raw deflate fixture is cut in the middle of the compressed stream
# so streaming decompression fails before Markdown headers are committed.
trunc_deflate_line="$(curl -sS -D "${RAW_DIR}/trunc_deflate.hdr" -o "${RAW_DIR}/trunc_deflate.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/truncated-deflate" \
  -w "${CURL_METRICS_FMT}" || true)"
# || true: same rationale as truncated-gzip case above.
echo "${trunc_deflate_line}" | tee "${RAW_DIR}/trunc_deflate.metrics" >/dev/null
echo "${trunc_deflate_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "truncated-deflate failed: ${trunc_deflate_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/trunc_deflate.hdr" || {
  echo "truncated-deflate expected HTML Content-Type after fail-open" >&2
  exit 1
}
grep -qi '^Content-Encoding: deflate' "${RAW_DIR}/trunc_deflate.hdr" || {
  echo "truncated-deflate expected preserved deflate Content-Encoding" >&2
  exit 1
}
actual_trunc_deflate_bytes="$(wc -c < "${RAW_DIR}/trunc_deflate.body" | tr -d '[:space:]')"
if [[ "${actual_trunc_deflate_bytes}" != "${TRUNCATED_DEFLATE_COMPRESSED_LEN}" ]]; then
  echo "truncated-deflate compressed length mismatch: expected ${TRUNCATED_DEFLATE_COMPRESSED_LEN}, got ${actual_trunc_deflate_bytes}" >&2
  exit 1
fi

echo "==> Case 6b: truncated zlib-wrapped deflate streaming path should fail open"
trunc_deflate_zlib_line="$(curl -sS -D "${RAW_DIR}/trunc_deflate_zlib.hdr" -o "${RAW_DIR}/trunc_deflate_zlib.body" \
  -H "${ACCEPT_MARKDOWN_HEADER}" --max-time 180 \
  "http://127.0.0.1:${PORT}/streaming/truncated-deflate-zlib" \
  -w "${CURL_METRICS_FMT}" || true)"
echo "${trunc_deflate_zlib_line}" | tee "${RAW_DIR}/trunc_deflate_zlib.metrics" >/dev/null
echo "${trunc_deflate_zlib_line}" | grep -q "${PATTERN_HTTP_200}" || { echo "truncated-deflate-zlib failed: ${trunc_deflate_zlib_line}" >&2; exit 1; }
grep -qi "${PATTERN_CT_HTML}" "${RAW_DIR}/trunc_deflate_zlib.hdr" || {
  echo "truncated-deflate-zlib expected HTML Content-Type after fail-open" >&2
  exit 1
}
grep -qi '^Content-Encoding: deflate' "${RAW_DIR}/trunc_deflate_zlib.hdr" || {
  echo "truncated-deflate-zlib expected preserved deflate Content-Encoding" >&2
  exit 1
}
actual_trunc_deflate_zlib_bytes="$(wc -c < "${RAW_DIR}/trunc_deflate_zlib.body" | tr -d '[:space:]')"
if [[ "${actual_trunc_deflate_zlib_bytes}" != "${TRUNCATED_DEFLATE_ZLIB_COMPRESSED_LEN}" ]]; then
  echo "truncated-deflate-zlib compressed length mismatch: expected ${TRUNCATED_DEFLATE_ZLIB_COMPRESSED_LEN}, got ${actual_trunc_deflate_zlib_bytes}" >&2
  exit 1
fi

echo "==> Log sanity checks"
grep -q 'response size exceeds limit' "${RUNTIME}/logs/error.log" || {
  echo "missing size-limit log for chunked oversize case" >&2
  exit 1
}
# Verify that the truncated compressed streams produced the expected
# internal diagnostics: decompression failure followed by pre-commit
# conversion fail-open.  Full-buffer and streaming paths use different
# decompressor implementations, so accept either production log prefix.
# Full-buffer logs "rust decompress failed" or "decompression failed";
# streaming finalize logs "decomp_finish failed" or
# "finish inflate incomplete stream" or "finish inflate error".
decompress_failed_count="$(grep -E -c 'markdown: (rust decompress failed|decompression failed|decomp_finish failed|finish inflate (incomplete stream|error))' "${RUNTIME}/logs/error.log" || true)"
# Full-buffer decision uses "reason=failed_open category=conversion_error";
# streaming precommit uses "reason=STREAMING_PRECOMMIT_FAILOPEN" (no category).
conversion_failopen_count="$(grep -E -c '(reason=failed_open category=conversion_error|reason=STREAMING_PRECOMMIT_FAILOPEN)' "${RUNTIME}/logs/error.log" || true)"
if [[ "${decompress_failed_count}" -lt 3 ]]; then
  echo "missing decompression failure logs for truncated gzip/deflate/zlib-deflate cases: ${decompress_failed_count}" >&2
  exit 1
fi
if [[ "${conversion_failopen_count}" -lt 3 ]]; then
  echo "missing conversion fail-open logs for truncated gzip/deflate/zlib-deflate cases: ${conversion_failopen_count}" >&2
  exit 1
fi

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
echo "  markdown_limits_memory=${MARKDOWN_MAX_SIZE}"
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
echo "  deflate_zlib_source_html_bytes=${DEFLATE_ZLIB_SOURCE_LEN}"
echo "  deflate_zlib_compressed_bytes=${DEFLATE_ZLIB_COMPRESSED_LEN}"
echo "  deflate_zlib_result=$(cat "${RAW_DIR}/deflate_zlib.metrics")"
echo "  truncated_gzip_compressed_bytes=${TRUNCATED_GZIP_COMPRESSED_LEN}"
echo "  truncated_gzip_result=$(cat "${RAW_DIR}/trunc_gzip.metrics")"
echo "  truncated_deflate_compressed_bytes=${TRUNCATED_DEFLATE_COMPRESSED_LEN}"
echo "  truncated_deflate_result=$(cat "${RAW_DIR}/trunc_deflate.metrics")"
echo "  truncated_deflate_zlib_compressed_bytes=${TRUNCATED_DEFLATE_ZLIB_COMPRESSED_LEN}"
echo "  truncated_deflate_zlib_result=$(cat "${RAW_DIR}/trunc_deflate_zlib.metrics")"
echo "  zero_copy_result=$(cat "${RAW_DIR}/zc.metrics" 2>/dev/null || echo "missing")"
echo "  zero_copy_output_total=${zc_output_total:-0}"
echo "  zero_copy_backpressure_total=${zc_backpressure_after:-0}"
echo "  zero_copy_backpressure_resume_total=${zc_backpressure_resume_after:-0}"
echo "  zero_copy_slow_reader_result=$(cat "${RAW_DIR}/zc_slow_reader.log" 2>/dev/null || echo "missing")"
echo "  zero_copy_abort_client_result=$(cat "${RAW_DIR}/zc_abort_client.log" 2>/dev/null || echo "missing")"
echo "  zero_copy_post_abort_result=$(cat "${RAW_DIR}/zc_post_abort.metrics" 2>/dev/null || echo "missing")"
if [[ "${PROFILE}" == "stress" ]]; then
  echo "  stress_small_ab_rps=${small_rps:-unknown}"
  echo "  stress_oversize_ab_rps=${oversize_rps:-unknown}"
fi
echo "  artifacts=${BUILDROOT}"
