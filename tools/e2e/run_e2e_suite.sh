#!/usr/bin/env bash
# run_e2e_suite.sh - Orchestrator for the canonical E2E test suite.
#
# Runs all E2E verification scripts in sequence:
#   1. proxy/TLS backend verification
#   2. chunked native smoke verification
#   3. large-response native verification
#   4. streaming failure cache verification
#   5. accept negotiation verification
#   6. security verification
#   7. error handling verification
#   8. metrics endpoint verification
#   9. conditional requests verification
#  10. config merge verification
#  11. auth cache verification
#  12. status codes verification
#
# Options:
#   --keep-artifacts  Preserve build artifacts after the suite completes.
#
# Exit behaviour:
#   0 if all scripts pass, 1 if any script fails.
set -euo pipefail

KEEP_ARTIFACTS=0
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROXY_TLS_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_proxy_tls_backend_e2e.sh"
CHUNKED_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_chunked_streaming_native_e2e.sh"
LARGE_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_large_markdown_response_e2e.sh"
STREAMING_FAILURE_CACHE_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_streaming_failure_cache_e2e.sh"
ACCEPT_NEGOTIATION_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_accept_negotiation_e2e.sh"
SECURITY_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_security_e2e.sh"
ERROR_HANDLING_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_error_handling_e2e.sh"
METRICS_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_metrics_endpoint_e2e.sh"
CONDITIONAL_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_conditional_requests_e2e.sh"
CONFIG_MERGE_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_config_merge_e2e.sh"
AUTH_CACHE_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_auth_cache_e2e.sh"
STATUS_CODES_SCRIPT="${WORKSPACE_ROOT}/tools/e2e/verify_status_codes_e2e.sh"
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
   4. streaming failure/cache semantics verification
   5. Accept-header content-negotiation verification
   6. security behavior verification
   7. error-handling and fail-open verification
   8. metrics endpoint verification
   9. conditional-request (ETag/If-None-Match/If-Modified-Since) verification
  10. config-merge (http/server/location) verification
  11. auth/cache interaction verification
  12. upstream status-code passthrough verification

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

# cleanup - Remove temporary build artifacts on exit.
#
# On success with --keep-artifacts disabled, removes the entire SUITE_BUILDROOT.
# On failure, retains SUITE_BUILDROOT and prints its path for debugging.
# Always removes the temporary NGINX_BIN/BUILDROOT output capture files.
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

streaming_fc_args=()
if [[ "${KEEP_ARTIFACTS}" -eq 1 ]]; then
  streaming_fc_args=(--keep-artifacts)
fi
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${STREAMING_FAILURE_CACHE_SCRIPT}" "${streaming_fc_args[@]}"

accept_args=()
security_args=()
error_args=()
metrics_args=()
conditional_args=()
config_merge_args=()
auth_cache_args=()
status_codes_args=()
if [[ "${KEEP_ARTIFACTS}" -eq 1 ]]; then
  accept_args=(--keep-artifacts)
  security_args=(--keep-artifacts)
  error_args=(--keep-artifacts)
  metrics_args=(--keep-artifacts)
  conditional_args=(--keep-artifacts)
  config_merge_args=(--keep-artifacts)
  auth_cache_args=(--keep-artifacts)
  status_codes_args=(--keep-artifacts)
fi
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${ACCEPT_NEGOTIATION_SCRIPT}" "${accept_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${SECURITY_SCRIPT}" "${security_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${ERROR_HANDLING_SCRIPT}" "${error_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${METRICS_SCRIPT}" "${metrics_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${CONDITIONAL_SCRIPT}" "${conditional_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${CONFIG_MERGE_SCRIPT}" "${config_merge_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${AUTH_CACHE_SCRIPT}" "${auth_cache_args[@]}"
env NGINX_BIN="${SUITE_NGINX_BIN}" bash "${STATUS_CODES_SCRIPT}" "${status_codes_args[@]}"

echo "Canonical E2E suite summary:"
echo "  proxy_tls_backend=passed"
echo "  chunked_native_smoke=passed"
echo "  large_response_native=passed"
echo "  streaming_failure_cache=passed"
echo "  accept_negotiation=passed"
echo "  security=passed"
echo "  error_handling=passed"
echo "  metrics_endpoint=passed"
echo "  conditional_requests=passed"
echo "  config_merge=passed"
echo "  auth_cache=passed"
echo "  status_codes=passed"
echo "  reusable_nginx_bin=${SUITE_NGINX_BIN}"
