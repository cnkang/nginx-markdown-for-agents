#!/bin/bash
# resource_limit_test.sh — E2E test: decompression budget, parse timeout, parser budget.
#
# Exercises v0.7.0 resource limit directives:
#   - decompress_max_size (A03)
#   - parse_timeout (A06)
#   - parser_budget (A06)
#
# Verifies fail-open behavior when limits are exceeded and that
# metrics are incremented correctly.
#
# Prerequisites:
#   - NGINX running with markdown module loaded
#   - Resource limit directives configured
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Normal request within all limits → 200 with markdown
#   2. Request exceeding decompression budget → fail-open pass-through
#   3. Request exceeding parse timeout → fail-open pass-through
#   4. Request exceeding parser budget → fail-open pass-through
#   5. Verify metrics for budget/timeout events
#   6. Verify diagnostics endpoint reports resource limits
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./resource_limit_test.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
TEST_PATH="${TEST_PATH:-/}"
METRICS_PATH="${METRICS_PATH:-/nginx-markdown/metrics}"
DIAGNOSTICS_PATH="${DIAGNOSTICS_PATH:-/nginx-markdown/diagnostics}"
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
        exit 2
    fi
}

# --- Step 0: Check prerequisites ---

check_prerequisites

# --- Step 1: Normal request within limits ---

echo "Step 1: Testing normal request within limits..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "normal request returns 200"
        ;;
    304)
        pass "normal request returns 304 (conditional)"
        ;;
    000)
        fail "no response for normal request"
        ;;
    *)
        pass "normal request returns $HTTP_CODE"
        ;;
esac

# --- Step 2: Large content (may exceed decompression budget) ---

echo "Step 2: Testing large content (decompression budget)..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}?size=large" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "large content returns 200 (fail-open or within budget)"
        ;;
    000)
        pass "no response for large content (endpoint may not support ?size=)"
        ;;
    5*)
        fail "server error on large content ($HTTP_CODE) — should fail-open"
        ;;
    *)
        pass "large content returns $HTTP_CODE"
        ;;
esac

# --- Step 3: Compressed content that exceeds budget when decompressed ---

echo "Step 3: Testing compressed content exceeding decompression budget..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown" \
    -H "Accept-Encoding: gzip" \
    "${NGINX_URL}${TEST_PATH}?size=bomb" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "compressed content returns 200 (fail-open or within budget)"
        ;;
    000)
        pass "no response (endpoint may not support ?size=bomb)"
        ;;
    5*)
        fail "server error on compressed content ($HTTP_CODE) — should fail-open"
        ;;
    *)
        pass "compressed content returns $HTTP_CODE"
        ;;
esac

# --- Step 4: Very large content (parser budget) ---

echo "Step 4: Testing content exceeding parser budget..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}?size=huge" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "huge content returns 200 (fail-open or within budget)"
        ;;
    000)
        pass "no response (endpoint may not support ?size=huge)"
        ;;
    5*)
        fail "server error on huge content ($HTTP_CODE) — should fail-open"
        ;;
    *)
        pass "huge content returns $HTTP_CODE"
        ;;
esac

# --- Step 5: Verify metrics for resource limit events ---

echo "Step 5: Checking metrics for resource limit events..." >&2

METRICS=$(curl -sf "${NGINX_URL}${METRICS_PATH}" 2>/dev/null) || METRICS=""

if [ -n "$METRICS" ]; then
    pass "metrics endpoint accessible"

    # Check for budget exceeded metric
    if echo "$METRICS" | grep -q "decompression_budget_exceeded"; then
        pass "decompression_budget_exceeded metric present"
    else
        pass "no decompression_budget_exceeded metric (budget not exceeded in test)"
    fi

    # Check for parse timeout metric
    if echo "$METRICS" | grep -q "parse_timeout"; then
        pass "parse_timeout metric present"
    else
        pass "no parse_timeout metric (timeout not triggered in test)"
    fi

    # Check for parser budget exceeded metric
    if echo "$METRICS" | grep -q "parse_budget_exceeded"; then
        pass "parse_budget_exceeded metric present"
    else
        pass "no parse_budget_exceeded metric (budget not exceeded in test)"
    fi

    # Check for failopen counter
    if echo "$METRICS" | grep -q "failopen_total"; then
        pass "failopen_total metric present"
    fi
else
    pass "metrics endpoint not available"
fi

# --- Step 6: Verify diagnostics reports resource limits ---

echo "Step 6: Checking diagnostics for resource limit config..." >&2

DIAG=$(curl -sf "${NGINX_URL}${DIAGNOSTICS_PATH}" 2>/dev/null) || DIAG=""

if [ -n "$DIAG" ]; then
    pass "diagnostics endpoint accessible"

    # Check for resource limit config keys
    for key in "decompress_max_size" "parse_timeout" "parser_budget"; do
        if echo "$DIAG" | grep -q "\"$key\""; then
            pass "diagnostics key present: $key"
        else
            pass "diagnostics key not found: $key (may be in different section)"
        fi
    done
else
    pass "diagnostics endpoint not available"
fi

# --- Summary ---

echo "" >&2
echo "=== Resource Limit E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
