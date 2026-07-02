#!/usr/bin/env bash
# Profile system E2E smoke (spec 50).
#
# Validates that profile-specific diagnostics locations load in a real NGINX
# instance and expose the expected effective configuration. When NGINX_BIN is
# not set, exits 2 so release gates can explicitly treat the native dependency
# as deferred instead of silently passing.
set -euo pipefail

PORT="${PORT:-18098}"
KEEP_ARTIFACTS=0
NGINX_BIN="${NGINX_BIN:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"

BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
PASS_COUNT=0
FAIL_COUNT=0

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/e2e_common.sh"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-bin PATH] [--port PORT]

Profile E2E smoke. Requires a module-enabled NGINX binary.

Options:
  --keep-artifacts  Keep temporary runtime files
  --nginx-bin PATH  Path to module-enabled nginx binary
  --port PORT       NGINX listen port (default: ${PORT})
  -h, --help        Show this help
EOF
    return 0
}

safe_remove_buildroot() {
    if [[ -z "${BUILDROOT}" || ! -d "${BUILDROOT}" ]]; then
        return 0
    fi

    case "${BUILDROOT}" in
        /tmp/nginx-profile-smoke-e2e.*)
            rm -rf "${BUILDROOT}"
            ;;
        *)
            echo "Refusing to remove unexpected path: ${BUILDROOT}" >&2
            ;;
    esac
    return 0
}

cleanup() {
    if [[ -n "${NGINX_EXECUTABLE}" && -n "${RUNTIME}" ]]; then
        "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf \
            -s stop >/dev/null 2>&1 || true
    fi
    if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
        safe_remove_buildroot
    fi
    return 0
}
trap cleanup EXIT

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS] %s\n' "$1" >&2
    return 0
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[FAIL] %s\n' "$1" >&2
    return 0
}

validate_numeric() {
    local name="$1" value="$2"
    if ! printf '%s' "${value}" | grep -qE '^[0-9]+$'; then
        echo "ERROR: ${name} must be a positive integer, got: ${value}" >&2
        usage
        exit 1
    fi
    return 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-artifacts)
            KEEP_ARTIFACTS=1
            shift
            ;;
        --nginx-bin)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --nginx-bin requires an argument" >&2
                exit 1
            fi
            NGINX_BIN="$2"
            shift 2
            ;;
        --port)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --port requires an argument" >&2
                exit 1
            fi
            validate_numeric "--port" "$2"
            PORT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ -z "${NGINX_BIN}" ]]; then
    echo "SKIP: NGINX_BIN not set - profile E2E smoke deferred" >&2
    echo "Set NGINX_BIN=/path/to/nginx-with-module to run this smoke." >&2
    exit 2
fi

BUILDROOT="$(mktemp -d /tmp/nginx-profile-smoke-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs"

LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
NGINX_EXECUTABLE="${NGINX_BIN}"

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log info;
pid logs/nginx.pid;

events { worker_connections 128; }

http {
    include       mime.types;
    default_type  application/octet-stream;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location = /strict/diagnostics {
            markdown_profile strict_cache;
            markdown_diagnostics on;
        }

        location = /balanced/diagnostics {
            markdown_profile balanced;
            markdown_diagnostics on;
        }

        location = /streaming/diagnostics {
            markdown_profile streaming_first;
            markdown_diagnostics on;
        }
    }
}
EOF

cat > "${RUNTIME}/conf/invalid-strict-streaming.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/invalid-error.log info;
pid logs/invalid-nginx.pid;

events { worker_connections 128; }

http {
    include       mime.types;
    default_type  application/octet-stream;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location = /invalid {
            markdown_profile strict_cache;
            markdown_streaming force;
            markdown_diagnostics on;
        }
    }
}
EOF

extract_diag_field() {
    local path="$1" expression="$2"
    local url
    url="$(e2e_base_url)${path}"

    e2e_curl_get "${url}" --max-time 5 | python3 -c "
import json
import sys

data = json.load(sys.stdin)
value = data
for part in '${expression}'.split('.'):
    value = value[part]
print(value)
"
    return $?
}

assert_diag_field() {
    local path="$1" expression="$2" expected="$3" actual
    actual="$(extract_diag_field "${path}" "${expression}")" || {
        fail "${path} ${expression}: diagnostics parse failed"
        return 1
    }

    if [[ "${actual}" == "${expected}" ]]; then
        pass "${path} ${expression}=${expected}"
        return 0
    fi

    fail "${path} ${expression}: expected ${expected}, got ${actual}"
    return 1
}

echo "=== Profile E2E Smoke (spec 50) ===" >&2
echo "NGINX_BIN=${NGINX_BIN}" >&2
echo "PORT=${PORT}" >&2

"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -t
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
if [[ ! -s "${RUNTIME}/logs/nginx.pid" ]]; then
    echo "ERROR: nginx did not create pid file after startup" >&2
    sed -n '1,80p' "${RUNTIME}/logs/error.log" >&2 || true
    exit 1
fi
nginx_pid="$(cat "${RUNTIME}/logs/nginx.pid")"
if ! kill -0 "${nginx_pid}" >/dev/null 2>&1; then
    echo "ERROR: nginx process ${nginx_pid} is not running after startup" >&2
    sed -n '1,80p' "${RUNTIME}/logs/error.log" >&2 || true
    exit 1
fi
markdown_wait_for_http "$(e2e_base_url)/balanced/diagnostics" \
    "profile diagnostics on ${PORT}" || exit 1

assert_diag_field /strict/diagnostics profile strict_cache
assert_diag_field /strict/diagnostics effective_config.streaming off
assert_diag_field /strict/diagnostics effective_config.cache_validation full

assert_diag_field /balanced/diagnostics profile balanced
assert_diag_field /balanced/diagnostics effective_config.streaming auto
assert_diag_field /balanced/diagnostics effective_config.cache_validation ims_only

assert_diag_field /streaming/diagnostics profile streaming_first
assert_diag_field /streaming/diagnostics effective_config.streaming force
assert_diag_field /streaming/diagnostics effective_config.cache_validation off

if "${NGINX_EXECUTABLE}" -p "${RUNTIME}" \
    -c conf/invalid-strict-streaming.conf -t >/dev/null 2>&1; then
    fail "strict_cache rejects explicit streaming=force"
else
    pass "strict_cache rejects explicit streaming=force"
fi

echo "Profile E2E smoke: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
if [[ "${FAIL_COUNT}" -ne 0 ]]; then
    exit 1
fi
exit 0
