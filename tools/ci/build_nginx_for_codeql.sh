#!/usr/bin/env bash
set -euo pipefail

NGINX_VERSION="${NGINX_VERSION:-stable}"
KEEP_ARTIFACTS="${KEEP_ARTIFACTS:-0}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILDROOT=""

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

resolve_nginx_version() {
  local requested page version
  requested="$1"

  case "${requested}" in
    stable|mainline)
      page="$(curl -fsSL https://nginx.org/en/download.html)"
      version="$(
        NGINX_DOWNLOAD_HTML="${page}" CHANNEL="${requested}" python3 - <<'PY'
import os
import re

html = os.environ.get("NGINX_DOWNLOAD_HTML", "")
channel = os.environ.get("CHANNEL", "")

if channel == "mainline":
    pattern = r"Mainline version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
elif channel == "stable":
    pattern = r"Stable version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
else:
    raise SystemExit(1)

match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
if not match:
    raise SystemExit(1)

print(match.group(1))
PY
      )"

      if [[ -z "${version}" ]]; then
        echo "Failed to resolve latest ${requested} NGINX version from nginx.org" >&2
        exit 1
      fi

      printf '%s\n' "${version}"
      ;;
    *)
      printf '%s\n' "${requested}"
      ;;
  esac
}

cleanup() {
  local rc=$?
  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}"
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Build failed. Artifacts kept at: ${BUILDROOT}" >&2
  fi
}
trap cleanup EXIT

for cmd in curl tar make cargo python3; do
  need_cmd "$cmd"
done

NGINX_VERSION="$(resolve_nginx_version "${NGINX_VERSION}")"
BUILDROOT="$(mktemp -d /tmp/nginx-codeql-build.XXXXXX)"

echo "==> Building Rust converter static library"
(
  cd "${WORKSPACE_ROOT}/components/rust-converter"
  cargo build --release --locked

  header_src="${WORKSPACE_ROOT}/components/rust-converter/include/markdown_converter.h"
  header_dst="${WORKSPACE_ROOT}/components/nginx-module/src/markdown_converter.h"
  if [[ ! -f "${header_src}" ]]; then
    echo "Missing generated header: ${header_src}" >&2
    exit 1
  fi
  cp "${header_src}" "${header_dst}"
)

echo "==> Downloading NGINX ${NGINX_VERSION}"
curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}" --strip-components=1

echo "==> Configuring NGINX with markdown module (dynamic)"
(
  cd "${BUILDROOT}"
  ./configure \
    --with-compat \
    --without-http_rewrite_module \
    --add-dynamic-module="${WORKSPACE_ROOT}/components/nginx-module"
)

echo "==> Building NGINX dynamic modules for CodeQL extraction"
(
  cd "${BUILDROOT}"
  make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" modules
)

echo "==> Production C sources compiled successfully for CodeQL"
