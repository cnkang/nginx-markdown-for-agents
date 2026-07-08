#!/usr/bin/env bash
# Run the 0.9.1 performance evidence gate in non-blocking/report mode.

set -uo pipefail

set +e
python3 tools/perf/evidence_gate_091.py --mode non-blocking
rc=$?
set -e

if [[ $rc -eq 75 ]]; then
  echo "SKIP_NOT_PRESENT: Module benchmarks require NGINX_BIN."
  echo "  Set NGINX_BIN=/path/to/nginx to enable."
  exit 75
fi

if [[ $rc -ne 0 ]]; then
  echo "FAIL: Evidence gate script error (exit $rc)" >&2
  exit 1
fi
