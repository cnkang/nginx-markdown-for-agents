#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ARCH="x86_64"
DOCKER_PLATFORM="linux/amd64"
HOST_ARCH="$(uname -m)"
if [ "$HOST_ARCH" = "aarch64" ] || [ "$HOST_ARCH" = "arm64" ]; then
  TEST_ARCH="aarch64"
  DOCKER_PLATFORM="linux/arm64"
fi

ARTIFACT="${WORKSPACE_ROOT}/dist/1.28.2-glibc-${TEST_ARCH}/ngx_http_markdown_filter_module-1.28.2-glibc-${TEST_ARCH}.tar.gz"
MOCK_DIR=""
SERVER_PID=""

sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    openssl dgst -sha256 "$file" | awk '{print $2}'
  fi
}

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi

  if [ -n "$MOCK_DIR" ] && [ -d "$MOCK_DIR" ]; then
    rm -rf "$MOCK_DIR"
  fi
}
trap cleanup EXIT

if [ ! -f "$ARTIFACT" ]; then
  echo "Missing artifact: $ARTIFACT"
  echo "Build it first: ./tools/build_release.sh 1.28.2 glibc ${TEST_ARCH}"
  exit 1
fi

MOCK_DIR="$(mktemp -d /tmp/mock_github.XXXXXX)"
cp "$ARTIFACT" "$MOCK_DIR/"

ASSET_NAME="$(basename "$ARTIFACT")"
ASSET_SHA256="$(sha256_file "$ARTIFACT")"

(
  cd "$MOCK_DIR"
  python3 -m http.server 8000 >/tmp/mock_github_server.log 2>&1
) &
SERVER_PID=$!

sleep 1

docker run --rm \
  --platform "${DOCKER_PLATFORM}" \
  -v "${WORKSPACE_ROOT}/tools/install.sh:/install.sh:ro" \
  --add-host host.docker.internal:host-gateway \
  -e DOWNLOAD_URL_OVERRIDE="http://host.docker.internal:8000/${ASSET_NAME}" \
  -e DOWNLOAD_SHA256="${ASSET_SHA256}" \
  nginx:1.28.2 \
  bash -c "apt-get update && apt-get install -y curl python3 && /install.sh && printf '%s\n' 'load_module /etc/nginx/modules/ngx_http_markdown_filter_module.so;' 'worker_processes 1;' 'events {}' 'http {}' > /tmp/nginx-test.conf && nginx -t -c /tmp/nginx-test.conf"
