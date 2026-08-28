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
#   - openssl (for the local HTTPS certificate)
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
MOCK_PORT_FILE=""
MOCK_CERT_FILE=""
MOCK_KEY_FILE=""

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
  echo "Missing artifact: $ARTIFACT" >&2
  echo "Build it first: ./tools/build_release.sh 1.26.3 glibc ${TEST_ARCH}" >&2
  exit 1
fi

MOCK_DIR="$(mktemp -d /tmp/mock_github.XXXXXX)"
cp "$ARTIFACT" "$MOCK_DIR/"

if ! command -v openssl >/dev/null 2>&1; then
  echo "Missing prerequisite: openssl" >&2
  exit 1
fi
MOCK_CERT_FILE="${MOCK_DIR}/server.crt"
MOCK_KEY_FILE="${MOCK_DIR}/server.key"
if ! openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$MOCK_KEY_FILE" -out "$MOCK_CERT_FILE" -days 1 \
  -subj "/CN=host.docker.internal" \
  -addext "subjectAltName=DNS:host.docker.internal" \
  >/dev/null 2>&1; then
  echo "ERROR: failed to generate mock HTTPS certificate" >&2
  exit 1
fi
if [[ ! -s "$MOCK_CERT_FILE" || ! -s "$MOCK_KEY_FILE" ]]; then
  echo "ERROR: mock HTTPS certificate generation produced empty files" >&2
  exit 1
fi

ASSET_NAME="$(basename "$ARTIFACT")"
ASSET_SHA256="$(sha256_file "$ARTIFACT")"
MOCK_PORT_FILE="${MOCK_DIR}/port"

(
  python3 - "$MOCK_DIR" "$MOCK_PORT_FILE" "$MOCK_CERT_FILE" "$MOCK_KEY_FILE" <<'PY'
import ssl
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

directory = sys.argv[1]
port_file = Path(sys.argv[2])
cert_file = sys.argv[3]
key_file = sys.argv[4]
handler = partial(SimpleHTTPRequestHandler, directory=directory)
server = ThreadingHTTPServer(("", 0), handler)
tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
tls_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
server.socket = tls_context.wrap_socket(server.socket, server_side=True)
port_file.write_text(str(server.server_port), encoding="ascii")
try:
    server.serve_forever()
finally:
    server.server_close()
PY
) &
SERVER_PID=$!

MOCK_PORT=""
server_ready=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if [[ -z "$MOCK_PORT" ]] && [[ -s "$MOCK_PORT_FILE" ]]; then
    MOCK_PORT="$(<"$MOCK_PORT_FILE")"
  fi
  if [[ -n "$MOCK_PORT" ]] && python3 - "$MOCK_PORT" <<'PY'
import ssl
import sys
import urllib.request

try:
    with urllib.request.urlopen(
        f"https://127.0.0.1:{sys.argv[1]}/",
        timeout=1,
        context=ssl._create_unverified_context(),
    ):
        pass
except OSError:
    raise SystemExit(1)
PY
  then
    server_ready=1
    break
  fi
  if [[ "$attempt" -lt 10 ]]; then
    sleep 1
  fi
done
if [[ "$server_ready" -ne 1 ]]; then
  echo "ERROR: mock GitHub server did not become ready on port ${MOCK_PORT}" >&2
  exit 1
fi

docker run --rm \
  --platform "${DOCKER_PLATFORM}" \
  -v "${WORKSPACE_ROOT}/tools/install.sh:/install.sh:ro" \
  -v "${MOCK_CERT_FILE}:/mock/server.crt:ro" \
  --add-host host.docker.internal:host-gateway \
  -e CURL_CA_BUNDLE=/mock/server.crt \
  -e DOWNLOAD_URL_OVERRIDE="https://host.docker.internal:${MOCK_PORT}/${ASSET_NAME}" \
  -e DOWNLOAD_SHA256="${ASSET_SHA256}" \
  nginx:1.26.3 \
  bash -c "apt-get update && apt-get install -y --no-install-recommends curl gawk python3 && bash /install.sh && printf '%s\n' 'load_module /etc/nginx/modules/ngx_http_markdown_filter_module.so;' 'worker_processes 1;' 'events {}' 'http {}' > /tmp/nginx-test.conf && nginx -t -c /tmp/nginx-test.conf"
