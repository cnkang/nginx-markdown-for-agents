#!/usr/bin/env bash
set -euo pipefail

NGINX_VERSION="${NGINX_VERSION:-stable}"
KEEP_ARTIFACTS="${KEEP_ARTIFACTS:-0}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUST_TARGET=""
# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"

resolve_nginx_version() {
  local requested="$1"
  local page
  local version

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

  return 0
}

cleanup() {
  local rc=$?
  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}"
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Build failed. Artifacts kept at: ${BUILDROOT}" >&2
  fi

  return 0
}
trap cleanup EXIT

for cmd in curl tar make cargo python3; do
  markdown_need_cmd "$cmd"
done

NGINX_VERSION="$(resolve_nginx_version "${NGINX_VERSION}")"
BUILDROOT="$(mktemp -d /tmp/nginx-codeql-build.XXXXXX)"
RUST_TARGET="$(markdown_detect_rust_target)"

echo "==> Building Rust converter static library (${RUST_TARGET})"
markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}" --locked

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
