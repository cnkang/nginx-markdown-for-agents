#!/usr/bin/env bash
set -euo pipefail

KEEP_ARTIFACTS=0
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROXY_TLS_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_proxy_tls_backend_e2e.sh"
CHUNKED_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_chunked_streaming_native_e2e.sh"
LARGE_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_large_markdown_response_e2e.sh"
SUITE_BUILDROOT=""
SUITE_NGINX_BIN=""
NGINX_BIN_OUTPUT_FILE=""
BUILDROOT_OUTPUT_FILE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--keep-artifacts]

Run the canonical E2E suite:
  1. proxy/TLS backend verification
  2. chunked native smoke verification
  3. large-response native verification

Environment variables:
  NGINX_BIN       Optional reusable module-enabled nginx binary
  NGINX_VERSION   Optional nginx version forwarded to the self-building checks
EOF
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
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

cleanup() {
  local rc=$?

  rm -f "${NGINX_BIN_OUTPUT_FILE:-}" "${BUILDROOT_OUTPUT_FILE:-}"

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${SUITE_BUILDROOT}" && -d "${SUITE_BUILDROOT}" ]]; then
    rm -rf "${SUITE_BUILDROOT}" || true
  fi

  if [[ $rc -ne 0 && -n "${SUITE_BUILDROOT}" && -d "${SUITE_BUILDROOT}" ]]; then
    echo "Canonical E2E suite failed. Retained bootstrap artifacts: ${SUITE_BUILDROOT}" >&2
  elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${SUITE_BUILDROOT}" ]]; then
    echo "Canonical E2E suite succeeded. Retained bootstrap artifacts: ${SUITE_BUILDROOT}"
  fi

  return 0
}
trap cleanup EXIT

NGINX_BIN_OUTPUT_FILE="$(mktemp)"
BUILDROOT_OUTPUT_FILE="$(mktemp)"

proxy_args=(
  --keep-artifacts
  --nginx-bin-output "${NGINX_BIN_OUTPUT_FILE}"
  --buildroot-output "${BUILDROOT_OUTPUT_FILE}"
)

bash "${PROXY_TLS_SCRIPT}" "${proxy_args[@]}"
SUITE_NGINX_BIN="$(cat "${NGINX_BIN_OUTPUT_FILE}")"
SUITE_BUILDROOT="$(cat "${BUILDROOT_OUTPUT_FILE}")"

[[ -x "${SUITE_NGINX_BIN}" ]] || {
  echo "Canonical E2E suite failed to resolve reusable nginx binary" >&2
  exit 1
}

chunked_args=(--profile smoke)
large_args=()
if [[ "${KEEP_ARTIFACTS}" -eq 1 ]]; then
  chunked_args=(--keep-artifacts "${chunked_args[@]}")
  large_args=(--keep-artifacts)
fi

env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${CHUNKED_SCRIPT}" "${chunked_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${LARGE_SCRIPT}" "${large_args[@]}"

echo "Canonical E2E suite summary:"
echo "  proxy_tls_backend=passed"
echo "  chunked_native_smoke=passed"
echo "  large_response_native=passed"
echo "  reusable_nginx_bin=${SUITE_NGINX_BIN}"
