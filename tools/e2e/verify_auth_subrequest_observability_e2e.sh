#!/usr/bin/env bash
# verify_auth_subrequest_observability_e2e.sh — An auth subrequest must not
# double-count the client request in the module's metrics.
#
# Purpose:
#   `auth_request` issues an internal subrequest for every client request.  This
#   check drives conversions through an auth-protected location and requires the
#   module's terminal-outcome counters to account for exactly one outcome per
#   client request, so the subrequest cannot be counted as a second conversion.
#   It also requires a rejected request to produce no conversion at all.
#
# Usage:
#   verify_auth_subrequest_observability_e2e.sh [--image TAG] [--requests N]
#
# Environment:
#   MODULE_SO   module to load (default: build/ngx_http_markdown_filter_module.so)
#   IMAGE       container image (default: nginx:1.30.4-alpine)
#   REQUESTS    authorized requests to send (default: 4)
#
# Exit codes:
#   0  the counters match the client requests exactly and the denial converted nothing
#   1  a counter mismatch, a missing terminal outcome, or a converted denial
#   77  docker or curl is unavailable
#
# This script is FAIL-CLOSED: every unexpected outcome is a failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
IMAGE="${IMAGE:-nginx:1.30.4-alpine}"
REQUESTS="${REQUESTS:-4}"
CONTAINER="markdown-auth-e2e"
BASE="http://127.0.0.1:18091"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image) IMAGE="$2"; shift 2 ;;
        --requests) REQUESTS="$2"; shift 2 ;;
        -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

for tool in docker curl; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "SKIP: ${tool} is unavailable; the auth observability check did not run" >&2
        exit 77
    fi
done
if [[ ! -f "${MODULE_SO}" ]]; then
    echo "ERROR: module not found: ${MODULE_SO}" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/auth-obs-e2e.XXXXXX")"
cleanup() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

MODULE_DIR="$(cd "$(dirname "${MODULE_SO}")" && pwd)"
MODULE_NAME="$(basename "${MODULE_SO}")"

{
    printf '<html><body><h1>AuthObs</h1>\n'
    for ((i = 0; i < 50; i++)); do
        printf '<p>Paragraph %02d with <strong>weight</strong>.</p>\n' "$i"
    done
    printf '<p id="tail">TAILMARKERPRESENT</p>\n</body></html>\n'
} > "${WORK_DIR}/large.html"

cat > "${WORK_DIR}/nginx.conf" <<CONF
load_module /module/${MODULE_NAME};
worker_processes 1;
events { worker_connections 64; }
http {
    access_log off;
    server {
        # Published ports arrive over eth0, so the listener is not loopback-only.
        listen 8080;
        root /usr/share/nginx/html;

        # The auth subrequest: the marker header decides the outcome.
        location = /auth {
            if (\$http_x_auth_ok = "yes") {
                return 200;
            }
            return 403;
        }

        location = /large {
            auth_request /auth;
            default_type text/html;
            markdown_filter on;
            markdown_streaming force;
        }

        # The module admits only loopback peers, so this is queried from inside
        # the container rather than through the published port.
        location = /markdown-metrics {
            markdown_metrics;
            allow 127.0.0.1;
            allow ::1;
            deny all;
        }
    }
}
CONF

docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER}" \
    -p 127.0.0.1:18091:8080 \
    -v "${MODULE_DIR}:/module:ro" \
    -v "${WORK_DIR}/large.html:/usr/share/nginx/html/large:ro" \
    -v "${WORK_DIR}/nginx.conf:/etc/nginx/nginx.conf:ro" \
    "${IMAGE}" \
    sh -c 'apk add --no-cache libgcc curl >/dev/null 2>&1 || true; nginx -g "daemon off;"' >/dev/null

ready=0
for _ in $(seq 1 50); do
    if curl -sS -H 'Accept: text/markdown' -H 'X-Auth-Ok: yes' \
        "${BASE}/large" 2>/dev/null | grep -q TAILMARKERPRESENT; then
        ready=1
        break
    fi
    sleep 0.2
done
if [[ "${ready}" -ne 1 ]]; then
    echo "ERROR: the auth-protected conversion never became available" >&2
    docker logs "${CONTAINER}" >&2 | tail -20 || true
    exit 1
fi

echo "=== ${REQUESTS} authorized requests ===" >&2
for i in $(seq 1 "${REQUESTS}"); do
    body="$(curl -sS -H 'Accept: text/markdown' -H 'X-Auth-Ok: yes' "${BASE}/large")"
    if ! printf '%s' "${body}" | grep -q TAILMARKERPRESENT; then
        echo "ERROR: authorized request ${i} did not return the conversion" >&2
        exit 1
    fi
done

echo "=== one denied request ===" >&2
denied_status="$(curl -sS -o "${WORK_DIR}/denied.body" -w '%{http_code}' \
    -H 'Accept: text/markdown' "${BASE}/large")"
if [[ "${denied_status}" != "403" ]]; then
    echo "ERROR: the denied request returned ${denied_status}, expected 403" >&2
    exit 1
fi
if grep -q TAILMARKERPRESENT "${WORK_DIR}/denied.body"; then
    echo "ERROR: the denied request returned converted content" >&2
    exit 1
fi

echo "=== module metrics (queried from inside the container) ===" >&2
metrics="$(docker exec "${CONTAINER}" curl -sS http://127.0.0.1:8080/markdown-metrics)" || {
    echo "ERROR: the metrics endpoint could not be read from inside the container" >&2
    exit 1
}

# The documented contract is one terminal outcome per decision-chain request,
# and an auth subrequest is such a request, so the expected accounting is:
#   converted    = authorized requests + the readiness probe
#   not_eligible = the denied request
#   disabled     = one auth subrequest per client request that reached /large
# and the sum over all series equals the total number of decision-chain requests.
client_requests=$((REQUESTS + 1 + 1))
converted="$(printf '%s\n' "${metrics}" | awk '/outcome="converted"/ { print $NF; exit }')"
not_eligible="$(printf '%s\n' "${metrics}" | awk '/reason="not_eligible"/ { print $NF; exit }')"
disabled="$(printf '%s\n' "${metrics}" | awk '/reason="disabled"/ { print $NF; exit }')"
total="$(printf '%s\n' "${metrics}" | awk '/^nginx_markdown_requests_total\{/ { sum += $NF } END { printf "%d\n", sum }')"
series="$(printf '%s\n' "${metrics}" | grep -c '^nginx_markdown_requests_total{')"
echo "client requests: ${client_requests}, converted: ${converted}, not_eligible: ${not_eligible}, disabled: ${disabled}, summed: ${total}" >&2

if [[ "${series}" -lt 1 ]]; then
    echo "ERROR: no requests_total series were exported, so nothing was observed" >&2
    exit 1
fi
if [[ "${converted}" -ne $((REQUESTS + 1)) ]]; then
    echo "ERROR: converted=${converted}, expected ${REQUESTS} requests plus the readiness probe" >&2
    exit 1
fi
if [[ "${not_eligible}" -ne 1 ]]; then
    echo "ERROR: not_eligible=${not_eligible}, expected the single denied request" >&2
    exit 1
fi
if [[ "${disabled}" -ne "${client_requests}" ]]; then
    echo "ERROR: disabled=${disabled}, expected one auth subrequest per client request" >&2
    printf '%s\n' "${metrics}" | grep '^nginx_markdown_requests_total{' >&2
    exit 1
fi
if [[ "${total}" -ne $((client_requests + client_requests)) ]]; then
    echo "ERROR: ${total} decision-chain outcomes, expected client requests plus their subrequests" >&2
    exit 1
fi

echo "PASS: one terminal outcome per decision-chain request (${total} outcomes: ${client_requests} client requests and their auth subrequests), and the denial converted nothing" >&2
exit 0
