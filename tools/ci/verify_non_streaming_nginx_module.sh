#!/usr/bin/env bash
#
# Verify the production NGINX module boundary without Rust streaming features.
#
# The check builds the real Rust static archive, lets the module config detect
# its exported symbols, compiles and links every NGINX module source, and loads
# the resulting dynamic module through nginx -t.  It intentionally does not
# define MARKDOWN_STREAMING_ENABLED from the command line.
set -euo pipefail

NGINX_VERSION="${NGINX_VERSION:-1.30.3}"
KEEP_ARTIFACTS=0
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION]

Build and load the NGINX module with a Rust archive that has no optional
features. VERSION must be an x.y.z release covered by the checksum table.
EOF
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
      ;;
    --nginx-version)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "Missing value for --nginx-version" >&2
        exit 2
      fi
      NGINX_VERSION="$2"
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

# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"

resolve_nginx_version() {
  local requested="$1"

  if [[ ! "${requested}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid NGINX version: ${requested}" >&2
    return 1
  fi

  printf '%s\n' "${requested}"

  return 0
}

cleanup() {
  local rc=$?

  if [[ ${rc} -eq 0 && ${KEEP_ARTIFACTS} -eq 0 \
        && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    BUILDROOT_FOR_PY="${BUILDROOT}" python3 - <<'PY'
import os
import shutil

path = os.environ["BUILDROOT_FOR_PY"]
if path.startswith("/tmp/nginx-nonstreaming-module."):
    shutil.rmtree(path)
PY
  elif [[ -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Artifacts kept at: ${BUILDROOT}"
  fi

  return 0
}
trap cleanup EXIT

for cmd in cargo curl make nm python3 tar; do
  markdown_need_cmd "${cmd}"
done

RUST_TARGET="$(markdown_detect_rust_target)"
NGINX_VERSION="$(resolve_nginx_version "${NGINX_VERSION}")"
BUILDROOT="$(mktemp -d /tmp/nginx-nonstreaming-module.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
BUILD_LOG="${BUILDROOT}/build.log"
SYMBOLS_FILE="${BUILDROOT}/rust-symbols.txt"
ARCHIVE="${WORKSPACE_ROOT}/components/rust-converter/target/${RUST_TARGET}/release/libnginx_markdown_converter.a"

mkdir -p "${BUILDROOT}/src" "${RUNTIME}/conf" "${RUNTIME}/logs"
markdown_export_native_build_env
markdown_export_nginx_dependency_env pcre2 zlib openssl@3

echo "==> Building non-streaming Rust converter (${RUST_TARGET})"
(
  cd "${WORKSPACE_ROOT}/components/rust-converter"
  cargo build --locked --release --no-default-features \
    --target "${RUST_TARGET}"
)

nm "${ARCHIVE}" >"${SYMBOLS_FILE}" 2>/dev/null
if grep -q 'markdown_streaming_new' "${SYMBOLS_FILE}"; then
  echo "FAIL: non-streaming archive exports markdown_streaming_new" >&2
  exit 1
fi

echo "  Rust features: none (--no-default-features)"
echo "  archive: ${ARCHIVE}"
echo "  markdown_streaming_new present: no"

echo "==> Downloading NGINX ${NGINX_VERSION}"
curl --proto '=https' --tlsv1.2 --max-time 600 --connect-timeout 30 \
  -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" \
  -o "${BUILDROOT}/nginx.tar.gz"
"${WORKSPACE_ROOT}/packaging/scripts/verify-checksum.sh" \
  -f "${BUILDROOT}/nginx.tar.gz" \
  -i "nginx-${NGINX_VERSION}" \
  -c "${WORKSPACE_ROOT}/packaging/checksums.sha256"
tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" \
  --strip-components=1

echo "==> Configuring NGINX through components/nginx-module/config"
(
  cd "${BUILDROOT}/src"
  configure_args=(
    --without-http_rewrite_module
    --with-compat
    --prefix="${RUNTIME}"
    --modules-path="${RUNTIME}/modules"
    --add-dynamic-module="${WORKSPACE_ROOT}/components/nginx-module"
  )
  while IFS= read -r configure_arg; do
    configure_args+=("${configure_arg}")
  done < <(markdown_emit_nginx_configure_env \
    "${CPPFLAGS:-}" "${LDFLAGS:-}")
  ./configure "${configure_args[@]}" >"${BUILD_LOG}" 2>&1
)

if grep -q -- '-DMARKDOWN_STREAMING_ENABLED' \
  "${BUILDROOT}/src/objs/Makefile"; then
  echo "FAIL: module config enabled MARKDOWN_STREAMING_ENABLED" >&2
  exit 1
fi
echo "  MARKDOWN_STREAMING_ENABLED detected: no"
echo "  NGINX configure result: PASS"

echo "==> Compiling and linking the dynamic module"
(
  cd "${BUILDROOT}/src"
  make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" modules \
    >>"${BUILD_LOG}" 2>&1
)

POSTCOMMIT_OBJECT="$(find "${BUILDROOT}/src/objs" -type f \
  -name '*stream_postcommit*.o' -print -quit)"
MODULE="$(find "${BUILDROOT}/src/objs" -maxdepth 2 -type f \
  -name 'ngx_http_markdown*.so' -print -quit)"
if [[ -z "${POSTCOMMIT_OBJECT}" || ! -f "${POSTCOMMIT_OBJECT}" ]]; then
  echo "FAIL: stream_postcommit.c was not compiled" >&2
  exit 1
fi
if [[ -z "${MODULE}" || ! -f "${MODULE}" ]]; then
  echo "FAIL: dynamic module was not linked" >&2
  exit 1
fi
echo "  stream_postcommit.c compile result: PASS"
echo "  module link result: PASS"

echo "==> Building NGINX and loading the dynamic module"
(
  cd "${BUILDROOT}/src"
  make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" \
    >>"${BUILD_LOG}" 2>&1
)
NGINX_EXECUTABLE="${BUILDROOT}/src/objs/nginx"

cat >"${RUNTIME}/conf/nginx.conf" <<EOF
load_module ${MODULE};
worker_processes 1;
error_log logs/error.log info;
pid logs/nginx.pid;

events { worker_connections 16; }

http {
    server {
        listen 127.0.0.1:18099;
        location / { }
    }
}
EOF

if ! "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -t \
  >"${BUILDROOT}/nginx-t.log" 2>&1; then
  echo "  module load/nginx -t: FAIL" >&2
  sed -n '1,20p' "${BUILDROOT}/nginx-t.log" >&2
  exit 1
fi

echo "  module load/nginx -t: PASS"
cat "${BUILDROOT}/nginx-t.log"
echo "==> Non-streaming production module verification passed"
