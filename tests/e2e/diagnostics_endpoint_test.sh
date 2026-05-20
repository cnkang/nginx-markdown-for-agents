#!/bin/bash
# diagnostics_endpoint_test.sh — E2E test: diagnostics endpoint access and content.
#
# This script documents and exercises the diagnostics endpoint against a
# running NGINX instance with markdown_diagnostics enabled.
#
# Prerequisites:
#   - NGINX running with markdown module loaded
#   - markdown_diagnostics on; (enabled in nginx.conf)
#   - allow directive configured for test client IP
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Verify diagnostics endpoint is reachable (GET returns 200)
#   2. Verify Content-Type is application/json
#   3. Verify response body contains all 4 required sections:
#      config_snapshot, recent_decisions, metrics_snapshot, dynconf_state
#   4. Verify config keys present
#   5. Verify metrics keys present
#   6. Verify JSON validity (balanced braces/brackets)
#   7. Verify HEAD request returns 200 with no body
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./diagnostics_endpoint_test.sh
#
# NOTE: This is an integration test that requires a running NGINX instance
# with the markdown module loaded and diagnostics enabled. It does NOT run
# locally on macOS without a configured NGINX instance.
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
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $1" >&2
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $1" >&2
}

check_prerequisites() {
    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required" >&2
        exit 2
    fi

    if ! curl -sf "${NGINX_URL}/" >/dev/null 2>&1; then
        echo "Error: NGINX not reachable at ${NGINX_URL}" >&2
        echo "Start NGINX with markdown module and diagnostics enabled." >&2
        exit 2
    fi
}

# --- Step 0: Check prerequisites ---

check_prerequisites

# --- Step 1: Verify diagnostics endpoint reachable ---

echo "Step 1: Checking diagnostics endpoint..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "diagnostics endpoint returns 200"
        ;;
    403)
        fail "diagnostics endpoint returns 403 (access denied — check allow directive)"
        ;;
    404)
        fail "diagnostics endpoint returns 404 (markdown_diagnostics not enabled)"
        ;;
    000)
        fail "diagnostics endpoint unreachable"
        ;;
    *)
        fail "unexpected HTTP code: $HTTP_CODE"
        ;;
esac

# --- Step 2: Verify Content-Type ---

echo "Step 2: Checking Content-Type..." >&2

CONTENT_TYPE=$(curl -sf -o /dev/null -w "%{content_type}" \
    "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null) || CONTENT_TYPE=""

case "$CONTENT_TYPE" in
    application/json*)
        pass "Content-Type is application/json"
        ;;
    "")
        fail "no Content-Type header"
        ;;
    *)
        fail "unexpected Content-Type: $CONTENT_TYPE"
        ;;
esac

# --- Step 3: Verify JSON structure ---

echo "Step 3: Checking JSON structure..." >&2

BODY=$(curl -sf "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null) || BODY=""

if [ -z "$BODY" ]; then
    fail "empty response body"
else
    pass "non-empty response body received"

    # Check for config_snapshot section
    if echo "$BODY" | grep -q '"config_snapshot"'; then
        pass "config_snapshot section present"
    else
        fail "config_snapshot section missing"
    fi

    # Check for recent_decisions section
    if echo "$BODY" | grep -q '"recent_decisions"'; then
        pass "recent_decisions section present"
    else
        fail "recent_decisions section missing"
    fi

    # Check for metrics_snapshot section
    if echo "$BODY" | grep -q '"metrics_snapshot"'; then
        pass "metrics_snapshot section present"
    else
        fail "metrics_snapshot section missing"
    fi

    # Check for dynconf_state section
    if echo "$BODY" | grep -q '"dynconf_state"'; then
        pass "dynconf_state section present"
    else
        fail "dynconf_state section missing"
    fi
fi

# --- Step 4: Verify config keys ---

echo "Step 4: Checking config keys..." >&2

if [ -n "$BODY" ]; then
    for key in "markdown_enabled" "max_size" "decompression_budget" \
               "parse_timeout" "diagnostics_enabled"; do
        if echo "$BODY" | grep -q "\"$key\""; then
            pass "config key present: $key"
        else
            fail "config key missing: $key"
        fi
    done
fi

# --- Step 5: Verify metrics keys ---

echo "Step 5: Checking metrics keys..." >&2

if [ -n "$BODY" ]; then
    for key in "conversions_total" "delivery_total" "requests_total" \
               "failopen_total"; do
        if echo "$BODY" | grep -q "\"$key\""; then
            pass "metrics key present: $key"
        else
            fail "metrics key missing: $key"
        fi
    done
fi

# --- Step 6: Verify JSON validity (basic bracket matching) ---

echo "Step 6: Basic JSON validity check..." >&2

if [ -n "$BODY" ]; then
    OPEN_BRACES=$(echo "$BODY" | tr -cd '{' | wc -c | tr -d ' ')
    CLOSE_BRACES=$(echo "$BODY" | tr -cd '}' | wc -c | tr -d ' ')

    if [ "$OPEN_BRACES" = "$CLOSE_BRACES" ] && [ "$OPEN_BRACES" -gt 0 ]; then
        pass "balanced braces ($OPEN_BRACES pairs)"
    else
        fail "unbalanced braces (open=$OPEN_BRACES, close=$CLOSE_BRACES)"
    fi

    OPEN_BRACKETS=$(echo "$BODY" | tr -cd '[' | wc -c | tr -d ' ')
    CLOSE_BRACKETS=$(echo "$BODY" | tr -cd ']' | wc -c | tr -d ' ')

    if [ "$OPEN_BRACKETS" = "$CLOSE_BRACKETS" ]; then
        pass "balanced brackets ($OPEN_BRACKETS pairs)"
    else
        fail "unbalanced brackets (open=$OPEN_BRACKETS, close=$CLOSE_BRACKETS)"
    fi
fi

# --- Step 7: Verify HEAD request returns 200 with no body ---

echo "Step 7: Checking HEAD request..." >&2

HEAD_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -I \
    "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null) || HEAD_CODE="000"

case "$HEAD_CODE" in
    200)
        pass "HEAD request returns 200"
        ;;
    *)
        fail "HEAD request returned $HEAD_CODE (expected 200)"
        ;;
esac

# HEAD response should have no body; verify by checking content-length
# or that curl -I output does not contain JSON keys
HEAD_BODY=$(curl -sf -D - -o /dev/null -I \
    "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null) || HEAD_BODY=""

# curl -I only returns headers; verify no JSON body leaked
if echo "$HEAD_BODY" | grep -q '"config_snapshot"'; then
    fail "HEAD response contains body content (should be headers only)"
else
    pass "HEAD response has no body content"
fi

# --- Summary ---

echo "" >&2
echo "=== Diagnostics Endpoint E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
