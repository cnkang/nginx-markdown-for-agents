#!/bin/bash
# filter_ordering_test.sh — E2E test: filter ordering interactions.
#
# Verifies the markdown module's filter position relative to gzip, gunzip,
# Brotli, and proxy_cache (Requirements 10.7 and 15.3).
#
# Prerequisites:
#   - NGINX running with markdown module loaded
#   - gzip, gunzip, brotli, proxy_cache modules available
#   - curl available
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#   - TEST_PATH and GUNZIP_TEST_PATH point to the qualified fixture locations
#   - the gunzip fixture exposes the upstream encoding as
#     X-Upstream-Content-Encoding (override with UPSTREAM_ENCODING_HEADER)
#   - the proxy-cache fixture exposes MISS/HIT as X-Cache-Status (override
#     with CACHE_STATUS_HEADER)
#   - REQUIRE_FILTER_ORDERING_ALL=1 to turn every required-scenario SKIP into
#     a failure (used by canonical/release qualification)
#
# Test Scenarios:
#   1. markdown + gzip: client requests gzip, gets gzip-compressed Markdown
#   2. markdown + gunzip: upstream sends gzip, client gets uncompressed Markdown
#   3. markdown + Brotli: client requests br, gets Brotli-compressed Markdown
#   4. markdown + proxy_cache: cached Markdown served on second request
#   5. markdown + no compression: plain Markdown to client
#
# Usage:
#   NGINX_URL=http://localhost:8080 \
#   TEST_PATH=/markdown GUNZIP_TEST_PATH=/gunzip-markdown \
#   ./filter_ordering_test.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
TEST_PATH_CONFIGURED=0
TEST_PATH="${TEST_PATH:-}"
if [[ -n "$TEST_PATH" ]]; then
    TEST_PATH_CONFIGURED=1
fi
GUNZIP_TEST_PATH_CONFIGURED=0
GUNZIP_TEST_PATH="${GUNZIP_TEST_PATH:-}"
if [[ -n "$GUNZIP_TEST_PATH" ]]; then
    GUNZIP_TEST_PATH_CONFIGURED=1
fi
UPSTREAM_ENCODING_HEADER="${UPSTREAM_ENCODING_HEADER-X-Upstream-Content-Encoding}"
CACHE_STATUS_HEADER="${CACHE_STATUS_HEADER-X-Cache-Status}"
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
REQUIRE_FILTER_ORDERING_ALL="${REQUIRE_FILTER_ORDERING_ALL:-0}"

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

skip() {
    local msg="$1"
    if [[ "$REQUIRE_FILTER_ORDERING_ALL" == "1" ]]; then
        fail "Required filter-ordering assertion skipped: $msg"
        return
    fi
    SKIP_COUNT=$((SKIP_COUNT + 1))
    echo "SKIP: $msg" >&2
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

# Extract header value from curl -D output (case-insensitive)
get_header() {
    local headers="$1"
    local header_name="$2"
    echo "$headers" | tr -d '\r' | grep -i "^${header_name}:" | head -1 | cut -d: -f2- | sed 's/^ *//'
}


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: markdown + gzip — client requests gzip output
# ─────────────────────────────────────────────────────────────────────────────
test_markdown_gzip() {
    echo "--- Test 1: markdown + gzip ---" >&2

    local response headers content_type content_encoding status
    response="$(curl -sS -D - -o /dev/null -H "Accept: text/markdown" -H "Accept-Encoding: gzip" "${NGINX_URL}${TEST_PATH}" 2>&1)" || true
    headers="$response"

    status="$(echo "$headers" | head -1 | awk '{print $2}')"
    content_type="$(get_header "$headers" "Content-Type")"
    content_encoding="$(get_header "$headers" "Content-Encoding")"

    if [[ "$status" != "200" ]]; then
        fail "Expected status 200, got $status"
        return
    fi

    if echo "$content_type" | grep -iq "text/markdown"; then
        pass "Content-Type is text/markdown"
    else
        fail "Expected Content-Type text/markdown, got: $content_type"
    fi

    if echo "$content_encoding" | grep -iq "gzip"; then
        pass "Content-Encoding is gzip (Markdown compressed by gzip filter)"
    else
        skip "Content-Encoding is not gzip (gzip module may not be loaded): $content_encoding"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Test 2: markdown + gunzip — upstream sends gzip, markdown converts
# ─────────────────────────────────────────────────────────────────────────────
test_markdown_gunzip() {
    echo "--- Test 2: markdown + gunzip (upstream gzip) ---" >&2

    # Request with Accept: text/markdown; upstream sends gzip-encoded HTML
    # via the test configuration (the client does NOT request gzip, so the
    # upstream gzip scenario is preserved without the client negotiating it).
    local response headers content_type content_encoding status upstream_encoding
    response="$(curl -sS -D - -o /dev/null -H "Accept: text/markdown" "${NGINX_URL}${GUNZIP_TEST_PATH}" 2>&1)" || true
    headers="$response"

    status="$(echo "$headers" | head -1 | awk '{print $2}')"
    content_type="$(get_header "$headers" "Content-Type")"
    content_encoding="$(get_header "$headers" "Content-Encoding")"
    upstream_encoding="$(get_header "$headers" "$UPSTREAM_ENCODING_HEADER")"

    if [[ "$status" != "200" ]]; then
        fail "Expected status 200, got $status"
        return
    fi

    if echo "$content_type" | grep -iq "text/markdown"; then
        pass "Content-Type is text/markdown (upstream gzip decompressed + converted)"
    else
        fail "Expected Content-Type text/markdown, got: $content_type"
    fi

    # The client should NOT receive Content-Encoding: gzip (decompressed by gunzip or markdown)
    if echo "$content_encoding" | grep -iq "gzip"; then
        fail "Client received Content-Encoding: gzip (expected decompressed Markdown)"
    else
        pass "No Content-Encoding: gzip on client response (decompressed upstream)"
    fi

    if [[ -z "$UPSTREAM_ENCODING_HEADER" ]]; then
        skip "Upstream gzip assertion disabled explicitly"
    elif echo "$upstream_encoding" | grep -iq "gzip"; then
        pass "Fixture exposed upstream Content-Encoding: gzip"
    else
        fail "Expected ${UPSTREAM_ENCODING_HEADER}: gzip to prove the upstream was compressed; got: ${upstream_encoding:-<missing>}"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Test 3: markdown + Brotli — client requests Brotli output
# ─────────────────────────────────────────────────────────────────────────────
test_markdown_brotli() {
    echo "--- Test 3: markdown + Brotli ---" >&2

    local response headers content_type content_encoding status
    response="$(curl -sS -D - -o /dev/null -H "Accept: text/markdown" -H "Accept-Encoding: br" "${NGINX_URL}${TEST_PATH}" 2>&1)" || true
    headers="$response"

    status="$(echo "$headers" | head -1 | awk '{print $2}')"
    content_type="$(get_header "$headers" "Content-Type")"
    content_encoding="$(get_header "$headers" "Content-Encoding")"

    if [[ "$status" != "200" ]]; then
        fail "Expected status 200, got $status"
        return
    fi

    if echo "$content_type" | grep -iq "text/markdown"; then
        pass "Content-Type is text/markdown"
    else
        fail "Expected Content-Type text/markdown, got: $content_type"
    fi

    if echo "$content_encoding" | grep -iq "br"; then
        pass "Content-Encoding is br (Markdown compressed by Brotli filter)"
    else
        skip "Content-Encoding is not br (Brotli module may not be loaded): $content_encoding"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Test 4: markdown + proxy_cache — cached Markdown served on second request
# ─────────────────────────────────────────────────────────────────────────────
test_markdown_proxy_cache() {
    echo "--- Test 4: markdown + proxy_cache ---" >&2

    # Unique cache-busting query parameter so both requests share one fresh
    # cache entry instead of reusing a stale entry from a previous run.
    local cache_buster
    cache_buster="cb=$$-$(date +%s%N 2>/dev/null || date +%s)"
    local cache_path
    if [[ "$TEST_PATH" == *\?* ]]; then
        cache_path="${TEST_PATH}&${cache_buster}"
    else
        cache_path="${TEST_PATH}?${cache_buster}"
    fi

    # First request: should convert and cache
    local response1 headers1 content_type1 cache_status1 status1
    response1="$(curl -sS -D - -o /dev/null -H "Accept: text/markdown" "${NGINX_URL}${cache_path}" 2>&1)" || true
    headers1="$response1"
    status1="$(echo "$headers1" | head -1 | awk '{print $2}')"
    content_type1="$(get_header "$headers1" "Content-Type")"
    cache_status1="$(get_header "$headers1" "$CACHE_STATUS_HEADER")"

    if [[ "$status1" != "200" ]]; then
        fail "First request: expected status 200, got $status1"
        return
    fi

    if echo "$content_type1" | grep -iq "text/markdown"; then
        pass "First request: Content-Type is text/markdown"
    else
        fail "First request: expected text/markdown, got: $content_type1"
    fi

    # Second request: should serve from cache with same Content-Type
    local response2 headers2 content_type2 cache_status2 status2
    response2="$(curl -sS -D - -o /dev/null -H "Accept: text/markdown" "${NGINX_URL}${cache_path}" 2>&1)" || true
    headers2="$response2"
    status2="$(echo "$headers2" | head -1 | awk '{print $2}')"
    content_type2="$(get_header "$headers2" "Content-Type")"
    cache_status2="$(get_header "$headers2" "$CACHE_STATUS_HEADER")"

    if [[ "$status2" != "200" ]]; then
        fail "Second request: expected status 200, got $status2"
        return
    fi

    if echo "$content_type2" | grep -iq "text/markdown"; then
        pass "Second request (cached): Content-Type is text/markdown"
    else
        fail "Second request (cached): expected text/markdown, got: $content_type2"
    fi

    # Verify both responses have the same Content-Type
    if [[ "$content_type1" == "$content_type2" ]]; then
        pass "Cached response Content-Type matches first response"
    else
        fail "Content-Type mismatch: first=$content_type1, cached=$content_type2"
    fi

    if [[ -z "$CACHE_STATUS_HEADER" ]]; then
        skip "Proxy-cache status assertion disabled explicitly"
    elif [[ "$cache_status1" == "MISS" && "$cache_status2" == "HIT" ]]; then
        pass "Proxy-cache status changed from MISS to HIT"
    else
        fail "Expected ${CACHE_STATUS_HEADER}: MISS then HIT, got: ${cache_status1:-<missing>} then ${cache_status2:-<missing>}"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Test 5: markdown + no compression — plain Markdown to client
# ─────────────────────────────────────────────────────────────────────────────
test_markdown_no_compression() {
    echo "--- Test 5: markdown + no compression ---" >&2

    local response headers content_type content_encoding status
    response="$(curl -sS -D - -H "Accept: text/markdown" "${NGINX_URL}${TEST_PATH}" 2>&1)" || true

    # Split headers (headers end at first empty line)
    # Normalize CRLF line endings so header splitting works correctly
    headers="$(echo "$response" | tr -d '\r' | sed '/^$/q')"

    status="$(echo "$headers" | head -1 | awk '{print $2}')"
    content_type="$(get_header "$headers" "Content-Type")"
    content_encoding="$(get_header "$headers" "Content-Encoding")"

    if [[ "$status" != "200" ]]; then
        fail "Expected status 200, got $status"
        return
    fi

    if echo "$content_type" | grep -iq "text/markdown"; then
        pass "Content-Type is text/markdown (uncompressed)"
    else
        fail "Expected Content-Type text/markdown, got: $content_type"
    fi

    # Without Accept-Encoding, there should be no Content-Encoding
    if [[ -z "$content_encoding" ]] || echo "$content_encoding" | grep -iq "identity"; then
        pass "No Content-Encoding (uncompressed Markdown)"
    else
        skip "Content-Encoding present: $content_encoding (server may force compression)"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

main() {
    if [[ "${TEST_PATH_CONFIGURED}" -ne 1 ]] \
        || [[ "${GUNZIP_TEST_PATH_CONFIGURED}" -ne 1 ]]; then
        echo "Error: TEST_PATH and GUNZIP_TEST_PATH are required for filter-ordering qualification" >&2
        exit 2
    fi

    check_prerequisites

    echo "Running filter ordering E2E tests against ${NGINX_URL}" >&2
    echo >&2

    test_markdown_gzip
    test_markdown_gunzip
    test_markdown_brotli
    test_markdown_proxy_cache
    test_markdown_no_compression
    echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped" >&2

    if [[ ${FAIL_COUNT} -gt 0 ]] || [[ ${PASS_COUNT} -eq 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
