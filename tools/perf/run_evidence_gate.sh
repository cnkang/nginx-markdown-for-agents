#!/bin/bash
# Run the active module performance evidence gate with a trusted Python runtime.

set -uo pipefail

resolve_python3() {
  local candidate=""
  for candidate in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3 \
    /bin/python3; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "FAIL: no trusted python3 executable found" >&2
  return 1
}

PYTHON3="$(resolve_python3)" || exit 1
GATE_MODE="${EVIDENCE_GATE_MODE:-non-blocking}"
ALLOW_SKIP_MODULE="${EVIDENCE_GATE_ALLOW_SKIP_MODULE:-0}"

case "$GATE_MODE" in
  non-blocking|blocking)
    ;;
  *)
    echo "FAIL: EVIDENCE_GATE_MODE must be non-blocking or blocking" >&2
    exit 1
    ;;
esac

case "$ALLOW_SKIP_MODULE" in
  0|1)
    ;;
  *)
    echo "FAIL: EVIDENCE_GATE_ALLOW_SKIP_MODULE must be 0 or 1" >&2
    exit 1
    ;;
esac

GATE_ARGS=(--mode "$GATE_MODE")
if [[ "$ALLOW_SKIP_MODULE" == "1" ]]; then
  GATE_ARGS+=(--allow-skip-module)
fi

set +e
MODULE_BASELINE_VERSION="${MODULE_BASELINE_VERSION:-092}" \
    "$PYTHON3" tools/perf/evidence_gate.py "${GATE_ARGS[@]}"
rc=$?
set -e

if [[ "$GATE_MODE" == "non-blocking" && $rc -eq 75 ]]; then
  echo "SKIP_NOT_PRESENT: Module benchmarks require NGINX_BIN." >&2
  echo "  Set NGINX_BIN=/path/to/nginx to enable." >&2
  exit 0
fi

if [[ $rc -ne 0 ]]; then
  echo "FAIL: Evidence gate script error (exit $rc)" >&2
  exit 1
fi

exit 0
