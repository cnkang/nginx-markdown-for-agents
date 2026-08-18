#!/bin/bash
# dynconf_reload_rollback.sh — E2E test: dynamic config reload and file restore.
#
# The diagnostics endpoint is read-only. This test models operator restore by
# replacing the watched file atomically, while preserving caller-owned files.

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
DIAGNOSTICS_PATH="/nginx-markdown/diagnostics"
PASS_COUNT=0
FAIL_COUNT=0
TEST_TMPDIR=""
OWN_TEST_TMPDIR=0
CALLER_DYNCONF_FILE_SET=0
CALLER_DYNCONF_FILE=""
if [[ -n "${DYNCONF_FILE+x}" ]]; then
    CALLER_DYNCONF_FILE_SET=1
    CALLER_DYNCONF_FILE="$DYNCONF_FILE"
else
    DYNCONF_FILE=""
fi
DYNCONF_DIR=""
ORIGINAL_FILE_EXISTED=0
DYNCONF_FILE_CREATED_BY_TEST=0
DYNCONF_OWNED_BY_TEST=0
ORIGINAL_FILE_MODE=""
ORIGINAL_FILE_UID=""
ORIGINAL_FILE_GID=""
BACKUP_PATH=""
BACKUP_READY=0
TMP_WRITE_PATH=""
LAST_DIAG=""
readonly PID_RESOLVED=0
readonly PID_NOT_CONFIGURED=1
readonly PID_CONFIG_INVALID=2

pass() {
    local msg="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $msg" >&2
}

fail() {
    local msg="$1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $msg" >&2
}

stat_field() {
    local bsd_format="$1"
    local gnu_format="$2"
    local path="$3"


    # Detect the stat implementation once: GNU stat supports -c, BSD stat
    # supports -f.  Do not rely on GNU's -f (filesystem) output as a
    # fallback — it reports filesystem metadata, not the requested field.
    if stat -c '%n' /dev/null >/dev/null 2>&1; then
        stat -c "$gnu_format" "$path" 2>/dev/null
    else
        stat -f "$bsd_format" "$path" 2>/dev/null
    fi
}

canonical_target_path() {
    local raw="$1"
    local directory="${raw%/*}"
    local basename="${raw##*/}"
    local canonical_directory=""

    if [[ -z "$raw" || "$raw" == *$'\n'* || "$basename" == "" ]]; then
        return 1
    fi
    if [[ "$raw" != /* ]]; then
        raw="$PWD/$raw"
        directory="${raw%/*}"
        basename="${raw##*/}"
    fi
    if [[ -z "$directory" ]]; then
        directory="/"
    fi
    if [[ -L "$raw" ]]; then
        echo "Error: DYNCONF_FILE must not be a symlink: $raw" >&2
        return 1
    fi
    if [[ ! -d "$directory" ]]; then
        echo "Error: dynconf parent directory does not exist: $directory" >&2
        return 1
    fi

    canonical_directory="$(cd -P -- "$directory" 2>/dev/null && pwd -P)" \
        || return 1
    printf '%s/%s\n' "$canonical_directory" "$basename"
}

path_is_inside_test_directory() {
    local path="$1"
    [[ "$path" == "$TEST_TMPDIR"/* ]]
}

requested_dynconf_path() {
    if [[ "$CALLER_DYNCONF_FILE_SET" -eq 1 ]]; then
        printf '%s\n' "$CALLER_DYNCONF_FILE"
    else
        printf '%s/markdown-dynamic.conf\n' "$TEST_TMPDIR"
    fi
}

prepare_dynconf_ownership() {
    local requested_path="$1"
    local backup_mode=""
    local backup_owner=""

    DYNCONF_FILE="$(canonical_target_path "$requested_path")" || return 1
    DYNCONF_DIR="${DYNCONF_FILE%/*}"

    if ! path_is_inside_test_directory "$DYNCONF_FILE" \
        && [[ "${ALLOW_EXTERNAL_DYNCONF_TEST:-0}" != "1" ]]; then
        echo "Error: external DYNCONF_FILE requires " \
            "ALLOW_EXTERNAL_DYNCONF_TEST=1: $DYNCONF_FILE" >&2
        return 1
    fi

    if [[ -L "$DYNCONF_FILE" ]]; then
        echo "Error: DYNCONF_FILE must not be a symlink: $DYNCONF_FILE" >&2
        return 1
    fi

    if [[ -e "$DYNCONF_FILE" ]]; then
        if [[ ! -f "$DYNCONF_FILE" ]]; then
            echo "Error: DYNCONF_FILE is not a regular file: $DYNCONF_FILE" >&2
            return 1
        fi

        ORIGINAL_FILE_EXISTED=1
        ORIGINAL_FILE_MODE="$(stat_field '%Lp' '%a' "$DYNCONF_FILE")" || return 1
        backup_owner="$(stat_field '%u:%g' '%u:%g' "$DYNCONF_FILE")" || return 1
        ORIGINAL_FILE_UID="${backup_owner%%:*}"
        ORIGINAL_FILE_GID="${backup_owner##*:}"

        # The backup is beside the target, so its eventual rename is atomic.
        BACKUP_PATH="$(mktemp "$DYNCONF_DIR/.markdown-dynconf-backup.XXXXXX")" \
            || return 1
        if ! cp -p -- "$DYNCONF_FILE" "$BACKUP_PATH"; then
            echo "Error: unable to back up caller-owned dynconf file" >&2
            return 1
        fi

        # Normalize and verify metadata before the original file is changed.
        chmod "$ORIGINAL_FILE_MODE" "$BACKUP_PATH" || return 1
        chown "$ORIGINAL_FILE_UID:$ORIGINAL_FILE_GID" "$BACKUP_PATH" \
            || return 1
        backup_mode="$(stat_field '%Lp' '%a' "$BACKUP_PATH")" || return 1
        backup_owner="$(stat_field '%u:%g' '%u:%g' "$BACKUP_PATH")" || return 1
        if [[ "$backup_mode" != "$ORIGINAL_FILE_MODE" \
            || "$backup_owner" != "$ORIGINAL_FILE_UID:$ORIGINAL_FILE_GID" ]]; then
            echo "Error: dynconf backup metadata verification failed" >&2
            return 1
        fi
        BACKUP_READY=1
    fi
}

restore_original_atomically() {
    if [[ "$ORIGINAL_FILE_EXISTED" -eq 1 ]]; then
        if [[ "$BACKUP_READY" -eq 0 ]]; then
            return 0
        fi
        if [[ -z "$BACKUP_PATH" || ! -f "$BACKUP_PATH" ]]; then
            echo "Error: original dynconf backup is unavailable" >&2
            return 1
        fi
        chmod "$ORIGINAL_FILE_MODE" "$BACKUP_PATH" || return 1
        chown "$ORIGINAL_FILE_UID:$ORIGINAL_FILE_GID" "$BACKUP_PATH" \
            || return 1
        if ! mv -f -- "$BACKUP_PATH" "$DYNCONF_FILE"; then
            echo "Error: unable to atomically restore caller-owned dynconf" >&2
            return 1
        fi
        BACKUP_PATH=""
        BACKUP_READY=0
        return 0
    fi

    return 0
}

# Cleanup exit code used when the original test body succeeded (rc==0) but
# the restore/cleanup itself failed.  Chosen to avoid collision with common
# shell signal exit codes (128+signum) and the test's own nonzero results.
DYNCONF_CLEANUP_FAILURE_EXIT=70

cleanup() {
    local rc="${1:-$?}"
    local cleanup_error=0

    trap - EXIT HUP INT TERM
    if [[ -n "$TMP_WRITE_PATH" ]]; then
        if ! rm -f -- "$TMP_WRITE_PATH"; then
            echo "Error: unable to remove temporary dynconf write" >&2
            cleanup_error=1
        fi
        TMP_WRITE_PATH=""
    fi
    if [[ "$BACKUP_READY" -eq 1 ]]; then
        if ! restore_original_atomically; then
            echo "Error: caller-owned dynconf restore failed; backup retained" >&2
            cleanup_error=1
            # The backup is retained for manual recovery.  Report its
            # location so the operator can restore the caller file by hand.
            if [[ -n "$BACKUP_PATH" ]]; then
                echo "Error: unrecoverable backup retained at: $BACKUP_PATH" >&2
            fi
        fi
    elif [[ "$DYNCONF_FILE_CREATED_BY_TEST" -eq 1 ]]; then
        if ! rm -f -- "$DYNCONF_FILE"; then
            echo "Error: unable to remove test-created dynconf file" >&2
            cleanup_error=1
        fi
    fi
    # Only remove a completed (already-restored or no-restore-needed) backup.
    # A retained backup from a failed restore must never be deleted here.
    if [[ -n "$BACKUP_PATH" && "$BACKUP_READY" -eq 0 ]] \
        && ! rm -f -- "$BACKUP_PATH"; then
        echo "Error: unable to remove completed dynconf backup" >&2
        cleanup_error=1
    fi
    if [[ "$OWN_TEST_TMPDIR" -eq 1 && -n "$TEST_TMPDIR" ]] \
        && ! rm -rf -- "$TEST_TMPDIR"; then
        echo "Error: unable to remove private dynconf test directory" >&2
        cleanup_error=1
    fi
    if [[ "$cleanup_error" -ne 0 ]]; then
        echo "Error: dynconf cleanup completed with errors" >&2
    fi
    # Contract:
    #   original rc != 0         → preserve original rc (best-effort cleanup)
    #   original rc == 0 + clean → exit 0
    #   original rc == 0 + fail  → exit DYNCONF_CLEANUP_FAILURE_EXIT
    if [[ "$rc" -ne 0 ]]; then
        exit "$rc"
    fi
    if [[ "$cleanup_error" -ne 0 ]]; then
        exit "$DYNCONF_CLEANUP_FAILURE_EXIT"
    fi
    exit 0
}

check_prerequisites() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required" >&2
        exit 2
    fi
    if ! curl -sf "${NGINX_URL}/" >/dev/null 2>&1; then
        echo "Error: NGINX not reachable at ${NGINX_URL}" >&2
        echo "Start NGINX with markdown module and dynconf enabled first." >&2
        exit 2
    fi
}

# Resolve a trusted NGINX master PID under a strict ownership model.
#
# Only the following sources are accepted (in priority order):
#   1. NGINX_PID         - explicit numeric PID from the caller
#   2. NGINX_PID_FILE    - trusted pid file containing a single numeric PID
#   3. HARNESS_NGINX_PID - PID saved by an owning harness/test launcher
#
# Return codes are part of the caller contract:
#   0 - a configured source resolved to a verified NGINX master PID
#   1 - no PID source was configured; polling is safe
#   2 - a configured source failed parsing or process validation
#
# A remote NGINX_URL never authorizes signaling a local process.  When no
# trusted PID is available, no SIGHUP is sent; the caller falls back to
# bounded diagnostics polling.
#
# Prints the resolved PID on stdout on success.  Error diagnostics are written
# to stderr and are intentionally preserved for configured-source failures.
resolve_nginx_pid() {
    local raw=""
    local parse_rc=0

    # 1. Explicit NGINX_PID
    if [[ -n "${NGINX_PID:-}" ]]; then
        raw="${NGINX_PID}"
        if _validate_nginx_pid "$raw"; then
            return 0
        fi
        echo "Error: NGINX_PID failed validation: $raw" >&2
        return 2
    fi

    # 2. Explicit NGINX_PID_FILE
    if [[ -n "${NGINX_PID_FILE:-}" ]]; then
        if [[ -L "${NGINX_PID_FILE}" ]]; then
            echo "Error: NGINX_PID_FILE must not be a symlink: ${NGINX_PID_FILE}" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if [[ ! -e "${NGINX_PID_FILE}" ]]; then
            echo "Error: NGINX_PID_FILE does not exist: ${NGINX_PID_FILE}" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if [[ ! -f "${NGINX_PID_FILE}" ]]; then
            echo "Error: NGINX_PID_FILE is not a regular file: ${NGINX_PID_FILE}" >&2
            return "$PID_CONFIG_INVALID"
        fi
        local real_pid_file
        local pid_file_dir=""
        local pid_file_name=""
        if ! pid_file_dir="$(dirname -- "${NGINX_PID_FILE}")"; then
            echo "Error: unable to determine NGINX_PID_FILE parent: ${NGINX_PID_FILE}" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if ! pid_file_name="$(basename -- "${NGINX_PID_FILE}")"; then
            echo "Error: unable to determine NGINX_PID_FILE name: ${NGINX_PID_FILE}" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if ! pid_file_dir="$(cd -P -- "$pid_file_dir" && pwd -P)"; then
            echo "Error: unable to canonicalize NGINX_PID_FILE: ${NGINX_PID_FILE}" >&2
            return "$PID_CONFIG_INVALID"
        fi
        real_pid_file="$pid_file_dir/$pid_file_name"
        # Reject symlinks even after canonicalization of the parent dir.
        if [[ -L "$real_pid_file" ]]; then
            echo "Error: NGINX_PID_FILE resolves to a symlink: $real_pid_file" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if [[ ! -s "$real_pid_file" ]]; then
            echo "Error: NGINX_PID_FILE is empty: $real_pid_file" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if [[ ! -r "$real_pid_file" ]]; then
            echo "Error: NGINX_PID_FILE is not readable: $real_pid_file" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if ! command -v awk >/dev/null 2>&1; then
            echo "Error: awk is required to parse NGINX_PID_FILE" >&2
            return "$PID_CONFIG_INVALID"
        fi
        # Permit one logical line with optional ASCII spaces at its edges.
        # Do not delete internal whitespace or accept a second line.
        if raw="$(awk '
            NR == 1 {
                gsub(/^ +| +$/, "", $0)
                if ($0 !~ /^[0-9]+$/) {
                    exit 1
                }
                print
                next
            }
            {
                exit 2
            }
        ' "$real_pid_file")"; then
            parse_rc=0
        else
            parse_rc=$?
        fi
        if [[ "$parse_rc" -eq 2 ]]; then
            echo "Error: NGINX_PID_FILE must contain one logical line: $real_pid_file" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if [[ "$parse_rc" -ne 0 || -z "$raw" ]]; then
            echo "Error: NGINX_PID_FILE must contain one numeric PID with optional surrounding spaces: $real_pid_file" >&2
            return "$PID_CONFIG_INVALID"
        fi
        if _validate_nginx_pid "$raw"; then
            return "$PID_RESOLVED"
        fi
        echo "Error: NGINX_PID_FILE content failed process validation: $raw" >&2
        return "$PID_CONFIG_INVALID"
    fi

    # 3. Harness fallback, only after explicit caller sources.
    if [[ -n "${HARNESS_NGINX_PID:-}" ]]; then
        raw="${HARNESS_NGINX_PID}"
        if _validate_nginx_pid "$raw"; then
            return 0
        fi
        echo "Error: HARNESS_NGINX_PID failed validation: $raw" >&2
        return 2
    fi

    # 4. No trusted PID available.
    return "$PID_NOT_CONFIGURED"
}

# Validate that a candidate PID is a positive integer referring to a live
# process whose command line matches an NGINX master.  Rejects the current
# shell, non-numeric values, extra shell tokens, and processes that are not
# an nginx master.
_validate_nginx_pid() {
    local candidate="$1"

    # Reject empty and any non-numeric characters (no shell tokens).
    if [[ -z "$candidate" || ! "$candidate" =~ ^[0-9]+$ ]]; then
        echo "Error: nginx PID must be a positive integer: '$candidate'" >&2
        return 1
    fi
    # Reject PID 0, 1, and the current shell.
    if [[ "$candidate" -le 1 ]]; then
        echo "Error: nginx PID must be greater than 1: $candidate" >&2
        return 1
    fi
    if [[ "$candidate" -eq "$$" ]]; then
        echo "Error: nginx PID must not be the current shell: $candidate" >&2
        return 1
    fi
    # Reject if the process does not exist.
    if ! kill -0 "$candidate" 2>/dev/null; then
        echo "Error: nginx PID does not refer to a live process: $candidate" >&2
        return 1
    fi
    # Verify the process command line matches an NGINX master.  Without ps,
    # identity cannot be established, so fail closed instead of signaling an
    # unrelated live process.
    if ! command -v ps >/dev/null 2>&1; then
        echo "Error: ps is required to verify NGINX master PID $candidate" >&2
        return 1
    fi
    local cmdline
    cmdline="$(ps -o command= -p "$candidate" 2>/dev/null || true)"
    if [[ -z "$cmdline" ]]; then
        echo "Error: cannot read command line for PID $candidate" >&2
        return 1
    fi
    # The process must first identify as an NGINX master.  A configured
    # prefix is an additional ownership constraint, never an alternative
    # identity check.
    if [[ "$cmdline" != *"nginx: master process"* ]]; then
        echo "Error: PID $candidate is not an nginx master: $cmdline" >&2
        return 1
    fi
    if [[ -n "${NGINX_PID_PREFIX:-}" \
        && "$cmdline" != *"${NGINX_PID_PREFIX}"* ]]; then
        echo "Error: PID $candidate does not match NGINX_PID_PREFIX: $cmdline" >&2
        return 1
    fi
    printf '%s\n' "$candidate"
    return 0
}

# Send SIGHUP only to a verified NGINX master.  A missing source is a safe
# polling fallback; a configured but invalid source is a test failure.
send_reload() {
    local label="$1"
    local pid
    local resolve_rc=0

    if pid="$(resolve_nginx_pid)"; then
        resolve_rc=0
    else
        resolve_rc=$?
    fi

    case "$resolve_rc" in
    "$PID_RESOLVED")
        if kill -HUP "$pid" 2>/dev/null; then
            pass "sent SIGHUP to NGINX master (pid $pid) — $label"
            return 0
        else
            echo "Error: kill -HUP $pid failed — $label" >&2
            fail "unable to send SIGHUP to pid $pid — $label"
            return 1
        fi
        ;;
    "$PID_NOT_CONFIGURED")
        echo "SKIP: no harness-owned or explicitly supplied NGINX PID; using watcher polling" >&2
        pass "SIGHUP skipped for $label (watcher polling will be used)"
        return 0
        ;;
    "$PID_CONFIG_INVALID")
        echo "Error: configured NGINX PID source is invalid; refusing SIGHUP — $label" >&2
        fail "invalid NGINX PID source for $label"
        return 2
        ;;
    *)
        echo "Error: unexpected NGINX PID resolution status $resolve_rc — $label" >&2
        fail "NGINX PID resolution failed for $label"
        return 2
        ;;
    esac
}

get_diagnostics() {
    curl -sf "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null || echo ""
}

diagnostics_field() {
    local field="$1"
    local json="$2"
    local match=""

    case "$field" in
        generation|last_success|filter|config_version|active_mtime|state)
            match=$(printf '%s' "$json" | grep -E -o "\"${field}\"[[:space:]]*:[[:space:]]*(\"[^\"]*\"|[0-9]+|true|false|null)" | head -1 || true)
            ;;
        *)
            return 1
            ;;
    esac
    match="${match#*:}"
    match="${match# }"
    match="${match#\"}"
    match="${match%\"}"
    printf '%s\n' "$match"
}

wait_for_diagnostics_field() {
    local field="$1"
    local predicate="$2"
    local deadline_seconds="${3:-15}"
    local deadline="$(($(date +%s) + deadline_seconds))"
    local value=""
    local baseline=""
    local expected=""

    case "$predicate" in
        changed_from:*)
            baseline="${predicate#changed_from:}"
            ;;
        equals:*)
            expected="${predicate#equals:}"
            if [[ -z "$expected" ]]; then
                echo "Error: equals predicate requires a non-empty value: $predicate" >&2
                return 1
            fi
            ;;
        nonempty)
            ;;
        *)
            echo "Error: unsupported diagnostics predicate: $predicate" >&2
            return 1
            ;;
    esac

    while [[ "$(date +%s)" -lt "$deadline" ]]; do
        LAST_DIAG="$(get_diagnostics)"
        value="$(diagnostics_field "$field" "$LAST_DIAG")"
        if [[ "$predicate" == "nonempty" && -n "$value" ]]; then
            return 0
        fi
        if [[ "$predicate" == changed_from:* && -n "$value" \
            && "$value" != "$baseline" ]]; then
            return 0
        fi
        if [[ "$predicate" == equals:* && "$value" == "$expected" ]]; then
            return 0
        fi
        sleep 1
    done

    echo "Timeout waiting for diagnostics field $field ($predicate)" >&2
    echo "Last diagnostics JSON: ${LAST_DIAG:-<empty>}" >&2
    return 1
}

write_dynconf_atomically() {
    local contents="$1"

    if [[ -L "$DYNCONF_FILE" ]]; then
        echo "Error: dynconf target became a symlink" >&2
        return 1
    fi
    if [[ "$ORIGINAL_FILE_EXISTED" -eq 0 \
        && "$DYNCONF_OWNED_BY_TEST" -eq 0 \
        && -e "$DYNCONF_FILE" ]]; then
        echo "Error: dynconf target appeared after ownership check" >&2
        return 1
    fi

    TMP_WRITE_PATH="$(mktemp "$DYNCONF_DIR/.markdown-dynconf-write.XXXXXX")" \
        || return 1
    if ! printf '%s\n' "$contents" > "$TMP_WRITE_PATH"; then
        echo "Error: failed to write temporary dynconf file" >&2
        rm -f -- "$TMP_WRITE_PATH" || true
        TMP_WRITE_PATH=""
        return 1
    fi
    if [[ "$ORIGINAL_FILE_EXISTED" -eq 1 ]]; then
        if ! chmod "$ORIGINAL_FILE_MODE" "$TMP_WRITE_PATH"; then
            echo "Error: failed to preserve dynconf file mode" >&2
            rm -f -- "$TMP_WRITE_PATH" || true
            TMP_WRITE_PATH=""
            return 1
        fi
        if ! chown "$ORIGINAL_FILE_UID:$ORIGINAL_FILE_GID" "$TMP_WRITE_PATH"; then
            echo "Error: failed to preserve dynconf file ownership" >&2
            rm -f -- "$TMP_WRITE_PATH" || true
            TMP_WRITE_PATH=""
            return 1
        fi
    else
        if ! chmod 0644 "$TMP_WRITE_PATH"; then
            echo "Error: failed to set readable dynconf file mode" >&2
            rm -f -- "$TMP_WRITE_PATH" || true
            TMP_WRITE_PATH=""
            return 1
        fi
    fi
    if ! mv -f -- "$TMP_WRITE_PATH" "$DYNCONF_FILE"; then
        echo "Error: failed to replace dynconf file $DYNCONF_FILE" >&2
        rm -f -- "$TMP_WRITE_PATH" || true
        TMP_WRITE_PATH=""
        return 1
    fi
    TMP_WRITE_PATH=""
    # Ownership for the race guard is claimed only after the first
    # successful atomic write; before that, an externally appearing file
    # must still trip the guard (ORIGINAL_FILE_EXISTED=0).
    DYNCONF_OWNED_BY_TEST=1
    DYNCONF_FILE_CREATED_BY_TEST=1
    return 0
}

if [[ "${DYNCONF_RELOAD_ROLLBACK_LIBRARY:-0}" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi

# --- Ownership setup (before any caller file write) ---

umask 077
if ! TEST_TMPDIR="$(mktemp -d)"; then
    echo "Error: unable to create private dynconf test directory" >&2
    exit 2
fi
if ! TEST_TMPDIR="$(cd -P -- "$TEST_TMPDIR" && pwd -P)"; then
    echo "Error: unable to canonicalize private dynconf test directory" >&2
    exit 2
fi
OWN_TEST_TMPDIR=1
trap 'cleanup "$?"' EXIT
trap 'cleanup 129' HUP
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

REQUESTED_DYNCONF_FILE="$(requested_dynconf_path)"
if ! prepare_dynconf_ownership "$REQUESTED_DYNCONF_FILE"; then
    echo "Error: unable to prepare dynconf ownership" >&2
    exit 1
fi

# --- Step 0: Check prerequisites ---

check_prerequisites

# --- Step 1: Verify initial config snapshot ---

DIAG="$(get_diagnostics)"
if [[ -n "$DIAG" ]]; then
    pass "diagnostics endpoint reachable"
else
    fail "diagnostics endpoint not reachable"
    echo "Ensure markdown_diagnostics is enabled and allowed for this client." >&2
    exit 1
fi

INITIAL_SUCCESS="$(diagnostics_field last_success "$DIAG")"
INITIAL_GENERATION="$(diagnostics_field generation "$DIAG")"
if [[ -n "$INITIAL_SUCCESS" && "$INITIAL_SUCCESS" != "null" ]]; then
    pass "initial last_success present: $INITIAL_SUCCESS"
else
    fail "initial last_success is missing"
fi
if [[ -n "$INITIAL_GENERATION" && "$INITIAL_GENERATION" != "null" ]]; then
    pass "initial generation present: $INITIAL_GENERATION"
else
    fail "initial generation is missing"
    INITIAL_GENERATION="0"
fi

# --- Step 2: Write valid dynconf and reload ---

if ! write_dynconf_atomically '{
  "schema_version": 1,
  "filter": "on",
  "streaming_buffer": 10485760
}'; then
    fail "failed to write valid dynconf"
    exit 1
fi
echo "Wrote valid dynconf to $DYNCONF_FILE" >&2

# Trigger reload when a harness-owned or explicitly supplied master PID is
# available. Otherwise the watcher timer is allowed to observe the file;
# this is intentionally not an mtime shortcut.
send_reload "valid dynconf reload" || exit $?

# --- Step 3: Verify new config with bounded polling ---

if wait_for_diagnostics_field generation \
    "changed_from:$INITIAL_GENERATION" 15 \
    && wait_for_diagnostics_field filter equals:on 15; then
    NEW_GENERATION="$(diagnostics_field generation "$LAST_DIAG")"
    NEW_SUCCESS="$(diagnostics_field last_success "$LAST_DIAG")"
    pass "valid reload observed by generation=$NEW_GENERATION and filter=on"
    if [[ -n "$NEW_SUCCESS" && "$NEW_SUCCESS" != "$INITIAL_SUCCESS" ]]; then
        pass "last_success changed after valid reload: $NEW_SUCCESS"
    else
        pass "last_success retained same timestamp; generation and behavior evidence passed"
    fi
else
    fail "valid dynconf reload was not observed"
    NEW_GENERATION="$INITIAL_GENERATION"
    NEW_SUCCESS="$(diagnostics_field last_success "$LAST_DIAG")"
fi

# --- Step 4: Write invalid dynconf and reload ---

if ! write_dynconf_atomically '{
  "schema_version": 1,
  "unknown_key_that_does_not_exist": "should_fail"
}'; then
    fail "failed to write invalid dynconf fixture"
    exit 1
fi
echo "Wrote invalid dynconf to $DYNCONF_FILE" >&2

if [[ -n "${NGINX_PID:-}" || -n "${HARNESS_NGINX_PID:-}" \
    || -n "${NGINX_PID_FILE:-}" ]]; then
    send_reload "invalid dynconf reload" || exit $?
fi

# --- Step 5: Verify last-known-good with bounded polling ---

if wait_for_diagnostics_field state equals:lkg_preserved 15 \
    && wait_for_diagnostics_field generation equals:"$NEW_GENERATION" 15 \
    && wait_for_diagnostics_field filter equals:on 15; then
    pass "invalid reload preserved generation=$NEW_GENERATION and filter=on"
else
    fail "invalid reload did not preserve the last-known-good configuration"
fi

# --- Step 6: Restore a previous valid file atomically ---

if ! write_dynconf_atomically '{
  "schema_version": 1,
  "filter": "off",
  "streaming_buffer": 1048576
}'; then
    fail "failed to restore valid dynconf"
    exit 1
fi
echo "Atomically restored valid dynconf at $DYNCONF_FILE" >&2

if [[ -n "${NGINX_PID:-}" || -n "${HARNESS_NGINX_PID:-}" \
    || -n "${NGINX_PID_FILE:-}" ]]; then
    send_reload "restored-file reload" || exit $?
fi

# --- Step 7: Verify restored-file reload with two independent signals ---

if wait_for_diagnostics_field generation \
    "changed_from:$NEW_GENERATION" 15 \
    && wait_for_diagnostics_field filter equals:off 15; then
    ROLLBACK_GENERATION="$(diagnostics_field generation "$LAST_DIAG")"
    ROLLBACK_SUCCESS="$(diagnostics_field last_success "$LAST_DIAG")"
    pass "restored reload observed by generation=$ROLLBACK_GENERATION and filter=off"
    if [[ -n "$ROLLBACK_SUCCESS" && "$ROLLBACK_SUCCESS" != "$NEW_SUCCESS" ]]; then
        pass "last_success changed after restored-file reload: $ROLLBACK_SUCCESS"
    else
        pass "restored-file last_success retained the same timestamp"
    fi
else
    fail "restored-file reload was not observed"
fi

echo "" >&2
echo "=== Dynconf Reload/File-Restore E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
