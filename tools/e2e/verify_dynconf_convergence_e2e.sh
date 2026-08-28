#!/usr/bin/env bash
# Verify dynamic configuration convergence across NGINX workers.
#
# The test uses one watched JSON file and four workers. It verifies that an
# atomic replace with an unchanged mtime is observed, that an invalid
# candidate preserves the last-known-good snapshot, and that a request keeps
# the snapshot bound at header-filter entry while a reload is in progress.
set -euo pipefail

PORT="${PORT:-18103}"
UPSTREAM_PORT="${UPSTREAM_PORT:-19103}"
NGINX_BIN="${NGINX_BIN:-}"
KEEP_ARTIFACTS=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"

BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""
NGINX_PID=""
UPSTREAM_PID=""
DYNCONF_FILE=""
DYNCONF_CANDIDATE=""
OBSERVATIONS=""
OLD_WORKERS=""
PASS_COUNT=0
FAIL_COUNT=0

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/e2e_common.sh"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-bin PATH]
       [--port PORT] [--upstream-port PORT]

Dynamic-configuration multi-worker convergence E2E. Requires a module-enabled
NGINX binary.

Options:
  --keep-artifacts       Keep temporary runtime files
  --nginx-bin PATH       Path to module-enabled nginx binary
  --port PORT            NGINX listen port (default: ${PORT})
  --upstream-port PORT   Upstream listen port (default: ${UPSTREAM_PORT})
  -h, --help             Show this help
EOF
    return 0
}

validate_port() {
    local name="$1"
    local value="$2"
    if ! [[ "${value}" =~ ^[0-9]+$ ]] || (( value < 1 || value > 65535 )); then
        echo "ERROR: ${name} must be a TCP port, got: ${value}" >&2
        return 1
    fi
    return 0
}

safe_remove_buildroot() {
    if [[ -z "${BUILDROOT}" || ! -d "${BUILDROOT}" ]]; then
        return 0
    fi

    case "${BUILDROOT}" in
        /tmp/nginx-dynconf-convergence-e2e.*)
            rm -rf "${BUILDROOT}"
            ;;
        *)
            echo "Refusing to remove unexpected path: ${BUILDROOT}" >&2
            return 1
            ;;
    esac
    return 0
}

cleanup() {
    if [[ -n "${NGINX_EXECUTABLE}" && -n "${RUNTIME}" && \
          -s "${RUNTIME}/logs/nginx.pid" ]]; then
        "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf \
            -s stop >/dev/null 2>&1 || true
    fi
    if [[ -n "${UPSTREAM_PID}" ]]; then
        kill "${UPSTREAM_PID}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${DYNCONF_CANDIDATE}" && -e "${DYNCONF_CANDIDATE}" ]]; then
        rm -f "${DYNCONF_CANDIDATE}"
    fi
    if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
        safe_remove_buildroot
    else
        echo "Artifacts kept at: ${BUILDROOT}" >&2
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

record_diagnostics() {
    local label="$1"
    if e2e_curl_get "$(e2e_base_url)/nginx-markdown/diagnostics" \
        --max-time 2 | python3 -c '
import json
import sys

label = sys.argv[1]
data = json.load(sys.stdin)
dynconf = data["configuration"]["dynconf"]
effective = data["configuration"]["effective"]
generation = dynconf.get("generation")
if generation is None:
    generation = "null"
last_error = "yes" if dynconf.get("last_error") else "no"
print("\t".join((
    label,
    str(data["worker"]["pid"]),
    str(dynconf["state"]),
    str(effective["filter"]),
    str(generation),
    last_error,
)))
' "${label}" >> "${OBSERVATIONS}"; then
        return 0
    fi
    return 1
}

count_matching_workers() {
    local label="$1"
    local state="$2"
    local filter_state="$3"
    local minimum_generation="$4"
    local error_mode="$5"
    local excluded_file="$6"

    awk -F '\t' \
        -v label="${label}" \
        -v state="${state}" \
        -v filter_state="${filter_state}" \
        -v minimum_generation="${minimum_generation}" \
        -v error_mode="${error_mode}" \
        -v excluded_file="${excluded_file}" '
BEGIN {
    if (excluded_file != "") {
        while ((getline excluded_pid < excluded_file) > 0) {
            excluded[excluded_pid] = 1
        }
        close(excluded_file)
    }
}
$1 == label && $3 == state && $4 == filter_state && $5 != "null" \
    && ($5 + 0) >= (minimum_generation + 0) && !($2 in excluded) {
    matches[$2] = 1
    if (error_mode == "any" || $6 == error_mode) {
        errors[$2] = 1
    }
}
END {
    count = 0
    for (pid in matches) {
        if (error_mode == "any" || (pid in errors)) {
            count++
        }
    }
    print count + 0
}
' "${OBSERVATIONS}"
    return 0
}

wait_for_state() {
    local label="$1"
    local state="$2"
    local filter_state="$3"
    local minimum_generation="$4"
    local error_mode="$5"
    local excluded_file="$6"
    local matching_workers=""
    local attempt
    local request

    for ((attempt = 1; attempt <= 120; attempt++)); do
        for ((request = 1; request <= 12; request++)); do
            record_diagnostics "${label}" || true
        done
        matching_workers="$(count_matching_workers "${label}" \
            "${state}" "${filter_state}" "${minimum_generation}" \
            "${error_mode}" "${excluded_file}")"
        if [[ "${matching_workers}" -ge 4 ]]; then
            pass "${label}: four workers converged to ${state}/${filter_state}"
            return 0
        fi
        sleep 0.25
    done

    fail "${label}: fewer than four workers converged to ${state}/${filter_state}"
    return 1
}

max_generation() {
    local label="$1"
    awk -F '\t' -v label="${label}" '
$1 == label && $5 != "null" && ($5 + 0) > maximum { maximum = $5 + 0 }
END { print maximum + 0 }
' "${OBSERVATIONS}"
    return 0
}

assert_exact_generation() {
    local label="$1"
    local expected="$2"
    local matched=0
    local mismatch=0
    if awk -F '\t' -v label="${label}" -v expected="${expected}" '
$1 == label {
    matched = 1
    if ($5 != expected) {
        mismatch = 1
    }
}
END { exit (!matched || mismatch) }
' "${OBSERVATIONS}"; then
        pass "${label}: generation remained ${expected}"
        return 0
    fi
    fail "${label}: generation changed while preserving the last-known-good snapshot"
    return 1
}

assert_same_mtime_replace() {
    local content="$1"
    local before_mtime=""
    local after_mtime=""

    before_mtime="$(python3 - "${DYNCONF_FILE}" <<'PY'
import os
import sys

print(os.stat(sys.argv[1]).st_mtime_ns)
PY
)"
    printf '%s\n' "${content}" > "${DYNCONF_CANDIDATE}"
    python3 - "${DYNCONF_CANDIDATE}" "${DYNCONF_FILE}" <<'PY'
import os
import stat
import sys

candidate, target = sys.argv[1:]
target_stat = os.stat(target)
os.chmod(candidate, stat.S_IMODE(target_stat.st_mode))
os.utime(candidate, ns=(target_stat.st_atime_ns, target_stat.st_mtime_ns))
os.replace(candidate, target)
PY
    after_mtime="$(python3 - "${DYNCONF_FILE}" <<'PY'
import os
import sys

print(os.stat(sys.argv[1]).st_mtime_ns)
PY
)"
    if [[ "${before_mtime}" == "${after_mtime}" ]]; then
        pass "atomic dynconf replacement preserved mtime ${before_mtime}"
        return 0
    fi
    fail "atomic dynconf replacement changed mtime unexpectedly"
    return 1
}

capture_worker_pids() {
    local label="$1"
    local output_file="$2"
    awk -F '\t' -v label="${label}" '
$1 == label { workers[$2] = 1 }
END { for (pid in workers) print pid }
' "${OBSERVATIONS}" | sort -n > "${output_file}"
    return 0
}

assert_worker_count() {
    local master_pid="$1"
    local child_count=""
    if child_count="$(ps -axo pid=,ppid= | awk \
        -v master_pid="${master_pid}" '$2 == master_pid { count++ }
END { print count + 0 }')"; then
        :
    else
        fail "unable to inspect NGINX worker process count"
        return 1
    fi
    if [[ "${child_count}" -ge 4 ]]; then
        pass "NGINX has ${child_count} worker processes"
        return 0
    fi
    fail "NGINX has ${child_count} worker processes, expected at least four"
    return 1
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
                exit 2
            fi
            NGINX_BIN="$2"
            shift 2
            ;;
        --port)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --port requires an argument" >&2
                exit 2
            fi
            validate_port "--port" "$2"
            PORT="$2"
            shift 2
            ;;
        --upstream-port)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --upstream-port requires an argument" >&2
                exit 2
            fi
            validate_port "--upstream-port" "$2"
            UPSTREAM_PORT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage
            exit 2
            ;;
    esac
done

if [[ -z "${NGINX_BIN}" ]]; then
    echo "SKIP: NGINX_BIN not set - dynamic-config convergence E2E deferred" >&2
    exit 2
fi
if [[ ! -x "${NGINX_BIN}" ]]; then
    echo "ERROR: NGINX_BIN is not executable: ${NGINX_BIN}" >&2
    exit 2
fi

BUILDROOT="$(mktemp -d /tmp/nginx-dynconf-convergence-e2e.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/logs"
OBSERVATIONS="${BUILDROOT}/diagnostics.tsv"
OLD_WORKERS="${BUILDROOT}/old-workers.txt"
DYNCONF_FILE="${BUILDROOT}/markdown-dynamic.json"
DYNCONF_CANDIDATE="${DYNCONF_FILE}.candidate"
: > "${OBSERVATIONS}"

printf '%s\n' '{"schema_version":1,"filter":"on","prune_noise":"off","log_verbosity":"info","error_policy":"pass"}' \
    > "${DYNCONF_FILE}"

cat > "${BUILDROOT}/upstream.py" <<'PY'
import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return

        body = (
            b"<html><body><h1>Convergence request</h1>"
            b"<p>The request snapshot must remain stable.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.flush()
        time.sleep(1.5)
        midpoint = len(body) // 2
        self.wfile.write(body[:midpoint])
        self.wfile.flush()
        time.sleep(0.5)
        self.wfile.write(body[midpoint:])
        self.wfile.flush()

    def log_message(self, format_string, *args):
        return


parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
server = ThreadingHTTPServer((args.host, args.port), Handler)
server.serve_forever()
PY

echo "=== Dynamic Configuration Multi-Worker Convergence E2E ===" >&2
echo "NGINX_BIN=${NGINX_BIN}" >&2
echo "PORT=${PORT}" >&2
echo "UPSTREAM_PORT=${UPSTREAM_PORT}" >&2

python3 -u "${BUILDROOT}/upstream.py" --port "${UPSTREAM_PORT}" \
    > "${RUNTIME}/logs/upstream.log" 2>&1 &
UPSTREAM_PID=$!
for ((attempt = 1; attempt <= 50; attempt++)); do
    if curl -sS --max-time 1 \
        "http://127.0.0.1:${UPSTREAM_PORT}/healthz" >/dev/null; then
        break
    fi
    sleep 0.1
done
if ! curl -sS --max-time 2 \
    "http://127.0.0.1:${UPSTREAM_PORT}/healthz" >/dev/null; then
    echo "ERROR: convergence upstream did not become ready" >&2
    sed -n '1,80p' "${RUNTIME}/logs/upstream.log" >&2 || true
    exit 1
fi

LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
NGINX_EXECUTABLE="${NGINX_BIN}"

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 4;
error_log logs/error.log info;
pid logs/nginx.pid;

events { worker_connections 256; }

http {
    include mime.types;
    default_type application/octet-stream;

    markdown_dynamic_config on;
    markdown_dynamic_config_path ${DYNCONF_FILE};

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location = /nginx-markdown/diagnostics {
            markdown_diagnostics on;
            markdown_cache_validation off;
            markdown_streaming off;
        }

        location = /live.html {
            markdown_cache_validation off;
            markdown_streaming off;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_read_timeout 20s;
            proxy_pass http://127.0.0.1:${UPSTREAM_PORT};
        }
    }
}
EOF

"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -t
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf
for ((attempt = 1; attempt <= 50; attempt++)); do
    if [[ -s "${RUNTIME}/logs/nginx.pid" ]]; then
        break
    fi
    sleep 0.1
done
if [[ ! -s "${RUNTIME}/logs/nginx.pid" ]]; then
    echo "ERROR: NGINX did not create a pid file" >&2
    sed -n '1,120p' "${RUNTIME}/logs/error.log" >&2 || true
    exit 1
fi
NGINX_PID="$(sed -n '1p' "${RUNTIME}/logs/nginx.pid")"
if [[ -z "${NGINX_PID}" ]] || ! kill -0 "${NGINX_PID}" >/dev/null 2>&1; then
    echo "ERROR: NGINX master is not running after startup" >&2
    sed -n '1,120p' "${RUNTIME}/logs/error.log" >&2 || true
    exit 1
fi
markdown_wait_for_http "$(e2e_base_url)/nginx-markdown/diagnostics" \
    "dynamic configuration diagnostics" || exit 1
assert_worker_count "${NGINX_PID}" || true

wait_for_state initial active on 1 no "" || true
INITIAL_GENERATION="$(max_generation initial)"
if [[ "${INITIAL_GENERATION}" -ge 1 ]]; then
    pass "initial dynconf generation is ${INITIAL_GENERATION}"
else
    fail "initial dynconf generation is not positive"
fi

assert_same_mtime_replace \
    '{"schema_version":1,"filter":}' || true
wait_for_state malformed lkg_preserved on "${INITIAL_GENERATION}" yes "" || true
assert_exact_generation malformed "${INITIAL_GENERATION}" || true

assert_same_mtime_replace \
    '{"schema_version":1,"filter":"off","prune_noise":"off","log_verbosity":"info","error_policy":"pass"}' || true
wait_for_state restored active off "${INITIAL_GENERATION}" no "" || true
RESTORED_GENERATION="$(max_generation restored)"
if (( RESTORED_GENERATION > INITIAL_GENERATION )); then
    pass "valid restore advanced generation to ${RESTORED_GENERATION}"
else
    fail "valid restore did not advance generation"
fi

assert_same_mtime_replace \
    '{"schema_version":1,"filter":"on","prune_noise":"off","log_verbosity":"info","error_policy":"pass"}' || true
wait_for_state prepared_on active on "${RESTORED_GENERATION}" no "" || true
capture_worker_pids prepared_on "${OLD_WORKERS}" || true

RESPONSE_HEADERS="${BUILDROOT}/response.headers"
RESPONSE_BODY="${BUILDROOT}/response.body"
RESPONSE_CODE="${BUILDROOT}/response.code"
(
    curl -sS --max-time 20 -D "${RESPONSE_HEADERS}" -o "${RESPONSE_BODY}" \
        -H 'Accept: text/markdown' -w '%{http_code}' \
        "$(e2e_base_url)/live.html" > "${RESPONSE_CODE}"
) &
REQUEST_PID=$!
for ((attempt = 1; attempt <= 40; attempt++)); do
    if [[ -s "${RESPONSE_HEADERS}" ]]; then
        break
    fi
    sleep 0.1
done
if [[ ! -s "${RESPONSE_HEADERS}" ]]; then
    fail "in-flight request did not receive headers before dynconf rewrite"
else
    pass "in-flight request received headers before dynconf rewrite"
fi

assert_same_mtime_replace \
    '{"schema_version":1,"filter":"off","prune_noise":"off","log_verbosity":"info","error_policy":"pass"}' || true
if "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -s reload; then
    pass "NGINX reloaded while the request was in flight"
else
    fail "NGINX reload failed while the request was in flight"
fi

if wait "${REQUEST_PID}"; then
    REQUEST_STATUS=0
else
    REQUEST_STATUS=$?
fi
if [[ "${REQUEST_STATUS}" -eq 0 ]]; then
    pass "in-flight request completed successfully"
else
    fail "in-flight request failed with curl status ${REQUEST_STATUS}"
fi

RESPONSE_STATUS="$(sed -n '1p' "${RESPONSE_CODE}" 2>/dev/null || true)"
if [[ "${RESPONSE_STATUS}" == "200" ]]; then
    pass "in-flight response returned HTTP 200"
else
    fail "in-flight response returned HTTP ${RESPONSE_STATUS:-<missing>}"
fi
if grep -Eiq '^Content-Type:[[:space:]]*text/markdown' "${RESPONSE_HEADERS}"; then
    pass "in-flight response retained the filter snapshot and returned Markdown"
else
    fail "in-flight response did not return Content-Type: text/markdown"
fi
if grep -Fq '# Convergence request' "${RESPONSE_BODY}"; then
    pass "in-flight response contains converted Markdown"
else
    fail "in-flight response body is missing the converted heading"
fi

wait_for_state post_reload active off 1 no "${OLD_WORKERS}" || true
assert_worker_count "${NGINX_PID}" || true

if [[ "${FAIL_COUNT}" -eq 0 ]]; then
    echo "Dynamic configuration convergence E2E: PASSED (${PASS_COUNT} checks)" >&2
    exit 0
fi
echo "Dynamic configuration convergence E2E: FAILED (${FAIL_COUNT} failures)" >&2
sed -n '1,160p' "${RUNTIME}/logs/error.log" >&2 || true
exit 1
