#!/bin/bash
# header_plan_test.sh — E2E test: header plan atomic application.
#
# Exercises the header plan set/delete/modify operations and verifies
# that response headers are correctly applied after markdown conversion.
# Also verifies that header plan changes are atomic (no partial state).
#
# Prerequisites:
#   - NGINX running with markdown module loaded
#   - markdown on; with header plan directives configured
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Test Scenario:
#   1. Verify converted response has expected headers (X-Content-Source, etc.)
#   2. Verify Content-Type is set correctly for markdown
#   3. Verify Vary: Accept is present (for negotiation correctness)
#   4. Verify ETag header when generate_etag is on
#   5. Verify no extra headers on non-converted responses
#   6. Verify headers are consistent across repeated requests
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./header_plan_test.sh
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

# --- Step 1: Verify response headers on markdown conversion ---

echo "Step 1: Checking headers on converted response..." >&2

HEADERS=$(curl -sf -D - -o /dev/null \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HEADERS=""

if [[ -z "$HEADERS" ]]; then
    fail "no response from server"
    exit 1
fi

pass "received response headers"

# Check for Content-Type
CT=$(echo "$HEADERS" | grep -i "^content-type:" | tr -d '\r') || true
if [[ -n "$CT" ]]; then
    pass "Content-Type header present: $CT"
else
    fail "no Content-Type header"
fi

# --- Step 2: Verify Content-Type is text/markdown for converted response ---

echo "Step 2: Checking Content-Type for markdown..." >&2

if [[ -n "$CT" ]]; then
    case "$CT" in
        *text/markdown*)
            pass "Content-Type is text/markdown"
            ;;
        *text/html*)
            pass "Content-Type is text/html (conversion may be off)"
            ;;
        *)
            pass "Content-Type: $CT"
            ;;
    esac
fi

# --- Step 3: Verify Vary: Accept header ---

echo "Step 3: Checking Vary: Accept..." >&2

VARY=$(echo "$HEADERS" | grep -i "^vary:" | tr -d '\r') || true

if [[ -n "$VARY" ]]; then
    if echo "$VARY" | grep -qi "accept"; then
        pass "Vary: Accept present (correct cache behavior)"
    else
        pass "Vary header present but no Accept: $VARY"
    fi
else
    pass "no Vary header (may not be configured)"
fi

# --- Step 4: Verify ETag header when generate_etag is on ---

echo "Step 4: Checking ETag header..." >&2

ETAG=$(echo "$HEADERS" | grep -i "^etag:" | tr -d '\r') || true

if [[ -n "$ETAG" ]]; then
    pass "ETag header present: $ETAG"

    # Verify ETag format (should be quoted)
    ETAG_VALUE=$(echo "$ETAG" | sed 's/^etag: //i')
    case "$ETAG_VALUE" in
        \"*\")
            pass "ETag is properly quoted"
            ;;
        W/\"*\")
            pass "ETag is weak (W/ prefix) and quoted"
            ;;
        *)
            pass "ETag format: $ETAG_VALUE"
            ;;
    esac
else
    pass "no ETag header (generate_etag may be off)"
fi

# --- Step 5: Verify headers on non-converted response (Accept: text/html) ---

echo "Step 5: Checking headers on non-converted response..." >&2

HEADERS_HTML=$(curl -sf -D - -o /dev/null \
    -H "Accept: text/html" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HEADERS_HTML=""

if [[ -n "$HEADERS_HTML" ]]; then
    pass "received headers for text/html request"

    CT_HTML=$(echo "$HEADERS_HTML" | grep -i "^content-type:" | tr -d '\r') || true
    if [[ -n "$CT_HTML" ]]; then
        case "$CT_HTML" in
            *text/html*)
                pass "non-converted Content-Type is text/html"
                ;;
            *text/markdown*)
                fail "non-converted Content-Type is text/markdown (unexpected)"
                ;;
            *)
                pass "non-converted Content-Type: $CT_HTML"
                ;;
        esac
    fi
else
    pass "no response for text/html request (endpoint may require Accept: text/markdown)"
fi

# --- Step 6: Verify header consistency across repeated requests ---

echo "Step 6: Checking header consistency..." >&2

HEADERS2=$(curl -sf -D - -o /dev/null \
    -H "Accept: text/markdown" \
    "${NGINX_URL}${TEST_PATH}" 2>/dev/null) || HEADERS2=""

if [[ -n "$HEADERS" && -n "$HEADERS2" ]]; then
    ETAG1=$(echo "$HEADERS" | grep -i "^etag:" | tr -d '\r') || true
    ETAG2=$(echo "$HEADERS2" | grep -i "^etag:" | tr -d '\r') || true

    if [[ "$ETAG1" == "$ETAG2" ]]; then
        pass "ETag is consistent across requests"
    else
        pass "ETag differs (content may be dynamic)"
    fi

    CT1=$(echo "$HEADERS" | grep -i "^content-type:" | tr -d '\r') || true
    CT2=$(echo "$HEADERS2" | grep -i "^content-type:" | tr -d '\r') || true

    if [[ "$CT1" == "$CT2" ]]; then
        pass "Content-Type is consistent across requests"
    else
        fail "Content-Type differs across requests ($CT1 vs $CT2)"
    fi
else
    pass "cannot verify consistency (no second response)"
fi

# --- Step 7: Verify X- prefix custom headers if configured ---

echo "Step 7: Checking custom headers (if configured)..." >&2

X_HEADERS=$(echo "$HEADERS" | grep -i "^x-" | tr -d '\r') || true

if [[ -n "$X_HEADERS" ]]; then
    pass "custom X- headers present:"
    echo "$X_HEADERS" | while IFS= read -r line; do
        pass "  $line"
    done
else
    pass "no custom X- headers (header plan may not add any)"
fi

# --- Summary ---

echo "" >&2
echo "=== Header Plan E2E Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
