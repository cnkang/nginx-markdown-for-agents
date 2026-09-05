#!/usr/bin/env bash
# verify_real_nginx_ims.sh — Validate conditional-request behaviour for Markdown
# representations.
#
# Builds a local NGINX from source with the markdown module (or reuses
# an existing binary), starts it with cache validation enabled,
# and verifies the representation-validator contract:
#   1. A Markdown-negotiated response returns 200 with correct Content-Type,
#      a Markdown-derived ETag, and NO source HTML Last-Modified header.
#   2. If-None-Match carrying the returned Markdown ETag yields 304;
#      an unknown ETag yields 200.
#   3. If-Modified-Since carrying the source HTML mtime never yields 304
#      for a converted response — conversion runs and delivers fresh 200.
#   4. If-Match failures return 412, weak tags are rejected, and '*' passes;
#      If-Match takes precedence over an ignored converted-representation IUS.
#   5. A converted 412 clears source ETag, digest, and Trailer metadata, and
#      HEAD reports the failing precondition without a source Last-Modified.
#   6. A passthrough source response retains NGINX's normal IUS 412 semantics.
#   7. Proxied upstream responses obey the same validator contract.
#   8. HEAD responses describe the Markdown representation only (no source
#      Last-Modified either).
#
# Usage:
#   tools/ci/verify_real_nginx_ims.sh [--keep-artifacts] [--nginx-version VER] [--port PORT]
#
# Environment variables:
#   NGINX_BIN       Optional reusable module-enabled nginx binary
#   NGINX_VERSION   NGINX version (default: stable)
#   PORT            Listen port (default: 18088); backend origin listens on PORT+1
#
# Exit behaviour:
#   0 if all conditional-validation checks pass.
#   1 if any check fails or prerequisites are missing.
set -euo pipefail

NGINX_VERSION="${NGINX_VERSION:-stable}"
PORT="${PORT:-18088}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
NGINX_BIN_OUTPUT_FILE=""
BUILDROOT_OUTPUT_FILE=""
ORIG_ARGS=("$@")
readonly ACCEPT_MARKDOWN_HEADER='Accept: text/markdown'
readonly HTTP_CODE_FORMAT='%{http_code}'
readonly LAST_MODIFIED_HEADER_PATTERN='^Last-Modified:'
readonly MARKDOWN_CONTENT_TYPE_HEADER_PATTERN='^Content-Type: text/markdown; charset=utf-8'
readonly STALE_IF_UNMODIFIED_SINCE_HEADER='If-Unmodified-Since: Thu, 01 Jan 1970 00:00:00 GMT'

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT]
                         [--nginx-bin-output FILE] [--buildroot-output FILE]

Builds a local NGINX from source with the markdown module and validates delegated
If-Modified-Since behavior for Markdown-negotiated responses.

VERSION accepts:
  - stable    (resolve latest stable from nginx.org)
  - mainline  (resolve latest mainline from nginx.org)
  - x.y.z     (use explicit nginx release)

Environment variables:
  NGINX_VERSION   Default: stable (or explicit x.y.z)
  PORT            Default: 18088
  NGINX_BIN       Optional module-enabled nginx binary to reuse instead of rebuilding
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
      NGINX_VERSION="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
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

# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"

if (( ${#ORIG_ARGS[@]} )); then
  markdown_ensure_native_apple_silicon "$0" "${ORIG_ARGS[@]}"
else
  markdown_ensure_native_apple_silicon "$0"
fi

resolve_nginx_version() {
  local requested="$1"
  local page
  local version

  case "${requested}" in
    stable|mainline)
      page="$(curl --proto '=https' --tlsv1.2 -fsSL https://nginx.org/en/download.html)"
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

for cmd in curl rsync awk python3; do
  markdown_need_cmd "$cmd"
done
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

NGINX_VERSION="$(resolve_nginx_version "${NGINX_VERSION}")"

cleanup() {
  local rc=$?
  if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
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
  return 0
}
trap cleanup EXIT

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d "${TMPDIR:-/tmp}/nginx-ims-verify.XXXXXX")"
RUNTIME="${BUILDROOT}/runtime"

# NGINX workers may run as an unprivileged user, so the temporary build root
# must be traversable by that user for static-file reads during validation.
umask 022
chmod 755 "${BUILDROOT}"

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html" "${RUNTIME}/logs"

if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
  LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
  NGINX_EXECUTABLE="${NGINX_BIN}"
else
  echo "==> Building Rust converter (${RUST_TARGET})"
  # Keep streaming feature enabled so this retained binary can be safely
  # reused by downstream streaming E2E checks in CI.
  markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}" \
    --features streaming

  echo "==> Downloading NGINX ${NGINX_VERSION}"
  curl --proto '=https' --tlsv1.2 -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}" --strip-components=1

  echo "==> Configuring NGINX"
  (
    cd "${BUILDROOT}"
    # Enable streaming at compile time so the C module includes streaming
    # code paths; this must match the Rust --features streaming flag above.
    ./configure \
      --without-http_rewrite_module \
      --with-http_gunzip_module \
      --with-http_auth_request_module \
      --with-cc-opt="-DMARKDOWN_STREAMING_ENABLED" \
      --prefix="${RUNTIME}" \
      --add-module="${WORKSPACE_ROOT}/components/nginx-module"
  )

  echo "==> Building and installing NGINX"
  (
    cd "${BUILDROOT}"
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
    make install
  )

  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes  1;
error_log  logs/error.log info;
pid        logs/nginx.pid;

events { worker_connections 128; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout  5;

    # Plain upstream origin: serves the source HTML with its own
    # Last-Modified and no module directives, so the frontend proxy
    # location exercises conversion of an upstream (non-static) response.
    server {
        listen 127.0.0.1:$((PORT + 1));
        server_name origin.localhost;

        location / {
            root html;
            add_header Digest "sha-256=source-html" always;
            add_header Trailer "Digest" always;
        }

        location /source/ {
            alias html/;
            markdown_filter off;
        }
    }

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location / {
            root html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_cache_validation full;
            markdown_log_verbosity info;
        }

        location /proxy/ {
            proxy_pass http://127.0.0.1:$((PORT + 1))/;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_cache_validation full;
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
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Running conditional-request validation scenario"
(
  cd "${BUILDROOT}"

  rm -f resp0.headers resp1.headers resp1.body resp2.headers resp2.body \
        resp3.headers resp3.body resp4.headers resp4.body \
        resp5.headers resp5.body resp6.headers resp7.headers resp7.body \
        resp8.headers resp8.body resp9.headers resp9.body \
        resp10.headers resp10.body resp11.headers resp12.headers resp12.body \
        resp13.headers resp13.body

  # Harvest the source HTML validators from the module-free origin.
  code0="$(curl -sS -D resp0.headers -o /dev/null \
    "http://127.0.0.1:$((PORT + 1))/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code0}" == "200" ]] || { echo "Expected source HTML 200, got ${code0}" >&2; exit 1; }
  lm="$(awk 'BEGIN{IGNORECASE=1} /^Last-Modified:/ {sub(/^Last-Modified:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit}' resp0.headers)"
  [[ -n "${lm}" ]] || { echo "Source HTML missing Last-Modified header" >&2; exit 1; }
  source_etag="$(awk 'BEGIN{IGNORECASE=1} /^ETag:/ {sub(/^ETag:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit}' resp0.headers)"
  [[ -n "${source_etag}" ]] || { echo "Source HTML missing ETag header" >&2; exit 1; }

  # 1. Converted Markdown GET: fresh 200, Markdown Content-Type, Vary,
  #    Markdown-derived ETag, and never the source HTML Last-Modified.
  code1="$(curl -sS -D resp1.headers -o resp1.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"

  etag="$(awk 'BEGIN{IGNORECASE=1} /^ETag:/ {sub(/^ETag:[[:space:]]*/, ""); sub(/\r$/, ""); print; exit}' resp1.headers)"
  [[ "${code1}" == "200" ]] || { echo "Expected converted response 200, got ${code1}" >&2; exit 1; }
  [[ -n "${etag}" ]] || { echo "Converted response missing Markdown-derived ETag" >&2; exit 1; }

  grep -qi "${MARKDOWN_CONTENT_TYPE_HEADER_PATTERN}" resp1.headers || {
    echo "Converted response missing markdown Content-Type" >&2
    exit 1
  }
  grep -qi '^Vary: .*Accept' resp1.headers || {
    echo "Converted response missing Vary: Accept" >&2
    exit 1
  }
  if grep -qi "${LAST_MODIFIED_HEADER_PATTERN}" resp1.headers; then
    echo "Converted response must not carry the source HTML Last-Modified" >&2
    exit 1
  fi
  grep -q '^# Hello IMS$' resp1.body || {
    echo "Converted response body does not contain expected Markdown heading" >&2
    exit 1
  }

  cl="$(awk 'BEGIN{IGNORECASE=1} /^Content-Length:/ {gsub(/\r/, ""); print $2; exit}' resp1.headers)"
  body_size="$(wc -c < resp1.body | tr -d ' ')"
  [[ "${cl}" == "${body_size}" ]] || {
    echo "Content-Length/body mismatch: header=${cl} body=${body_size}" >&2
    exit 1
  }

  # 2. If-None-Match with the returned Markdown ETag validates to 304;
  #    an unknown ETag does not.
  code2="$(curl -sS -D resp2.headers -o resp2.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H "If-None-Match: ${etag}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  code3="$(curl -sS -D resp3.headers -o resp3.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H 'If-None-Match: "unknown-etag-value"' \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"

  [[ "${code2}" == "304" ]] || { echo "Expected matching ETag response 304, got ${code2}" >&2; exit 1; }
  [[ "${code3}" == "200" ]] || { echo "Expected unknown ETag response 200, got ${code3}" >&2; exit 1; }

  # curl creates an empty file for a 304 with -o on most platforms; accept missing or empty.
  if [[ -f resp2.body && -s resp2.body ]]; then
    echo "Expected empty 304 response body, but resp2.body is non-empty" >&2
    exit 1
  fi

  # 3. The source HTML mtime must never validate a converted response:
  #    an IMS-only request converts and delivers a fresh 200 instead of 304.
  code4="$(curl -sS -D resp4.headers -o resp4.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H "If-Modified-Since: ${lm}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code4}" == "200" ]] || { echo "Source HTML mtime must not yield 304, got ${code4}" >&2; exit 1; }
  if grep -qi "${LAST_MODIFIED_HEADER_PATTERN}" resp4.headers; then
    echo "Fresh converted response after IMS-only request carries source Last-Modified" >&2
    exit 1
  fi
  grep -q '^# Hello IMS$' resp4.body || {
    echo "IMS-only fallback body is not the converted Markdown document" >&2
    exit 1
  }

  # 4. Proxied upstream conversion obeys the same validator contract.
  code5="$(curl -sS -D resp5.headers -o resp5.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    "http://127.0.0.1:${PORT}/proxy/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code5}" == "200" ]] || { echo "Expected proxied conversion 200, got ${code5}" >&2; exit 1; }
  grep -qi "${MARKDOWN_CONTENT_TYPE_HEADER_PATTERN}" resp5.headers || {
    echo "Proxied converted response missing markdown Content-Type" >&2
    exit 1
  }
  grep -qi '^Vary: .*Accept' resp5.headers || {
    echo "Proxied converted response missing Vary: Accept" >&2
    exit 1
  }
  if grep -qi "${LAST_MODIFIED_HEADER_PATTERN}" resp5.headers; then
    echo "Proxied converted response carries upstream Last-Modified" >&2
    exit 1
  fi

  # 5. HEAD describes the Markdown representation only.
  code6="$(curl -sS -I -D resp6.headers -o /dev/null \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}" | tr -d '\n')"
  [[ "${code6}" == "200" ]] || { echo "Expected HEAD 200, got ${code6}" >&2; exit 1; }
  grep -qi "${MARKDOWN_CONTENT_TYPE_HEADER_PATTERN}" resp6.headers || {
    echo "HEAD representation missing markdown Content-Type" >&2
    exit 1
  }
  grep -qi '^Vary: .*Accept' resp6.headers || {
    echo "HEAD representation missing Vary: Accept" >&2
    exit 1
  }
  if grep -qi "${LAST_MODIFIED_HEADER_PATTERN}" resp6.headers; then
    echo "HEAD representation carries source HTML Last-Modified" >&2
    exit 1
  fi

  # 6. If-Match is evaluated against the generated Markdown ETag. A failed
  # strong comparison returns 412 even when an independent IUS value is also
  # stale; the converted representation ignores source Last-Modified.
  code7="$(curl -sS -D resp7.headers -o resp7.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H 'If-Match: "different-etag-value"' \
    -H "${STALE_IF_UNMODIFIED_SINCE_HEADER}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code7}" == "412" ]] || {
    echo "Expected failing If-Match response 412, got ${code7}" >&2
    exit 1
  }
  if [[ -f resp7.body && -s resp7.body ]]; then
    echo "Expected empty 412 response body, but resp7.body is non-empty" >&2
    exit 1
  fi
  grep -qi "${MARKDOWN_CONTENT_TYPE_HEADER_PATTERN}" resp7.headers || {
    echo "412 response missing Markdown Content-Type" >&2
    exit 1
  }
  for source_header in '^Last-Modified:' '^ETag:' '^Digest:' \
      '^Content-Digest:' '^Content-MD5:' '^Repr-Digest:' '^Trailer:'; do
    if grep -qi "${source_header}" resp7.headers; then
      echo "412 response must not carry source header ${source_header}" >&2
      exit 1
    fi
  done

  # 7. If-Match uses strong comparison: a weak form of the returned ETag is
  # rejected, while the wildcard succeeds.
  code8="$(curl -sS -D resp8.headers -o resp8.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H "If-Match: W/${etag}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code8}" == "412" ]] || {
    echo "Expected weak If-Match response 412, got ${code8}" >&2
    exit 1
  }
  code9="$(curl -sS -D resp9.headers -o resp9.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H 'If-Match: *' \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code9}" == "200" ]] || {
    echo "Expected wildcard If-Match response 200, got ${code9}" >&2
    exit 1
  }
  grep -q '^# Hello IMS$' resp9.body || {
    echo "Wildcard If-Match response is not converted Markdown" >&2
    exit 1
  }

  # A matching If-Match plus a stale source date still succeeds because IUS
  # is not a validator for the transformed Markdown representation.
  code10="$(curl -sS -D resp10.headers -o resp10.body \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H "If-Match: ${etag}" \
    -H "${STALE_IF_UNMODIFIED_SINCE_HEADER}" \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code10}" == "200" ]] || {
    echo "Expected matching If-Match plus converted IUS response 200, got ${code10}" >&2
    exit 1
  }

  # HEAD follows the same failing-precondition path and must not reveal the
  # source Last-Modified header.
  code11="$(curl -sS -I -D resp11.headers -o /dev/null \
    -H "${ACCEPT_MARKDOWN_HEADER}" \
    -H 'If-Match: "different-etag-value"' \
    "http://127.0.0.1:${PORT}/index.html" \
    -w "${HTTP_CODE_FORMAT}" | tr -d '\n')"
  [[ "${code11}" == "412" ]] || {
    echo "Expected HEAD failing If-Match response 412, got ${code11}" >&2
    exit 1
  }
  if grep -qi "${LAST_MODIFIED_HEADER_PATTERN}" resp11.headers; then
    echo "HEAD 412 response carries source HTML Last-Modified" >&2
    exit 1
  fi

  # Passthrough remains source-scoped: NGINX itself must reject a stale
  # If-Unmodified-Since against the origin Last-Modified value.
  code12="$(curl -sS -D resp12.headers -o resp12.body \
    -H "${STALE_IF_UNMODIFIED_SINCE_HEADER}" \
    "http://127.0.0.1:$((PORT + 1))/source/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code12}" == "412" ]] || {
    echo "Expected passthrough IUS response 412, got ${code12}" >&2
    exit 1
  }
  code13="$(curl -sS -D resp13.headers -o resp13.body \
    -H "If-Match: ${source_etag}" \
    -H "${STALE_IF_UNMODIFIED_SINCE_HEADER}" \
    "http://127.0.0.1:$((PORT + 1))/source/index.html" \
    -w "${HTTP_CODE_FORMAT}")"
  [[ "${code13}" == "412" ]] || {
    echo "Expected passthrough If-Match plus IUS response 412, got ${code13}" >&2
    exit 1
  }

  echo "Validation summary:"
  echo "  plain=${code0} get=${code1} inm_match=${code2} inm_miss=${code3} ims_only=${code4} proxy=${code5} head=${code6}"
  echo "  if_match_fail=${code7} weak_if_match=${code8} wildcard_if_match=${code9}"
  echo "  if_match_plus_ius=${code10} head_if_match_fail=${code11}"
  echo "  passthrough_ius=${code12} passthrough_if_match_plus_ius=${code13}"
  echo "  source Last-Modified=${lm}"
  echo "  Markdown ETag=${etag}"
  echo "  Content-Length=${cl}"
  echo "  Body bytes=${body_size}"
)

echo "==> Real NGINX conditional-validation passed"


if [[ -n "${NGINX_BIN_OUTPUT_FILE}" ]]; then
  printf '%s\n' "${NGINX_EXECUTABLE}" > "${NGINX_BIN_OUTPUT_FILE}"
fi

if [[ -n "${BUILDROOT_OUTPUT_FILE}" ]]; then
  printf '%s\n' "${BUILDROOT}" > "${BUILDROOT_OUTPUT_FILE}"
fi
