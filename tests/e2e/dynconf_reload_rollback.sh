#!/bin/bash
# dynconf_reload_rollback.sh — E2E test: dynamic config reload and file restore.
#
# This script exercises the dynconf reload and last-known-good protection
# against a running NGINX instance with the markdown module loaded.  The
# diagnostics endpoint is read-only; operator rollback is modeled by an
# atomic replacement of the watched file.
#
# Prerequisites:
#   - NGINX running with markdown module and dynconf enabled
#   - markdown_dynamic_config_path pointing to a writable key=value config file
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Verify initial config snapshot via diagnostics endpoint
#   2. Atomically write a valid dynconf update and trigger the reload path
#   3. Verify new config is active (applied_mtime updated)
#   4. Write an invalid dynconf update and trigger reload
#   5. Verify config remains at last-known-good after the invalid file
#   6. Atomically restore a previous valid file and verify the reload
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./dynconf_reload_rollback.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
DIAGNOSTICS_PATH="/nginx-markdown/diagnostics"
PASS_COUNT=0
FAIL_COUNT=0

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

check_prerequisites() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required" >&2
        exit 2
    fi

    # Verify NGINX is reachable
    if ! curl -sf "${NGINX_URL}/" >/dev/null 2>&1; then
        echo "Error: NGINX not reachable at ${NGINX_URL}" >&2
        echo "Start NGINX with markdown module and dynconf enabled first." >&2
        exit 2
    fi
}

get_diagnostics() {
    curl -sf "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null || echo ""
}

# --- Step 0: Check prerequisites ---

check_prerequisites

# --- Step 1: Verify initial config snapshot ---

DIAG=$(get_diagnostics)
if [[ -n "$DIAG" ]]; then
    pass "diagnostics endpoint reachable"
else
    fail "diagnostics endpoint not reachable"
    echo "Ensure markdown_diagnostics is enabled and allowed for this client." >&2
    exit 1
fi

# Extract applied_mtime from diagnostics (JSON field)
INITIAL_MTIME=$(echo "$DIAG" | grep -o '"applied_mtime":[0-9]*' | head -1 | cut -d: -f2)
if [[ -n "$INITIAL_MTIME" ]]; then
    pass "initial applied_mtime present: $INITIAL_MTIME"
else
    # applied_mtime may not be present if dynconf was never loaded
    pass "initial state: no applied_mtime (dynconf not yet loaded)"
    INITIAL_MTIME="0"
fi

# --- Step 2: Write valid dynconf and reload ---

DYNCONF_FILE_CREATED_BY_SCRIPT=0
if [[ -n "${DYNCONF_FILE+x}" ]]; then
    DYNCONF_FILE_CLEANUP_ALLOWED=0
else
    DYNCONF_FILE="/tmp/nginx-markdown-dynconf-test.conf"
    DYNCONF_FILE_CLEANUP_ALLOWED=1
fi
if [[ "$DYNCONF_FILE_CLEANUP_ALLOWED" -eq 1 && -e "$DYNCONF_FILE" ]]; then
    DYNCONF_FILE_CLEANUP_ALLOWED=0
fi
umask 077

write_dynconf_atomically() {
    # Write beside the watched file and rename into place so NGINX observes
    # either the old complete file or the new complete file, never a partial
    # write.  Arguments: $1 - complete key=value configuration contents.
    local contents="$1"
    local tmpfile="${DYNCONF_FILE}.tmp.$$"

    if ! printf '%s\n' "$contents" > "$tmpfile"; then
        echo "Error: failed to write temporary dynconf file $tmpfile" >&2
        rm -f "$tmpfile" || true
        return 1
    fi
    if ! mv -f "$tmpfile" "$DYNCONF_FILE"; then
        echo "Error: failed to replace dynconf file $DYNCONF_FILE" >&2
        rm -f "$tmpfile" || true
        return 1
    fi
    if [[ "$DYNCONF_FILE_CLEANUP_ALLOWED" -eq 1 ]]; then
        DYNCONF_FILE_CREATED_BY_SCRIPT=1
    fi
    return 0
}

# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap.
cleanup_dynconf() {
    local tmpfile="${DYNCONF_FILE}.tmp.$$"

    rm -f "$tmpfile" || true
    if [[ "$DYNCONF_FILE_CREATED_BY_SCRIPT" -eq 1 ]]; then
        rm -f "$DYNCONF_FILE" || true
    fi
    return 0
}

trap 'cleanup_dynconf' EXIT

write_dynconf_atomically 'schema_version=0.9
memory_budget=2m
streaming_budget=10m'

echo "Wrote valid dynconf to $DYNCONF_FILE" >&2

# Trigger reload (send SIGHUP to NGINX master)
NGINX_PID=$(pgrep -f "nginx: master" 2>/dev/null | head -1)
if [[ -n "$NGINX_PID" ]]; then
    kill -HUP "$NGINX_PID" 2>/dev/null || true
    sleep 1
    pass "sent SIGHUP to NGINX master (pid $NGINX_PID)"
else
    echo "SKIP: cannot find NGINX master process for SIGHUP" >&2
    pass "SIGHUP skipped (manual reload required)"
fi

# --- Step 3: Verify new config is active ---

DIAG=$(get_diagnostics)
NEW_MTIME=$(echo "$DIAG" | grep -o '"applied_mtime":[0-9]*' | head -1 | cut -d: -f2)
if [[ -n "$NEW_MTIME" && "$NEW_MTIME" != "$INITIAL_MTIME" ]]; then
    pass "applied_mtime updated after valid reload: $NEW_MTIME"
else
    fail "applied_mtime not updated after valid reload (got: ${NEW_MTIME:-empty})"
fi

# --- Step 4: Write invalid dynconf and reload ---

write_dynconf_atomically 'schema_version=0.9
unknown_key_that_does_not_exist=should_fail
memory_budget=invalid_not_a_size'

echo "Wrote invalid dynconf to $DYNCONF_FILE" >&2

if [[ -n "$NGINX_PID" ]]; then
    kill -HUP "$NGINX_PID" 2>/dev/null || true
    sleep 1
    pass "sent SIGHUP for invalid config reload"
fi

# --- Step 5: Verify config remains at last-known-good ---

DIAG=$(get_diagnostics)
POST_INVALID_MTIME=$(echo "$DIAG" | grep -o '"applied_mtime":[0-9]*' | head -1 | cut -d: -f2)
if [[ -n "$POST_INVALID_MTIME" && "$POST_INVALID_MTIME" == "$NEW_MTIME" ]]; then
    pass "applied_mtime unchanged after invalid reload (active config preserved)"
else
    fail "applied_mtime changed after invalid reload (expected: $NEW_MTIME, got: ${POST_INVALID_MTIME:-empty})"
fi

# --- Step 6: Restore a previous valid file atomically ---

sleep 1
write_dynconf_atomically 'schema_version=0.9
memory_budget=1m'

echo "Atomically restored valid dynconf at $DYNCONF_FILE" >&2

if [[ -n "$NGINX_PID" ]]; then
    kill -HUP "$NGINX_PID" 2>/dev/null || true
    sleep 1
    pass "sent SIGHUP for restored-file reload"
fi

# --- Step 7: Verify restored-file reload succeeded ---

ROLLBACK_MTIME=""
for ((attempt = 0; attempt < 5; attempt++)); do
    DIAG=$(get_diagnostics)
    ROLLBACK_MTIME=$(echo "$DIAG" | grep -o '"applied_mtime":[0-9]*' | head -1 | cut -d: -f2)
    if [[ -n "$ROLLBACK_MTIME" && "$ROLLBACK_MTIME" != "$POST_INVALID_MTIME" ]]; then
        break
    fi
    sleep 1
done
if [[ -n "$ROLLBACK_MTIME" && "$ROLLBACK_MTIME" != "$POST_INVALID_MTIME" ]]; then
    pass "applied_mtime updated after restored-file reload: $ROLLBACK_MTIME"
else
    fail "applied_mtime not updated after restored-file reload (got: ${ROLLBACK_MTIME:-empty})"
fi

# Cleanup is owned by the EXIT trap so failure paths remove the same files.

# --- Summary ---

echo "" >&2
echo "=== Dynconf Reload/File-Restore E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
