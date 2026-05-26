#!/usr/bin/env bash
# test_install_workflow.sh — End-to-end smoke test for the binary install script.
#
# Builds a release artifact, spins up a local HTTP server to simulate a
# GitHub release download, then runs install.sh inside an official nginx
# Docker container to verify the full install-and-load workflow.
#
# Usage:
#   tools/test_install_workflow.sh
#
# Prerequisites:
#   - docker (with buildx for multi-platform support)
#   - python3 (for the mock HTTP server)
#   - A pre-built release artifact (see tools/build_release.sh)
#
# Exit behaviour:
#   0 if the install script succeeds and nginx -t passes inside the container.
#   1 if any prerequisite is missing, the artifact is absent, or the
#     containerised install/nginx validation fails.
set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ARCH="x86_64"
DOCKER_PLATFORM="linux/amd64"
HOST_ARCH="$(uname -m)"
if [[ "$HOST_ARCH" == "aarch64" ]] || [[ "$HOST_ARCH" == "arm64" ]]; then
  TEST_ARCH="aarch64"
  DOCKER_PLATFORM="linux/arm64"
fi

ARTIFACT="${WORKSPACE_ROOT}/dist/1.26.3-glibc-${TEST_ARCH}/ngx_http_markdown_filter_module-1.26.3-glibc-${TEST_ARCH}.tar.gz"
MOCK_DIR=""
SERVER_PID=""

# Compute the SHA-256 hash of a file using the best available tool.
#
# Arguments:
#   $1 - path to the file to hash
#
# Outputs:
#   Writes the hex SHA-256 digest to stdout
#
# Returns:
#   0 always (falls back through sha256sum, shasum, openssl)
sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    openssl dgst -sha256 "$file" | awk '{print $2}'
  fi

  return 0
}

# cleanup — Remove temporary artifacts on script exit.
#
# Stops the mock HTTP server if it is still running and removes the
# temporary directory used to host the release artifact.
#
# Arguments:
#   (none)
#
# Outputs:
#   None.
#
# Returns:
#   0 always.
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi

  if [[ -n "$MOCK_DIR" ]] && [[ -d "$MOCK_DIR" ]]; then
    rm -rf "$MOCK_DIR"
  fi

  return 0
}
trap cleanup EXIT

if [[ ! -f "$ARTIFACT" ]]; then
  echo "Missing artifact: $ARTIFACT"
  echo "Build it first: ./tools/build_release.sh 1.26.3 glibc ${TEST_ARCH}"
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
  nginx:1.26.3 \
  bash -c "apt-get update && apt-get install -y --no-install-recommends curl python3 && /install.sh && printf '%s\n' 'load_module /etc/nginx/modules/ngx_http_markdown_filter_module.so;' 'worker_processes 1;' 'events {}' 'http {}' > /tmp/nginx-test.conf && nginx -t -c /tmp/nginx-test.conf"
