#!/bin/bash
# dynconf_reload_rollback.sh — E2E test: dynamic config reload and rollback.
#
# This script documents and exercises the dynconf reload + rollback scenario
# against a running NGINX instance with the markdown module loaded.
#
# Prerequisites:
#   - NGINX running with markdown module and dynconf enabled
#   - markdown_dynconf_file pointing to a writable JSON config file
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Verify initial config snapshot via diagnostics endpoint
#   2. Write a valid dynconf update and trigger reload (SIGHUP or endpoint)
#   3. Verify new config is active (applied_mtime updated)
#   4. Write an invalid dynconf update and trigger reload
#   5. Verify config remains at last-known-good (rollback)
#   6. Trigger manual rollback to previous config
#   7. Verify rollback succeeded
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

DYNCONF_FILE="${DYNCONF_FILE:-/tmp/nginx-markdown-dynconf-test.json}"

cat > "$DYNCONF_FILE" <<'DYNCONF_VALID'
{
    "markdown_limits_memory": "2m",
    "markdown_decompression_budget": "10m"
}
DYNCONF_VALID

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

cat > "$DYNCONF_FILE" <<'DYNCONF_INVALID'
{
    "unknown_key_that_does_not_exist": "should_fail",
    "markdown_limits_memory": "invalid_not_a_size"
}
DYNCONF_INVALID

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
    pass "applied_mtime unchanged after invalid reload (rollback to LKG)"
else
    fail "applied_mtime changed after invalid reload (expected: $NEW_MTIME, got: ${POST_INVALID_MTIME:-empty})"
fi

# --- Step 6: Restore valid config (simulate manual rollback) ---

cat > "$DYNCONF_FILE" <<'DYNCONF_ROLLBACK'
{
    "markdown_limits_memory": "1m"
}
DYNCONF_ROLLBACK

echo "Wrote rollback dynconf to $DYNCONF_FILE" >&2

if [[ -n "$NGINX_PID" ]]; then
    kill -HUP "$NGINX_PID" 2>/dev/null || true
    sleep 1
    pass "sent SIGHUP for rollback reload"
fi

# --- Step 7: Verify rollback succeeded ---

DIAG=$(get_diagnostics)
ROLLBACK_MTIME=$(echo "$DIAG" | grep -o '"applied_mtime":[0-9]*' | head -1 | cut -d: -f2)
if [[ -n "$ROLLBACK_MTIME" && "$ROLLBACK_MTIME" != "$POST_INVALID_MTIME" ]]; then
    pass "applied_mtime updated after rollback reload: $ROLLBACK_MTIME"
else
    fail "applied_mtime not updated after rollback (got: ${ROLLBACK_MTIME:-empty})"
fi

# --- Cleanup ---

rm -f "$DYNCONF_FILE"

# --- Summary ---

echo "" >&2
echo "=== Dynconf Reload/Rollback E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
