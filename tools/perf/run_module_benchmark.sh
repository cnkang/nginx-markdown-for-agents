#!/usr/bin/env bash
# Module-level benchmark orchestrator for nginx-markdown-for-agents.
#
# Starts a lightweight upstream mock (Python http.server) serving test corpus
# fixtures, configures NGINX with the markdown filter module, runs load
# generation, and collects performance metrics.
#
# Usage:
#   NGINX_BIN=/path/to/nginx tools/perf/run_module_benchmark.sh [OPTIONS]
#
# Options:
#   --scenario <name>    Run only the named scenario (default: all)
#   --iterations <N>     Number of load-gen iterations per scenario (default: 1000)
#   --output <path>      Write JSON report to <path> (default: stdout)
#   --concurrency <N>    Override default concurrency for all scenarios
#   --help               Show this usage message
#
# Environment:
#   NGINX_BIN            Path to nginx binary with markdown module (required)
#   MODULE_SO            Path to module .so if dynamic (optional)
#
# Exit codes:
#   0   Success
#   1   Error
#   75  SKIP_NOT_PRESENT (NGINX_BIN not set)
#
# Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
# macOS bash 3.2 compatible (Rule 11); no GNU-only flags

set -euo pipefail

###############################################################################
# Constants
###############################################################################

readonly EX_SKIP_NOT_PRESENT=75

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Port range 19100-19199 for isolation (Requirement 1.5)
readonly UPSTREAM_PORT=19100
readonly NGINX_PORT=19101
NGINX_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/ngx_md_bench.XXXXXX")" \
  || {
    echo >&2 "[bench] ERROR: failed to create benchmark workdir"
    exit 1
  }
readonly NGINX_WORKDIR
readonly PID_FILE="$NGINX_WORKDIR/bench.pid"

###############################################################################
# Usage
###############################################################################

# usage prints help text to stderr and exits with the given code.
usage() {
  echo >&2 "usage: $0 [--scenario <name>] [--iterations <N>] [--output <path>] [--concurrency <N>] [--help]"
  echo >&2 ""
  echo >&2 "Environment:"
  echo >&2 "  NGINX_BIN   Path to nginx binary with markdown module (required)"
  echo >&2 ""
  echo >&2 "Scenarios: plain-small, chunked-medium, gzip-large, large-body, streaming-first"
  exit "${1:-1}"
}

###############################################################################
# Helpers
###############################################################################

# log writes a message to stderr.
log() {
  echo >&2 "[bench] $*"
  return 0
}

# die writes an error to stderr and exits with code 1.
die() {
  echo >&2 "[bench] ERROR: $*"
  exit 1
}

###############################################################################
# Cleanup (trap-based, Requirement 1.5)
###############################################################################

UPSTREAM_PID=""
NGINX_PID=""

# cleanup kills any spawned processes and removes temp files.
# shellcheck disable=SC2329
cleanup() {
  local exit_code=$?
  log "Cleaning up..."

  # Stop NGINX
  if [[ -n "$NGINX_PID" ]] && kill -0 "$NGINX_PID" 2>/dev/null; then
    kill "$NGINX_PID" 2>/dev/null || true
    wait "$NGINX_PID" 2>/dev/null || true
  fi

  # Stop upstream mock
  if [[ -n "$UPSTREAM_PID" ]] && kill -0 "$UPSTREAM_PID" 2>/dev/null; then
    kill "$UPSTREAM_PID" 2>/dev/null || true
    wait "$UPSTREAM_PID" 2>/dev/null || true
  fi

  # Remove PID file and temp directory
  rm -f "$PID_FILE"
  if [[ -d "$NGINX_WORKDIR" ]]; then
    rm -rf "$NGINX_WORKDIR"
  fi

  log "Cleanup complete."
  return "$exit_code"
}

trap cleanup EXIT INT TERM

###############################################################################
# Argument parsing
###############################################################################

SCENARIO=""
ITERATIONS=1000
OUTPUT_PATH=""
CONCURRENCY_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      [[ $# -ge 2 ]] || { echo >&2 "error: --scenario requires an argument"; usage 1; }
      SCENARIO="$2"
      shift 2
      ;;
    --iterations)
      [[ $# -ge 2 ]] || { echo >&2 "error: --iterations requires an argument"; usage 1; }
      ITERATIONS="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { echo >&2 "error: --output requires an argument"; usage 1; }
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --concurrency)
      [[ $# -ge 2 ]] || { echo >&2 "error: --concurrency requires an argument"; usage 1; }
      CONCURRENCY_OVERRIDE="$2"
      shift 2
      ;;
    --help)
      usage 0
      ;;
    *)
      echo >&2 "error: unknown argument: $1"
      usage 1
      ;;
  esac
done

###############################################################################
# Pre-flight: NGINX_BIN check (Requirement 1.7)
###############################################################################

if [[ -z "${NGINX_BIN:-}" ]]; then
  log "SKIP_NOT_PRESENT: NGINX_BIN is not set"
  log "Set NGINX_BIN to the path of an nginx binary with the markdown module loaded."
  exit $EX_SKIP_NOT_PRESENT
fi

if [[ ! -x "$NGINX_BIN" ]]; then
  die "NGINX_BIN is set but not executable: $NGINX_BIN"
fi

###############################################################################
# Pre-flight: required tools
###############################################################################

if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required for the upstream mock server"
fi

# Determine load generator: prefer 'hey' then 'ab'
LOAD_GEN=""
if command -v hey >/dev/null 2>&1; then
  LOAD_GEN="hey"
elif command -v ab >/dev/null 2>&1; then
  LOAD_GEN="ab"
else
  die "No load generator found. Install 'hey' (preferred) or 'ab' (Apache Bench)."
fi

log "Load generator: $LOAD_GEN"

###############################################################################
# Setup working directory
###############################################################################

mkdir -p "$NGINX_WORKDIR/logs"
mkdir -p "$NGINX_WORKDIR/temp"
echo "$$" > "$PID_FILE"
log "Workdir: $NGINX_WORKDIR"

###############################################################################
# Start upstream mock (Python http.server serving tests/corpus/)
###############################################################################

CORPUS_DIR="$REPO_ROOT/tests/corpus"
if [[ ! -d "$CORPUS_DIR" ]]; then
  die "Corpus directory not found: $CORPUS_DIR"
fi

log "Starting upstream mock on port $UPSTREAM_PORT (serving $CORPUS_DIR)"

python3 "$SCRIPT_DIR/upstream_mock.py" "$UPSTREAM_PORT" >/dev/null 2>&1 &
UPSTREAM_PID=$!

# Wait for upstream to become ready
_wait_attempts=0
while ! python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.settimeout(0.5)
    s.connect(('127.0.0.1', $UPSTREAM_PORT))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
  _wait_attempts=$((_wait_attempts + 1))
  if [[ $_wait_attempts -ge 20 ]]; then
    die "Upstream mock failed to start on port $UPSTREAM_PORT"
  fi
  sleep 0.1
done

log "Upstream mock ready (pid=$UPSTREAM_PID)"

###############################################################################
# Generate NGINX config (parameterized)
###############################################################################

# generate_nginx_conf writes an NGINX config for the given profile.
# Arguments:
#   $1 - profile: balanced, streaming_first, strict_cache
#   $2 - worker_connections (concurrency ceiling)
generate_nginx_conf() {
  local profile="${1:-balanced}"
  local worker_conns="${2:-128}"
  local conf_path="$NGINX_WORKDIR/nginx.conf"

  # Determine module load directive if MODULE_SO is set
  local load_module_line=""
  if [[ -n "${MODULE_SO:-}" ]]; then
    load_module_line="load_module $MODULE_SO;"
  fi

  # Profile-specific directives
  local profile_directives=""
  case "$profile" in
    streaming_first)
      profile_directives="
        markdown_profile streaming_first;
        markdown_streaming_zero_copy on;"
      ;;
    strict_cache)
      profile_directives="
        markdown_cache_validation full;"
      ;;
    balanced)
      profile_directives=""
      ;;
    *)
      log "warning: unknown profile '$profile', using balanced"
      profile_directives=""
      ;;
  esac

  cat > "$conf_path" <<CONFEOF
# Auto-generated benchmark NGINX config
# Profile: $profile | Worker connections: $worker_conns
daemon off;
worker_processes 1;
error_log $NGINX_WORKDIR/logs/error.log warn;
pid $NGINX_WORKDIR/nginx.pid;
$load_module_line

events {
    worker_connections $worker_conns;
}

http {
    types {
        text/html html;
        text/markdown md;
    }
    default_type  application/octet-stream;

    access_log off;

    upstream backend {
        server 127.0.0.1:$UPSTREAM_PORT;
    }

    server {
        listen 127.0.0.1:$NGINX_PORT;
        server_name localhost;

        location / {
            proxy_pass http://backend;
            proxy_set_header Accept "text/markdown";
            proxy_set_header Host \$host;

            markdown_filter on;
            markdown_limits memory=64m timeout=2s streaming_buffer=256k max_inflight=64;
            $profile_directives
        }

        location /markdown-metrics {
            markdown_metrics;
        }
    }
}
CONFEOF

  echo "$conf_path"
  return 0
}

###############################################################################
# Start NGINX
###############################################################################

# start_nginx generates config and starts nginx in foreground (daemon off).
# Arguments:
#   $1 - profile
#   $2 - worker_connections
start_nginx() {
  local profile="$1"
  local worker_conns="$2"

  local conf_path
  conf_path="$(generate_nginx_conf "$profile" "$worker_conns")"

  # Validate config
  if ! "$NGINX_BIN" -t -c "$conf_path" -p "$NGINX_WORKDIR" 2>/dev/null; then
    log "NGINX config validation failed; attempting with error output:"
    "$NGINX_BIN" -t -c "$conf_path" -p "$NGINX_WORKDIR" >&2 || true
    die "NGINX config validation failed"
  fi

  # Start NGINX (daemon off runs in background via &)
  "$NGINX_BIN" -c "$conf_path" -p "$NGINX_WORKDIR" &
  NGINX_PID=$!

  # Wait for NGINX to be ready
  local _attempts=0
  while ! python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.settimeout(0.5)
    s.connect(('127.0.0.1', $NGINX_PORT))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    _attempts=$((_attempts + 1))
    if [[ $_attempts -ge 30 ]]; then
      die "NGINX failed to start on port $NGINX_PORT"
    fi
    sleep 0.1
  done

  log "NGINX ready (pid=$NGINX_PID, profile=$profile)"
  return 0
}

# stop_nginx gracefully stops the running NGINX instance.
stop_nginx() {
  if [[ -n "$NGINX_PID" ]] && kill -0 "$NGINX_PID" 2>/dev/null; then
    kill -QUIT "$NGINX_PID" 2>/dev/null || true
    wait "$NGINX_PID" 2>/dev/null || true
    NGINX_PID=""
  fi
  return 0
}

###############################################################################
# Load generation
###############################################################################

# run_load_gen runs the load generator against the specified URL path.
# Arguments:
#   $1 - URL path (e.g., /simple/basic.html)
#   $2 - concurrency
#   $3 - total requests
#   $4 - output file for raw results
# Returns: 0 on success
run_load_gen() {
  local url_path="$1"
  local concurrency="$2"
  local total_requests="$3"
  local raw_output="$4"
  local url="http://127.0.0.1:${NGINX_PORT}${url_path}"

  case "$LOAD_GEN" in
    hey)
      hey -n "$total_requests" -c "$concurrency" \
        -H "Accept: text/markdown" \
        -o csv \
        "$url" > "$raw_output" 2>/dev/null
      ;;
    ab)
      ab -n "$total_requests" -c "$concurrency" \
        -H "Accept: text/markdown" \
        "$url" > "$raw_output" 2>/dev/null
      ;;
    *)
      die "Unknown load generator: $LOAD_GEN"
      ;;
  esac

  return 0
}

###############################################################################
# Worker RSS measurement
###############################################################################

# get_worker_rss returns the RSS in KB of the NGINX worker process.
get_worker_rss() {
  if [[ -z "$NGINX_PID" ]]; then
    echo "0"
    return 0
  fi

  # Find worker child process (NGINX master spawns workers).
  # Use portable ps output instead of GNU-only -ppid filtering.
  local worker_pid
  worker_pid="$(ps -axo pid=,ppid= 2>/dev/null \
    | awk -v ppid="$NGINX_PID" '$2 == ppid { print $1; exit }')" || true

  if [[ -z "$worker_pid" ]]; then
    # Single process mode — use master PID
    worker_pid="$NGINX_PID"
  fi

  local rss_kb
  rss_kb="$(ps -o rss= -p "$worker_pid" 2>/dev/null | tr -d ' ')" || true

  if [[ -z "$rss_kb" ]] || ! [[ "$rss_kb" =~ ^[0-9]+$ ]]; then
    echo "0"
  else
    echo "$rss_kb"
  fi
  return 0
}

# get_worker_pid returns the PID of the NGINX worker process.
get_worker_pid() {
  if [[ -z "$NGINX_PID" ]]; then
    echo ""
    return 0
  fi

  local worker_pid
  worker_pid="$(ps -axo pid=,ppid= 2>/dev/null \
    | awk -v ppid="$NGINX_PID" '$2 == ppid { print $1; exit }')" || true

  if [[ -z "$worker_pid" ]]; then
    worker_pid="$NGINX_PID"
  fi

  echo "$worker_pid"
  return 0
}

# sample_rss_background starts a background loop that periodically samples
# the worker RSS and writes the maximum observed value to a file.
#
# This function does NOT background itself — the caller must add `&`
# and capture the PID via `$!`.  This avoids the double-background
# problem where the caller's PID tracks an outer shell function that
# exits immediately, not the inner sampling loop.
#
# The sampler writes the current peak to the file on EVERY iteration
# (atomic single-line write), so the file is always up-to-date even if
# the sampler is killed before the loop exits naturally.
#
# Arguments:
#   $1 - output file for peak RSS (in KB)
#   $2 - worker PID
#   $3 - sample interval in seconds (default 0.1)
sample_rss_background() {
  local peak_file="$1"
  local worker_pid="$2"
  local interval="${3:-0.1}"

  : > "$peak_file"
  echo "0" > "$peak_file"

  local peak=0
  while true; do
    if ! kill -0 "$worker_pid" 2>/dev/null; then
      break
    fi
    local rss
    rss="$(ps -o rss= -p "$worker_pid" 2>/dev/null | tr -d ' ')" || true
    if [[ -n "$rss" && "$rss" =~ ^[0-9]+$ && "$rss" -gt "$peak" ]]; then
      peak="$rss"
      # Write immediately so the file is always up-to-date,
      # even if the sampler is killed mid-loop.
      echo "$peak" > "$peak_file"
    fi
    sleep "$interval"
  done

  # Final write to ensure the last peak is captured
  echo "$peak" > "$peak_file"
  return 0
}

###############################################################################
# Scenario definitions (Requirement 1.3)
###############################################################################

# Each scenario: name|fixture_path|profile|compression|transfer|concurrency
# fixture_path is relative to corpus root (upstream mock document root)
SCENARIOS=(
  "plain-small|simple/basic.html|balanced|none|identity|10"
  "chunked-medium|simple/tables.html|balanced|none|chunked|10"
  "gzip-large|complex/blog-post.html|balanced|gzip|identity|10"
  "large-body|large/large-1mb.html|balanced|none|identity|5"
  "streaming-first|complex/documentation.html|streaming_first|none|chunked|20"
)

###############################################################################
# TTFB measurement (supplemental curl-based, Requirement 1.2)
#
# ab/hey report TTLB (total request latency) and cannot reliably isolate
# first-byte latency from transfer time. This supplemental measurement uses
# curl %{time_starttransfer} which corresponds to CURLINFO_STARTTRANSFER_TIME
# — the time from request start until the first byte of the response body is
# received. This isolates TTFB from TTLB.
#
# CRITICAL: TTLB SHALL NOT be reported as TTFB (Requirement 1.2).
###############################################################################

# TTFB_SAMPLE_COUNT controls how many curl requests are used for TTFB
# percentile calculation. Kept small to avoid adding significant runtime.
readonly TTFB_SAMPLE_COUNT=30

# measure_ttfb runs curl-based TTFB measurement for a URL path.
# Arguments:
#   $1 - URL path (e.g., /simple/basic.html)
# Outputs: JSON fragment with ttfb_p50_ms and ttfb_p95_ms to stdout.
#          Reports null values if curl is unavailable or measurement fails.
measure_ttfb() {
  local url_path="$1"
  local url="http://127.0.0.1:${NGINX_PORT}${url_path}"

  # Verify curl is available
  if ! command -v curl >/dev/null 2>&1; then
    log "  TTFB: curl not available; reporting null (limitation documented)"
    echo '{"ttfb_p50_ms":null,"ttfb_p95_ms":null}'
    return 0
  fi

  # Collect TTFB samples using curl %{time_starttransfer}
  # time_starttransfer = time from start until first byte received (CURLINFO_STARTTRANSFER_TIME)
  # This is the true TTFB — it does NOT include transfer time.
  local ttfb_file="$NGINX_WORKDIR/ttfb_samples.txt"
  local i=0
  : > "$ttfb_file"

  while [[ $i -lt $TTFB_SAMPLE_COUNT ]]; do
    local ttfb_s
    ttfb_s="$(curl -s -o /dev/null -w '%{time_starttransfer}' \
      -H "Accept: text/markdown" \
      "$url" 2>/dev/null)" || true

    # Validate we got a numeric result
    if [[ -n "$ttfb_s" ]] && [[ "$ttfb_s" =~ ^[0-9]*\.?[0-9]+$ ]]; then
      echo "$ttfb_s" >> "$ttfb_file"
    fi
    i=$((i + 1))
  done

  # Compute p50/p95 from samples
  local sample_count
  sample_count="$(wc -l < "$ttfb_file" | tr -d ' ')"

  if [[ "$sample_count" -lt 5 ]]; then
    log "  TTFB: insufficient samples ($sample_count); reporting null"
    echo '{"ttfb_p50_ms":null,"ttfb_p95_ms":null}'
    rm -f "$ttfb_file"
    return 0
  fi

  # Use python3 for percentile calculation (sort + index)
  local ttfb_json
  ttfb_json="$(python3 - "$ttfb_file" <<'TTFB_PYEOF'
import json
import sys

samples_file = sys.argv[1]
samples = []

with open(samples_file, "r") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                samples.append(float(line))
            except ValueError:
                continue

if len(samples) < 5:
    print(json.dumps({"ttfb_p50_ms": None, "ttfb_p95_ms": None}))
    sys.exit(0)

samples.sort()
n = len(samples)
p50_s = samples[int(n * 0.50)]
p95_s = samples[int(n * 0.95)]

# Convert seconds to milliseconds
result = {
    "ttfb_p50_ms": round(p50_s * 1000.0, 3),
    "ttfb_p95_ms": round(p95_s * 1000.0, 3),
}
print(json.dumps(result))
TTFB_PYEOF
  )" || true

  rm -f "$ttfb_file"

  if [[ -z "$ttfb_json" ]]; then
    echo '{"ttfb_p50_ms":null,"ttfb_p95_ms":null}'
  else
    echo "$ttfb_json"
  fi
  return 0
}

###############################################################################
# Scenario execution
###############################################################################

# parse_scenario splits a scenario definition string into variables.
# Sets: SC_NAME, SC_FIXTURE, SC_PROFILE, SC_COMPRESSION, SC_TRANSFER, SC_CONCURRENCY
parse_scenario() {
  local def="$1"
  SC_NAME="$(echo "$def" | cut -d'|' -f1)"
  SC_FIXTURE="$(echo "$def" | cut -d'|' -f2)"
  SC_PROFILE="$(echo "$def" | cut -d'|' -f3)"
  SC_COMPRESSION="$(echo "$def" | cut -d'|' -f4)"
  SC_TRANSFER="$(echo "$def" | cut -d'|' -f5)"
  SC_CONCURRENCY="$(echo "$def" | cut -d'|' -f6)"

  # Apply concurrency override if set
  if [[ -n "$CONCURRENCY_OVERRIDE" ]]; then
    SC_CONCURRENCY="$CONCURRENCY_OVERRIDE"
  fi

  return 0
}

# run_scenario executes a single benchmark scenario.
# Arguments:
#   $1 - scenario definition string
# Outputs: JSON fragment for the scenario to stdout
run_scenario() {
  local def="$1"
  parse_scenario "$def"

  log "--- Scenario: $SC_NAME ---"
  log "  fixture=$SC_FIXTURE profile=$SC_PROFILE compression=$SC_COMPRESSION"
  log "  transfer=$SC_TRANSFER concurrency=$SC_CONCURRENCY iterations=$ITERATIONS"

  # Verify fixture exists
  if [[ ! -f "$CORPUS_DIR/$SC_FIXTURE" ]]; then
    log "  WARNING: fixture not found: $SC_FIXTURE, skipping"
    echo "{\"name\":\"$SC_NAME\",\"status\":\"skipped\",\"reason\":\"fixture_not_found\"}"
    return 0
  fi
  local fixture_bytes
  fixture_bytes="$(wc -c < "$CORPUS_DIR/$SC_FIXTURE")"

  # Start NGINX with appropriate profile
  start_nginx "$SC_PROFILE" "128"

  # Measure baseline RSS before load generation
  local rss_baseline
  rss_baseline="$(get_worker_rss)"

  # Start background RSS sampler for peak tracking
  local worker_pid
  worker_pid="$(get_worker_pid)"
  local peak_rss_file="$NGINX_WORKDIR/${SC_NAME}_peak_rss.txt"
  local sampler_pid=""
  if [[ -n "$worker_pid" ]]; then
    sample_rss_background "$peak_rss_file" "$worker_pid" 0.1 &
    sampler_pid=$!
  else
    echo "0" > "$peak_rss_file"
  fi

  # Run load generation
  local raw_output="$NGINX_WORKDIR/${SC_NAME}_raw.csv"
  local url_path="/$SC_FIXTURE"

  # ponytail: dynamically map scenario labels to actual traffic via query params
  if [[ "$SC_COMPRESSION" == "gzip" ]]; then
    url_path="${url_path}?gzip=1"
  elif [[ "$SC_COMPRESSION" == "deflate" ]]; then
    url_path="${url_path}?deflate=1"
  fi

  if [[ "$SC_TRANSFER" == "chunked" ]]; then
    if [[ "$url_path" == *\?* ]]; then
      url_path="${url_path}&chunked=1"
    else
      url_path="${url_path}?chunked=1"
    fi
  fi

  run_load_gen "$url_path" "$SC_CONCURRENCY" "$ITERATIONS" "$raw_output"

  # Stop the background sampler and read peak RSS
  if [[ -n "$sampler_pid" ]] && kill -0 "$sampler_pid" 2>/dev/null; then
    kill "$sampler_pid" 2>/dev/null || true
    wait "$sampler_pid" 2>/dev/null || true
  fi

  # The sampler writes the peak file on every iteration, so the
  # file is up-to-date even after a kill.  A short wait ensures
  # any pending I/O completes before reading.
  sleep 0.1

  local rss_peak="0"
  if [[ -f "$peak_rss_file" ]]; then
    rss_peak="$(cat "$peak_rss_file" | tr -d '[:space:]')"
    [[ "$rss_peak" =~ ^[0-9]+$ ]] || rss_peak="0"
  fi

  # Measure post-run RSS
  local rss_after
  rss_after="$(get_worker_rss)"

  # Supplemental TTFB measurement using curl (Requirement 1.2)
  # This isolates first-byte latency from transfer time.
  # TTLB SHALL NOT be reported as TTFB.
  log "  Measuring TTFB via curl (time_starttransfer)..."
  local ttfb_json
  ttfb_json="$(measure_ttfb "$url_path")"
  log "  TTFB result: $ttfb_json"
  local ttfb_file="$NGINX_WORKDIR/${SC_NAME}_ttfb.json"
  printf '%s\n' "$ttfb_json" > "$ttfb_file"

  # Fetch real NGINX metrics from metrics endpoint
  log "  Fetching real NGINX metrics..."
  local metrics_json
  metrics_json="$(curl -s -H 'Accept: application/json' "http://127.0.0.1:${NGINX_PORT}/markdown-metrics" || echo '{}')"
  local metrics_file="$NGINX_WORKDIR/${SC_NAME}_metrics.json"
  printf '%s\n' "$metrics_json" > "$metrics_file"

  # Parse results and emit JSON (passing TTFB data for integration)
  local scenario_json
  scenario_json="$(parse_load_gen_results "$raw_output" "$SC_NAME" "$SC_PROFILE" \
    "$SC_COMPRESSION" "$SC_TRANSFER" "$SC_CONCURRENCY" "$rss_after" \
    "$ttfb_file" "$metrics_file" "$fixture_bytes" \
    "$rss_baseline" "$rss_peak")"

  # Stop NGINX for next scenario
  stop_nginx

  echo "$scenario_json"
  return 0
}

###############################################################################
# Result parsing
###############################################################################

# parse_load_gen_results parses raw load-gen output into a JSON object.
# Arguments:
#   $1  - raw output file
#   $2  - scenario name
#   $3  - profile
#   $4  - compression
#   $5  - transfer encoding
#   $6  - concurrency
#   $7  - worker RSS in KB (post-run)
#   $8  - path to TTFB JSON (from measure_ttfb)
#   $9  - path to NGINX metrics JSON
#   $10 - actual input fixture size in bytes
#   $11 - baseline worker RSS in KB (before load)
#   $12 - peak worker RSS in KB (during load, sampled in background)
parse_load_gen_results() {
  local raw_file="$1"
  local name="$2"
  local profile="$3"
  local compression="$4"
  local transfer="$5"
  local concurrency="$6"
  local rss_kb="$7"
  local ttfb_file="${8:-}"
  local metrics_file="${9:-}"
  local input_bytes="${10:-0}"
  local rss_baseline_kb="${11:-0}"
  local rss_peak_kb="${12:-0}"
  local ttfb_file="${8:-}"
  local metrics_file="${9:-}"
  local input_bytes="${10:-0}"

  python3 - "$raw_file" "$name" "$profile" "$compression" "$transfer" \
    "$concurrency" "$rss_kb" "$LOAD_GEN" "$ttfb_file" "$metrics_file" \
    "$input_bytes" "$rss_baseline_kb" "$rss_peak_kb" <<'PYEOF'
import csv
import json
import sys
import os
import re


def read_json_file(path, default):
    if not path:
        return default
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return default

raw_file = sys.argv[1]
name = sys.argv[2]
profile = sys.argv[3]
compression = sys.argv[4]
transfer = sys.argv[5]
concurrency = int(sys.argv[6])
rss_kb = int(sys.argv[7])
load_gen = sys.argv[8]
ttfb_json_path = sys.argv[9]
metrics_json_path = sys.argv[10]
input_bytes = int(sys.argv[11])
rss_baseline_kb = int(sys.argv[12]) if len(sys.argv) > 12 else 0
rss_peak_kb = int(sys.argv[13]) if len(sys.argv) > 13 else 0

# Parse supplemental TTFB data (measured via curl time_starttransfer)
ttfb_data = read_json_file(
    ttfb_json_path,
    {"ttfb_p50_ms": None, "ttfb_p95_ms": None},
)

ttfb_p50 = ttfb_data.get("ttfb_p50_ms")
ttfb_p95 = ttfb_data.get("ttfb_p95_ms")

# Parse real NGINX metrics
nginx_metrics = read_json_file(metrics_json_path, {})

perf_data = nginx_metrics.get("perf", {})
streaming_data = nginx_metrics.get("streaming", {})

# 1. fallback_rate
fallback_total = streaming_data.get("fallback_total", 0)
requests_total = streaming_data.get("requests_total", 0)
precommit_failopen_total = streaming_data.get("precommit_failopen_total", 0)
fallback_rate = (
    float(precommit_failopen_total) / requests_total
    if requests_total > 0
    else 0.0
)

# 2. streaming_ratio and fullbuffer_ratio
sf_hits = nginx_metrics.get("streaming_path_hits", 0)
fb_hits = nginx_metrics.get("fullbuffer_path_hits", 0)
total_hits = sf_hits + fb_hits
if total_hits > 0:
    streaming_ratio = float(sf_hits) / total_hits
    fullbuffer_ratio = float(fb_hits) / total_hits
else:
    # No path-hit data from the metrics endpoint.  For release-blocking
    # evidence, we must NOT synthesize path coverage from the profile
    # name — that would claim a path was exercised without proof.
    # Report None so the evidence gate returns MISSING_EVIDENCE.
    streaming_ratio = None
    fullbuffer_ratio = None

# 3. decompression streaming vs fullbuffer counts
decomp_streaming = perf_data.get("decompression_streaming_total", 0)
decomp_fullbuffer = perf_data.get("decompression_fullbuffer_total", 0)
if decomp_streaming == 0 and decomp_fullbuffer == 0:
    decomp_attempted = nginx_metrics.get("decompressions_attempted", 0)
    if decomp_attempted > 0:
        # Do NOT synthesize decompression path from the profile name.
        # Report only what the metrics endpoint actually observed.
        # The evidence gate will treat 0 as MISSING_EVIDENCE if the
        # scenario requires decompression evidence.
        pass

latencies = []
wall_end_s = 0.0

if load_gen == "hey":
    try:
        with open(raw_file, "r") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0] == "response-time":
                    continue
                try:
                    lat_s = float(row[0])
                    latencies.append(lat_s * 1000.0)  # convert to ms
                    if len(row) > 6:
                        end_s = float(row[6]) + lat_s
                        if end_s > wall_end_s:
                            wall_end_s = end_s
                except (ValueError, IndexError):
                    continue
    except (FileNotFoundError, IOError):
        pass
elif load_gen == "ab":
    try:
        with open(raw_file, "r") as f:
            content = f.read()
        rps_match = re.search(r"Requests per second:\s+([\d.]+)", content)
        p50_match = re.search(r"\s+50%\s+(\d+)", content)
        p95_match = re.search(r"\s+95%\s+(\d+)", content)
        p99_match = re.search(r"\s+99%\s+(\d+)", content)

        rps_val = float(rps_match.group(1)) if rps_match else 0.0
        p50_val = float(p50_match.group(1)) if p50_match else 0.0
        p95_val = float(p95_match.group(1)) if p95_match else 0.0
        p99_val = float(p99_match.group(1)) if p99_match else 0.0

        result = {
            "name": name,
            "profile": profile,
            "compression": compression,
            "transfer_encoding": transfer,
            "concurrency": concurrency,
            "status": "completed",
            "metrics": {
                "rps": rps_val,
                "latency_p50_ms": p50_val,
                "latency_p95_ms": p95_val,
                "latency_p99_ms": p99_val,
                "ttfb_p50_ms": ttfb_p50,
                "ttfb_p95_ms": ttfb_p95,
                "ttlb_p50_ms": p50_val,
                "worker_rss_mb": rss_kb / 1024.0,
                "baseline_rss_bytes": rss_baseline_kb * 1024,
                "peak_rss_bytes": rss_peak_kb * 1024,
                "input_bytes": input_bytes,
                "streaming_path_hits": sf_hits,
                "fullbuffer_path_hits": fb_hits,
                "streaming_ratio": streaming_ratio,
                "fullbuffer_ratio": fullbuffer_ratio,
                "fallback_rate": fallback_rate,
                "streaming_fallback_total": fallback_total,
                "streaming_requests_total": requests_total,
                "precommit_failopen_total": precommit_failopen_total,
                "throughput_mbps": 0.0,
                "decompression_streaming_total": decomp_streaming,
                "decompression_fullbuffer_total": decomp_fullbuffer,
                "zero_copy_output_total": perf_data.get("zero_copy_output_total", 0),
                "copied_output_total": perf_data.get("copied_output_total", 0),
                "pending_output_high_watermark_bytes": perf_data.get("pending_output_high_watermark_bytes", 0),
            },
        }
        print(json.dumps(result))
        sys.exit(0)
    except (FileNotFoundError, IOError):
        pass

if latencies:
    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    total_time_s = wall_end_s if wall_end_s > 0 else sum(latencies) / 1000.0
    rps = n / total_time_s if total_time_s > 0 else 0.0
else:
    p50 = p95 = p99 = 0.0
    rps = 0.0

result = {
    "name": name,
    "profile": profile,
    "compression": compression,
    "transfer_encoding": transfer,
    "concurrency": concurrency,
    "status": "completed",
    "metrics": {
        "rps": rps,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "latency_p99_ms": p99,
        "ttfb_p50_ms": ttfb_p50,
        "ttfb_p95_ms": ttfb_p95,
        "ttlb_p50_ms": p50,
        "worker_rss_mb": rss_kb / 1024.0,
        "baseline_rss_bytes": rss_baseline_kb * 1024,
        "peak_rss_bytes": rss_peak_kb * 1024,
        "input_bytes": input_bytes,
        "streaming_path_hits": sf_hits,
        "fullbuffer_path_hits": fb_hits,
        "streaming_ratio": streaming_ratio,
        "fullbuffer_ratio": fullbuffer_ratio,
        "fallback_rate": fallback_rate,
        "streaming_fallback_total": fallback_total,
        "streaming_requests_total": requests_total,
        "precommit_failopen_total": precommit_failopen_total,
        "throughput_mbps": 0.0,
        "decompression_streaming_total": decomp_streaming,
        "decompression_fullbuffer_total": decomp_fullbuffer,
        "zero_copy_output_total": perf_data.get("zero_copy_output_total", 0),
        "copied_output_total": perf_data.get("copied_output_total", 0),
        "pending_output_high_watermark_bytes": perf_data.get("pending_output_high_watermark_bytes", 0),
    },
}

print(json.dumps(result))
PYEOF

  return 0
}

###############################################################################
# Main execution
###############################################################################

log "=== Module-Level Benchmark Harness ==="
log "NGINX_BIN=$NGINX_BIN"
log "Load generator: $LOAD_GEN"
log "Upstream port: $UPSTREAM_PORT"
log "NGINX port: $NGINX_PORT"
log "PID file: $PID_FILE"
log "Iterations: $ITERATIONS"

# Collect scenario results
RESULTS_FILE="$NGINX_WORKDIR/scenario-results.jsonl"
SCENARIO_COUNT=0
: > "$RESULTS_FILE"

for scenario_def in "${SCENARIOS[@]}"; do
  parse_scenario "$scenario_def"

  # Filter by --scenario if specified
  if [[ -n "$SCENARIO" && "$SC_NAME" != "$SCENARIO" ]]; then
    continue
  fi

  result="$(run_scenario "$scenario_def")"
  printf '%s\n' "$result" >> "$RESULTS_FILE"
  SCENARIO_COUNT=$((SCENARIO_COUNT + 1))
done

if [[ $SCENARIO_COUNT -eq 0 ]]; then
  if [[ -n "$SCENARIO" ]]; then
    die "Unknown scenario: $SCENARIO"
  else
    die "No scenarios to run"
  fi
fi

###############################################################################
# Assemble final JSON report
###############################################################################

TIMESTAMP="$(python3 -c 'import datetime; print(datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))' 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"

# Capture the NGINX version for the benchmark environment identity
NGINX_VERSION_INFO="unknown"
if [[ -x "$NGINX_BIN" ]]; then
  NGINX_VERSION_INFO="$("$NGINX_BIN" -v 2>&1 | head -1 || echo "unknown")"
fi

REPORT_JSON="$(python3 - "$TIMESTAMP" "$GIT_COMMIT" "$PLATFORM" "$LOAD_GEN" "$RESULTS_FILE" "$NGINX_VERSION_INFO" <<'PYEOF'
import json
import sys

timestamp = sys.argv[1]
git_commit = sys.argv[2]
platform = sys.argv[3]
load_gen = sys.argv[4]
results_file = sys.argv[5]
nginx_version = sys.argv[6] if len(sys.argv) > 6 else "unknown"

scenarios = []
with open(results_file, encoding="utf-8") as handle:
    lines = [line.strip() for line in handle if line.strip()]

for line in lines:
    try:
        scenarios.append(json.loads(line))
    except json.JSONDecodeError:
        pass

report = {
    "module_benchmark": {
        "version": "1.0.0",
        "timestamp": timestamp,
        "git_commit": git_commit,
        "platform": platform,
        "load_generator": load_gen,
        "nginx_version": nginx_version,
        "scenarios": scenarios,
        # memory_slope is intentionally omitted here — it is computed
        # by the evidence gate from per-scenario baseline_rss_bytes and
        # peak_rss_bytes.  A placeholder of 0.0 would mask missing
        # evidence as "perfect 0 slope" and must never be written.
    },
    "decompression_coverage": {
        "decompression_streaming_total": sum(s.get("metrics", {}).get("decompression_streaming_total", 0) for s in scenarios),
        "decompression_fullbuffer_total": sum(s.get("metrics", {}).get("decompression_fullbuffer_total", 0) for s in scenarios),
    }
}

print(json.dumps(report, indent=2))
PYEOF
)"

# Output report
if [[ -n "$OUTPUT_PATH" ]]; then
  mkdir -p "$(dirname "$OUTPUT_PATH")"
  echo "$REPORT_JSON" > "$OUTPUT_PATH"
  log "Report written to: $OUTPUT_PATH"
else
  echo "$REPORT_JSON"
fi

log "=== Benchmark complete: $SCENARIO_COUNT scenario(s) ==="
exit 0
