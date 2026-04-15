#!/bin/bash
set -euo pipefail

# Native-only E2E validation for large bodies when markdown_max_size allows them.
# Covers:
#  - 100MB valid HTML conversion path (best-effort success path)
#  - 1GB allowed-size path with deterministic conversion failure + fail-open replay
#
# Why the 1GB file is intentionally invalid UTF-8:
# - It still forces full buffering (max_size allows it)
# - Conversion fails quickly and deterministically in Rust (encoding validation)
# - We can validate the body filter's fail-open replay correctness without waiting
#   for a full 1GB DOM parse in html5ever (which is impractical for routine local runs)

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18093}"
KEEP_ARTIFACTS=0
RUN_1G_GET="${RUN_1G_GET:-1}"
MARKDOWN_MAX_SIZE="${MARKDOWN_MAX_SIZE:-1536m}"
ACCEPT_MARKDOWN_HEADER='Accept: text/markdown'
NGINX_BIN="${NGINX_BIN:-}"

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
ORIG_ARGS=("$@")
# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--skip-1g-get] [--markdown-max-size SIZE]

Build local NGINX with the markdown module and validate very large bodies when
markdown_max_size allows them (native-only on Apple Silicon).

Checks:
  1) 100MB valid HTML converts successfully to Markdown (GET, HEAD)
  2) 1GB HTML is allowed by size and reaches conversion; conversion fails fast due
     to invalid UTF-8, then fail-open returns the full original body (GET, HEAD)

Notes:
  - This script auto-reexecs under native arm64 on Apple Silicon if launched under Rosetta.
  - 1GB GET can be skipped with --skip-1g-get.
  - Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

cleanup() {
  local rc=$?

  if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}" || true
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Allowed-size huge-body E2E validation failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Allowed-size huge-body E2E validation succeeded. Artifacts kept at: ${BUILDROOT}"
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
      NGINX_VERSION="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --skip-1g-get)
      RUN_1G_GET=0
      shift
      ;;
    --markdown-max-size)
      MARKDOWN_MAX_SIZE="$2"
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

for cmd in curl python3 truncate dd awk grep sed wc; do
  markdown_need_cmd "$cmd"
done
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-huge-allow-native.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
mkdir -p "${RAW_DIR}"

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

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html/allow" "${RUNTIME}/logs"

echo "==> Generating 100MB valid HTML (single large text node in <pre>)"
python3 - <<'PY' "${RUNTIME}/html/allow/convert-100m.html"
import os
import sys

out_path = sys.argv[1]
target_size = 100 * 1024 * 1024
prefix = b'<!doctype html><html><head><meta charset="UTF-8"><title>100m</title></head><body><pre>\n'
suffix = b'\n</pre></body></html>\n'
chunk = (b'0123456789abcdef' * 4096) + b'\n'  # ~64KB + newline

with open(out_path, 'wb') as f:
    f.write(prefix)
    remaining = target_size - len(prefix) - len(suffix)
    while remaining > 0:
        piece = chunk if remaining >= len(chunk) else chunk[:remaining]
        f.write(piece)
        remaining -= len(piece)
    f.write(suffix)

print(f"convert_100m_bytes={os.path.getsize(out_path)}")
PY

echo "==> Generating 1GB HTML artifact (allowed by size, deterministic conversion failure)"
truncate -s 1073741824 "${RUNTIME}/html/allow/failopen-1g-invalid.html"
printf '\377' | dd of="${RUNTIME}/html/allow/failopen-1g-invalid.html" bs=1 count=1 conv=notrunc status=none

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log info;
pid logs/nginx.pid;

events { worker_connections 256; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;
        root html;

        location /allow/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_conditional_requests full_support;
            markdown_max_size ${MARKDOWN_MAX_SIZE};
            markdown_on_error pass;
            markdown_timeout 600000;
            markdown_log_verbosity info;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

check_head_markdown() {
  local name="$1"
  local hdr="${RAW_DIR}/${name}.head.hdr"
  local code
  code="$(curl -sS -I -D "${hdr}" -o /dev/null -H "${ACCEPT_MARKDOWN_HEADER}" \
    --max-time 600 "http://127.0.0.1:${PORT}/allow/${name}.html" -w '%{http_code}')"
  [[ "${code}" == "200" ]] || { echo "${name}: expected HEAD 200, got ${code}" >&2; exit 1; }
  grep -qi '^Content-Type: text/markdown; charset=utf-8' "${hdr}" || {
    echo "${name}: expected markdown Content-Type on HEAD" >&2
    exit 1
  }
  return 0
}

check_get_markdown() {
  local name="$1"
  local timeout_s="$2"
  local hdr="${RAW_DIR}/${name}.get.hdr"
  local body="${RAW_DIR}/${name}.get.body"
  local metrics="${RAW_DIR}/${name}.get.metrics"
  local line

  line="$(curl -sS -D "${hdr}" -o "${body}" -H "${ACCEPT_MARKDOWN_HEADER}" \
    --max-time "${timeout_s}" "http://127.0.0.1:${PORT}/allow/${name}.html" \
    -w 'http=%{http_code} size=%{size_download} total=%{time_total}\n')"
  echo "${line}" | tee "${metrics}" >/dev/null

  echo "${line}" | grep -q 'http=200 ' || {
    echo "${name}: GET failed: ${line}" >&2
    exit 1
  }
  grep -qi '^Content-Type: text/markdown; charset=utf-8' "${hdr}" || {
    echo "${name}: expected markdown Content-Type on GET" >&2
    exit 1
  }

  local sz
  sz="$(wc -c < "${body}" | tr -d ' ')"
  [[ "${sz}" -gt 0 ]] || { echo "${name}: markdown body empty" >&2; exit 1; }
  return 0
}

check_head_failopen_passthrough() {
  local name="$1" expected_bytes="$2"
  local hdr="${RAW_DIR}/${name}.head.hdr"
  local code

  code="$(curl -sS -I -D "${hdr}" -o /dev/null -H "${ACCEPT_MARKDOWN_HEADER}" \
    --max-time 180 "http://127.0.0.1:${PORT}/allow/${name}.html" -w '%{http_code}')"
  [[ "${code}" == "200" ]] || { echo "${name}: expected HEAD 200, got ${code}" >&2; exit 1; }

  grep -qi '^Content-Type: text/html' "${hdr}" || {
    echo "${name}: expected pass-through Content-Type text/html on HEAD" >&2
    exit 1
  }

  local cl
  cl="$(awk 'BEGIN{IGNORECASE=1} /^Content-Length:/ {gsub(/\r/, ""); print $2; exit}' "${hdr}")"
  [[ "${cl}" == "${expected_bytes}" ]] || {
    echo "${name}: Content-Length mismatch (expected ${expected_bytes}, got ${cl:-missing})" >&2
    exit 1
  }
  return 0
}

check_get_failopen_passthrough() {
  local name="$1" expected_bytes="$2" timeout_s="$3"
  local hdr="${RAW_DIR}/${name}.get.hdr"
  local metrics="${RAW_DIR}/${name}.get.metrics"
  local line

  line="$(curl -sS -D "${hdr}" -o /dev/null -H "${ACCEPT_MARKDOWN_HEADER}" \
    --max-time "${timeout_s}" "http://127.0.0.1:${PORT}/allow/${name}.html" \
    -w 'http=%{http_code} size=%{size_download} total=%{time_total}\n')"
  echo "${line}" | tee "${metrics}" >/dev/null

  echo "${line}" | grep -q 'http=200 ' || {
    echo "${name}: GET failed: ${line}" >&2
    exit 1
  }
  echo "${line}" | grep -q "size=${expected_bytes} " || {
    echo "${name}: GET size mismatch: ${line}" >&2
    exit 1
  }
  grep -qi '^Content-Type: text/html' "${hdr}" || {
    echo "${name}: expected pass-through Content-Type text/html on GET" >&2
    exit 1
  }
  return 0
}

echo "==> 100MB conversion validation (max_size allowed)"
check_head_markdown "convert-100m"
check_get_markdown "convert-100m" 600

echo "==> 1GB fail-open replay validation (max_size allowed)"
check_head_failopen_passthrough "failopen-1g-invalid" "1073741824"
if [[ "${RUN_1G_GET}" == "1" ]]; then
  check_get_failopen_passthrough "failopen-1g-invalid" "1073741824" 300
else
  echo "==> Skipping 1GB GET validation (--skip-1g-get)"
fi

echo "==> Log sanity checks"
grep -q 'conversion failed' "${RUNTIME}/logs/error.log" || {
  echo "Expected conversion failure log for failopen-1g-invalid not found" >&2
  exit 1
}
if grep -q 'response size exceeds limit' "${RUNTIME}/logs/error.log"; then
  echo "Unexpected size-limit bypass log found in allowed-size scenario" >&2
  exit 1
fi

echo "Allowed-size huge-body summary:"
echo "  nginx_version=${NGINX_VERSION}"
echo "  arch=$(uname -m)"
echo "  markdown_max_size=${MARKDOWN_MAX_SIZE}"
echo "  convert_100m=$(cat "${RAW_DIR}/convert-100m.get.metrics")"
if [[ -f "${RAW_DIR}/failopen-1g-invalid.get.metrics" ]]; then
  echo "  failopen_1g=$(cat "${RAW_DIR}/failopen-1g-invalid.get.metrics")"
else
  echo "  failopen_1g=skipped"
fi
echo "  artifacts=${BUILDROOT}"
