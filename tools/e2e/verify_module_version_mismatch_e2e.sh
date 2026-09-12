#!/usr/bin/env bash
# verify_module_version_mismatch_e2e.sh — The module must refuse an
# incompatible NGINX, loudly and by name.
#
# Purpose:
#   A dynamic module is only valid for the NGINX version and build signature it
#   was compiled against.  This check loads the module built for the repository's
#   pinned NGINX into an NGINX of a different version and requires `nginx -t` to
#   fail with a message that names the module, so an unrelated configuration
#   error cannot satisfy the assertion.  A control run proves the container
#   itself works without the module.
#
# Usage:
#   verify_module_version_mismatch_e2e.sh [--module PATH] [--incompatible-tag TAG]
#
# Environment:
#   MODULE_SO          module to load (default: build/ngx_http_markdown_filter_module.so)
#   INCOMPATIBLE_TAG   container image tag (default: 1.26-alpine). The image
#                      must match the module's libc, and the run installs the
#                      module's dynamic dependencies so that only a version or
#                      signature mismatch can fail the load.
#
# Exit codes:
#   0  the incompatible version refused the module, and the control run passed
#   1  the module loaded (or the failure did not name it), or the control failed
#   77 docker is unavailable; the check was skipped
#
# This script is FAIL-CLOSED: any unexpected outcome is a failure, and the only
# skip is an absent container runtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
INCOMPATIBLE_TAG="${INCOMPATIBLE_TAG:-1.26-alpine}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --module) MODULE_SO="$2"; shift 2 ;;
        --incompatible-tag) INCOMPATIBLE_TAG="$2"; shift 2 ;;
        -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "SKIP: docker is unavailable; the version-mismatch check did not run" >&2
    exit 77
fi
if [[ ! -f "${MODULE_SO}" ]]; then
    echo "ERROR: module not found: ${MODULE_SO}" >&2
    exit 1
fi

MODULE_DIR="$(cd "$(dirname "${MODULE_SO}")" && pwd)"
MODULE_NAME="$(basename "${MODULE_SO}")"
IMAGE="nginx:${INCOMPATIBLE_TAG}"

run_nginx() {
    local label="$1"
    local conf="$2"
    docker run --rm \
        -v "${MODULE_DIR}:/module:ro" \
        -v "${conf}:/etc/nginx/nginx.conf:ro" \
        "${IMAGE}" \
        sh -c 'apk add --no-cache libgcc >/dev/null 2>&1 || true; nginx -t 2>&1'
}

echo "=== control: ${IMAGE} without the module must pass ===" >&2
CONTROL_CONF="$(mktemp "${TMPDIR:-/tmp}/e2e-control.XXXXXX")"
MISMATCH_CONF=""
trap 'rm -f "${CONTROL_CONF}" "${MISMATCH_CONF}" 2>/dev/null || true' EXIT
cat > "${CONTROL_CONF}" <<'CONF'
# A complete nginx.conf: the file is mounted over the image's own nginx.conf,
# so the top-level events/http structure belongs here.
events { worker_connections 32; }
http { server { listen 127.0.0.1:8080; } }
CONF

control_status=0
control_output="$(run_nginx control "${CONTROL_CONF}")" || control_status=$?
if [[ "${control_status}" -ne 0 ]]; then
    echo "ERROR: the control configuration failed in ${IMAGE}; the image itself is unusable" >&2
    printf '%s\n' "${control_output}" >&2
    exit 1
fi
echo "control passed" >&2

echo "=== incompatible: ${IMAGE} must refuse ${MODULE_NAME} ===" >&2
MISMATCH_CONF="$(mktemp "${TMPDIR:-/tmp}/e2e-mismatch.XXXXXX")"
cat > "${MISMATCH_CONF}" <<CONF
load_module /module/${MODULE_NAME};
events { worker_connections 32; }
http { server { listen 127.0.0.1:8080; markdown_filter on; } }
CONF

mismatch_status=0
mismatch_output="$(run_nginx mismatch "${MISMATCH_CONF}")" || mismatch_status=$?

if [[ "${mismatch_status}" -eq 0 ]]; then
    echo "ERROR: ${IMAGE} loaded a module built for a different NGINX version" >&2
    exit 1
fi
if [[ "${mismatch_output}" != *"${MODULE_NAME}"* ]]; then
    echo "ERROR: the failure does not name ${MODULE_NAME}, so it may be unrelated" >&2
    printf '%s\n' "${mismatch_output}" >&2
    exit 1
fi
# The load must fail because the module was built for another NGINX version or
# build signature.  A missing shared library, an unreadable file, or any other
# unrelated error must not satisfy this check.
if [[ "${mismatch_output}" != *"binary compatible"* \
      && "${mismatch_output}" != *"version"* \
      && "${mismatch_output}" != *"signature"* ]]; then
    echo "ERROR: the failure does not report a version or signature mismatch," \
         "so it does not exercise the incompatible-NGINX contract" >&2
    printf '%s\n' "${mismatch_output}" >&2
    exit 1
fi

echo "PASS: ${IMAGE} refused ${MODULE_NAME} with a module-specific error" >&2
printf '%s\n' "${mismatch_output}" | head -3 >&2
exit 0
