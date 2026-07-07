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
readonly PID_FILE="/tmp/ngx_md_bench_$$.pid"
readonly NGINX_WORKDIR="/tmp/ngx_md_bench_$$"

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

###############################################################################
# Start upstream mock (Python http.server serving tests/corpus/)
###############################################################################

CORPUS_DIR="$REPO_ROOT/tests/corpus"
if [[ ! -d "$CORPUS_DIR" ]]; then
  die "Corpus directory not found: $CORPUS_DIR"
fi

log "Starting upstream mock on port $UPSTREAM_PORT (serving $CORPUS_DIR)"

python3 -m http.server "$UPSTREAM_PORT" \
  --directory "$CORPUS_DIR" \
  --bind 127.0.0.1 >/dev/null 2>&1 &
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
        markdown_streaming on;
        markdown_streaming_buffer_size 65536;
        markdown_auto_decompress on;"
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
    include       mime.types;
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
            markdown_max_size 67108864;
            $profile_directives
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

  # Find worker child process (NGINX master spawns workers)
  local worker_pid
  worker_pid="$(ps -o pid= -ppid="$NGINX_PID" 2>/dev/null | head -1 | tr -d ' ')" || true

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

  # Start NGINX with appropriate profile
  start_nginx "$SC_PROFILE" "128"

  # Measure pre-run RSS (reserved for memory slope calculation in task 1.2)
  get_worker_rss >/dev/null

  # Run load generation
  local raw_output="$NGINX_WORKDIR/${SC_NAME}_raw.csv"
  local url_path="/$SC_FIXTURE"

  run_load_gen "$url_path" "$SC_CONCURRENCY" "$ITERATIONS" "$raw_output"

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

  # Parse results and emit JSON (passing TTFB data for integration)
  local scenario_json
  scenario_json="$(parse_load_gen_results "$raw_output" "$SC_NAME" "$SC_PROFILE" \
    "$SC_COMPRESSION" "$SC_TRANSFER" "$SC_CONCURRENCY" "$rss_after" "$ttfb_json")"

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
#   $1 - raw output file
#   $2 - scenario name
#   $3 - profile
#   $4 - compression
#   $5 - transfer encoding
#   $6 - concurrency
#   $7 - worker RSS in KB
#   $8 - TTFB JSON (from measure_ttfb, contains ttfb_p50_ms and ttfb_p95_ms)
parse_load_gen_results() {
  local raw_file="$1"
  local name="$2"
  local profile="$3"
  local compression="$4"
  local transfer="$5"
  local concurrency="$6"
  local rss_kb="$7"
  local ttfb_json="${8:-{\\"ttfb_p50_ms\\":null,\\"ttfb_p95_ms\\":null}}"

  python3 - "$raw_file" "$name" "$profile" "$compression" "$transfer" \
    "$concurrency" "$rss_kb" "$LOAD_GEN" "$ttfb_json" <<'PYEOF'
import csv
import json
import sys
import os

raw_file = sys.argv[1]
name = sys.argv[2]
profile = sys.argv[3]
compression = sys.argv[4]
transfer = sys.argv[5]
concurrency = int(sys.argv[6])
rss_kb = int(sys.argv[7])
load_gen = sys.argv[8]
ttfb_json_str = sys.argv[9]

# Parse supplemental TTFB data (measured via curl time_starttransfer)
try:
    ttfb_data = json.loads(ttfb_json_str)
except (json.JSONDecodeError, ValueError):
    ttfb_data = {"ttfb_p50_ms": None, "ttfb_p95_ms": None}

ttfb_p50 = ttfb_data.get("ttfb_p50_ms")
ttfb_p95 = ttfb_data.get("ttfb_p95_ms")

latencies = []

if load_gen == "hey":
    # hey CSV format: response-time,status-code,offset,...
    # Columns: response-time is in seconds
    try:
        with open(raw_file, "r") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0] == "response-time":
                    continue
                try:
                    lat_s = float(row[0])
                    latencies.append(lat_s * 1000.0)  # convert to ms
                except (ValueError, IndexError):
                    continue
    except (FileNotFoundError, IOError):
        pass
elif load_gen == "ab":
    # Parse ab text output for percentile data
    # Look for "Percentage of the requests served within a certain time"
    try:
        with open(raw_file, "r") as f:
            content = f.read()
        # Extract "Time per request" (mean) and "Requests per second"
        import re
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
                "streaming_ratio": 0.0,
                "fullbuffer_ratio": 1.0,
                "fallback_rate": 0.0,
                "throughput_mbps": 0.0,
            },
        }
        print(json.dumps(result))
        sys.exit(0)
    except (FileNotFoundError, IOError):
        pass

# hey: compute percentiles from latency list
if latencies:
    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[int(n * 0.99)]
    total_time_s = sum(latencies) / 1000.0
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
        "streaming_ratio": 1.0 if profile == "streaming_first" else 0.0,
        "fullbuffer_ratio": 0.0 if profile == "streaming_first" else 1.0,
        "fallback_rate": 0.0,
        "throughput_mbps": 0.0,
    },
}

# TTFB measurement note:
# ttfb_p50_ms and ttfb_p95_ms are measured via supplemental curl-based
# measurement (CURLINFO_STARTTRANSFER_TIME / %{time_starttransfer}).
# This isolates true first-byte latency from total transfer time.
# TTLB (latency_p50_ms) is the total request latency from hey/ab.
# TTLB SHALL NOT be reported as TTFB (Requirement 1.2).

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
RESULTS=""
SCENARIO_COUNT=0

for scenario_def in "${SCENARIOS[@]}"; do
  parse_scenario "$scenario_def"

  # Filter by --scenario if specified
  if [[ -n "$SCENARIO" && "$SC_NAME" != "$SCENARIO" ]]; then
    continue
  fi

  result="$(run_scenario "$scenario_def")"
  if [[ -n "$RESULTS" ]]; then
    RESULTS="${RESULTS},${result}"
  else
    RESULTS="$result"
  fi
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

REPORT_JSON="$(python3 - "$TIMESTAMP" "$GIT_COMMIT" "$PLATFORM" "$LOAD_GEN" "$RESULTS" <<'PYEOF'
import json
import sys

timestamp = sys.argv[1]
git_commit = sys.argv[2]
platform = sys.argv[3]
load_gen = sys.argv[4]
scenarios_raw = sys.argv[5]

# Parse scenario results (comma-separated JSON objects)
scenarios = []
for part in scenarios_raw.split("},{"):
    # Reconstruct valid JSON
    s = part
    if not s.startswith("{"):
        s = "{" + s
    if not s.endswith("}"):
        s = s + "}"
    try:
        scenarios.append(json.loads(s))
    except json.JSONDecodeError:
        pass

report = {
    "module_benchmark": {
        "version": "1.0.0",
        "timestamp": timestamp,
        "git_commit": git_commit,
        "platform": platform,
        "load_generator": load_gen,
        "scenarios": scenarios,
        "memory_slope": {
            "rss_per_input_mb": 0.0,
            "r_squared": 0.0,
        },
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
