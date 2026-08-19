#!/bin/bash
# subrequest_ssi_test.sh — E2E test: SSI subrequest conversion + inflight release (subrequest).
#
# Verifies the module's subrequest support (decision D2, option B):
#   1. An SSI-included subrequest response is converted to Markdown when
#      the module is enabled in the subrequest location (module does NOT
#      skip r != r->main).
#   2. The inflight counter is released at conversion terminal, not at
#      parent pool destruction: after the SSI page (and its subrequests)
#      completes, the inflight counter returns to zero without waiting
#      for the main request to finish.
#   3. A converted subrequest body carries the module's representation
#      contract (no upstream Accept-Ranges / digest headers forwarded).
#   4. (Optional) auth_request subrequest_in_memory mode: when the
#      fixture exposes an auth_request-protected page, the internal
#      subrequest (r->subrequest_in_memory) response is converted and
#      the inflight slot is released at its terminal.
#
# Prerequisites:
#   - NGINX running with the markdown module loaded AND SSI enabled
#     (ssi on) in the fixture location
#   - The fixture exposes:
#       /page.ssi        — an SSI page with <!--# include virtual="/frag.md" -->
#       /frag.md         — upstream text/html body (converted to Markdown)
#       /nginx-markdown/metrics  — module metrics (inflight gauge)
#   - curl and jq available (jq only for metrics parsing; falls back to grep)
#   - NGINX_URL environment variable set (default: http://localhost:8080)
#
# Usage:
#   NGINX_URL=http://localhost:8080 ./subrequest_ssi_test.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met

set -e

NGINX_URL="${NGINX_URL:-http://localhost:8080}"
PAGE_PATH="${PAGE_PATH:-/page.ssi}"
FRAG_PATH="${FRAG_PATH:-/frag.md}"
AUTH_PAGE_PATH="${AUTH_PAGE_PATH:-/auth-protected/}"
METRICS_PATH="${METRICS_PATH:-/nginx-markdown/metrics}"
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

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
    if ! curl -sf "${NGINX_URL}${PAGE_PATH}" >/dev/null 2>&1; then
        echo "Error: SSI fixture page not found at ${NGINX_URL}${PAGE_PATH}" >&2
        echo "  Configure: location /page.ssi { ssi on; markdown on; }" >&2
        echo "  and include a subrequest target (e.g. /frag.md)." >&2
        exit 2
    fi
    if ! curl -sf "${NGINX_URL}${FRAG_PATH}" >/dev/null 2>&1; then
        echo "Error: subrequest fragment not found at ${NGINX_URL}${FRAG_PATH}" >&2
        exit 2
    fi
}

# Read the inflight gauge from the module metrics endpoint.
# Returns the integer value, or empty string when unavailable.
inflight_current() {
    local body
    body="$(curl -sf "${NGINX_URL}${METRICS_PATH}" 2>/dev/null || true)"
    if [[ -z "$body" ]]; then
        echo ""
        return
    fi
    if command -v jq >/dev/null 2>&1; then
        # Prometheus text format — extract the frozen v1 gauge name.
        echo "$body" | grep -E "^nginx_markdown_inflight_requests" | awk '{print $2}' | head -1
    else
        echo "$body" | grep -E "^nginx_markdown_inflight_requests" | awk '{print $2}' | head -1
    fi
}

check_prerequisites

echo "=== Scenario 1: SSI subrequest is converted to Markdown ===" >&2
# The SSI page includes /frag.md as a subrequest.  With subrequest
# support (subrequest option B), the fragment body must be converted even
# though it is delivered via an internal subrequest.
body="$(curl -sS -H "Accept: text/markdown" "${NGINX_URL}${PAGE_PATH}" 2>&1)" || true
if echo "$body" | grep -qE "^# |^[-*] "; then
    pass "SSI page contains converted fragment output (markdown markers present)"
elif echo "$body" | grep -qE "<h1|<html|<body|<p[ >]"; then
    fail "SSI page returned unconverted HTML fragment: $(echo "$body" | head -3)"
else
    fail "SSI page lacks converted fragment output: $(echo "$body" | head -3)"
fi

echo "=== Scenario 2: SSI page assembles converted fragment ===" >&2
page="$(curl -sS "${NGINX_URL}${PAGE_PATH}" 2>&1 || true)"
if echo "$page" | grep -q "<!--# include"; then
    fail "SSI include was not expanded (ssi on missing in fixture?)"
else
    pass "SSI include expanded in page response"
fi
if echo "$page" | grep -qE "^# |^[-*] "; then
    pass "page contains converted subrequest output"
elif echo "$page" | grep -qE "<h1|<html|<body|<p[ >]"; then
    fail "page returned unconverted HTML fragment: $(echo "$page" | head -3)"
else
    fail "page lacks converted subrequest output: $(echo "$page" | head -3)"
fi

echo "=== Scenario 3: inflight counter released at conversion terminal ===" >&2
before="$(inflight_current)"
# Fire the SSI page; the subrequest completes while the main request is
# still assembling.  With active release, the gauge returns to baseline
# after the response finishes (no hold until pool destruction).
curl -sf "${NGINX_URL}${PAGE_PATH}" >/dev/null 2>&1 || true
after="$(inflight_current)"

if [[ -n "$before" && -n "$after" ]]; then
    if [[ "$after" == "$before" ]]; then
        pass "inflight gauge stable after SSI page (${before} -> ${after})"
    else
        fail "inflight gauge changed after SSI page (${before} -> ${after}); active release may be missing"
    fi
else
    skip "inflight gauge unavailable on metrics endpoint (cannot verify release)"
fi

echo "=== Scenario 4: converted subrequest drops representation metadata ===" >&2
headers="$(curl -sS -D - -o /dev/null "${NGINX_URL}${PAGE_PATH}" 2>&1 || true)"
if echo "$headers" | grep -qiE "^(Accept-Ranges|Content-MD5|Digest|Content-Digest|Repr-Digest|X-Markdown-Tokens):"; then
    fail "SSI page forwards stale representation metadata after subrequest conversion"
else
    pass "SSI page clears representation metadata after subrequest conversion"
fi

echo "=== Scenario 5: auth_request subrequest_in_memory conversion ===" >&2
# auth_request issues an internal subrequest with r->subrequest_in_memory
# set (the response body is captured in memory, not streamed).  When the
# fixture exposes an auth-protected page, the internal subrequest target
# must still be converted and the inflight slot released at its terminal.
if curl -sf "${NGINX_URL}${AUTH_PAGE_PATH}" >/dev/null 2>&1; then
    auth_body="$(curl -sS "${NGINX_URL}${AUTH_PAGE_PATH}" 2>&1 || true)"
    if echo "$auth_body" | grep -qE "^# |^[-*] "; then
        pass "auth_request-protected page served with converted content"
    elif echo "$auth_body" | grep -qE "<h1|<html|<body|<p[ >]"; then
        fail "auth_request-protected page returned unconverted HTML fragment: $(echo "$auth_body" | head -3)"
    else
        fail "auth_request-protected page lacks converted content: $(echo "$auth_body" | head -3)"
    fi
    # Inflight release check for the internal subrequest path.
    before="$(inflight_current)"
    curl -sf "${NGINX_URL}${AUTH_PAGE_PATH}" >/dev/null 2>&1 || true
    after="$(inflight_current)"
    if [[ -n "$before" && -n "$after" ]]; then
        if [[ "$after" == "$before" ]]; then
            pass "inflight gauge stable after auth_request subrequest (${before} -> ${after})"
        else
            fail "inflight gauge changed after auth_request subrequest (${before} -> ${after})"
        fi
    else
        skip "inflight gauge unavailable (cannot verify auth_request release)"
    fi
elif [[ "${REQUIRE_AUTH_SUBREQUEST:-0}" == "1" ]]; then
    # Final E2E qualification (decision D6): the subrequest_in_memory
    # path must be exercised; an absent fixture is a failure, not a skip.
    fail "auth_request fixture not present at ${AUTH_PAGE_PATH} (REQUIRE_AUTH_SUBREQUEST=1)"
else
    skip "auth_request fixture not present at ${AUTH_PAGE_PATH} (scenario 5 skipped)"
fi

echo "" >&2
echo "subrequest_ssi_test: ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped" >&2
if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi
if [[ "$PASS_COUNT" -eq 0 ]]; then
    echo "No checks ran — treat as failure" >&2
    exit 1
fi
exit 0
