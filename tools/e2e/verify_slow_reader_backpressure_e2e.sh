#!/usr/bin/env bash
# verify_slow_reader_backpressure_e2e.sh — A slow reader must not truncate or
# corrupt a streamed Markdown response.
#
# Purpose:
#   A client that reads slowly forces the body filter to return NGX_AGAIN
#   repeatedly, so the module must hold the pending output and resume exactly
#   where it stopped.  This check serves a large document through the streaming
#   path, fetches it twice from the same container — once unthrottled and once
#   rate-limited — and requires the two bodies to be byte-identical and complete.
#
# Usage:
#   verify_slow_reader_backpressure_e2e.sh [--image TAG] [--paragraphs N] [--rate RATE]
#
# Environment:
#   MODULE_SO   module to load (default: build/ngx_http_markdown_filter_module.so)
#   IMAGE       container image (default: nginx:1.30.4-alpine, matching the module build)
#   PARAGRAPHS  number of body paragraphs to generate (default: 4000)
#   RATE        slow-reader limit passed to curl (default: 32k)
#
# Exit codes:
#   0  both bodies are complete and identical
#   1  the fast or slow body was empty, truncated, or different
#   77  docker is unavailable; the check was skipped
#
# This script is FAIL-CLOSED: every unexpected outcome is a failure, and the only
# skip is an absent container runtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
IMAGE="${IMAGE:-nginx:1.30.4-alpine}"
PARAGRAPHS="${PARAGRAPHS:-4000}"
RATE="${RATE:-32k}"
CONTAINER="markdown-backpressure-e2e"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image) IMAGE="$2"; shift 2 ;;
        --paragraphs) PARAGRAPHS="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

if ! command -v docker >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    echo "SKIP: docker and curl are both required; the slow-reader check did not run" >&2
    exit 77
fi
if [[ ! -f "${MODULE_SO}" ]]; then
    echo "ERROR: module not found: ${MODULE_SO}" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/slow-reader-e2e.XXXXXX")"
cleanup() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

MODULE_DIR="$(cd "$(dirname "${MODULE_SO}")" && pwd)"
MODULE_NAME="$(basename "${MODULE_SO}")"

# A large document whose final bytes identify a complete conversion: a truncated
# response loses the tail marker, which is what this check detects.  The marker is
# plain alphanumerics because the converter legitimately escapes underscores and
# other Markdown-significant characters in body text.
{
    printf '<html><body>\n'
    for ((i = 0; i < PARAGRAPHS; i++)); do
        printf '<p>Paragraph %05d with <strong>weight</strong> and a <a href="/page/%05d">link</a>.</p>\n' "$i" "$i"
    done
    printf '<p id="tail-marker">TAILMARKERPRESENT</p>\n'
    printf '</body></html>\n'
} > "${WORK_DIR}/large.html"

cat > "${WORK_DIR}/nginx.conf" <<CONF
load_module /module/${MODULE_NAME};
worker_processes 1;
events { worker_connections 64; }
http {
    access_log off;
    server {
        # Bind every interface inside the container: Docker forwards published
        # ports to the container's eth0, so a loopback-only listener would
        # never receive them.
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
    -p 127.0.0.1:18080:8080 \
    -v "${MODULE_DIR}:/module:ro" \
    -v "${WORK_DIR}/large.html:/usr/share/nginx/html/large:ro" \
    -v "${WORK_DIR}/nginx.conf:/etc/nginx/nginx.conf:ro" \
    "${IMAGE}" \
    sh -c 'apk add --no-cache libgcc >/dev/null 2>&1 || true; nginx -g "daemon off;"' >/dev/null

# Wait for the listener instead of sleeping a fixed interval.
ready=0
for _ in $(seq 1 50); do
    if docker exec "${CONTAINER}" sh -c 'nginx -t >/dev/null 2>&1'; then
        # A 404 page would satisfy a plain reachability probe, so require the
        # converted document's tail marker.
        if docker exec "${CONTAINER}" sh -c \
            'wget -qO- http://127.0.0.1:8080/large 2>/dev/null | grep -q TAILMARKERPRESENT'; then
            ready=1
            break
        fi
    fi
    sleep 0.2
done
if [[ "${ready}" -ne 1 ]]; then
    echo "ERROR: the container never served /large" >&2
    docker logs "${CONTAINER}" >&2 | tail -20 || true
    exit 1
fi

# The container's BusyBox wget has no rate limit, so the host acts as the reader:
# curl --limit-rate throttles the download and forces the repeated NGX_AGAIN path
# this check targets.
ENDPOINT="http://127.0.0.1:18080/large"

# The Accept header is what selects conversion, so both reads must carry it:
# without it the module passes the HTML through and the comparison below would
# compare two unconverted bodies.
fetch() {
    local limit="$1"
    if [[ -n "${limit}" ]]; then
        curl -sS -H 'Accept: text/markdown' --limit-rate "${limit}" "${ENDPOINT}"
    else
        curl -sS -H 'Accept: text/markdown' "${ENDPOINT}"
    fi
}

echo "=== fast read (no throttle) ===" >&2
fast_out="${WORK_DIR}/fast.md"
fetch "" > "${fast_out}"
fast_bytes="$(wc -c < "${fast_out}" | tr -d ' ')"
echo "fast body: ${fast_bytes} bytes" >&2

echo "=== slow read (limit-rate ${RATE}) ===" >&2
slow_out="${WORK_DIR}/slow.md"
fetch "${RATE}" > "${slow_out}"
slow_bytes="$(wc -c < "${slow_out}" | tr -d ' ')"
echo "slow body: ${slow_bytes} bytes" >&2

if [[ "${fast_bytes}" -eq 0 ]]; then
    echo "ERROR: the fast read returned no body" >&2
    exit 1
fi
if [[ "${slow_bytes}" -eq 0 ]]; then
    echo "ERROR: the slow read returned no body" >&2
    exit 1
fi
if ! grep -q 'TAILMARKERPRESENT' "${fast_out}"; then
    echo "ERROR: the fast read lost the tail marker" >&2
    exit 1
fi
if ! grep -q 'TAILMARKERPRESENT' "${slow_out}"; then
    echo "ERROR: the slow read lost the tail marker (truncated under backpressure)" >&2
    exit 1
fi
if ! cmp -s "${fast_out}" "${slow_out}"; then
    echo "ERROR: the slow read differs from the fast read" >&2
    cmp "${fast_out}" "${slow_out}" >&2 || true
    exit 1
fi

# The response is a converted document, not the original HTML.
if grep -q '<p>' "${slow_out}"; then
    echo "ERROR: the body still contains HTML paragraphs, so it was not converted" >&2
    exit 1
fi

echo "PASS: a slow reader received the complete conversion byte-for-byte (${slow_bytes} bytes)" >&2
exit 0
