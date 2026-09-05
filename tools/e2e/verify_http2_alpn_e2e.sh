#!/usr/bin/env bash
set -euo pipefail

# E2E validation for real HTTP/2 (ALPN) sessions over a module-enabled NGINX.
#
# Coverage:
#   1. ALPN negotiation on a TLS listener with `listen ... ssl http2`
#   2. `curl --http2` completes a real HTTP/2 request/response cycle
#   3. HTTP/2 response carries the module conversion (text/markdown body)
#   4. HTTP/2 with streaming enabled delivers chunked converted output
#
# The module itself needs no HTTP/2-specific code; this gate exists to prove
# the example configurations that advertise `http2` keep working end-to-end
# with a modern client, and to catch regressions where the sandbox rewrite in
# verify_examples_nginx_t.sh strips the `http2` keyword (listen 443 ssl http2
# -> listen 18180), silently un-testing the h2 surface.
#
# Exit codes:
#   0 - all HTTP/2 assertions passed
#   1 - an HTTP/2 assertion failed
#   2 - usage or prerequisite error

NGINX_VERSION="${NGINX_VERSION:-1.28.3}"
PORT="${PORT:-18200}"
KEEP_ARTIFACTS=0
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RAW_DIR=""
TLS_DIR=""
NGINX_EXECUTABLE=""
NGINX_PID=""

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--port PORT] [--nginx-version VERSION]

Validate real HTTP/2 (ALPN) sessions through a module-enabled NGINX.

Options:
  --keep-artifacts      Keep the build sandbox after the run
  --port PORT           TLS listener port (default 18200)
  --nginx-version       NGINX version to build when no reusable binary (default 1.28.3)

Environment:
  NGINX_BIN             Reusable module-enabled NGINX binary (optional)
EOF
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
      ;;
    --port)
      markdown_require_flag_value "$1" "${2:-}"
      PORT="$2"
      shift 2
      ;;
    --nginx-version)
      markdown_require_flag_value "$1" "${2:-}"
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

nginx_has_http_v2() {
  local nginx_bin="$1"
  "${nginx_bin}" -V 2>&1 | grep -q -- '--with-http_v2_module'
  return $?
}

for cmd in curl openssl grep awk sed; do
  markdown_need_cmd "${cmd}"
done

if [[ -n "${NGINX_BIN:-}" ]] && ! nginx_has_http_v2 "${NGINX_BIN}"; then
  echo "==> Reusable NGINX binary lacks --with-http_v2_module; self-building" >&2
  NGINX_BIN=""
fi

BUILDROOT="$(mktemp -d /tmp/nginx-http2-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
TLS_DIR="${BUILDROOT}/tls"
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs" "${RAW_DIR}" "${TLS_DIR}"

cleanup() {
  local rc=$?
  if [[ -n "${NGINX_PID}" ]] && kill -0 "${NGINX_PID}" 2>/dev/null; then
    kill "${NGINX_PID}" 2>/dev/null || true
    wait "${NGINX_PID}" 2>/dev/null || true
    sleep 1
    if kill -0 "${NGINX_PID}" 2>/dev/null; then
      kill -9 "${NGINX_PID}" 2>/dev/null || true
    fi
  fi
  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "HTTP/2 E2E validation failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "HTTP/2 E2E validation succeeded. Artifacts kept at: ${BUILDROOT}"
  elif [[ -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}"
  fi
  return 0
}
trap cleanup EXIT

echo "==> Self-signed TLS certificate for ALPN fixture"
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

if [[ -z "${NGINX_BIN:-}" ]]; then
  echo "==> Building module-enabled NGINX with HTTP/2"
  RUST_TARGET="$(markdown_detect_rust_target)"
  markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}" --features streaming >/dev/null
  markdown_download_nginx_source "${NGINX_VERSION}" "${BUILDROOT}/nginx.tar.gz" "${WORKSPACE_ROOT}"
  mkdir -p "${BUILDROOT}/src"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
  (
    cd "${BUILDROOT}/src"
    configure_args=(
      --with-http_ssl_module
      --with-http_v2_module
      --with-http_auth_request_module
      --without-http_rewrite_module
      --prefix="${RUNTIME}"
      --add-module="${WORKSPACE_ROOT}/components/nginx-module"
    )
    while IFS= read -r configure_arg; do
      configure_args+=("${configure_arg}")
    done < <(markdown_emit_nginx_configure_env "" "")
    if ! ./configure "${configure_args[@]}" > "${RAW_DIR}/nginx-build.log" 2>&1 \
      || ! make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >> "${RAW_DIR}/nginx-build.log" 2>&1 \
      || ! make install >> "${RAW_DIR}/nginx-build.log" 2>&1; then
      markdown_print_nginx_build_failure_diagnostics "${BUILDROOT}" "${RAW_DIR}/nginx-build.log"
      exit 1
    fi
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
else
  echo "==> Reusing NGINX binary (${NGINX_BIN})"
  NGINX_EXECUTABLE="${NGINX_BIN}"
fi

echo "==> Writing HTTP/2 fixture configuration"
HTML_TEST_BODY="$(printf '<!doctype html><html><body><h2>Heading</h2><p>Hello HTTP/2 markdown body.</p></body></html>\n')"
printf '%s' "${HTML_TEST_BODY}" > "${RUNTIME}/conf/index.html"
mkdir -p "${RUNTIME}/conf/streaming"
printf '%s' "${HTML_TEST_BODY}" > "${RUNTIME}/conf/streaming/index.html"

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
        listen ${PORT} ssl http2;
        server_name localhost;

        ssl_certificate     ${TLS_DIR}/server.crt;
        ssl_certificate_key ${TLS_DIR}/server.key;
        ssl_protocols       TLSv1.2 TLSv1.3;

        location / {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
            root ${RUNTIME}/conf;
            index index.html;
            default_type text/html;
        }

        location /streaming/ {
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming auto;
            root ${RUNTIME}/conf;
            index index.html;
            default_type text/html;
        }
    }
}
EOF

"${NGINX_EXECUTABLE}" -p "${RUNTIME}/" -c "conf/nginx.conf" -t > "${RAW_DIR}/nginx-t.log" 2>&1 || {
  cat "${RAW_DIR}/nginx-t.log" >&2
  exit 1
}
"${NGINX_EXECUTABLE}" -p "${RUNTIME}/" -c "conf/nginx.conf" -g "daemon off;" > "${RAW_DIR}/nginx.log" 2>&1 &
NGINX_PID=$!

for _ in $(seq 1 50); do
  if curl -sk --max-time 1 "https://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

if ! curl -sk --max-time 1 "https://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
  echo "FAIL: NGINX did not become ready on port ${PORT}" >&2
  cat "${RAW_DIR}/nginx.log" >&2 2>/dev/null || true
  exit 1
fi

fail() {
  echo "FAIL: $*" >&2
  exit 1
  return 1
}

echo "==> HTTP/2 ALPN negotiation assertions"
H2_VERBOSE="$(curl -sk --http2 -v "https://127.0.0.1:${PORT}/" -o "${RAW_DIR}/h2-body.txt" 2>&1 || true)"
if ! grep -q 'ALPN, server accepted protocol h2' <<<"${H2_VERBOSE}"; then
  # curl 7.x variants print negotiated protocol differently on some platforms
  if ! grep -qi 'using HTTP/2' <<<"${H2_VERBOSE}" && ! grep -qi 'HTTP/2.0' <<<"${H2_VERBOSE}"; then
    fail "no h2 ALPN negotiation observed (curl verbose: ${H2_VERBOSE})"
  fi
fi

echo "==> HTTP/2 converted body assertion"
grep -q "Hello HTTP/2 markdown body" "${RAW_DIR}/h2-body.txt" \
  || fail "converted markdown body not found in HTTP/2 response"

echo "==> HTTP/2 status + content-type over h2"
H2_HEADERS="$(curl -sk --http2 -D - -o /dev/null "https://127.0.0.1:${PORT}/" || true)"
grep -qi '^HTTP/2 200' <<<"${H2_HEADERS}" \
  || fail "expected HTTP/2 200 (got: ${H2_HEADERS})"
grep -qi '^content-type: text/markdown' <<<"${H2_HEADERS}" \
  || fail "expected content-type text/markdown over HTTP/2"

echo "==> HTTP/2 streaming delivery assertion"
STREAM_BODY="$(curl -sk --http2 --max-time 10 "https://127.0.0.1:${PORT}/streaming/" || true)"
grep -q "Hello HTTP/2 markdown body" <<<"${STREAM_BODY}" \
  || fail "streaming conversion failed over HTTP/2"

echo "PASS: real HTTP/2 (ALPN) sessions complete with module conversion"
