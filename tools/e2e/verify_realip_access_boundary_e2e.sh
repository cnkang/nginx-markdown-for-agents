#!/usr/bin/env bash
# verify_realip_access_boundary_e2e.sh — realip must not widen the module's
# metrics/diagnostics access boundary.
#
# Purpose:
#   `real_ip_header X-Forwarded-For` rewrites the client address from a request
#   header.  If the module decided access on the rewritten address, any client
#   able to reach the listener could claim to be 127.0.0.1 and read the metrics
#   and diagnostics endpoints.  The module must keep deciding on the ORIGINAL
#   transport peer instead.
#
#   This check enables realip for every source, proves from a non-loopback
#   transport peer that the rewrite really happened, and then requires the
#   module to reject that request and to keep accepting a genuine loopback peer.
#
# Usage:
#   verify_realip_access_boundary_e2e.sh [--image TAG]
#
# Environment:
#   MODULE_SO   module to load (default: build/ngx_http_markdown_filter_module.so)
#   IMAGE       container image (default: nginx:1.30.4-alpine, matching the build)
#
# Exit codes:
#   0  the rewrite was proven live and the boundary held
#   1  the rewrite was not proven, or the boundary was crossed
#   77  docker is unavailable; the check was skipped
#
# This script is FAIL-CLOSED: a missing prerequisite or an unexpected status is
# a failure, and the only skip is an absent container runtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
IMAGE="${IMAGE:-nginx:1.30.4-alpine}"
CONTAINER="markdown-realip-e2e"
PORT="${PORT:-18088}"

if ! command -v docker >/dev/null 2>&1; then
    echo "SKIP: docker is unavailable" >&2
    exit 77
fi
if [[ ! -f "${MODULE_SO}" ]]; then
    echo "ERROR: module not found at ${MODULE_SO}; build it first" >&2
    exit 1
fi
# Docker requires an absolute source path for a bind mount.
MODULE_SO="$(cd "$(dirname "${MODULE_SO}")" && pwd)/$(basename "${MODULE_SO}")"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/realip-e2e.XXXXXX")"
cleanup() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

mkdir -p "${WORK_DIR}/etc"

cat > "${WORK_DIR}/etc/nginx.conf" <<'CONF'
# The image's entrypoint starts NGINX with this file, so the module must be
# loaded here rather than injected after the container starts.
load_module /etc/nginx/modules/ngx_http_markdown_filter_module.so;

worker_processes 1;
error_log /dev/stderr warn;
pid /tmp/nginx.pid;
events { worker_connections 64; }
http {
    access_log off;
    default_type text/plain;

    # Trust every source so any client can rewrite its own address from the
    # header: this is the configuration that must not sway the module.
    set_real_ip_from 0.0.0.0/0;
    real_ip_header X-Forwarded-For;
    real_ip_recursive on;

    server {
        listen 8080;

        # Reports both addresses so the check can prove the rewrite is live:
        # X-Real-Addr is the rewritten peer, X-Realip-Remote-Addr the original.
        location = /probe {
            add_header X-Real-Addr $remote_addr always;
            add_header X-Realip-Remote-Addr $realip_remote_addr always;
            return 200 "probe\n";
        }

        location = /markdown-metrics { markdown_metrics; }
        location = /markdown-diagnostics { markdown_diagnostics on; }
    }
}
CONF

echo "=== starting the container ===" >&2
docker run -d --name "${CONTAINER}" \
    -v "${MODULE_SO}:/etc/nginx/modules/ngx_http_markdown_filter_module.so:ro" \
    -v "${WORK_DIR}/etc/nginx.conf:/etc/nginx/nginx.conf:ro" \
    -p "127.0.0.1:${PORT}:8080" \
    "${IMAGE}" >/dev/null

ready=0
for _ in $(seq 1 40); do
    if docker exec "${CONTAINER}" nginx -t >/dev/null 2>&1; then
        if docker exec "${CONTAINER}" sh -c 'nginx -s reload 2>/dev/null || nginx' >/dev/null 2>&1; then
            if curl -sS -o /dev/null "http://127.0.0.1:${PORT}/probe" 2>/dev/null; then
                ready=1
                break
            fi
        fi
    fi
    sleep 0.5
done
if [[ "${ready}" -ne 1 ]]; then
    echo "ERROR: the container never served the probe endpoint" >&2
    docker logs "${CONTAINER}" >&2 | tail -20 || true
    exit 1
fi

# --- Evidence that realip is actually rewriting the peer -------------------
echo "=== proving the rewrite is live (non-loopback transport peer) ===" >&2
probe_headers="$(curl -sS -D - -o /dev/null -H 'X-Forwarded-For: 127.0.0.1' \
    "http://127.0.0.1:${PORT}/probe")"
real_addr="$(printf '%s' "${probe_headers}" | tr -d '\r' | awk -F': ' 'tolower($1) == "x-real-addr" {print $2}')"
transport_addr="$(printf '%s' "${probe_headers}" | tr -d '\r' | awk -F': ' 'tolower($1) == "x-realip-remote-addr" {print $2}')"
echo "rewritten peer: ${real_addr} | original transport peer: ${transport_addr}" >&2
echo "--- probe headers ---" >&2
printf '%s\n' "${probe_headers}" | tr -d '\r' | sed 's/^/    /' >&2

if [[ -z "${real_addr}" || -z "${transport_addr}" ]]; then
    echo "ERROR: the probe did not report both addresses, so the rewrite cannot be proven" >&2
    exit 1
fi
if [[ "${real_addr}" != "127.0.0.1" ]]; then
    echo "ERROR: realip did not rewrite the peer to 127.0.0.1 (got ${real_addr}), so this check would prove nothing" >&2
    exit 1
fi
if [[ "${transport_addr}" == "127.0.0.1" ]]; then
    echo "ERROR: the transport peer is loopback, so the boundary is not being tested" >&2
    exit 1
fi

# --- The boundary must hold ------------------------------------------------
echo "=== claiming 127.0.0.1 from a non-loopback peer ===" >&2
for endpoint in /markdown-metrics /markdown-diagnostics; do
    status="$(curl -sS -o /dev/null -w '%{http_code}' \
        -H 'X-Forwarded-For: 127.0.0.1' "http://127.0.0.1:${PORT}${endpoint}")"
    echo "${endpoint} from the host: ${status}" >&2
    if [[ "${status}" != "403" ]]; then
        echo "ERROR: ${endpoint} returned ${status} for a non-loopback peer claiming 127.0.0.1; realip widened the boundary" >&2
        exit 1
    fi
done

# --- A genuine loopback peer stays allowed ---------------------------------
echo "=== from a genuine loopback peer inside the container ===" >&2
for endpoint in /markdown-metrics /markdown-diagnostics; do
    # wget exits 0 only for a 2xx response; the loopback peer needs no header,
    # so the image's own wget is enough and no package install is required.
    if docker exec "${CONTAINER}" wget -q -O /dev/null "http://127.0.0.1:8080${endpoint}"; then
        echo "${endpoint} from loopback: 200" >&2
    else
        echo "ERROR: ${endpoint} was refused for a genuine loopback peer" >&2
        exit 1
    fi
done

echo "PASS: realip rewrote the peer (${transport_addr} -> ${real_addr}) and the metrics/diagnostics boundary still rejects the non-loopback caller" >&2
exit 0
