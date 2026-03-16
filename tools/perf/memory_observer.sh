#!/usr/bin/env bash
# Memory peak observer for performance profiling.
#
# Monitors a target process and records peak RSS (resident set size).
# On Linux, reads VmHWM from /proc/<pid>/status at process exit — this is
# the OS-tracked lifetime peak (os_reported_peak).
# On macOS, polls `ps -o rss` at the configured interval and tracks the
# maximum observed value (sampled_peak).
#
# The JSON report includes a `memory_peak_method` field so consumers know
# which collection strategy was used.  Cross-platform reports should NOT
# compare absolute values directly because the methods have different
# accuracy characteristics.
#
# Usage:
#   tools/perf/memory_observer.sh --pid <pid> --interval <ms> --output <path>
#
# Options:
#   --pid <pid>         PID of the process to monitor (required)
#   --interval <ms>     Sampling interval in milliseconds (required)
#   --output <path>     Path to write the JSON report (required)
#
# Must be executed from the repository root.

set -euo pipefail

###############################################################################
# Argument parsing
###############################################################################

PID=""
INTERVAL_MS=""
OUTPUT=""

usage() {
  echo >&2 "usage: $0 --pid <pid> --interval <ms> --output <path>"
  exit 1
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pid)
      [[ $# -ge 2 ]] || usage
      PID="$2"
      shift 2
      ;;
    --interval)
      [[ $# -ge 2 ]] || usage
      INTERVAL_MS="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || usage
      OUTPUT="$2"
      shift 2
      ;;
    *)
      echo >&2 "error: unknown argument: $1"
      usage
      ;;
  esac
done

if [[ -z "$PID" || -z "$INTERVAL_MS" || -z "$OUTPUT" ]]; then
  echo >&2 "error: --pid, --interval, and --output are all required"
  usage
fi

# Validate PID is a positive integer.
if ! [[ "$PID" =~ ^[0-9]+$ ]] || [[ "$PID" -eq 0 ]]; then
  echo >&2 "error: --pid must be a positive integer, got: $PID"
  exit 1
fi

# Validate interval is a positive integer.
if ! [[ "$INTERVAL_MS" =~ ^[0-9]+$ ]] || [[ "$INTERVAL_MS" -eq 0 ]]; then
  echo >&2 "error: --interval must be a positive integer (ms), got: $INTERVAL_MS"
  exit 1
fi

###############################################################################
# Platform detection
###############################################################################

OS="$(uname -s)"

case "$OS" in
  Linux)  MEMORY_PEAK_METHOD="os_reported_peak" ;;
  Darwin) MEMORY_PEAK_METHOD="sampled_peak" ;;
  *)
    echo >&2 "error: unsupported platform: $OS"
    exit 1
    ;;
esac

###############################################################################
# Helpers
###############################################################################

log() {
  echo >&2 "$@"
  return 0
}

# Return current time in milliseconds (portable).
now_ms() {
  if [[ "$OS" == "Darwin" ]]; then
    # macOS date does not support %N; use python3 as fallback.
    python3 -c 'import time; print(int(time.time() * 1000))'
  else
    echo $(( $(date +%s%N) / 1000000 ))
  fi
  return 0
}

# Check whether the target process is still alive.
process_alive() {
  kill -0 "$PID" 2>/dev/null
  return $?
}

# Convert interval from milliseconds to a fractional-second sleep argument.
interval_to_sleep() {
  local ms="$1"
  # Use awk for portable floating-point division.
  awk "BEGIN { printf \"%.3f\", $ms / 1000 }"
  return 0
}

###############################################################################
# Pre-flight checks
###############################################################################

if ! process_alive; then
  echo >&2 "error: process $PID not found or not accessible"
  exit 1
fi

SLEEP_SEC="$(interval_to_sleep "$INTERVAL_MS")"

log "memory_observer: monitoring pid=$PID interval=${INTERVAL_MS}ms method=$MEMORY_PEAK_METHOD"

###############################################################################
# Observation loop
###############################################################################

SAMPLES=0
MAX_RSS_KB=0
START_MS="$(now_ms)"

# Trap SIGINT / SIGTERM so we still write the report on interruption.
INTERRUPTED=false
trap 'INTERRUPTED=true' INT TERM

if [[ "$OS" == "Darwin" ]]; then
  # macOS: poll ps -o rss and track the maximum observed value.
  while process_alive && [[ "$INTERRUPTED" == false ]]; do
    # ps -o rss= outputs RSS in kilobytes (no header).
    RSS_KB="$(ps -o rss= -p "$PID" 2>/dev/null || true)"
    RSS_KB="${RSS_KB// /}"  # trim whitespace

    if [[ -n "$RSS_KB" ]] && [[ "$RSS_KB" =~ ^[0-9]+$ ]]; then
      SAMPLES=$((SAMPLES + 1))
      if [[ "$RSS_KB" -gt "$MAX_RSS_KB" ]]; then
        MAX_RSS_KB="$RSS_KB"
      fi
    fi

    sleep "$SLEEP_SEC"
  done
else
  # Linux: poll VmRSS during the loop so we have a fallback if VmHWM is
  # unavailable after the process exits.  The final peak comes from VmHWM
  # when possible (kernel-tracked high-water mark).
  SAMPLED_MAX_RSS_KB=0
  while process_alive && [[ "$INTERRUPTED" == false ]]; do
    SAMPLES=$((SAMPLES + 1))

    # Sample VmRSS from /proc as a fallback for VmHWM.
    PROC_STATUS="/proc/$PID/status"
    if [[ -r "$PROC_STATUS" ]]; then
      VmRSS_LINE="$(grep '^VmRSS:' "$PROC_STATUS" 2>/dev/null || true)"
      if [[ -n "$VmRSS_LINE" ]]; then
        CURRENT_RSS_KB="$(echo "$VmRSS_LINE" | awk '{print $2}')"
        if [[ -n "$CURRENT_RSS_KB" ]] && [[ "$CURRENT_RSS_KB" =~ ^[0-9]+$ ]]; then
          if [[ "$CURRENT_RSS_KB" -gt "$SAMPLED_MAX_RSS_KB" ]]; then
            SAMPLED_MAX_RSS_KB="$CURRENT_RSS_KB"
          fi
        fi
      fi
    fi

    sleep "$SLEEP_SEC"
  done

  # Read VmHWM (peak RSS tracked by the kernel) if the proc entry still
  # exists.  The entry may vanish between the last alive-check and this
  # read, so we tolerate failure gracefully.
  PROC_STATUS="/proc/$PID/status"
  if [[ -r "$PROC_STATUS" ]]; then
    VmHWM_LINE="$(grep '^VmHWM:' "$PROC_STATUS" 2>/dev/null || true)"
    if [[ -n "$VmHWM_LINE" ]]; then
      MAX_RSS_KB="$(echo "$VmHWM_LINE" | awk '{print $2}')"
    fi
  fi

  # If we could not read VmHWM (process already reaped), fall back to the
  # maximum sampled VmRSS observed during the polling loop.
  if [[ "$MAX_RSS_KB" -eq 0 ]] && [[ "$SAMPLED_MAX_RSS_KB" -gt 0 ]]; then
    MAX_RSS_KB="$SAMPLED_MAX_RSS_KB"
    MEMORY_PEAK_METHOD="sampled_peak"
    log "warning: VmHWM unavailable for pid $PID, falling back to sampled VmRSS peak"
  elif [[ "$MAX_RSS_KB" -eq 0 ]]; then
    log "warning: could not read VmHWM or VmRSS for pid $PID (process already exited)"
  fi
fi

END_MS="$(now_ms)"
DURATION_MS=$((END_MS - START_MS))

# Convert KB to bytes.
PEAK_RSS_BYTES=$((MAX_RSS_KB * 1024))

###############################################################################
# Write JSON report
###############################################################################

mkdir -p "$(dirname "$OUTPUT")"

cat > "$OUTPUT" <<JSONEOF
{
  "pid": $PID,
  "interval_ms": $INTERVAL_MS,
  "memory_peak_method": "$MEMORY_PEAK_METHOD",
  "peak_rss_bytes": $PEAK_RSS_BYTES,
  "samples_count": $SAMPLES,
  "duration_ms": $DURATION_MS
}
JSONEOF

log "memory_observer: done — peak_rss_bytes=$PEAK_RSS_BYTES samples=$SAMPLES duration=${DURATION_MS}ms"
log "memory_observer: report written to $OUTPUT"
