#!/usr/bin/env bash
# retry_transient.sh -- Run one network or registry command and retry it only
# when its output shows a transient infrastructure failure.
#
# Use it for operations that fetch or push over the network: docker pull,
# docker buildx build (registry and frontend fetches), image pushes, curl
# downloads, apt/apk/dnf fetches, and cargo fetch.  Never wrap a compile,
# test, or validation command: a real failure must fail on the first run.
#
# A failure is transient when the combined output matches one of these
# classes: HTTP 5xx or 429, a timeout, a reset or refused connection, a TLS
# handshake error, a DNS failure, or a registry "unexpected status",
# "toomanyrequests", or EOF error.  Only the last TAIL_LINES lines of output
# are classified, because the failing error is printed last; an earlier
# warning that the tool already recovered from cannot turn a real failure
# into a retry.  Any other failure returns at once with the command's own
# exit code.  The last attempt also returns the command's own
# exit code.  The command output streams through unchanged (stderr is merged
# into stdout), so the log shows every attempt.
#
# Usage:
#   retry_transient.sh [--attempts N] [--delay SECONDS] -- COMMAND [ARGS...]
#
# Options:
#   --attempts N     Total attempts, 1 to 3 (default 3)
#   --delay SECONDS  Delay before the second attempt; it doubles before the
#                    third (default 10, or RETRY_TRANSIENT_DELAY when set)
#
# Exit codes:
#   The exit code of the last attempt that ran; 2 for a usage error.

set -uo pipefail

MAX_ATTEMPTS=3
ATTEMPTS="${MAX_ATTEMPTS}"
DELAY="${RETRY_TRANSIENT_DELAY:-10}"
TAIL_LINES=40

usage() {
    sed -n '2,27p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

log() {
    printf '[retry_transient] %s\n' "$1" >&2
    return 0
}

# Fixed, case-insensitive markers.  Fixed strings keep the classifier free of
# backtracking patterns (Rule 10).
TRANSIENT_MARKERS=(
    "Bad Gateway"
    "Service Unavailable"
    "Gateway Timeout"
    "Gateway Time-out"
    "Internal Server Error"
    "Too Many Requests"
    "toomanyrequests"
    "unexpected status"
    "unexpected EOF"
    "Operation timed out"
    "Connection timed out"
    "Read timed out"
    "i/o timeout"
    "handshake timeout"
    "Curl error (28)"
    "deadline exceeded"
    "Connection reset"
    "connection reset by peer"
    "Connection refused"
    "TLS handshake"
    "tls: handshake"
    "SSL connect error"
    "Could not resolve host"
    "Temporary failure resolving"
    "Temporary failure in name resolution"
    "no such host"
    "server misbehaving"
    "Could not connect to"
    "spurious network error"
    "temporary error (try again later)"
    "\": EOF"
)

# HTTP status classes.  Each pattern names the status after a fixed prefix,
# so no two quantifiers overlap.
TRANSIENT_STATUS_PATTERNS=(
    'returned error: (5[0-9][0-9]|429)'
    'status code:? (5[0-9][0-9]|429)'
    'HTTP/[0-9.]+ (5[0-9][0-9]|429)'
    'Status code: (5[0-9][0-9]|429)'
    'error (5[0-9][0-9]|429)'
)

# Return 0 when the tail of the log file shows a transient failure class.
is_transient() {
    local log_file="$1"
    local tail_text marker pattern
    tail_text="$(tail -n "${TAIL_LINES}" "${log_file}")"
    for marker in "${TRANSIENT_MARKERS[@]}"; do
        if grep -Fqi -- "${marker}" <<<"${tail_text}"; then
            return 0
        fi
    done
    for pattern in "${TRANSIENT_STATUS_PATTERNS[@]}"; do
        if grep -Eqi -- "${pattern}" <<<"${tail_text}"; then
            return 0
        fi
    done
    return 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --attempts)
            [[ $# -ge 2 ]] || { log "--attempts requires a value"; exit 2; }
            ATTEMPTS="$2"
            shift 2
            ;;
        --delay)
            [[ $# -ge 2 ]] || { log "--delay requires a value"; exit 2; }
            DELAY="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            usage
            log "unknown argument: $1"
            exit 2
            ;;
    esac
done

if [[ $# -eq 0 ]]; then
    usage
    log "a command is required after --"
    exit 2
fi
if [[ ! "${ATTEMPTS}" =~ ^[1-3]$ ]]; then
    log "--attempts must be 1, 2, or 3"
    exit 2
fi
if [[ ! "${DELAY}" =~ ^[0-9]+$ ]]; then
    log "--delay must be a whole number of seconds"
    exit 2
fi

LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/retry-transient.XXXXXX")" || {
    log "cannot create a temporary log file"
    exit 2
}
trap 'rm -f "${LOG_FILE}"' EXIT

attempt=1
wait_seconds="${DELAY}"
rc=0
while true; do
    rc=0
    "$@" 2>&1 | tee "${LOG_FILE}" || rc="${PIPESTATUS[0]}"
    if [[ "${rc}" -eq 0 ]]; then
        exit 0
    fi
    if ! is_transient "${LOG_FILE}"; then
        log "attempt ${attempt} failed with exit ${rc}; not a transient failure, no retry"
        exit "${rc}"
    fi
    if [[ "${attempt}" -ge "${ATTEMPTS}" ]]; then
        log "attempt ${attempt} of ${ATTEMPTS} failed with exit ${rc} (transient); giving up"
        exit "${rc}"
    fi
    log "attempt ${attempt} of ${ATTEMPTS} failed with exit ${rc} (transient); retrying in ${wait_seconds}s"
    sleep "${wait_seconds}"
    attempt=$((attempt + 1))
    wait_seconds=$((wait_seconds * 2))
done
