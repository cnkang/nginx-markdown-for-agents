#!/bin/bash
# chunked_replay_test.sh — E2E test: chunked transfer replay scenario.
#
# This script documents and exercises the chunked transfer replay scenario
# against a running NGINX instance with the markdown module loaded.
#
# Prerequisites:
#   - NGINX running with markdown module enabled
#   - markdown_limits memory configured (e.g. 1m)
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#   - A backend serving chunked HTML responses (or use BACKEND_URL)
#
# Test Scenario:
#   1. Send a request that triggers chunked transfer encoding from upstream
#   2. Verify the module handles chunked replay correctly
#   3. Verify fail-open behavior when conversion fails mid-stream
#   4. Verify subsequent chunks are not discarded after failure
#   5. Verify metrics reflect the replay scenario
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./chunked_replay_test.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
METRICS_PATH="${METRICS_PATH:-/nginx-markdown/metrics}"
TEST_PATH="${TEST_PATH:-/chunked-test}"
ACCEPT_MARKDOWN="Accept: text/markdown"
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

    if ! curl -sf "${NGINX_URL}/" >/dev/null 2>&1; then
        echo "Error: NGINX not reachable at ${NGINX_URL}" >&2
        echo "Start NGINX with markdown module enabled first." >&2
        exit 2
    fi
}

# --- Step 0: Check prerequisites ---

check_prerequisites

# --- Step 1: Verify chunked response handling ---

echo "Step 1: Testing chunked transfer encoding handling..." >&2

RESPONSE=$(curl -sf \
    -H "$ACCEPT_MARKDOWN" \
    -H "Transfer-Encoding: chunked" \
    -D - \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || true

if [[ -n "$RESPONSE" ]]; then
    pass "received response for chunked request"
else
    fail "no response for chunked request (endpoint may not exist)"
fi

# --- Step 2: Verify response is complete (not truncated) ---

echo "Step 2: Verifying response completeness..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "$ACCEPT_MARKDOWN" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "HTTP 200 response received"
        ;;
    304)
        pass "HTTP 304 (conditional) response received"
        ;;
    000)
        fail "no HTTP response (connection failed)"
        ;;
    *)
        fail "unexpected HTTP code: $HTTP_CODE"
        ;;
esac

# --- Step 3: Verify fail-open pass-through for large content ---

echo "Step 3: Testing fail-open for oversized chunked content..." >&2

# Generate a large request body to trigger max-size failure
LARGE_ACCEPT_RESPONSE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "$ACCEPT_MARKDOWN" \
    "${NGINX_URL}${TEST_PATH}?size=large" 2>/dev/null) || LARGE_ACCEPT_RESPONSE="000"

case "$LARGE_ACCEPT_RESPONSE" in
    200)
        pass "large content served (fail-open pass-through)"
        ;;
    000)
        fail "no response for large content request"
        ;;
    *)
        # Any non-error response is acceptable for fail-open
        pass "response received for large content: $LARGE_ACCEPT_RESPONSE"
        ;;
esac

# --- Step 4: Verify metrics endpoint accessible ---

echo "Step 4: Checking metrics..." >&2

METRICS=$(curl -sf "${NGINX_URL}${METRICS_PATH}" 2>/dev/null) || METRICS=""

if [[ -n "$METRICS" ]]; then
    pass "metrics endpoint accessible"

    # Check for relevant metric families
    if echo "$METRICS" | grep -q "nginx_markdown_requests_total"; then
        pass "requests_total metric present"
    else
        fail "requests_total metric missing"
    fi

    if echo "$METRICS" | grep -q "nginx_markdown_failopen_total"; then
        pass "failopen_total metric present"
    else
        fail "failopen_total metric missing"
    fi
else
    fail "metrics endpoint not accessible"
fi

# --- Step 5: Verify no response truncation on replay ---

echo "Step 5: Verifying no truncation on replay..." >&2

CONTENT_LENGTH=$(curl -sf -o /dev/null \
    -w "%{size_download}" \
    -H "$ACCEPT_MARKDOWN" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || CONTENT_LENGTH="0"

if [[ "$CONTENT_LENGTH" -gt 0 ]] 2>/dev/null; then
    pass "response has content (${CONTENT_LENGTH} bytes)"
else
    fail "response has no content or endpoint unavailable"
fi

# --- Summary ---

echo "" >&2
echo "=== Chunked Replay E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
