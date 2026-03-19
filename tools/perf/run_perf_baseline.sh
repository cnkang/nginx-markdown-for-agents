#!/usr/bin/env bash
# Local performance baseline runner.
#
# Builds the release binary, runs benchmarks, generates a Measurement Report
# JSON, and prints a human-readable summary to stderr.
#
# Usage:
#   tools/perf/run_perf_baseline.sh [OPTIONS]
#
# Options:
#   --tier <name>            Run only the specified sample tier
#   --json-output <path>     Write Measurement Report to <path>
#   --verdict-output <path>  Write Verdict Report to <path>
#   --update-baseline        Extract core metrics and save as baseline
#
# Must be executed from the repository root.

set -euo pipefail

###############################################################################
# Defaults & constants
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPORT_UTILS="$REPO_ROOT/tools/perf/report_utils.py"

PERF_BINARY="components/rust-converter/target/release/examples/perf_baseline"

# Detect platform: lowercase os-arch (e.g. linux-x86_64, darwin-arm64)
if command -v python3 &>/dev/null; then
  PLATFORM="$(python3 "$REPORT_UTILS" detect-platform)"
else
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH="$(uname -m)"
  PLATFORM="${OS}-${ARCH}"
fi

DEFAULT_REPORTS_DIR="perf/reports"
DEFAULT_JSON_OUTPUT="${DEFAULT_REPORTS_DIR}/latest-measurement-${PLATFORM}.json"
DEFAULT_VERDICT_OUTPUT="${DEFAULT_REPORTS_DIR}/latest-verdict-${PLATFORM}.json"

METRICS_SCHEMA="perf/metrics-schema.json"
THRESHOLDS_CONFIG="perf/thresholds.json"
BASELINES_DIR="perf/baselines"

###############################################################################
# Argument parsing
###############################################################################

TIER=""
JSON_OUTPUT=""
VERDICT_OUTPUT=""
UPDATE_BASELINE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)
      TIER="$2"
      shift 2
      ;;
    --json-output)
      JSON_OUTPUT="$2"
      shift 2
      ;;
    --verdict-output)
      VERDICT_OUTPUT="$2"
      shift 2
      ;;
    --update-baseline)
      UPDATE_BASELINE=true
      shift
      ;;
    *)
      echo >&2 "error: unknown argument: $1"
      echo >&2 "usage: $0 [--tier <name>] [--json-output <path>] [--verdict-output <path>] [--update-baseline]"
      exit 1
      ;;
  esac
done

# --update-baseline must run all tiers; reject --tier combination.
if [[ "$UPDATE_BASELINE" == true && -n "$TIER" ]]; then
  echo >&2 "error: --update-baseline and --tier cannot be used together"
  echo >&2 "       --update-baseline must run all sample tiers to produce a complete baseline"
  exit 1
fi

# Resolve default output path when not specified.
if [[ -z "$JSON_OUTPUT" ]]; then
  JSON_OUTPUT="$DEFAULT_JSON_OUTPUT"
fi

# Resolve verdict output path.
if [[ -z "$VERDICT_OUTPUT" ]]; then
  # Derive from json-output: same directory, verdict-<filename>
  JSON_DIR="$(dirname "$JSON_OUTPUT")"
  JSON_BASE="$(basename "$JSON_OUTPUT")"
  VERDICT_OUTPUT="${JSON_DIR}/verdict-${JSON_BASE}"
  # If both were unspecified (json-output is default), use the dedicated default
  if [[ "$JSON_OUTPUT" == "$DEFAULT_JSON_OUTPUT" ]]; then
    VERDICT_OUTPUT="$DEFAULT_VERDICT_OUTPUT"
  fi
fi

###############################################################################
# Helpers
###############################################################################

# log writes its arguments to stderr and returns exit status 0.

log() {
  echo >&2 "$@"
  return 0
}

###############################################################################
# Step 1: Build release binary
###############################################################################

log "=== Building release binary ==="
(cd "$REPO_ROOT/components/rust-converter" && cargo build --release --example perf_baseline)
log "Build complete."

###############################################################################
# Step 2: Ensure output directory exists
###############################################################################

mkdir -p "$(dirname "$JSON_OUTPUT")"

###############################################################################
# Step 3: Run benchmarks
###############################################################################

PERF_ARGS=("--json-output" "$JSON_OUTPUT" "--platform" "$PLATFORM")

if [[ -n "$TIER" ]]; then
  PERF_ARGS+=("--single" "$TIER")
fi

log "=== Running benchmarks (platform: ${PLATFORM}) ==="
if [[ -n "$TIER" ]]; then
  log "  tier: $TIER"
fi
log "  json output: $JSON_OUTPUT"

# Run the benchmark binary.  Redirect its stdout to stderr so the Local
# Runner keeps stdout clean for pipeline consumers (spec requirement 8.6).
# The JSON report is written to the file via --json-output.
"$REPO_ROOT/$PERF_BINARY" "${PERF_ARGS[@]}" >&2

log "=== Benchmarks complete ==="

###############################################################################
# Step 4: Print text summary to stderr
# print_summary prints a human-readable performance summary from a Measurement Report JSON to stderr; it expects the path to the JSON file and uses python3 (logs a warning and returns if python3 is unavailable).

print_summary() {
  local json_file="$1"

  if ! command -v python3 &>/dev/null; then
    log "[warn] python3 not found; skipping text summary"
    return 0
  fi

  python3 - "$json_file" <<'PYEOF'
import json, sys

json_file = sys.argv[1]

with open(json_file) as f:
    report = json.load(f)

platform = report.get('platform', 'unknown')
commit = report.get('git_commit', 'unknown')
ts = report.get('timestamp', 'unknown')

print('', file=sys.stderr)
print('=== Performance Summary ===', file=sys.stderr)
print(f'Platform : {platform}', file=sys.stderr)
print(f'Commit   : {commit}', file=sys.stderr)
print(f'Timestamp: {ts}', file=sys.stderr)
print('', file=sys.stderr)
print(f'{"Tier":<25} {"P50 ms":>10} {"P95 ms":>10} {"P99 ms":>10} {"Req/s":>12} {"MB/s":>10} {"Peak Mem":>14}', file=sys.stderr)
print('-' * 95, file=sys.stderr)

tiers = report.get('tiers', {})
for tier_name in sorted(tiers.keys()):
    t = tiers[tier_name]
    p50 = t.get('p50_ms', 0)
    p95 = t.get('p95_ms', 0)
    p99 = t.get('p99_ms', 0)
    rps = t.get('req_per_s', 0)
    mbps = t.get('input_mb_per_s', 0)
    mem = t.get('peak_memory_bytes', 0)
    mem_str = f'{mem / (1024*1024):.1f} MB' if mem > 0 else 'N/A'
    print(f'{tier_name:<25} {p50:>10.3f} {p95:>10.3f} {p99:>10.3f} {rps:>12.1f} {mbps:>10.2f} {mem_str:>14}', file=sys.stderr)

print('', file=sys.stderr)
print(f'Measurement report: {json_file}', file=sys.stderr)
PYEOF
}

print_summary "$JSON_OUTPUT"

###############################################################################
# Step 5: Threshold engine verdict
###############################################################################

BASELINE_JSON="${BASELINES_DIR}/${PLATFORM}.json"

# run_threshold_engine runs the threshold engine to compare the current measurement JSON against the baseline and write a verdict JSON.
# If python3 is unavailable the function logs a warning and returns with success; otherwise it invokes the Python threshold_engine tool and returns its exit code.
run_threshold_engine() {
  if ! command -v python3 &>/dev/null; then
    log "[warn] python3 not found; skipping threshold judgement"
    return 0
  fi

  mkdir -p "$(dirname "$VERDICT_OUTPUT")"

  log "=== Running threshold engine ==="
  log "  baseline: $BASELINE_JSON"
  log "  verdict output: $VERDICT_OUTPUT"

  local exit_code=0
  python3 "$REPO_ROOT/tools/perf/threshold_engine.py" \
    --baseline "$BASELINE_JSON" \
    --current "$JSON_OUTPUT" \
    --thresholds "$REPO_ROOT/$THRESHOLDS_CONFIG" \
    --metrics-schema "$REPO_ROOT/$METRICS_SCHEMA" \
    --output-json "$VERDICT_OUTPUT" \
    --platform "$PLATFORM" || exit_code=$?

  return $exit_code
}

THRESHOLD_EXIT=0
run_threshold_engine || THRESHOLD_EXIT=$?

###############################################################################
# Step 6: Update baseline (optional)
###############################################################################

if [[ "$UPDATE_BASELINE" == true ]]; then
  BASELINE_UPDATE_DIR="$REPO_ROOT/perf/baselines"
  BASELINE_UPDATE_FILE="${BASELINE_UPDATE_DIR}/${PLATFORM}.json"
  mkdir -p "$BASELINE_UPDATE_DIR"

  log "=== Updating baseline: $BASELINE_UPDATE_FILE ==="

  if ! command -v python3 &>/dev/null; then
    log "[error] python3 is required for --update-baseline"
    exit 1
  fi

  python3 "$REPORT_UTILS" extract-baseline \
    --measurement "$JSON_OUTPUT" \
    --output "$BASELINE_UPDATE_FILE" \
    --platform "$PLATFORM"

  log "=== Baseline updated ==="
fi

log "Done."
exit $THRESHOLD_EXIT
