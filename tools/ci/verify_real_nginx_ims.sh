#!/usr/bin/env bash
set -euo pipefail

NGINX_VERSION="${NGINX_VERSION:-1.26.2}"
PORT="${PORT:-18088}"
KEEP_ARTIFACTS=0
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT]

Builds a local NGINX from source with the markdown module and validates delegated
If-Modified-Since behavior for Markdown-negotiated responses.

Environment variables:
  NGINX_VERSION   Default: 1.26.2
  PORT            Default: 18088
EOF
}

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

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

for cmd in curl tar make cargo rsync awk python3; do
  need_cmd "$cmd"
done

detect_rust_target() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "${os}:${arch}" in
    Darwin:arm64) echo "aarch64-apple-darwin" ;;
    Darwin:x86_64) echo "x86_64-apple-darwin" ;;
    Linux:x86_64) echo "x86_64-unknown-linux-gnu" ;;
    Linux:aarch64) echo "aarch64-unknown-linux-gnu" ;;
    *)
      echo "Unsupported host for automatic Rust target detection: ${os}/${arch}" >&2
      exit 1
      ;;
  esac
}

cleanup() {
  local rc=$?
  if [[ -n "${RUNTIME}" && -x "${RUNTIME}/sbin/nginx" ]]; then
    "${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    BUILDROOT_FOR_PY="${BUILDROOT}" python3 - <<'PY'
import os
import shutil

p = os.environ["BUILDROOT_FOR_PY"]
if os.path.exists(p):
    shutil.rmtree(p)
PY
  fi

  if [[ $rc -ne 0 ]]; then
    if [[ -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
      echo "Validation failed. Build artifacts kept at: ${BUILDROOT}" >&2
    fi
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 ]]; then
    echo "Validation succeeded. Artifacts kept at: ${BUILDROOT}"
  fi
}
trap cleanup EXIT

RUST_TARGET="$(detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-ims-verify.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"

echo "==> Building Rust converter (${RUST_TARGET})"
(
  cd "${WORKSPACE_ROOT}/components/rust-converter"
  cargo build --target "${RUST_TARGET}" --release
  mkdir -p target/release
  cp "target/${RUST_TARGET}/release/libnginx_markdown_converter.a" \
     "target/release/libnginx_markdown_converter.a"

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

echo "==> Configuring NGINX"
(
  cd "${BUILDROOT}"
  ./configure \
    --without-http_rewrite_module \
    --prefix="${RUNTIME}" \
    --add-module="${WORKSPACE_ROOT}/components/nginx-module"
)

echo "==> Building and installing NGINX"
(
  cd "${BUILDROOT}"
  make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
  make install
)

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html"

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
worker_processes  1;
error_log  logs/error.log info;
pid        logs/nginx.pid;

events { worker_connections 128; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout  5;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location / {
            root html;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_conditional_requests full_support;
            markdown_log_verbosity info;
        }
    }
}
EOF

cat > "${RUNTIME}/html/index.html" <<'EOF'
<!doctype html>
<html>
  <head><title>IMS Validation</title></head>
  <body><h1>Hello IMS</h1><p>Conditional request test.</p></body>
</html>
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Running If-Modified-Since validation scenario"
(
  cd "${BUILDROOT}"

  rm -f resp1.headers resp1.body resp2.headers resp2.body resp3.headers resp3.body

  code1="$(curl -sS -D resp1.headers -o resp1.body \
    -H 'Accept: text/markdown' \
    "http://127.0.0.1:${PORT}/index.html" \
    -w '%{http_code}')"

  lm="$(awk 'BEGIN{IGNORECASE=1} /^Last-Modified:/ {sub(/^Last-Modified:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit}' resp1.headers)"
  [[ -n "${lm}" ]] || { echo "Missing Last-Modified in first response" >&2; exit 1; }

  code2="$(curl -sS -D resp2.headers -o resp2.body \
    -H 'Accept: text/markdown' \
    -H "If-Modified-Since: ${lm}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w '%{http_code}')"

  code3="$(curl -sS -D resp3.headers -o resp3.body \
    -H 'Accept: text/markdown' \
    -H 'If-Modified-Since: Wed, 01 Jan 2020 00:00:00 GMT' \
    "http://127.0.0.1:${PORT}/index.html" \
    -w '%{http_code}')"

  [[ "${code1}" == "200" ]] || { echo "Expected first response 200, got ${code1}" >&2; exit 1; }
  [[ "${code2}" == "304" ]] || { echo "Expected IMS match response 304, got ${code2}" >&2; exit 1; }
  [[ "${code3}" == "200" ]] || { echo "Expected IMS older-date response 200, got ${code3}" >&2; exit 1; }

  grep -qi '^Content-Type: text/markdown; charset=utf-8' resp1.headers || {
    echo "Converted response missing markdown Content-Type" >&2
    exit 1
  }
  grep -qi '^Vary: .*Accept' resp1.headers || {
    echo "Converted response missing Vary: Accept" >&2
    exit 1
  }
  grep -q '^# Hello IMS$' resp1.body || {
    echo "Converted response body does not contain expected Markdown heading" >&2
    exit 1
  }

  # curl creates an empty file for a 304 with -o on most platforms; accept missing or empty.
  if [[ -f resp2.body && -s resp2.body ]]; then
    echo "Expected empty 304 response body, but resp2.body is non-empty" >&2
    exit 1
  fi

  cl="$(awk 'BEGIN{IGNORECASE=1} /^Content-Length:/ {gsub(/\r/, ""); print $2; exit}' resp1.headers)"
  body_size="$(wc -c < resp1.body | tr -d ' ')"
  [[ "${cl}" == "${body_size}" ]] || {
    echo "Content-Length/body mismatch: header=${cl} body=${body_size}" >&2
    exit 1
  }

  echo "Validation summary:"
  echo "  code1=${code1} code2=${code2} code3=${code3}"
  echo "  Last-Modified=${lm}"
  echo "  Content-Length=${cl}"
  echo "  Body bytes=${body_size}"
)

echo "==> Real NGINX IMS validation passed"
