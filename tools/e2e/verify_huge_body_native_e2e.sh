#!/bin/bash
set -euo pipefail

# Native-only E2E validation for very large response bodies (100MB / 1GB).
# Goal: ensure requests remain correct under Accept: text/markdown by taking the
# size-limit bypass path (no conversion) rather than hanging/truncating.

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18092}"
KEEP_ARTIFACTS=0
RUN_1G_GET="${RUN_1G_GET:-1}"
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
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--skip-1g-get]

Build local NGINX with the markdown module and validate that very large HTML
responses (100MB / 1GB) are handled correctly under Accept: text/markdown.

Validation expectation:
  - Response stays pass-through (Content-Type remains text/html)
  - No hang / timeout
  - 100MB GET completes successfully
  - 1GB HEAD always validated; 1GB GET validated unless --skip-1g-get

This script auto-reexecs under native arm64 on Apple Silicon if launched under Rosetta.
Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
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
    echo "Huge-body E2E validation failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Huge-body E2E validation succeeded. Artifacts kept at: ${BUILDROOT}"
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

for cmd in curl truncate python3 awk grep; do
  markdown_need_cmd "$cmd"
done
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-huge-native.XXXXXX)"
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
  curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
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

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html/full" "${RUNTIME}/logs"

# Create sparse files quickly. We only need size-path behavior, not HTML validity,
# because oversized responses must be bypassed before conversion.
truncate -s 104857600 "${RUNTIME}/html/full/huge-100m.html"
truncate -s 1073741824 "${RUNTIME}/html/full/huge-1g.html"

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log warn;
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

        location /full/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_conditional_requests full_support;
            # default max_size=10m should force size-based bypass for 100m/1g
            markdown_log_verbosity warn;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

check_head() {
  local name="$1" expected_bytes="$2"
  local hdr="${RAW_DIR}/${name}.head.hdr"
  local code

  code="$(curl -sS -I -D "${hdr}" -o /dev/null -H 'Accept: text/markdown' \
    --max-time 20 "http://127.0.0.1:${PORT}/full/${name}.html" -w '%{http_code}')"
  [[ "${code}" == "200" ]] || { echo "${name}: expected HEAD 200, got ${code}" >&2; exit 1; }

  grep -qi '^Content-Type: text/html' "${hdr}" || {
    echo "${name}: expected pass-through Content-Type text/html" >&2
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

check_get() {
  local name="$1" expected_bytes="$2" timeout_s="$3"
  local hdr="${RAW_DIR}/${name}.get.hdr"
  local body="/dev/null"
  local metrics="${RAW_DIR}/${name}.get.metrics"
  local code

  code="$(curl -sS -D "${hdr}" -o "${body}" -H 'Accept: text/markdown' \
    --max-time "${timeout_s}" "http://127.0.0.1:${PORT}/full/${name}.html" \
    -w 'http=%{http_code} size=%{size_download} total=%{time_total}\n')"
  echo "${code}" | tee "${metrics}" >/dev/null

  echo "${code}" | grep -q 'http=200 ' || {
    echo "${name}: GET failed: ${code}" >&2
    exit 1
  }
  echo "${code}" | grep -q "size=${expected_bytes} " || {
    echo "${name}: GET size mismatch: ${code}" >&2
    exit 1
  }

  grep -qi '^Content-Type: text/html' "${hdr}" || {
    echo "${name}: expected pass-through Content-Type text/html on GET" >&2
    exit 1
  }
  return 0
}

echo "==> HEAD validation (size-bypass correctness)"
check_head "huge-100m" "104857600"
check_head "huge-1g" "1073741824"

echo "==> GET validation (100MB)"
check_get "huge-100m" "104857600" 60

if [[ "${RUN_1G_GET}" == "1" ]]; then
  echo "==> GET validation (1GB)"
  check_get "huge-1g" "1073741824" 180
else
  echo "==> Skipping 1GB GET validation (--skip-1g-get)"
fi

echo "Huge-body validation summary:"
echo "  nginx_version=${NGINX_VERSION}"
echo "  arch=$(uname -m)"
echo "  max_size(default)=10m (size-based bypass expected)"
echo "  100MB_GET=$(cat "${RAW_DIR}/huge-100m.get.metrics")"
if [[ -f "${RAW_DIR}/huge-1g.get.metrics" ]]; then
  echo "  1GB_GET=$(cat "${RAW_DIR}/huge-1g.get.metrics")"
fi
echo "  artifacts=${BUILDROOT}"
