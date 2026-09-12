#!/usr/bin/env bash
# verify_graceful_reload_streaming_e2e.sh — A reload during a streamed response
# must not truncate or alter it.
#
# Purpose:
#   `nginx -s reload` retires the old worker while it finishes its in-flight
#   requests.  This check starts a rate-limited read of a streamed conversion,
#   confirms the transfer is genuinely in flight, reloads NGINX mid-response, and
#   requires the slow body to match an unthrottled baseline byte for byte.
#
# Usage:
#   verify_graceful_reload_streaming_e2e.sh [--image TAG] [--paragraphs N] [--rate RATE]
#
# Environment:
#   MODULE_SO   module to load (default: build/ngx_http_markdown_filter_module.so)
#   IMAGE       container image (default: nginx:1.30.4-alpine)
#   PARAGRAPHS  body paragraphs to generate (default: 1500)
#   RATE        slow-reader limit (default: 48k)
#
# Exit codes:
#   0  the reload happened mid-transfer and the slow body matches the baseline
#   1  the transfer never started, the reload failed, or the bodies differ
#   77  docker or curl is unavailable
#
# This script is FAIL-CLOSED: every unexpected outcome is a failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
IMAGE="${IMAGE:-nginx:1.30.4-alpine}"
PARAGRAPHS="${PARAGRAPHS:-1500}"
RATE="${RATE:-48k}"
CONTAINER="markdown-reload-e2e"
ENDPOINT="http://127.0.0.1:18090/large"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image) IMAGE="$2"; shift 2 ;;
        --paragraphs) PARAGRAPHS="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

for tool in docker curl; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "SKIP: ${tool} is unavailable; the reload check did not run" >&2
        exit 77
    fi
done
if [[ ! -f "${MODULE_SO}" ]]; then
    echo "ERROR: module not found: ${MODULE_SO}" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/reload-e2e.XXXXXX")"
cleanup() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${WORK_DIR}"
    return 0
}
trap cleanup EXIT

MODULE_DIR="$(cd "$(dirname "${MODULE_SO}")" && pwd)"
MODULE_NAME="$(basename "${MODULE_SO}")"

# The marker is plain alphanumerics: the converter escapes underscores and other
# Markdown-significant characters in body text, so a marker relying on them would
# never match the emitted output.
{
    printf '<html><body>\n'
    for ((i = 0; i < PARAGRAPHS; i++)); do
        printf '<p>Paragraph %05d with <strong>weight</strong> and a <a href="/p/%05d">link</a>.</p>\n' "$i" "$i"
    done
    printf '<p id="tail">TAILMARKERPRESENT</p>\n'
    printf '</body></html>\n'
} > "${WORK_DIR}/large.html"

cat > "${WORK_DIR}/nginx.conf" <<CONF
load_module /module/${MODULE_NAME};
worker_processes 1;
events { worker_connections 64; }
http {
    access_log off;
    server {
        # Every interface: Docker forwards published ports to the container's
        # eth0, so a loopback-only listener would never receive them.
        listen 8080;
        root /usr/share/nginx/html;
        location = /large {
            default_type text/html;
            markdown_filter on;
            markdown_streaming force;
        }
    }
}
CONF

docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
docker run -d --name "${CONTAINER}" \
    -p 127.0.0.1:18090:8080 \
    -v "${MODULE_DIR}:/module:ro" \
    -v "${WORK_DIR}/large.html:/usr/share/nginx/html/large:ro" \
    -v "${WORK_DIR}/nginx.conf:/etc/nginx/nginx.conf:ro" \
    "${IMAGE}" \
    sh -c 'apk add --no-cache libgcc curl >/dev/null 2>&1 || true; nginx -g "daemon off;"' >/dev/null

# Readiness must prove a real conversion, not merely a reachable port.
ready=0
for _ in $(seq 1 50); do
    if docker exec "${CONTAINER}" curl -sS -H 'Accept: text/markdown' \
        http://127.0.0.1:8080/large 2>/dev/null | grep -q TAILMARKERPRESENT; then
        ready=1
        break
    fi
    sleep 0.2
done
if [[ "${ready}" -ne 1 ]]; then
    echo "ERROR: the container never served a converted /large" >&2
    docker logs "${CONTAINER}" >&2 | tail -20 || true
    exit 1
fi

echo "=== baseline read (no throttle) ===" >&2
baseline="${WORK_DIR}/baseline.md"
curl -sS -H 'Accept: text/markdown' "${ENDPOINT}" > "${baseline}"
baseline_bytes="$(wc -c < "${baseline}" | tr -d ' ')"
echo "baseline: ${baseline_bytes} bytes" >&2
if [[ "${baseline_bytes}" -eq 0 ]]; then
    echo "ERROR: the baseline read returned no body" >&2
    exit 1
fi
if ! grep -q TAILMARKERPRESENT "${baseline}"; then
    echo "ERROR: the baseline lost the tail marker, so the conversion is incomplete" >&2
    exit 1
fi

slow="${WORK_DIR}/slow.md"
echo "=== slow read ${RATE} with a reload mid-transfer ===" >&2
curl -sS -H 'Accept: text/markdown' --limit-rate "${RATE}" "${ENDPOINT}" > "${slow}" &
slow_pid=$!

# The reload must land while the response is on the wire, so wait until the
# client has received part of the body before signalling the master.
in_flight=0
for _ in $(seq 1 100); do
    if [[ -s "${slow}" ]]; then
        in_flight=1
        break
    fi
    sleep 0.1
done
if [[ "${in_flight}" -ne 1 ]]; then
    echo "ERROR: the slow read never received any bytes, so no transfer was in flight" >&2
    kill "${slow_pid}" 2>/dev/null || true
    exit 1
fi
partial_bytes="$(wc -c < "${slow}" | tr -d ' ')"
echo "reloading with ${partial_bytes} bytes delivered so far" >&2

reload_output="$(docker exec "${CONTAINER}" nginx -s reload 2>&1)" || {
    echo "ERROR: reload failed: ${reload_output}" >&2
    kill "${slow_pid}" 2>/dev/null || true
    exit 1
}

if ! wait "${slow_pid}"; then
    echo "ERROR: the slow read failed after the reload" >&2
    exit 1
fi
slow_bytes="$(wc -c < "${slow}" | tr -d ' ')"
echo "slow body: ${slow_bytes} bytes" >&2

if ! grep -q TAILMARKERPRESENT "${slow}"; then
    echo "ERROR: the slow read lost the tail marker, so the reload truncated it" >&2
    exit 1
fi
if ! cmp -s "${baseline}" "${slow}"; then
    echo "ERROR: the reloaded response differs from the baseline" >&2
    cmp "${baseline}" "${slow}" >&2 || true
    exit 1
fi

# The old worker must exit once its in-flight request finished; one worker plus
# the master is the steady state.
workers="$(docker exec "${CONTAINER}" sh -c 'ps -o comm= | grep -c "^nginx$"' 2>/dev/null || echo 0)"
echo "nginx processes after the reload: ${workers}" >&2
if ! [[ "${workers}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: could not count the nginx processes after the reload (got '${workers}')" >&2
    exit 1
fi
# One master plus one worker is the steady state: the retired worker must exit
# once its in-flight request finished, and a stuck extra worker would leak.
if [[ "${workers}" -ne 2 ]]; then
    echo "ERROR: expected 2 nginx processes after the reload, found ${workers}" >&2
    exit 1
fi

echo "PASS: a reload landed mid-transfer and the response stayed complete (${slow_bytes} bytes)" >&2
exit 0
