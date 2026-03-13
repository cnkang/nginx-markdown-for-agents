#!/usr/bin/env bash
set -euo pipefail

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18089}"
BACKEND_PORT="${BACKEND_PORT:-19089}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BACKEND_SERVER="${WORKSPACE_ROOT}/tools/e2e/fixtures/tls_backend_server.py"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
BACKEND_PID=""
NGINX_BIN_OUTPUT_FILE=""
BUILDROOT_OUTPUT_FILE=""
ORIG_ARGS=("$@")

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT]
                         [--backend-port PORT] [--nginx-bin-output FILE] [--buildroot-output FILE]

Build or reuse a module-enabled NGINX runtime and validate Markdown conversion
through a real HTTPS proxy chain backed by a local TLS test server.

Environment variables:
  NGINX_VERSION       Default: 1.28.2
  PORT                Default: 18089
  BACKEND_PORT        Default: 19089
  NGINX_BIN           Optional module-enabled nginx binary to reuse instead of rebuilding
EOF
  return 0
}

nginx_supports_ssl_upstream() {
  local nginx_bin="$1"
  "${nginx_bin}" -V 2>&1 | grep -q -- '--with-http_ssl_module'
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
    --backend-port)
      BACKEND_PORT="$2"
      shift 2
      ;;
    --nginx-bin-output)
      NGINX_BIN_OUTPUT_FILE="$2"
      shift 2
      ;;
    --buildroot-output)
      BUILDROOT_OUTPUT_FILE="$2"
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

source "${NATIVE_BUILD_HELPER}"

if (( ${#ORIG_ARGS[@]} )); then
  markdown_ensure_native_apple_silicon "$0" "${ORIG_ARGS[@]}"
else
  markdown_ensure_native_apple_silicon "$0"
fi

header_value() {
  local file_path="$1"
  local header_name="$2"

  awk -F': ' -v header_name="${header_name}" '
    BEGIN { IGNORECASE = 1 }
    tolower($1) == tolower(header_name) {
      sub(/\r$/, "", $2);
      print $2;
      exit;
    }
  ' "${file_path}"
}

cleanup() {
  local rc=$?

  if [[ -n "${BACKEND_PID}" ]]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
    wait "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}" || true
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Proxy TLS backend E2E validation failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Proxy TLS backend E2E validation succeeded. Artifacts kept at: ${BUILDROOT}"
  fi
  return 0
}
trap cleanup EXIT

for cmd in curl python3 openssl awk grep sed; do
  markdown_need_cmd "${cmd}"
done

if [[ -n "${NGINX_BIN}" ]]; then
  if ! nginx_supports_ssl_upstream "${NGINX_BIN}"; then
    echo "==> Reusable NGINX binary lacks http_ssl_module; falling back to self-build"
    NGINX_BIN=""
  fi
fi

if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "${cmd}"
  done
fi

if [[ ! -f "${BACKEND_SERVER}" ]]; then
  echo "Missing TLS backend fixture: ${BACKEND_SERVER}" >&2
  exit 1
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-proxy-tls-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
TLS_DIR="${BUILDROOT}/tls"
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs" "${RAW_DIR}" "${TLS_DIR}"

echo "==> Host architecture: $(uname -m)"
if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
  markdown_copy_runtime_conf_from_nginx_bin "${NGINX_BIN}" "${RUNTIME}"
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
    if command -v brew >/dev/null 2>&1; then
      openssl_prefix="$(brew --prefix openssl@3 2>/dev/null || true)"
      if [[ -n "${openssl_prefix}" ]]; then
        export CPPFLAGS="-I${openssl_prefix}/include ${CPPFLAGS:-}"
        export LDFLAGS="-L${openssl_prefix}/lib ${LDFLAGS:-}"
      fi
    fi
    ./configure \
      --with-http_ssl_module \
      --without-http_rewrite_module \
      --prefix="${RUNTIME}" \
      --add-module="${WORKSPACE_ROOT}/components/nginx-module" >/dev/null
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
    make install >/dev/null
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

echo "==> Generating TLS certificate for backend fixture"
if ! openssl req \
  -x509 -newkey rsa:2048 -sha256 -days 1 -nodes \
  -keyout "${TLS_DIR}/server.key" \
  -out "${TLS_DIR}/server.crt" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  > "${RAW_DIR}/tls-cert.log" 2>&1; then
  cat "${RAW_DIR}/tls-cert.log" >&2
  exit 1
fi

echo "==> Starting TLS backend on 127.0.0.1:${BACKEND_PORT}"
python3 "${BACKEND_SERVER}" \
  --port "${BACKEND_PORT}" \
  --tls-cert "${TLS_DIR}/server.crt" \
  --tls-key "${TLS_DIR}/server.key" \
  > "${RAW_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!

for _ in $(seq 1 50); do
  if curl -sk "https://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -sk "https://127.0.0.1:${BACKEND_PORT}/health" >/dev/null

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
worker_processes 1;
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

        location / {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_conditional_requests full_support;
            markdown_log_verbosity info;

            proxy_pass https://127.0.0.1:${BACKEND_PORT};
            proxy_ssl_server_name on;
            proxy_ssl_name localhost;
            proxy_ssl_verify off;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Case 1: proxy/TLS Markdown conversion"
basic_code="$(curl -sS -D "${RAW_DIR}/basic.hdr" -o "${RAW_DIR}/basic.body" \
  -H 'Accept: text/markdown' \
  "http://127.0.0.1:${PORT}/simple" \
  -w '%{http_code}')"
[[ "${basic_code}" == "200" ]] || { echo "Expected /simple 200, got ${basic_code}" >&2; exit 1; }
grep -qi '^Content-Type: text/markdown; charset=utf-8' "${RAW_DIR}/basic.hdr" || {
  echo "Missing markdown Content-Type on /simple" >&2
  exit 1
}
grep -qi '^Vary: .*Accept' "${RAW_DIR}/basic.hdr" || {
  echo "Missing Vary: Accept on /simple" >&2
  exit 1
}
grep -q '^# Simple Test Page$' "${RAW_DIR}/basic.body" || {
  echo "Markdown heading missing on /simple" >&2
  exit 1
}
grep -q '\[Example\](https://example.com)' "${RAW_DIR}/basic.body" || {
  echo "Markdown link missing on /simple" >&2
  exit 1
}

echo "==> Case 2: backend headers preserved and ETag regenerated"
cache_control="$(header_value "${RAW_DIR}/basic.hdr" 'Cache-Control')"
etag="$(header_value "${RAW_DIR}/basic.hdr" 'ETag')"
[[ "${cache_control}" == "public, max-age=3600" ]] || {
  echo "Expected backend Cache-Control, got ${cache_control:-<empty>}" >&2
  exit 1
}
[[ -n "${etag}" && "${etag}" != '"simple-v1"' ]] || {
  echo "Expected module-generated ETag, got ${etag:-<empty>}" >&2
  exit 1
}

echo "==> Case 3: backend 500 passthrough"
error_code="$(curl -sS -D "${RAW_DIR}/error.hdr" -o "${RAW_DIR}/error.body" \
  -H 'Accept: text/markdown' \
  "http://127.0.0.1:${PORT}/error" \
  -w '%{http_code}')"
[[ "${error_code}" == "500" ]] || { echo "Expected /error 500, got ${error_code}" >&2; exit 1; }
grep -qi '^Content-Type: text/html' "${RAW_DIR}/error.hdr" || {
  echo "Expected passthrough text/html on /error" >&2
  exit 1
}

echo "==> Case 4: HEAD through proxy"
head_code="$(curl -sS -D "${RAW_DIR}/head.hdr" -o "${RAW_DIR}/head.body" \
  -X HEAD -H 'Accept: text/markdown' \
  "http://127.0.0.1:${PORT}/simple" \
  -w '%{http_code}')"
[[ "${head_code}" == "200" ]] || { echo "Expected HEAD /simple 200, got ${head_code}" >&2; exit 1; }
grep -qi '^Content-Type: text/markdown; charset=utf-8' "${RAW_DIR}/head.hdr" || {
  echo "Expected markdown Content-Type on HEAD /simple" >&2
  exit 1
}
[[ ! -s "${RAW_DIR}/head.body" ]] || {
  echo "Expected empty HEAD response body" >&2
  exit 1
}

echo "Proxy TLS backend summary:"
echo "  nginx_version=${NGINX_VERSION}"
echo "  arch=$(uname -m)"
echo "  basic_http=${basic_code}"
echo "  passthrough_error_http=${error_code}"
echo "  head_http=${head_code}"
echo "  cache_control=${cache_control}"
echo "  etag=${etag}"
echo "  artifacts=${BUILDROOT}"

if [[ -n "${NGINX_BIN_OUTPUT_FILE}" ]]; then
  printf '%s\n' "${NGINX_EXECUTABLE}" > "${NGINX_BIN_OUTPUT_FILE}"
fi

if [[ -n "${BUILDROOT_OUTPUT_FILE}" ]]; then
  printf '%s\n' "${BUILDROOT}" > "${BUILDROOT_OUTPUT_FILE}"
fi
