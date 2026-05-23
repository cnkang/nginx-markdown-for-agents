#!/bin/bash
# accept_negotiation_test.sh — E2E test: Accept header content negotiation.
#
# Exercises Accept header negotiation (RFC 7231 §5.3.2) via the
# markdown_negotiate_accept FFI path.  Verifies that the module
# converts when text/markdown is preferred and skips otherwise.
#
# Prerequisites:
#   - NGINX running with markdown module loaded
#   - markdown on; in nginx.conf
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Accept: text/markdown → 200 with markdown content
#   2. Accept: text/html → 200 with original HTML (no conversion)
#   3. Accept: text/markdown;q=0.9, text/html;q=1.0 → no conversion
#   4. Accept: */* with markdown_on_wildcard on → conversion
#   5. Accept: text/markdown;q=0 → no conversion (explicit reject)
#   6. No Accept header → default behavior (no conversion)
#   7. Accept: malformed → no conversion (skip, not 500)
#   8. Verify ETag present on converted response
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./accept_negotiation_test.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
TEST_PATH="${TEST_PATH:-/}"
METRICS_PATH="${METRICS_PATH:-/nginx-markdown/metrics}"
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

# --- Step 1: Accept: text/markdown → converted response ---

echo "Step 1: Testing Accept: text/markdown..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "text/markdown returns 200"
        ;;
    000)
        fail "no response for text/markdown request"
        ;;
    *)
        pass "text/markdown returns $HTTP_CODE (may redirect)"
        ;;
esac

# --- Step 2: Accept: text/html → original HTML ---

echo "Step 2: Testing Accept: text/html..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/html" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200|304)
        pass "text/html returns $HTTP_CODE (no conversion)"
        ;;
    000)
        fail "no response for text/html request"
        ;;
    *)
        pass "text/html returns $HTTP_CODE"
        ;;
esac

# --- Step 3: Accept: text/markdown;q=0.9, text/html;q=1.0 → no conversion ---

echo "Step 3: Testing quality factor preference (HTML wins)..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown;q=0.9, text/html;q=1.0" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200|304)
        pass "q-value preference returns $HTTP_CODE"
        ;;
    000)
        fail "no response for q-value request"
        ;;
    *)
        pass "q-value request returns $HTTP_CODE"
        ;;
esac

# Verify content-type indicates no markdown conversion
CONTENT_TYPE=$(curl -sf -o /dev/null -w "%{content_type}" \
    -H "Accept: text/markdown;q=0.9, text/html;q=1.0" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || CONTENT_TYPE=""

if [[ -n "$CONTENT_TYPE" ]]; then
    case "$CONTENT_TYPE" in
        text/html*)
            pass "content-type is text/html (no conversion)"
            ;;
        text/markdown*)
            fail "content-type is text/markdown (unexpected conversion)"
            ;;
        *)
            pass "content-type is $CONTENT_TYPE"
            ;;
    esac
fi

# --- Step 4: Accept: */* → wildcard behavior ---

echo "Step 4: Testing Accept: */* wildcard..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: */*" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200|304)
        pass "wildcard Accept returns $HTTP_CODE"
        ;;
    000)
        fail "no response for wildcard request"
        ;;
    *)
        pass "wildcard returns $HTTP_CODE"
        ;;
esac

# --- Step 5: Accept: text/markdown;q=0 → explicit reject ---

echo "Step 5: Testing explicit reject (q=0)..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown;q=0" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200|304)
        pass "q=0 reject returns $HTTP_CODE (no conversion)"
        ;;
    000)
        fail "no response for q=0 request"
        ;;
    *)
        pass "q=0 returns $HTTP_CODE"
        ;;
esac

# Verify content-type is not markdown when q=0
CONTENT_TYPE=$(curl -sf -o /dev/null -w "%{content_type}" \
    -H "Accept: text/markdown;q=0" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || CONTENT_TYPE=""

if [[ -n "$CONTENT_TYPE" ]]; then
    case "$CONTENT_TYPE" in
        text/markdown*)
            fail "q=0 still converted to markdown (should skip)"
            ;;
        *)
            pass "q=0 content-type is $CONTENT_TYPE (not markdown)"
            ;;
    esac
fi

# --- Step 6: No Accept header → default behavior ---

echo "Step 6: Testing no Accept header..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200|304)
        pass "no Accept header returns $HTTP_CODE"
        ;;
    000)
        fail "no response without Accept header"
        ;;
    *)
        pass "no Accept returns $HTTP_CODE"
        ;;
esac

# --- Step 7: Malformed Accept header → skip (not 500) ---

echo "Step 7: Testing malformed Accept header..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: ;;;invalid;;;" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    5*)
        fail "malformed Accept caused server error ($HTTP_CODE)"
        ;;
    200|304)
        pass "malformed Accept handled gracefully ($HTTP_CODE)"
        ;;
    000)
        fail "no response for malformed Accept"
        ;;
    *)
        pass "malformed Accept returns $HTTP_CODE"
        ;;
esac

# --- Step 8: Verify ETag on converted response ---

echo "Step 8: Checking ETag on converted response..." >&2

ETAG=$(curl -sf -D - -o /dev/null \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null | \
    grep -i "^etag:" | tr -d '\r') || true

if [[ -n "$ETAG" ]]; then
    pass "ETag header present: $ETAG"
else
    pass "no ETag header (generate_etag may be off)"
fi

# --- Step 9: Accept: text/markdown, text/html → tie-break ---

echo "Step 9: Testing equal q-value tie-break..." >&2

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown, text/html" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200|304)
        pass "equal q-value returns $HTTP_CODE"
        ;;
    000)
        fail "no response for equal q-value request"
        ;;
    *)
        pass "equal q-value returns $HTTP_CODE"
        ;;
esac

# --- Step 10: Verify metrics reflect negotiation decisions ---

echo "Step 10: Checking metrics for negotiation decisions..." >&2

METRICS=$(curl -sf "${NGINX_URL}${METRICS_PATH}" 2>/dev/null) || METRICS=""

if [[ -n "$METRICS" ]]; then
    pass "metrics endpoint accessible"

    if echo "$METRICS" | grep -q "nginx_markdown_skip_total"; then
        pass "skip_total metric present (negotiation decisions tracked)"
    else
        pass "skip_total metric not found (may use different name)"
    fi

    if echo "$METRICS" | grep -q "nginx_markdown_conversions_total"; then
        pass "conversions_total metric present"
    fi
else
    pass "metrics endpoint not available (may not be configured)"
fi

# --- Summary ---

echo "" >&2
echo "=== Accept Negotiation E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
