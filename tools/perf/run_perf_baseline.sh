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
#   --engine <mode>          Benchmark engine: full-buffer, streaming, both (default: full-buffer)
#   --json-output <path>     Write Measurement Report to <path>
#   --verdict-output <path>  Write Verdict Report to <path>
#   --update-baseline        Extract core metrics and save as baseline
#   --generate-evidence-pack Generate evidence pack JSON
#   --evidence-output <path> Evidence pack output path (default: perf/reports/evidence-pack-{platform}-{timestamp}.json)
#   --parity-report <path>   Path to parity report for dual-threshold evaluation
#
# Must be executed from the repository root.

set -euo pipefail

###############################################################################
# Defaults & constants
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPORT_UTILS="$REPO_ROOT/tools/perf/report_utils.py"
EVIDENCE_GENERATOR="$REPO_ROOT/tools/perf/evidence_pack_generator.py"

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
DEFAULT_EVIDENCE_OUTPUT="${DEFAULT_REPORTS_DIR}/evidence-pack-${PLATFORM}.json"

METRICS_SCHEMA="perf/metrics-schema.json"
THRESHOLDS_CONFIG="perf/thresholds.json"
EVIDENCE_TARGETS="perf/streaming-evidence-targets.json"
BASELINES_DIR="perf/baselines"

###############################################################################
# Argument parsing
###############################################################################

TIER=""
ENGINE="full-buffer"
JSON_OUTPUT=""
VERDICT_OUTPUT=""
EVIDENCE_OUTPUT=""
PARITY_REPORT=""
UPDATE_BASELINE=false
GENERATE_EVIDENCE_PACK=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)
      TIER="$2"
      shift 2
      ;;
    --engine)
      ENGINE="$2"
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
    --generate-evidence-pack)
      GENERATE_EVIDENCE_PACK=true
      shift
      ;;
    --evidence-output)
      EVIDENCE_OUTPUT="$2"
      shift 2
      ;;
    --parity-report)
      PARITY_REPORT="$2"
      shift 2
      ;;
    *)
      echo >&2 "error: unknown argument: $1"
      echo >&2 "usage: $0 [--tier <name>] [--engine <mode>] [--json-output <path>] [--verdict-output <path>] [--update-baseline] [--generate-evidence-pack] [--evidence-output <path>] [--parity-report <path>]"
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

# --generate-evidence-pack requires both engines for meaningful comparison.
if [[ "$GENERATE_EVIDENCE_PACK" == true && "$ENGINE" != "both" ]]; then
  echo >&2 "error: --generate-evidence-pack requires --engine both"
  echo >&2 "       evidence pack needs both full-buffer and streaming metrics for comparison"
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

PERF_ARGS=("--engine" "$ENGINE" "--json-output" "$JSON_OUTPUT" "--platform" "$PLATFORM")

if [[ -n "$TIER" ]]; then
  PERF_ARGS+=("--single" "$TIER")
fi

log "=== Running benchmarks (platform: ${PLATFORM}) ==="
log "  engine: $ENGINE"
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
engine = report.get('engine', 'full-buffer')

print('', file=sys.stderr)
print('=== Performance Summary ===', file=sys.stderr)
print(f'Platform : {platform}', file=sys.stderr)
print(f'Commit   : {commit}', file=sys.stderr)
print(f'Timestamp: {ts}', file=sys.stderr)
print(f'Engine   : {engine}', file=sys.stderr)
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

# Show streaming metrics if present
streaming_metrics = report.get('streaming_metrics', {})
if streaming_metrics:
    print('', file=sys.stderr)
    print('=== Streaming Metrics ===', file=sys.stderr)
    print(f'{"Tier":<25} {"TTFB ms":>10} {"TTLB ms":>10} {"CPU ms":>10} {"Flushes":>10} {"Fallback%":>12} {"Peak Mem":>14}', file=sys.stderr)
    print('-' * 95, file=sys.stderr)
    for tier_name in sorted(streaming_metrics.keys()):
        sm = streaming_metrics[tier_name]
        ttfb = sm.get('ttfb_ms', 0)
        ttlb = sm.get('ttlb_ms', 0)
        cpu = sm.get('cpu_time_ms', 0)
        flushes = sm.get('flush_count', 0)
        fallback = sm.get('fallback_rate', 0) * 100
        mem = sm.get('peak_memory_bytes', 0)
        mem_str = f'{mem / (1024*1024):.1f} MB' if mem > 0 else 'N/A'
        print(f'{tier_name:<25} {ttfb:>10.3f} {ttlb:>10.3f} {cpu:>10.3f} {flushes:>10} {fallback:>11.2f}% {mem_str:>14}', file=sys.stderr)

print('', file=sys.stderr)
print(f'Measurement report: {json_file}', file=sys.stderr)
PYEOF
}

print_summary "$JSON_OUTPUT"

###############################################################################
# Step 4b: Generate Evidence Pack (optional)
###############################################################################

if [[ "$GENERATE_EVIDENCE_PACK" == true ]]; then
  # Resolve evidence output path
  if [[ -z "$EVIDENCE_OUTPUT" ]]; then
    TIMESTAMP="$(python3 -c 'import datetime; print(datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"))' 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)"
    EVIDENCE_OUTPUT="${DEFAULT_EVIDENCE_OUTPUT/.json/-${TIMESTAMP}.json}"
  fi

  mkdir -p "$(dirname "$EVIDENCE_OUTPUT")"

  log "=== Generating Evidence Pack ==="
  log "  evidence output: $EVIDENCE_OUTPUT"

  if [[ -n "$PARITY_REPORT" ]]; then
    log "  parity report: $PARITY_REPORT"
  fi

  if ! command -v python3 &>/dev/null; then
    log "[error] python3 is required for --generate-evidence-pack"
    exit 1
  fi

  # Run evidence pack generator
  EVIDENCE_ARGS=(
    "$EVIDENCE_GENERATOR"
    "--fullbuffer-report" "$JSON_OUTPUT"
    "--streaming-report" "$JSON_OUTPUT"
    "--evidence-targets" "$REPO_ROOT/$EVIDENCE_TARGETS"
    "--output" "$EVIDENCE_OUTPUT"
  )

  if [[ -n "$PARITY_REPORT" && -f "$PARITY_REPORT" ]]; then
    EVIDENCE_ARGS+=("--parity-report" "$PARITY_REPORT")
  fi

  EVIDENCE_EXIT=0
  python3 "${EVIDENCE_ARGS[@]}" || EVIDENCE_EXIT=$?

  # Exit code 0 = GO verdict, 1 = NO_GO verdict (valid outcome),
  # exit code >= 2 = generator error (missing file, JSON parse, etc.).
  if [[ $EVIDENCE_EXIT -ge 2 ]]; then
    log "[error] evidence pack generator failed (exit code: $EVIDENCE_EXIT)"
    exit $EVIDENCE_EXIT
  fi

  if [[ $EVIDENCE_EXIT -eq 1 ]]; then
    log "[warn] streaming evidence verdict is NO_GO; continuing to threshold engine"
  fi

  log "=== Evidence Pack generated ==="
  log "  output: $EVIDENCE_OUTPUT"
fi

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

# Final exit code: combine threshold engine and evidence pack verdicts.
# If evidence pack was generated and returned NO_GO (exit 1), propagate
# that even if the threshold engine passed (exit 0).  Errors (exit >= 2)
# from either component are already handled above.
FINAL_EXIT=$THRESHOLD_EXIT
if [[ "$GENERATE_EVIDENCE_PACK" == true && $EVIDENCE_EXIT -eq 1 && $FINAL_EXIT -eq 0 ]]; then
  log "[info] overriding exit code: evidence pack verdict is NO_GO"
  FINAL_EXIT=1
fi
exit $FINAL_EXIT
