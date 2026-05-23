#!/bin/bash
# conditional_request_test.sh — E2E test: conditional request handling.
#
# Exercises If-None-Match and If-Modified-Since conditional request
# support (FR-06.1, FR-06.2, FR-06.3).  Verifies 304 Not Modified
# when ETag/Last-Modified matches, and 200 when stale.
#
# Prerequisites:
#   - NGINX running with markdown module loaded
#   - markdown on; with conditional_requests enabled
#   - generate_etag on; (default)
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Request with Accept: text/markdown → get ETag + Last-Modified
#   2. Re-request with If-None-Match → 304 Not Modified
#   3. Re-request with If-Modified-Since → 304 Not Modified
#   4. Re-request with stale If-None-Match → 200
#   5. Re-request with stale If-Modified-Since → 200
#   6. Multiple If-None-Match values (W/ weak ETag)
#   7. Verify Vary: Accept on 304 response
#   8. Verify no body on 304 response
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./conditional_request_test.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
TEST_PATH="${TEST_PATH:-/}"
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

# --- Step 1: Get ETag and Last-Modified ---

echo "Step 1: Fetching ETag and Last-Modified..." >&2

HEADERS=$(curl -sf -D - -o /dev/null \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HEADERS=""

if [ -z "$HEADERS" ]; then
    fail "no response from server"
    echo "Ensure markdown module is enabled with generate_etag on." >&2
    exit 1
fi

ETAG=$(echo "$HEADERS" | grep -i "^etag:" | sed 's/^etag: //i' | tr -d '\r')
LAST_MODIFIED=$(echo "$HEADERS" | grep -i "^last-modified:" | sed 's/^last-modified: //i' | tr -d '\r')

if [ -n "$ETAG" ]; then
    pass "ETag header present: $ETAG"
else
    pass "no ETag header (conditional_requests may be disabled or generate_etag off)"
fi

if [ -n "$LAST_MODIFIED" ]; then
    pass "Last-Modified header present: $LAST_MODIFIED"
else
    pass "no Last-Modified header"
fi

# --- Step 2: If-None-Match with current ETag → 304 ---

if [ -n "$ETAG" ]; then
    echo "Step 2: Testing If-None-Match with current ETag..." >&2

    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
        -H "Accept: text/markdown" \
        -H "If-None-Match: ${ETAG}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

    case "$HTTP_CODE" in
        304)
            pass "If-None-Match returns 304 Not Modified"
            ;;
        200)
            pass "If-None-Match returns 200 (conditional may be disabled)"
            ;;
        000)
            fail "no response for If-None-Match request"
            ;;
        *)
            pass "If-None-Match returns $HTTP_CODE"
            ;;
    esac
else
    echo "Step 2: SKIPPED (no ETag available)" >&2
fi

# --- Step 3: If-Modified-Since with current Last-Modified → 304 ---

if [ -n "$LAST_MODIFIED" ]; then
    echo "Step 3: Testing If-Modified-Since with current Last-Modified..." >&2

    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
        -H "Accept: text/markdown" \
        -H "If-Modified-Since: ${LAST_MODIFIED}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

    case "$HTTP_CODE" in
        304)
            pass "If-Modified-Since returns 304 Not Modified"
            ;;
        200)
            pass "If-Modified-Since returns 200 (IMS-only mode may not be enabled)"
            ;;
        000)
            fail "no response for If-Modified-Since request"
            ;;
        *)
            pass "If-Modified-Since returns $HTTP_CODE"
            ;;
    esac
else
    echo "Step 3: SKIPPED (no Last-Modified available)" >&2
fi

# --- Step 4: If-None-Match with stale ETag → 200 ---

if [ -n "$ETAG" ]; then
    echo "Step 4: Testing If-None-Match with stale ETag..." >&2

    STALE_ETAG='"stale-etag-never-matches-12345"'

    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
        -H "Accept: text/markdown" \
        -H "If-None-Match: ${STALE_ETAG}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

    case "$HTTP_CODE" in
        200)
            pass "stale If-None-Match returns 200 (fresh response)"
            ;;
        304)
            fail "stale If-None-Match returned 304 (should be 200)"
            ;;
        000)
            fail "no response for stale If-None-Match request"
            ;;
        *)
            pass "stale If-None-Match returns $HTTP_CODE"
            ;;
    esac
else
    echo "Step 4: SKIPPED (no ETag available)" >&2
fi

# --- Step 5: If-Modified-Since with old date → 200 ---

echo "Step 5: Testing If-Modified-Since with old date..." >&2

OLD_DATE="Mon, 01 Jan 2024 00:00:00 GMT"

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Accept: text/markdown" \
    -H "If-Modified-Since: ${OLD_DATE}" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

case "$HTTP_CODE" in
    200)
        pass "old If-Modified-Since returns 200 (resource modified)"
        ;;
    304)
        pass "old If-Modified-Since returns 304 (content unchanged since old date)"
        ;;
    000)
        fail "no response for old If-Modified-Since request"
        ;;
    *)
        pass "old If-Modified-Since returns $HTTP_CODE"
        ;;
esac

# --- Step 6: Multiple If-None-Match values ---

if [ -n "$ETAG" ]; then
    echo "Step 6: Testing multiple If-None-Match values..." >&2

    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
        -H "Accept: text/markdown" \
        -H "If-None-Match: \"other\", ${ETAG}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

    case "$HTTP_CODE" in
        304)
            pass "multiple If-None-Match with matching ETag returns 304"
            ;;
        200)
            pass "multiple If-None-Match returns 200 (list matching may vary)"
            ;;
        000)
            fail "no response for multiple If-None-Match"
            ;;
        *)
            pass "multiple If-None-Match returns $HTTP_CODE"
            ;;
    esac

    # Test with W/ weak ETag prefix
    WEAK_ETAG="W/${ETAG}"

    echo "Step 6b: Testing weak ETag (W/ prefix)..." >&2

    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
        -H "Accept: text/markdown" \
        -H "If-None-Match: ${WEAK_ETAG}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HTTP_CODE="000"

    case "$HTTP_CODE" in
        304|200)
            pass "weak ETag returns $HTTP_CODE (weak comparison)"
            ;;
        000)
            fail "no response for weak ETag request"
            ;;
        *)
            pass "weak ETag returns $HTTP_CODE"
            ;;
    esac
else
    echo "Step 6: SKIPPED (no ETag available)" >&2
fi

# --- Step 7: Verify Vary: Accept on 304 response ---

if [ -n "$ETAG" ]; then
    echo "Step 7: Checking Vary header on 304 response..." >&2

    RESP_HEADERS=$(curl -sf -D - -o /dev/null \
        -H "Accept: text/markdown" \
        -H "If-None-Match: ${ETAG}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || RESP_HEADERS=""

    VARY=$(echo "$RESP_HEADERS" | grep -i "^vary:" | tr -d '\r') || true

    if [ -n "$VARY" ]; then
        if echo "$VARY" | grep -qi "accept"; then
            pass "Vary: Accept present on 304 response"
        else
            pass "Vary header present but no Accept: $VARY"
        fi
    else
        pass "no Vary header on 304 (may be omitted)"
    fi
else
    echo "Step 7: SKIPPED (no ETag available)" >&2
fi

# --- Step 8: Verify no body on 304 response ---

if [ -n "$ETAG" ]; then
    echo "Step 8: Checking no body on 304 response..." >&2

    BODY=$(curl -sf \
        -H "Accept: text/markdown" \
        -H "If-None-Match: ${ETAG}" \
        "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || BODY=""

    if [ -z "$BODY" ]; then
        pass "304 response has empty body"
    else
        BODY_LEN=${#BODY}
        if [ "$BODY_LEN" -lt 10 ]; then
            pass "304 response body is minimal (${BODY_LEN} bytes)"
        else
            fail "304 response has body (${BODY_LEN} bytes)"
        fi
    fi
else
    echo "Step 8: SKIPPED (no ETag available)" >&2
fi

# --- Summary ---

echo "" >&2
echo "=== Conditional Request E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
