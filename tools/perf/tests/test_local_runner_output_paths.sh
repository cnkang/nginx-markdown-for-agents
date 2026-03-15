#!/usr/bin/env bash
# Integration tests for Local Runner output path parameter combinations.
#
# Verifies that --json-output and --verdict-output produce files at the
# expected locations and that each file is valid JSON.
#
# Must be executed from the repository root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RUNNER="$REPO_ROOT/tools/perf/run_perf_baseline.sh"

TMPDIR_BASE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BASE"' EXIT

PASS=0
FAIL=0

assert_json() {
  local file="$1"
  local label="$2"
  if [[ ! -f "$file" ]]; then
    echo >&2 "FAIL [$label]: file not found: $file"
    FAIL=$((FAIL + 1))
    return 1
  fi
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$file" 2>/dev/null; then
    echo >&2 "FAIL [$label]: invalid JSON: $file"
    FAIL=$((FAIL + 1))
    return 1
  fi
  echo "PASS [$label]: $file"
  PASS=$((PASS + 1))
}

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
PLATFORM="${OS}-${ARCH}"

###############################################################################
# Scenario 1: Only --json-output
# Measurement → specified path
# Verdict → same dir, verdict-<filename>
###############################################################################
echo "=== Scenario 1: Only --json-output ==="
S1_DIR="$TMPDIR_BASE/s1"
mkdir -p "$S1_DIR"
"$RUNNER" --tier small --json-output "$S1_DIR/measurement.json" 2>/dev/null || true
assert_json "$S1_DIR/measurement.json" "S1-measurement"
assert_json "$S1_DIR/verdict-measurement.json" "S1-verdict"

###############################################################################
# Scenario 2: Only --verdict-output
# Measurement → default path
# Verdict → specified path
###############################################################################
echo "=== Scenario 2: Only --verdict-output ==="
S2_DIR="$TMPDIR_BASE/s2"
mkdir -p "$S2_DIR"
# Clean default location first
rm -f "perf/reports/latest-measurement-${PLATFORM}.json"
"$RUNNER" --tier small --verdict-output "$S2_DIR/my-verdict.json" 2>/dev/null || true
assert_json "perf/reports/latest-measurement-${PLATFORM}.json" "S2-measurement-default"
assert_json "$S2_DIR/my-verdict.json" "S2-verdict"

###############################################################################
# Scenario 3: Both --json-output and --verdict-output
###############################################################################
echo "=== Scenario 3: Both specified ==="
S3_DIR="$TMPDIR_BASE/s3"
mkdir -p "$S3_DIR"
"$RUNNER" --tier small --json-output "$S3_DIR/m.json" --verdict-output "$S3_DIR/v.json" 2>/dev/null || true
assert_json "$S3_DIR/m.json" "S3-measurement"
assert_json "$S3_DIR/v.json" "S3-verdict"

###############################################################################
# Scenario 4: Neither specified (defaults)
###############################################################################
echo "=== Scenario 4: Neither specified ==="
rm -f "perf/reports/latest-measurement-${PLATFORM}.json"
rm -f "perf/reports/latest-verdict-${PLATFORM}.json"
"$RUNNER" --tier small 2>/dev/null || true
assert_json "perf/reports/latest-measurement-${PLATFORM}.json" "S4-measurement-default"
assert_json "perf/reports/latest-verdict-${PLATFORM}.json" "S4-verdict-default"

###############################################################################
# Summary
###############################################################################
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
