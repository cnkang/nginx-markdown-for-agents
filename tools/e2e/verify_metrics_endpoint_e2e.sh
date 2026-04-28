#!/usr/bin/env bash
set -euo pipefail

# E2E validation for the markdown metrics endpoint.
#
# Validates critical metrics-endpoint paths:
#  1) JSON format via Accept: application/json
#  2) Plain-text format (default, no Accept override)
#  3) Prometheus format via Accept: text/plain (Prometheus convention)
#  4) Metrics endpoint returns 200 with non-empty body
#  5) JSON metrics contain required top-level keys
#  6) Prometheus metrics contain expected metric family prefixes
#  7) After a conversion, request counters are non-zero
#  8) Invalid Accept header returns plain-text fallback

NGINX_VERSION="${NGINX_VERSION:-1.28.2}"
PORT="${PORT:-18097}"
METRICS_PORT="${METRICS_PORT:-18098}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
BUILDROOT=""
RUNTIME=""
RUST_TARGET=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
ORIG_ARGS=("$@")

readonly ACCEPT_MARKDOWN='Accept: text/markdown'
readonly ACCEPT_JSON='Accept: application/json'
readonly ACCEPT_PROMETHEUS='Accept: text/plain'
readonly PATTERN_HTTP_200='HTTP/1.1 200'
readonly PATTERN_CT_JSON='Content-Type: application/json'
readonly PATTERN_CT_TEXT='Content-Type: text/plain'
readonly PATTERN_CT_PROMETHEUS='Content-Type: text/plain.*version=0\.0\.4'

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

#
# Print command-line usage for this E2E script.
#
usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--metrics-port PORT]

Build local NGINX with the markdown module and run metrics-endpoint E2E checks.

Checks:
  1) JSON format via Accept: application/json
  2) Plain-text format (default)
  3) Prometheus exposition format
  4) Non-empty metrics body
  5) JSON metrics contain required keys
  6) Prometheus metrics contain expected prefixes
  7) Counters non-zero after conversion
  8) Invalid Accept falls back to plain-text

Set NGINX_BIN to reuse an existing module-enabled nginx binary and skip rebuilding.
EOF
  return 0
}

#
# Trap handler: stop NGINX, then optionally remove build artifacts.
#
cleanup() {
  local rc=$?

  if [[ -n "${RUNTIME}" && -n "${NGINX_EXECUTABLE}" && -x "${NGINX_EXECUTABLE}" ]]; then
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s stop >/dev/null 2>&1 || true
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}" || true
  fi

  if [[ $rc -ne 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
    echo "Metrics-endpoint E2E failed. Artifacts kept at: ${BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
    echo "Metrics-endpoint E2E succeeded. Artifacts kept at: ${BUILDROOT}"
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
      markdown_require_flag_value "$1" "${2:-}"
      NGINX_VERSION="$2"
      shift 2
      ;;
    --port)
      markdown_require_flag_value "$1" "${2:-}"
      PORT="$2"
      shift 2
      ;;
    --metrics-port)
      markdown_require_flag_value "$1" "${2:-}"
      METRICS_PORT="$2"
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

for cmd in curl python3 grep; do
  markdown_need_cmd "$cmd"
done
if [[ -z "${NGINX_BIN}" ]]; then
  for cmd in tar make cargo; do
    markdown_need_cmd "$cmd"
  done
fi

RUST_TARGET="$(markdown_detect_rust_target)"
BUILDROOT="$(mktemp -d /tmp/nginx-metrics-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
RAW_DIR="${BUILDROOT}/raw"
mkdir -p "${RAW_DIR}"

# --- Build or reuse NGINX ---
echo "==> Host architecture: $(uname -m)"
if [[ -n "${NGINX_BIN}" ]]; then
  echo "==> Reusing existing NGINX binary (${NGINX_BIN})"
  LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
  NGINX_EXECUTABLE="${NGINX_BIN}"
else
  echo "==> Building Rust converter (${RUST_TARGET})"
  markdown_prepare_rust_converter_release \
    "${WORKSPACE_ROOT}" "${RUST_TARGET}" --features streaming >/dev/null

  echo "==> Downloading/building NGINX ${NGINX_VERSION}"
  curl --proto '=https' --tlsv1.2 -fsSL "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" -o "${BUILDROOT}/nginx.tar.gz"
  mkdir -p "${BUILDROOT}/src"
  tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}/src" --strip-components=1
  (
    cd "${BUILDROOT}/src"
    ./configure \
      --without-http_rewrite_module \
      --with-cc-opt="-DMARKDOWN_STREAMING_ENABLED" \
      --prefix="${RUNTIME}" \
      --add-module="${WORKSPACE_ROOT}/components/nginx-module" >/dev/null
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
    make install >/dev/null
  )
  NGINX_EXECUTABLE="${RUNTIME}/sbin/nginx"
fi

mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs" "${RUNTIME}/html"

# --- Create a simple HTML page for conversion ---
cat > "${RUNTIME}/html/test.html" <<'HTMLEOF'
<!doctype html>
<html><head><meta charset="UTF-8"><title>Metrics Test</title></head>
<body><h1>Metrics Test Page</h1><p>Content for metrics counting.</p></body></html>
HTMLEOF

# --- NGINX config with metrics endpoint ---
cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log info;
pid logs/nginx.pid;

events { worker_connections 512; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 5;

    markdown_metrics on;
    markdown_metrics_format text;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location /md/ {
            markdown_filter on;
            markdown_max_size 10m;
            markdown_on_error pass;
            markdown_timeout 120000;
            alias html/;
        }

        location /nomd/ {
            alias html/;
        }
    }

    server {
        listen 127.0.0.1:${METRICS_PORT};
        server_name localhost;

        location /metrics {
            markdown_metrics on;
        }
    }
}
EOF

echo "==> Starting NGINX on 127.0.0.1:${PORT} (app) and 127.0.0.1:${METRICS_PORT} (metrics)"
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
markdown_wait_for_http "http://127.0.0.1:${PORT}/nomd/test.html" "NGINX app" || exit 1
markdown_wait_for_http "http://127.0.0.1:${METRICS_PORT}/metrics" "NGINX metrics" || exit 1

# --- Case 1: JSON format via Accept: application/json ---
echo "==> Case 1: JSON format via Accept: application/json"
curl -sS -D "${RAW_DIR}/case1.hdr" -o "${RAW_DIR}/case1.body" \
  -H "${ACCEPT_JSON}" --max-time 30 \
  "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null
markdown_expect_status "Case 1" "${RAW_DIR}/case1.hdr" "${PATTERN_HTTP_200}"
markdown_expect_header "Case 1" "${RAW_DIR}/case1.hdr" "${PATTERN_CT_JSON}"
METRICS_BODY="${RAW_DIR}/case1.body" python3 -c '
import json, os, sys
json.load(open(os.environ["METRICS_BODY"]))
' || {
  echo "FAIL: Case 1 - metrics body is not valid JSON" >&2
  exit 1
}
echo "  PASS: JSON format returned with correct Content-Type"

# --- Case 2: Plain-text format (default, no Accept override) ---
echo "==> Case 2: Plain-text format (default)"
curl -sS -D "${RAW_DIR}/case2.hdr" -o "${RAW_DIR}/case2.body" \
  --max-time 30 \
  "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null
markdown_expect_status "Case 2" "${RAW_DIR}/case2.hdr" "${PATTERN_HTTP_200}"
markdown_expect_header "Case 2" "${RAW_DIR}/case2.hdr" "${PATTERN_CT_TEXT}"
[[ -s "${RAW_DIR}/case2.body" ]] || {
  echo "FAIL: Case 2 - plain-text metrics body is empty" >&2
  exit 1
}
echo "  PASS: Plain-text format returned with non-empty body"

# --- Case 3: Prometheus exposition format ---
echo "==> Case 3: Prometheus exposition format"
curl -sS -D "${RAW_DIR}/case3.hdr" -o "${RAW_DIR}/case3.body" \
  -H "${ACCEPT_PROMETHEUS}" --max-time 30 \
  "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null
markdown_expect_status "Case 3" "${RAW_DIR}/case3.hdr" "${PATTERN_HTTP_200}"
grep -qi "${PATTERN_CT_PROMETHEUS}" "${RAW_DIR}/case3.hdr" || {
  echo "INFO: Case 3 - Prometheus Content-Type variant not matched; checking body format as fallback" >&2
  grep -qi "^# HELP" "${RAW_DIR}/case3.body" || {
    echo "FAIL: Case 3 - response body does not look like Prometheus format" >&2
    exit 1
  }
}
echo "  PASS: Prometheus format returned"

# --- Case 4: Non-empty metrics body ---
echo "==> Case 4: Metrics endpoint returns non-empty body for all formats"
[[ -s "${RAW_DIR}/case1.body" ]] || {
  echo "FAIL: Case 4 - JSON metrics body is empty" >&2
  exit 1
}
[[ -s "${RAW_DIR}/case2.body" ]] || {
  echo "FAIL: Case 4 - plain-text metrics body is empty" >&2
  exit 1
}
[[ -s "${RAW_DIR}/case3.body" ]] || {
  echo "FAIL: Case 4 - Prometheus metrics body is empty" >&2
  exit 1
}
echo "  PASS: All format bodies are non-empty"

# --- Case 5: JSON metrics contain required top-level keys ---
echo "==> Case 5: JSON metrics contain required top-level keys"
METRICS_BODY="${RAW_DIR}/case1.body" python3 -c '
import json, os, sys
data = json.load(open(os.environ["METRICS_BODY"]))
required = ["total_requests", "converted_total", "skipped_total"]
missing = [k for k in required if k not in data]
if missing:
    print(f"FAIL: missing keys: {missing}", file=sys.stderr)
    sys.exit(1)
print("  PASS: JSON contains required top-level keys")
' || exit 1

# --- Case 6: Prometheus metrics contain expected metric family prefixes ---
echo "==> Case 6: Prometheus metrics contain expected metric family prefixes"
grep -q "^# HELP nginx_markdown" "${RAW_DIR}/case3.body" || {
  echo "FAIL: Case 6 - no HELP lines with nginx_markdown prefix in Prometheus output" >&2
  exit 1
}
grep -q "^# TYPE nginx_markdown" "${RAW_DIR}/case3.body" || {
  echo "FAIL: Case 6 - no TYPE lines with nginx_markdown prefix in Prometheus output" >&2
  exit 1
}
echo "  PASS: Prometheus output contains HELP and TYPE annotations"

# --- Case 7: After a conversion, request counters are non-zero ---
echo "==> Case 7: Counters non-zero after conversion"
# Trigger a conversion request
curl -sS -o /dev/null -H "${ACCEPT_MARKDOWN}" --max-time 30 \
  "http://127.0.0.1:${PORT}/md/test.html" || {
  echo "FAIL: Case 7 - conversion request failed" >&2
  exit 1
}
# Re-fetch JSON metrics
curl -sS -o "${RAW_DIR}/case7.body" \
  -H "${ACCEPT_JSON}" --max-time 30 \
  "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null
METRICS_BODY="${RAW_DIR}/case7.body" python3 -c '
import json, os, sys
data = json.load(open(os.environ["METRICS_BODY"]))
total = data.get("total_requests", 0)
converted = data.get("converted_total", 0)
if total < 1:
    print(f"FAIL: total_requests is {total}, expected >= 1", file=sys.stderr)
    sys.exit(1)
if converted < 1:
    print(f"FAIL: converted_total is {converted}, expected >= 1", file=sys.stderr)
    sys.exit(1)
print(f"  PASS: total_requests={total}, converted_total={converted} (non-zero)")
' || exit 1

# --- Case 8: Invalid Accept header returns plain-text fallback ---
echo "==> Case 8: Invalid Accept header falls back to plain-text"
curl -sS -D "${RAW_DIR}/case8.hdr" -o "${RAW_DIR}/case8.body" \
  -H 'Accept: application/xml' --max-time 30 \
  "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null
markdown_expect_status "Case 8" "${RAW_DIR}/case8.hdr" "${PATTERN_HTTP_200}"
markdown_expect_header "Case 8" "${RAW_DIR}/case8.hdr" "${PATTERN_CT_TEXT}"
echo "  PASS: Unknown Accept returns plain-text fallback"

echo ""
echo "========================================"
echo "All metrics-endpoint E2E tests passed!"
echo "========================================"
