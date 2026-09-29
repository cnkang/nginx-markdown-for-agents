#!/usr/bin/env bash
# verify_metrics_endpoint_e2e.sh — Thin wrapper for the metrics-endpoint E2E scenario.
#
# Delegates execution to the Rust e2e-harness binary which validates
# the markdown metrics endpoint using the frozen Prometheus text 0.0.4
# contract, fixed content type, and shared-memory aggregation.
#
# This script is a backward-compatible entry point retained for CI and
# Makefile compatibility.  All assertion logic lives in the Rust harness.
#
# Usage:
#   tools/e2e/verify_metrics_endpoint_e2e.sh [--keep-artifacts] [--port PORT] [--nginx-bin PATH]
#
# Exit behaviour:
#   0 if the scenario passes.
#   1 if the harness binary cannot be built or the scenario fails.
set -euo pipefail

SCENARIO_NAME="metrics-endpoint"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E_HARNESS_BIN="${WORKSPACE_ROOT}/tools/e2e-harness/target/debug/e2e-harness"
E2E_HARNESS_MANIFEST="${WORKSPACE_ROOT}/tools/e2e-harness/Cargo.toml"

# Cleanup ownership boundary:
# The Rust harness owns the lifecycle of the working directory it creates:
# it settles the tree when a run finishes - removing it on success and
# retaining it with a `.harness-completed` marker on failure or under
# --keep-artifacts (see `settle_run_tree` in tools/e2e-harness).
# The trap below only recovers trees orphaned by an invocation that died
# before it could settle, and it proves ownership before removing
# anything: a tree is reclaimed only when it carries no settle marker, the
# process id embedded in its name is gone, and its invocation record names
# this scenario.  Age is never used as evidence - a run that outlives any
# threshold keeps its working directory.
_wrapper_cleanup() {
  if [[ "${KEEP_ARTIFACTS:-0}" -eq 1 ]]; then
    return
  fi
  # The Rust harness creates its tree under std::env::temp_dir(), which is
  # TMPDIR when the caller sets one; a probe like `mktemp -u` can resolve a
  # different directory, so the base is taken from TMPDIR directly.
  local base="${TMPDIR:-/tmp}"
  local d name pid inv candidate
  for d in "${base}"/e2e-harness-${SCENARIO_NAME}-*; do
    [[ -d "$d" ]] || continue
    name="${d##*/}"
    if [[ ! "$name" =~ ^e2e-harness-${SCENARIO_NAME}-([0-9]+)-[0-9]+$ ]]; then
      continue
    fi
    # A finished run settles its tree: the marker means the harness
    # completed and any retained artifacts are final, so never reclaim it.
    if [[ -e "${d}/.harness-completed" ]]; then
      continue
    fi
    pid="${BASH_REMATCH[1]}"
    if kill -0 "$pid" 2>/dev/null; then
      # The creating invocation is still running; its directory is not ours.
      continue
    fi
    # Ownership must be proven: the harness writes an invocation record into
    # every tree it creates, so recover only a tree whose record names this
    # scenario.  A tree without a record is left alone - the failure
    # direction is a leak, never a wrong deletion.
    inv=""
    for candidate in "${d}"/artifacts/scenarios/*/invocation.json; do
      if [[ -f "$candidate" ]]; then
        inv="$candidate"
        break
      fi
    done
    if [[ -z "$inv" ]] \
        || ! grep -q "\"scenario\"[[:space:]]*:[[:space:]]*\"${SCENARIO_NAME}\"" "$inv" 2>/dev/null; then
      continue
    fi
    rm -rf "$d" 2>/dev/null || true
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
