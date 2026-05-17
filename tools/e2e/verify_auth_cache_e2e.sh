#!/usr/bin/env bash
# verify_auth_cache_e2e.sh — Thin wrapper for the auth-cache E2E scenario.
#
# Delegates execution to the Rust e2e-harness binary which validates
# authenticated-content cache-control behaviour: Cache-Control header
# rewriting when auth policy is active (deny/allow), cookie-based and
# bearer-token authentication detection, and private/public cache
# directive handling.
#
# This script is a backward-compatible entry point retained for CI and
# Makefile compatibility.  All assertion logic lives in the Rust harness.
#
# Usage:
#   tools/e2e/verify_auth_cache_e2e.sh [--keep-artifacts] [--port PORT] [--nginx-bin PATH]
#
# Exit behaviour:
#   0 if the scenario passes.
#   1 if the harness binary cannot be built or the scenario fails.
set -euo pipefail

SCENARIO_NAME="auth-cache"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E_HARNESS_BIN="${WORKSPACE_ROOT}/tools/e2e-harness/target/debug/e2e-harness"
E2E_HARNESS_MANIFEST="${WORKSPACE_ROOT}/tools/e2e-harness/Cargo.toml"

# Cleanup ownership boundary:
# Normal exits are handled by the Rust harness process lifecycle (Drop).
# This trap provides best-effort cleanup for hard kills or signals that
# prevent the Rust harness from running its own cleanup.  It removes
# stale e2e-harness temp directories matching this scenario that are
# older than 10 minutes (a running harness will hold files open and
# thus be immune to removal).
_wrapper_cleanup() {
  if [[ "${KEEP_ARTIFACTS:-0}" -eq 1 ]]; then
    return
  fi
  local tmpdir
  tmpdir="$(mktemp -u -q)" 2>/dev/null || tmpdir="${TMPDIR:-/tmp}"
  local base="${tmpdir%/*}"
  local stale_sec=600
  local now
  now="$(date +%s)" 2>/dev/null || return
  for d in "${base}"/e2e-harness-${SCENARIO_NAME}-*; do
    [[ -d "$d" ]] || continue
    local mtime
    if [[ "$(uname -s)" == "Darwin" ]]; then
      mtime="$(stat -f %m "$d" 2>/dev/null)" || continue
    else
      mtime="$(stat -c %Y "$d" 2>/dev/null)" || continue
    fi
    if [[ $(( now - mtime )) -gt stale_sec ]]; then
      rm -rf "$d" 2>/dev/null || true
    fi
  done
  return 0
}
trap _wrapper_cleanup EXIT
trap 'trap - EXIT; _wrapper_cleanup; exit 130' INT TERM

KEEP_ARTIFACTS=0
PORT_ARG=""
UPSTREAM_PORT_ARG=""
NGINX_BIN_ARG=""

# usage — Print command-line help text to stderr.
#
# Arguments:
#   (none)
#
# Outputs:
#   Writes usage text to stderr.
#
# Returns:
#   0 always.
usage() {
  cat <<USAGE >&2
Usage: $(basename "$0") [--keep-artifacts] [--port PORT] [--upstream-port PORT] [--nginx-bin PATH] [--nginx-version VERSION] [--metrics-port PORT]

Thin compatibility wrapper for migrated scenario '${SCENARIO_NAME}'.
Delegates execution to: e2e-harness scenario ${SCENARIO_NAME}

Compatibility notes:
  --nginx-version and --metrics-port are accepted for backward compatibility
  but are not used by the Rust harness scenario.
USAGE
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
      ;;
    --port)
      [[ -n "${2:-}" ]] || { echo "missing value for --port" >&2; exit 2; }
      PORT_ARG="$2"
      shift 2
      ;;
    --upstream-port)
      [[ -n "${2:-}" ]] || { echo "missing value for --upstream-port" >&2; exit 2; }
      UPSTREAM_PORT_ARG="$2"
      shift 2
      ;;
    --nginx-bin)
      [[ -n "${2:-}" ]] || { echo "missing value for --nginx-bin" >&2; exit 2; }
      NGINX_BIN_ARG="$2"
      shift 2
      ;;
    --nginx-version)
      [[ -n "${2:-}" ]] || { echo "missing value for --nginx-version" >&2; exit 2; }
      shift 2
      ;;
    --metrics-port)
      [[ -n "${2:-}" ]] || { echo "missing value for --metrics-port" >&2; exit 2; }
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

cmd=()
if [[ -x "${E2E_HARNESS_BIN}" ]]; then
  cmd=("${E2E_HARNESS_BIN}")
else
  cmd=(cargo run --manifest-path "${E2E_HARNESS_MANIFEST}" --)
fi

args=(scenario "${SCENARIO_NAME}")
if [[ "${KEEP_ARTIFACTS}" -eq 1 ]]; then
  args+=(--keep-artifacts)
fi
if [[ -n "${NGINX_BIN_ARG}" ]]; then
  args+=(--nginx-bin "${NGINX_BIN_ARG}")
fi
if [[ -n "${PORT_ARG}" ]]; then
  args+=(--port "${PORT_ARG}")
fi
if [[ -n "${UPSTREAM_PORT_ARG}" ]]; then
  args+=(--upstream-port "${UPSTREAM_PORT_ARG}")
fi

"${cmd[@]}" "${args[@]}"
