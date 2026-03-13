#!/bin/bash
set -euo pipefail

# Large-response regression guard for the markdown body filter buffering path.
# This script builds a local NGINX with the module and verifies that a ~1MB HTML
# response can be converted to Markdown without stalling/timeouts.

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18091}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
ORIG_ARGS=("$@")
source "${NATIVE_BUILD_HELPER}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT]

Build a local NGINX (default stable ${NGINX_VERSION}) with the markdown module and
run a real HTTP E2E regression check for large (~1MB) Markdown conversion.

This script auto-reexecs to native arm64 on Apple Silicon when launched under
Rosetta translation.

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

cleanup() {
  local rc=$?

  if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}" || true
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Regression check failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Regression check succeeded. Artifacts kept at: ${BUILDROOT}"
  fi
  return 0
}
trap cleanup EXIT

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

if (( ${#ORIG_ARGS[@]} )); then
  markdown_ensure_native_apple_silicon "$0" "${ORIG_ARGS[@]}"
else
  markdown_ensure_native_apple_silicon "$0"
fi

for cmd in curl python3 ab awk sed grep; do
  markdown_need_cmd "$cmd"
done
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-large-regress.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
mkdir -p "${RAW_DIR}"

echo "==> Host architecture: $(uname -m)"
if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
  markdown_copy_runtime_conf_from_nginx_bin "${NGINX_BIN}" "${RUNTIME}"
  NGINX_EXECUTABLE="${NGINX_BIN}"
else
  echo "==> Building Rust converter (${RUST_TARGET})"
  markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}"

  echo "==> Downloading/building NGINX ${NGINX_VERSION}"
  curl -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
  mkdir -p "${BUILDROOT}/src"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
  (
    cd "${BUILDROOT}/src"
    ./configure \
      --without-http_rewrite_module \
      --prefix="${RUNTIME}" \
      --add-module="${WORKSPACE_ROOT}/components/nginx-module" >/dev/null
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
    make install >/dev/null
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html/full" "${RUNTIME}/html/passthrough" "${RUNTIME}/logs"

python3 - <<'PY' "${WORKSPACE_ROOT}" "${RUNTIME}/html"
import pathlib, sys
repo = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
seed = (repo / "tests/corpus/complex/blog-post.html").read_bytes()

def repeat_to_size(seed_bytes: bytes, target: int) -> bytes:
    out = bytearray(b'<!doctype html><html><head><meta charset="UTF-8"><title>large</title></head><body>\n')
    while len(out) + len(seed_bytes) + 32 < target:
        out.extend(seed_bytes)
        out.extend(b"\n")
    out.extend(b"</body></html>\n")
    return bytes(out)

payload = repeat_to_size(seed, 1024 * 1024)
(root / "full" / "large.html").write_bytes(payload)
(root / "passthrough" / "large.html").write_bytes(payload)
print(f"generated_large_html_bytes={len(payload)}")
PY

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
worker_processes 1;
error_log logs/error.log warn;
pid logs/nginx.pid;

events { worker_connections 256; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout  5;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;
        root html;

        location /passthrough/ {
            markdown_filter off;
        }

        location /full/ {
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_conditional_requests full_support;
            markdown_log_verbosity warn;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Validating large passthrough + markdown responses"
pt_code="$(curl -sS -D "${RAW_DIR}/passthrough_large.hdr" -o "${RAW_DIR}/passthrough_large.body" \
  --max-time 20 "http://127.0.0.1:${PORT}/passthrough/large.html" -w '%{http_code}')"
md_code="$(curl -sS -D "${RAW_DIR}/markdown_large.hdr" -o "${RAW_DIR}/markdown_large.body" \
  -H 'Accept: text/markdown' --max-time 20 "http://127.0.0.1:${PORT}/full/large.html" -w '%{http_code}')"

[[ "${pt_code}" == "200" ]] || { echo "Expected passthrough 200, got ${pt_code}" >&2; exit 1; }
[[ "${md_code}" == "200" ]] || { echo "Expected markdown 200, got ${md_code}" >&2; exit 1; }

pt_bytes="$(wc -c < "${RAW_DIR}/passthrough_large.body" | tr -d ' ')"
md_bytes="$(wc -c < "${RAW_DIR}/markdown_large.body" | tr -d ' ')"
[[ "${pt_bytes}" -gt 65536 ]] || { echo "Passthrough body too small (${pt_bytes})" >&2; exit 1; }
[[ "${md_bytes}" -gt 0 ]] || { echo "Markdown body is empty" >&2; exit 1; }

grep -qi '^Content-Type: text/markdown; charset=utf-8' "${RAW_DIR}/markdown_large.hdr" || {
  echo "Missing markdown Content-Type on large response" >&2
  exit 1
}

echo "==> Running small-load ApacheBench regression (large markdown response)"
ab -H 'Accept: text/markdown' -n 30 -c 3 \
  "http://127.0.0.1:${PORT}/full/large.html" > "${RAW_DIR}/ab_markdown_large.txt" 2>&1

failed="$(awk -F': *' '/Failed requests/ {print $2}' "${RAW_DIR}/ab_markdown_large.txt" | tr -d ' ' )"
non2xx="$(awk -F': *' '/Non-2xx responses/ {print $2}' "${RAW_DIR}/ab_markdown_large.txt" | tr -d ' ' )"
rps="$(awk -F': *' '/Requests per second/ {print $2}' "${RAW_DIR}/ab_markdown_large.txt" | awk '{print $1}')"
mean_ms="$(awk '/^Time per request:/ {print $4; exit}' "${RAW_DIR}/ab_markdown_large.txt")"

[[ -n "${failed}" ]] || failed="0"
[[ -n "${non2xx}" ]] || non2xx="0"
[[ "${failed}" == "0" ]] || { echo "ApacheBench failed requests: ${failed}" >&2; exit 1; }
[[ "${non2xx}" == "0" ]] || { echo "ApacheBench non-2xx responses: ${non2xx}" >&2; exit 1; }

echo "Regression summary:"
echo "  nginx_version=${NGINX_VERSION}"
echo "  arch=$(uname -m)"
echo "  passthrough_large_http=${pt_code} bytes=${pt_bytes}"
echo "  markdown_large_http=${md_code} bytes=${md_bytes}"
echo "  markdown_large_ab_rps=${rps:-unknown}"
echo "  markdown_large_ab_mean_ms=${mean_ms:-unknown}"
echo "  artifacts=${BUILDROOT}"
