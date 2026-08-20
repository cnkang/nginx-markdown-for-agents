#!/bin/bash
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

readonly ACCEPT_MD_HEADER="Accept: text/markdown"
readonly EX_SKIP_NOT_PRESENT=75
readonly SYSTEM_BASENAME="/usr/bin/basename"
readonly SYSTEM_DIRNAME="/usr/bin/dirname"
readonly SYSTEM_READLINK="/usr/bin/readlink"
readonly SYSTEM_STAT="/usr/bin/stat"
readonly SYSTEM_UNAME="/usr/bin/uname"

SCRIPT_DIR="$(cd "$("$SYSTEM_DIRNAME" "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Port range 19100-19199 for isolation (Requirement 1.5)
readonly UPSTREAM_PORT=19100
readonly NGINX_PORT=19101

###############################################################################
# Usage
###############################################################################

# usage prints help text to stderr and exits with the given code.
usage() {
  local exit_code="${1:-1}"
  echo >&2 "usage: $0 [--scenario <name>] [--iterations <N>] [--output <path>] [--concurrency <N>] [--help]"
  echo >&2 ""
  echo >&2 "Environment:"
  echo >&2 "  NGINX_BIN   Path to nginx binary with markdown module (required)"
  echo >&2 ""
  echo >&2 "Scenarios: plain-small, chunked-medium, gzip-large, large-body, streaming-first, gzip-streaming-first, deflate-streaming-first, brotli-streaming-first"
  exit "$exit_code"
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
# Trusted tool resolution (security: no PATH-shadowable helper execution)
###############################################################################

# Trusted system directories in which a PATH-discovered helper executable may
# legitimately live.  A helper discovered outside these roots is rejected.
readonly TRUSTED_TOOL_ROOTS=(
  /usr/sbin
  /usr/bin
  /sbin
  /bin
  /usr/local/sbin
  /usr/local/bin
  /usr/local/opt/nginx/sbin
  /opt/homebrew/bin
  /opt/homebrew/sbin
  /opt/homebrew/opt/nginx/sbin
  /opt/homebrew/Cellar
  /usr/local/Cellar
  /usr/lib/nginx
  # actions/setup-python installs the runner interpreter here on GitHub-hosted
  # Linux runners; the runner image owns this immutable toolcache.
  /opt/hostedtoolcache
)

# is_trusted_tool_path returns 0 when the given path lives directly under one
# of the trusted system executable roots (the literal candidate location, so a
# user-writable symlink pointing into a trusted root stays rejected).
#
# Arguments:
#   $1 - path to check
#
# Returns:
#   0 when trusted; 1 otherwise
is_trusted_tool_path() {
  local path="$1"
  local root=""
  for root in "${TRUSTED_TOOL_ROOTS[@]}"; do
    case "$path" in
      "$root"|"$root"/*)
        return 0
        ;;
      *)
        ;;
    esac
  done
  return 1
}

# stat_owner prints the numeric owner of a file using the host's stat syntax.
stat_owner() {
  local path="$1"
  case "$("$SYSTEM_UNAME" -s 2>/dev/null)" in
    Darwin)
      "$SYSTEM_STAT" -f '%u' "$path"
      return $?
      ;;
    *)
      "$SYSTEM_STAT" -c '%u' "$path"
      return $?
      ;;
  esac
}

# stat_mode prints the numeric permission mode of a file using the host's stat
# syntax. A failed stat is propagated so privileged checks fail closed.
stat_mode() {
  local path="$1"
  case "$("$SYSTEM_UNAME" -s 2>/dev/null)" in
    Darwin)
      "$SYSTEM_STAT" -f '%Lp' "$path"
      return $?
      ;;
    *)
      "$SYSTEM_STAT" -c '%a' "$path"
      return $?
      ;;
  esac
}

# is_secure_root_file returns 0 only when the file is root-owned and has no
# group/other write bits. Metadata lookup failures are unsafe and return 1.
#
# Arguments:
#   $1 - path to check
#
# Returns:
#   0 when secure; 1 otherwise
is_secure_root_file() {
  local path="$1"
  local owner=""
  local mode=""

  owner="$(stat_owner "$path" 2>/dev/null)" || return 1
  [[ "$owner" == "0" ]] || return 1
  mode="$(stat_mode "$path" 2>/dev/null)" || return 1
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  if (( (8#$mode & 8#22) == 0 )); then
    return 0
  fi
  return 1
}

# canonicalize_path resolves symlinks and prints the canonical absolute path.
#
# Arguments:
#   $1 - path to canonicalize (relative paths are resolved from $PWD)
#
# Outputs:
#   Writes the canonical absolute path to stdout
#
# Returns:
#   0 on success; 1 if the input is empty
canonicalize_path() {
  local path="$1"
  local dir=""
  local file=""
  local target=""
  local i=0

  if [[ -z "$path" ]]; then
    return 1
  fi
  if [[ "$path" != /* ]]; then
    path="$(pwd)/$path"
  fi

  dir="$(cd "$("$SYSTEM_DIRNAME" "$path")" 2>/dev/null && pwd -P)" \
    || dir="$("$SYSTEM_DIRNAME" "$path")"
  file="$("$SYSTEM_BASENAME" "$path")"

  while [[ -L "$dir/$file" ]] && [[ $i -lt 40 ]]; do
    target="$("$SYSTEM_READLINK" "$dir/$file" 2>/dev/null)" || break
    if [[ "$target" != /* ]]; then
      target="$dir/$target"
    fi
    dir="$(cd "$("$SYSTEM_DIRNAME" "$target")" 2>/dev/null && pwd -P)" \
      || dir="$("$SYSTEM_DIRNAME" "$target")"
    file="$("$SYSTEM_BASENAME" "$target")"
    i=$((i + 1))
  done

  printf '%s/%s\n' "$dir" "$file"
  return 0
}

# resolve_tool resolves a command name to an approved absolute executable path
# and stores it in the variable named by the second argument.
#
# The candidate must resolve to a regular executable whose literal location is
# under a trusted system executable directory; when running as root the final
# target must additionally be owned by root and not writable by group or other
# users.
#
# Arguments:
#   $1 - command name (e.g. curl, python3, git)
#   $2 - destination variable name (e.g. RESOLVED_CURL)
#
# Returns:
#   0 on success with the resolved path stored in the named variable;
#   1 when the command is missing, not executable, outside a trusted root, or
#   unsafe to run with elevated privileges.
resolve_tool() {
  local name="$1"
  local varname="$2"
  local candidate=""
  local resolved=""

  if ! candidate="$(command -v "$name" 2>/dev/null)"; then
    return 1
  fi

  resolved="$(canonicalize_path "$candidate")"
  if [[ -z "$resolved" ]] || [[ ! -f "$resolved" ]] || [[ ! -x "$resolved" ]]; then
    return 1
  fi

  if ! is_trusted_tool_path "$candidate" \
    || ! is_trusted_tool_path "$resolved"; then
    return 1
  fi

  if [[ "$EUID" -eq 0 ]]; then
    if ! is_secure_root_file "$resolved"; then
      return 1
    fi
  fi

  printf -v "$varname" '%s' "$resolved"
  return 0
}

###############################################################################
# Working directory
###############################################################################

if ! resolve_tool mktemp RESOLVED_MKTEMP; then
  echo >&2 "[bench] ERROR: required command is missing or not from a trusted location: mktemp"
  exit 1
fi
# rm is needed by the cleanup trap on every exit path, so it is resolved here
# (before any early NGINX_BIN preflight exit) rather than only in preflight.
if ! resolve_tool rm RESOLVED_RM; then
  echo >&2 "[bench] ERROR: required command is missing or not from a trusted location: rm"
  exit 1
fi
NGINX_WORKDIR="$("$RESOLVED_MKTEMP" -d "${TMPDIR:-/tmp}/ngx_md_bench.XXXXXX")" \
  || {
    echo >&2 "[bench] ERROR: failed to create benchmark workdir"
    exit 1
  }
readonly NGINX_WORKDIR
readonly PID_FILE="$NGINX_WORKDIR/bench.pid"

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
  "$RESOLVED_RM" -f "$PID_FILE"
  if [[ -d "$NGINX_WORKDIR" ]]; then
    "$RESOLVED_RM" -rf "$NGINX_WORKDIR"
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

required_commands=(
  awk
  cat
  cp
  cut
  date
  git
  head
  mkdir
  mktemp
  rm
  curl
  python3
  sleep
  tr
  uname
  wc
)
# Every helper the script executes is resolved to an approved absolute path
# (never a PATH-shadowable bare name) and stored in a RESOLVED_* variable.
# The mapping is explicit so the loop itself never invokes a helper that has
# not been resolved yet (no bare 'tr' while computing uppercase names).
for required_command in "${required_commands[@]}"; do
  var_name=""
  case "$required_command" in
    awk)     var_name="RESOLVED_AWK" ;;
    cat)     var_name="RESOLVED_CAT" ;;
    cp)      var_name="RESOLVED_CP" ;;
    cut)     var_name="RESOLVED_CUT" ;;
    date)    var_name="RESOLVED_DATE" ;;
    git)     var_name="RESOLVED_GIT" ;;
    head)    var_name="RESOLVED_HEAD" ;;
    mkdir)   var_name="RESOLVED_MKDIR" ;;
    mktemp)  var_name="RESOLVED_MKTEMP" ;;
    rm)      var_name="RESOLVED_RM" ;;
    curl)    var_name="RESOLVED_CURL" ;;
    python3) var_name="RESOLVED_PYTHON3" ;;
    sleep)   var_name="RESOLVED_SLEEP" ;;
    tr)      var_name="RESOLVED_TR" ;;
    uname)   var_name="RESOLVED_UNAME" ;;
    wc)      var_name="RESOLVED_WC" ;;
    *)
      die "unknown required command: $required_command"
      ;;
  esac
  if ! resolve_tool "$required_command" "$var_name"; then
    die "required command is missing or not from a trusted location: $required_command"
  fi
done

RESOLVED_PS=""
# ps is required for RSS sampling.
if ! resolve_tool ps RESOLVED_PS; then
  RSS_SUPPORTED=0
  log "RSS sampling unavailable; memory evidence will fail closed"
else
  # RSS is optional for lifecycle execution, but a report without RSS evidence
  # must fail the evidence gate rather than inventing a memory measurement.
  RSS_SUPPORTED=1
  if ! "$RESOLVED_PS" -o rss= -p "$$" >/dev/null 2>&1; then
    RSS_SUPPORTED=0
    log "RSS sampling unavailable; memory evidence will fail closed"
  fi
fi

if [[ -z "$SCENARIO" || "$SCENARIO" == "brotli-streaming-first" ]]; then
  # Chunked Brotli streaming has no CLI fallback.  Fail during preflight so a
  # missing or drifting Python dependency cannot surface as a late request
  # error.
  if ! BROTLI_VERSION="$("$RESOLVED_PYTHON3" -c 'import brotli; print(brotli.__version__)' 2>/dev/null)"; then
    die "Python Brotli package is required for brotli-streaming-first; install requirements-perf.txt"
  fi
  if [[ "$BROTLI_VERSION" != "1.2.0" ]]; then
    die "Python Brotli version must be 1.2.0, found $BROTLI_VERSION"
  fi
  log "Python Brotli: $BROTLI_VERSION"
fi

# Determine load generator: prefer 'hey' then 'ab'.  The chosen generator is
# resolved to an approved absolute path so request generation never runs a
# PATH-shadowable helper.
LOAD_GEN=""
RESOLVED_LOAD_GEN=""
if resolve_tool hey RESOLVED_LOAD_GEN; then
  LOAD_GEN="hey"
elif resolve_tool ab RESOLVED_LOAD_GEN; then
  LOAD_GEN="ab"
else
  die "No trusted load generator found. Install 'hey' (preferred) or 'ab' (Apache Bench)."
fi

log "Load generator: $LOAD_GEN ($RESOLVED_LOAD_GEN)"

###############################################################################
# Setup working directory
###############################################################################

"$RESOLVED_MKDIR" -p "$NGINX_WORKDIR/logs"
"$RESOLVED_MKDIR" -p "$NGINX_WORKDIR/temp"
PROBE_DIR="$NGINX_WORKDIR/probes"
"$RESOLVED_MKDIR" -p "$PROBE_DIR"
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

"$RESOLVED_PYTHON3" "$SCRIPT_DIR/upstream_mock.py" "$UPSTREAM_PORT" >/dev/null 2>&1 &
UPSTREAM_PID=$!

# Wait for upstream to become ready
_wait_attempts=0
while ! "$RESOLVED_PYTHON3" -c "
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
  "$RESOLVED_SLEEP" 0.1
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
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_set_header Connection \"\";
        markdown_streaming force;"
      ;;
    strict_cache)
      profile_directives="
        markdown_cache_validation full;
        markdown_streaming off;"
      ;;
    balanced)
      profile_directives="
        markdown_streaming auto;"
      ;;
    *)
      log "warning: unknown profile '$profile', using balanced"
      profile_directives=""
      ;;
  esac

  "$RESOLVED_CAT" > "$conf_path" <<CONFEOF
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
            # The 1 MiB Brotli fixture can expand from one very small wire
            # chunk. Keep both conversion and pre-commit replay buffers large
            # enough for that valid first batch while retaining hard caps.
            markdown_limits conversion_memory=64m parser_memory=64m
                conversion_timeout=2s parser_timeout=2s streaming_buffer=16m
                max_inflight=64;
            $profile_directives
        }

        location /markdown-metrics {
            markdown_metrics;
        }

        location = /nginx-markdown/diagnostics {
            markdown_diagnostics on;
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
  while ! "$RESOLVED_PYTHON3" -c "
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
    "$RESOLVED_SLEEP" 0.1
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
      "$RESOLVED_LOAD_GEN" -n "$total_requests" -c "$concurrency" \
        -H "$ACCEPT_MD_HEADER" \
        -o csv \
        "$url" > "$raw_output" 2>/dev/null
      ;;
    ab)
      "$RESOLVED_LOAD_GEN" -n "$total_requests" -c "$concurrency" \
        -H "$ACCEPT_MD_HEADER" \
        "$url" > "$raw_output" 2>/dev/null
      ;;
    *)
      die "Unknown load generator: $LOAD_GEN"
      ;;
  esac

  return 0
}

# probe_expectations returns a visible heading and terminal integrity token.
probe_expectations() {
  local fixture="$1"
  case "$fixture" in
    simple/basic.html)
      echo "Welcome to the Test Page|This is a second paragraph"
      ;;
    simple/tables.html)
      echo "Table Examples|Implemented"
      ;;
    complex/blog-post.html)
      echo "Building an NGINX Module for AI Agents|Share on Facebook"
      ;;
    large/large-1mb.html)
      echo "Repeated Heading|gamma"
      ;;
    *)
      die "No correctness-probe expectations for fixture: $fixture"
      ;;
  esac
  return 0
}

# run_response_probe captures and validates one response after metrics snapshot.
run_response_probe() {
  local name="$1"
  local fixture="$2"
  local compression="$3"
  local url_path="$4"
  local headers_file="$PROBE_DIR/${name}.headers"
  local body_file="$PROBE_DIR/${name}.body"
  local result_file="$PROBE_DIR/${name}.json"
  local http_status="0"
  local curl_exit=0
  local expectations
  local expected_heading
  local expected_tail

  expectations="$(probe_expectations "$fixture")"
  expected_heading="${expectations%%|*}"
  expected_tail="${expectations#*|}"
  http_status="$("$RESOLVED_CURL" -sS -D "$headers_file" -o "$body_file" \
    -w '%{http_code}' -H "$ACCEPT_MD_HEADER" \
    "http://127.0.0.1:${NGINX_PORT}${url_path}")" || curl_exit=$?

  "$RESOLVED_PYTHON3" - "$REPO_ROOT" "$http_status" "$headers_file" "$body_file" \
    "$CORPUS_DIR/$fixture" "$expected_heading" "$expected_tail" \
    "$compression" "$curl_exit" <<'PROBE_PYEOF' > "$result_file"
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from tools.perf.benchmark_validation import (
    parse_curl_header_artifact,
    validate_response_probe,
)

status = int(sys.argv[2])
headers_path = Path(sys.argv[3])
body_path = Path(sys.argv[4])
fixture_path = Path(sys.argv[5])
expected_heading = sys.argv[6]
expected_tail = sys.argv[7]
compressed = sys.argv[8] != "none"
curl_exit = int(sys.argv[9])

header_parse_error = ""
headers = {}
if headers_path.exists():
    try:
        header_status, headers = parse_curl_header_artifact(
            headers_path.read_text(encoding="utf-8", errors="replace")
        )
        if header_status != status:
            header_parse_error = (
                f"status line {header_status} does not match curl status {status}"
            )
    except ValueError as exc:
        header_parse_error = str(exc)
body = body_path.read_bytes() if body_path.exists() else b""
fixture = fixture_path.read_text(encoding="utf-8")
result = validate_response_probe(
    status=status,
    headers=headers,
    body=body,
    expected_heading=expected_heading,
    expected_tail_token=expected_tail,
    expected_tail_count=fixture.count(expected_tail),
    compressed=compressed,
)
result["curl_exit_code"] = curl_exit
result["header_artifact"] = headers_path.name
result["body_artifact"] = body_path.name
if header_parse_error:
    result["verdict"] = "fail"
    result["failure_reason"] = f"header_artifact: {header_parse_error}"
if curl_exit:
    result["verdict"] = "fail"
    result["failure_reason"] = f"curl_exit: {curl_exit}"
print(json.dumps(result))
PROBE_PYEOF

  "$RESOLVED_CAT" "$result_file"
  return 0
}

###############################################################################
# Worker RSS measurement
###############################################################################

# get_worker_rss returns the RSS in KB of the NGINX worker process.  It emits
# zero when the platform cannot provide RSS; downstream evidence validation
# treats that sentinel as missing evidence.
get_worker_rss() {
  if [[ "$RSS_SUPPORTED" -eq 0 ]]; then
    echo "0"
    return 0
  fi

  if [[ -z "$NGINX_PID" ]]; then
    echo "0"
    return 0
  fi

  # Find worker child process (NGINX master spawns workers).
  # Use portable ps output instead of GNU-only -ppid filtering.
  local worker_pid
  worker_pid="$("$RESOLVED_PS" -axo pid=,ppid= 2>/dev/null \
    | "$RESOLVED_AWK" -v ppid="$NGINX_PID" '$2 == ppid { print $1; exit }')" || true

  if [[ -z "$worker_pid" ]]; then
    # Single process mode — use master PID
    worker_pid="$NGINX_PID"
  fi

  local rss_kb
  rss_kb="$("$RESOLVED_PS" -o rss= -p "$worker_pid" 2>/dev/null | "$RESOLVED_TR" -d ' ')" || true

  if [[ -z "$rss_kb" ]] || ! [[ "$rss_kb" =~ ^[0-9]+$ ]]; then
    echo "0"
  else
    echo "$rss_kb"
  fi
  return 0
}

# get_worker_pid returns the PID of the NGINX worker process.
get_worker_pid() {
  if [[ "$RSS_SUPPORTED" -eq 0 ]]; then
    echo ""
    return 0
  fi

  if [[ -z "$NGINX_PID" ]]; then
    echo ""
    return 0
  fi

  local worker_pid
  worker_pid="$("$RESOLVED_PS" -axo pid=,ppid= 2>/dev/null \
    | "$RESOLVED_AWK" -v ppid="$NGINX_PID" '$2 == ppid { print $1; exit }')" || true

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

  if [[ "$RSS_SUPPORTED" -eq 0 ]]; then
    # Preserve the zero sentinel so missing memory evidence fails closed.
    return 0
  fi

  local peak=0
  while true; do
    if ! kill -0 "$worker_pid" 2>/dev/null; then
      break
    fi
    local rss
    rss="$("$RESOLVED_PS" -o rss= -p "$worker_pid" 2>/dev/null | "$RESOLVED_TR" -d ' ')" || true
    if [[ -n "$rss" && "$rss" =~ ^[0-9]+$ && "$rss" -gt "$peak" ]]; then
      peak="$rss"
      # Write immediately so the file is always up-to-date,
      # even if the sampler is killed mid-loop.
      echo "$peak" > "$peak_file"
    fi
    "$RESOLVED_SLEEP" "$interval"
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
  "streaming-first|large/large-1mb.html|streaming_first|none|chunked|20"
  "gzip-streaming-first|large/large-1mb.html|streaming_first|gzip|chunked|10"
  "deflate-streaming-first|large/large-1mb.html|streaming_first|deflate|chunked|10"
  "brotli-streaming-first|large/large-1mb.html|streaming_first|brotli|chunked|10"
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
  if [[ -z "${RESOLVED_CURL:-}" ]]; then
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
    ttfb_s="$("$RESOLVED_CURL" -s -o /dev/null -w '%{time_starttransfer}' \
      -H "$ACCEPT_MD_HEADER" \
      "$url" 2>/dev/null)" || true

    # Validate we got a numeric result
    if [[ -n "$ttfb_s" ]] && [[ "$ttfb_s" =~ ^[0-9]*\.?[0-9]+$ ]]; then
      echo "$ttfb_s" >> "$ttfb_file"
    fi
    i=$((i + 1))
  done

  # Compute p50/p95 from samples
  local sample_count
  sample_count="$("$RESOLVED_WC" -l < "$ttfb_file" | "$RESOLVED_TR" -d ' ')"

  if [[ "$sample_count" -lt 5 ]]; then
    log "  TTFB: insufficient samples ($sample_count); reporting null"
    echo '{"ttfb_p50_ms":null,"ttfb_p95_ms":null}'
    "$RESOLVED_RM" -f "$ttfb_file"
    return 0
  fi

  # Use python3 for percentile calculation (sort + index)
  local ttfb_json
  ttfb_json="$("$RESOLVED_PYTHON3" - "$ttfb_file" <<'TTFB_PYEOF'
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

  "$RESOLVED_RM" -f "$ttfb_file"

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
  SC_NAME="$(printf '%s' "$def" | "$RESOLVED_CUT" -d'|' -f1)"
  SC_FIXTURE="$(printf '%s' "$def" | "$RESOLVED_CUT" -d'|' -f2)"
  SC_PROFILE="$(printf '%s' "$def" | "$RESOLVED_CUT" -d'|' -f3)"
  SC_COMPRESSION="$(printf '%s' "$def" | "$RESOLVED_CUT" -d'|' -f4)"
  SC_TRANSFER="$(printf '%s' "$def" | "$RESOLVED_CUT" -d'|' -f5)"
  SC_CONCURRENCY="$(printf '%s' "$def" | "$RESOLVED_CUT" -d'|' -f6)"

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
  fixture_bytes="$("$RESOLVED_WC" -c < "$CORPUS_DIR/$SC_FIXTURE")"

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

  # dynamically map scenario labels to actual traffic via query params
  if [[ "$SC_COMPRESSION" == "gzip" ]]; then
    url_path="${url_path}?gzip=1"
  elif [[ "$SC_COMPRESSION" == "deflate" ]]; then
    url_path="${url_path}?deflate=1"
  elif [[ "$SC_COMPRESSION" == "brotli" ]]; then
    url_path="${url_path}?brotli=1"
  fi

  if [[ "$SC_TRANSFER" == "chunked" ]]; then
    if [[ "$url_path" == *\?* ]]; then
      url_path="${url_path}&chunked=1"
    else
      url_path="${url_path}?chunked=1"
    fi
  fi

  local load_gen_exit=0
  run_load_gen "$url_path" "$SC_CONCURRENCY" "$ITERATIONS" "$raw_output" \
    || load_gen_exit=$?

  # Stop the background sampler and read peak RSS
  if [[ -n "$sampler_pid" ]] && kill -0 "$sampler_pid" 2>/dev/null; then
    kill "$sampler_pid" 2>/dev/null || true
    wait "$sampler_pid" 2>/dev/null || true
  fi

  # The sampler writes the peak file on every iteration, so the
  # file is up-to-date even after a kill.  A short wait ensures
  # any pending I/O completes before reading.
  "$RESOLVED_SLEEP" 0.1

  local rss_peak="0"
  if [[ -f "$peak_rss_file" ]]; then
    rss_peak="$("$RESOLVED_CAT" "$peak_rss_file" | "$RESOLVED_TR" -d '[:space:]')"
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
  local metrics_exit=0
  metrics_json="$("$RESOLVED_CURL" -fsS --connect-timeout 10 --max-time 60 \
    -H 'Accept: text/plain; version=0.0.4' \
    "http://127.0.0.1:${NGINX_PORT}/markdown-metrics")" || metrics_exit=$?
  local metrics_file="$NGINX_WORKDIR/${SC_NAME}_metrics.json"
  printf '%s\n' "$metrics_json" > "$metrics_file"

  # Fetch structured diagnostics for counters that are intentionally not part
  # of the frozen Prometheus v1 surface.
  log "  Fetching structured NGINX diagnostics..."
  local diagnostics_json
  local diagnostics_exit=0
  diagnostics_json="$("$RESOLVED_CURL" -fsS --connect-timeout 10 --max-time 60 \
    "http://127.0.0.1:${NGINX_PORT}/nginx-markdown/diagnostics")" \
    || diagnostics_exit=$?
  local diagnostics_file="$NGINX_WORKDIR/${SC_NAME}_diagnostics.json"
  printf '%s\n' "$diagnostics_json" > "$diagnostics_file"

  # Run after the metrics snapshot so the probe cannot contaminate evidence.
  log "  Running response correctness probe..."
  local probe_json
  probe_json="$(run_response_probe "$SC_NAME" "$SC_FIXTURE" \
    "$SC_COMPRESSION" "$url_path")"

  # Parse results and emit JSON (passing TTFB data for integration)
  local scenario_json
  scenario_json="$(parse_load_gen_results "$raw_output" "$SC_NAME" "$SC_PROFILE" \
    "$SC_COMPRESSION" "$SC_TRANSFER" "$SC_CONCURRENCY" "$rss_after" \
    "$ttfb_file" "$metrics_file" "$diagnostics_file" "$fixture_bytes" \
    "$rss_baseline" "$rss_peak" "$ITERATIONS" "$load_gen_exit" \
    "$metrics_exit" "$diagnostics_exit")"

  scenario_json="$("$RESOLVED_PYTHON3" - "$scenario_json" "$probe_json" \
    "$REPO_ROOT" <<'MERGE_PYEOF'
import json
import sys

sys.path.insert(0, sys.argv[3])

from tools.perf.benchmark_validation import attach_response_probe

scenario = json.loads(sys.argv[1])
probe = json.loads(sys.argv[2])
print(json.dumps(attach_response_probe(scenario, probe)))
MERGE_PYEOF
)"

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
#   $10 - path to NGINX diagnostics JSON
#   $11 - actual input fixture size in bytes
#   $12 - baseline worker RSS in KB (before load)
#   $13 - peak worker RSS in KB (during load, sampled in background)
#   $14 - configured request iterations
#   $15 - load-generator exit code
#   $16 - metrics curl exit code
#   $17 - diagnostics curl exit code
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
  local diagnostics_file="${10:-}"
  local input_bytes="${11:-0}"
  local rss_baseline_kb="${12:-0}"
  local rss_peak_kb="${13:-0}"
  local iterations="${14:-0}"
  local load_gen_exit="${15:-1}"
  local metrics_exit="${16:-1}"
  local diagnostics_exit="${17:-1}"

  "$RESOLVED_PYTHON3" - "$raw_file" "$name" "$profile" "$compression" "$transfer" \
    "$concurrency" "$rss_kb" "$LOAD_GEN" "$ttfb_file" "$metrics_file" \
    "$diagnostics_file" "$input_bytes" "$rss_baseline_kb" "$rss_peak_kb" \
    "$iterations" "$load_gen_exit" "$metrics_exit" "$diagnostics_exit" \
    "$REPO_ROOT" <<'PYEOF'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[19])

from tools.perf.benchmark_validation import (
    ScenarioResultInput,
    build_scenario_result,
    merge_diagnostics_metrics,
    parse_prometheus_metrics,
)


def read_metrics_file(path):
    try:
        content = Path(path).read_text(encoding="utf-8")
        try:
            value = json.loads(content)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return parse_prometheus_metrics(content)
    except OSError:
        return {}


def read_diagnostics_file(path):
    try:
        content = Path(path).read_text(encoding="utf-8")
        value = json.loads(content)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_ttfb_file(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


nginx_metrics = read_metrics_file(sys.argv[10])
merge_diagnostics_metrics(nginx_metrics, read_diagnostics_file(sys.argv[11]))


result = build_scenario_result(ScenarioResultInput(
    raw_content=Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace"),
    name=sys.argv[2],
    profile=sys.argv[3],
    compression=sys.argv[4],
    transfer_encoding=sys.argv[5],
    concurrency=int(sys.argv[6]),
    worker_rss_kb=int(sys.argv[7]),
    load_generator=sys.argv[8],
    ttfb=read_ttfb_file(sys.argv[9]),
    nginx_metrics=nginx_metrics,
    input_bytes=int(sys.argv[12]),
    baseline_rss_kb=int(sys.argv[13]),
    peak_rss_kb=int(sys.argv[14]),
    iterations=int(sys.argv[15]),
    load_exit_code=int(sys.argv[16]),
    metrics_exit_code=int(sys.argv[17]),
    diagnostics_exit_code=int(sys.argv[18]),
))
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

TIMESTAMP="$("$RESOLVED_PYTHON3" -c 'import datetime; print(datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))' 2>/dev/null || "$RESOLVED_DATE" -u +%Y-%m-%dT%H:%M:%SZ)"
GIT_COMMIT="$("$RESOLVED_GIT" -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")"
PLATFORM="$("$RESOLVED_UNAME" -s | "$RESOLVED_TR" '[:upper:]' '[:lower:]')-$("$RESOLVED_UNAME" -m)"

# Capture the NGINX version for the benchmark environment identity
NGINX_VERSION_INFO="unknown"
if [[ -x "$NGINX_BIN" ]]; then
  NGINX_VERSION_INFO="$("$NGINX_BIN" -v 2>&1 | "$RESOLVED_HEAD" -1 || echo "unknown")"
fi

# Record the resolved toolchain in the report so release gates consume only
# evidence generated under the validated toolchain (perf-evidence F4).
TOOLCHAIN_JSON="{"
TOOLCHAIN_JSON+="\"load_gen\":\"$LOAD_GEN\",\"load_gen_path\":\"$RESOLVED_LOAD_GEN\","
TOOLCHAIN_JSON+="\"git\":\"$RESOLVED_GIT\",\"python3\":\"$RESOLVED_PYTHON3\","
TOOLCHAIN_JSON+="\"curl\":\"$RESOLVED_CURL\",\"ps\":\"${RESOLVED_PS:-}\""
TOOLCHAIN_JSON+="}"

REPORT_JSON="$("$RESOLVED_PYTHON3" - "$TIMESTAMP" "$GIT_COMMIT" "$PLATFORM" "$LOAD_GEN" \
  "$RESULTS_FILE" "$NGINX_VERSION_INFO" "$REPO_ROOT" "$PROBE_DIR" "$TOOLCHAIN_JSON" <<'PYEOF'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[7])

from tools.perf.benchmark_validation import compare_streaming_probe_bodies

timestamp = sys.argv[1]
git_commit = sys.argv[2]
platform = sys.argv[3]
load_gen = sys.argv[4]
results_file = sys.argv[5]
nginx_version = sys.argv[6] if len(sys.argv) > 6 else "unknown"
probe_dir = Path(sys.argv[8])
toolchain = sys.argv[9]

scenarios = []
with open(results_file, encoding="utf-8") as handle:
    lines = [line.strip() for line in handle if line.strip()]

for line in lines:
    try:
        scenarios.append(json.loads(line))
    except json.JSONDecodeError:
        pass

probe_bodies = {
    name: path.read_bytes()
    for name in (
        "streaming-first",
        "gzip-streaming-first",
        "deflate-streaming-first",
        "brotli-streaming-first",
    )
    if (path := probe_dir / f"{name}.body").exists()
}
body_failures = compare_streaming_probe_bodies(probe_bodies)
for scenario in scenarios:
    if reason := body_failures.get(scenario.get("name", "")):
        scenario["status"] = "failed"
        scenario["reason"] = f"response_correctness_failed: {reason}"
        scenario["response_correctness"]["verdict"] = "fail"
        scenario["response_correctness"]["failure_reason"] = reason


def aggregate_metric(rows, metric):
    values = [
        row.get("metrics", {}).get(metric)
        for row in rows
        if row.get("status") != "skipped"
    ]
    if not values:
        return None
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               for value in values):
        return None
    return sum(values)

report = {
    "module_benchmark": {
        "version": "1.0.0",
        "timestamp": timestamp,
        "git_commit": git_commit,
        "platform": platform,
        "load_generator": load_gen,
        "nginx_version": nginx_version,
        "toolchain": json.loads(toolchain),
        "scenarios": scenarios,
        # memory_slope is intentionally omitted here — it is computed
        # by the evidence gate from per-scenario baseline_rss_bytes and
        # peak_rss_bytes.  A placeholder of 0.0 would mask missing
        # evidence as "perfect 0 slope" and must never be written.
    },
    "decompression_coverage": {
        "decompression_streaming_total": aggregate_metric(
            scenarios, "decompression_streaming_total"
        ),
        "decompression_fullbuffer_total": aggregate_metric(
            scenarios, "decompression_fullbuffer_total"
        ),
    }
}

print(json.dumps(report, indent=2))
PYEOF
)"

# Output report
if [[ -n "$OUTPUT_PATH" ]]; then
  "$RESOLVED_MKDIR" -p "$("$SYSTEM_DIRNAME" "$OUTPUT_PATH")"
  echo "$REPORT_JSON" > "$OUTPUT_PATH"
  PROBE_OUTPUT_DIR="${OUTPUT_PATH%.json}-probes"
  "$RESOLVED_MKDIR" -p "$PROBE_OUTPUT_DIR"
  "$RESOLVED_CP" -R "$PROBE_DIR/." "$PROBE_OUTPUT_DIR/"
  log "Report written to: $OUTPUT_PATH"
  log "Probe artifacts written to: $PROBE_OUTPUT_DIR"
else
  echo "$REPORT_JSON"
fi

log "=== Benchmark complete: $SCENARIO_COUNT scenario(s) ==="
exit 0
