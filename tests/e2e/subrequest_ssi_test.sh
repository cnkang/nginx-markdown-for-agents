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
#   - curl and awk available
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
EXPECT_SSI_WRAPPER="${EXPECT_SSI_WRAPPER:-0}"
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
    if ! curl -fsS "${NGINX_URL}${METRICS_PATH}" >/dev/null 2>&1; then
        echo "Error: module metrics endpoint not found at ${NGINX_URL}${METRICS_PATH}" >&2
        exit 2
    fi
}

# Return a metrics snapshot or fail the calling scenario.  The metrics
# endpoint is a prerequisite because the scenarios below prove both the
# conversion decision and its terminal delivery.
metrics_snapshot() {
    curl -fsS "${NGINX_URL}${METRICS_PATH}"
}

# Sum all samples whose line starts with the supplied metric prefix.
metric_sum() {
    local metrics="$1"
    local metric_prefix="$2"
    awk -v metric_prefix="$metric_prefix" '
        index($0, metric_prefix) == 1 {
            total += $NF
            found = 1
        }
        END {
            if (found) {
                printf "%.0f\n", total
            }
        }
    ' <<< "$metrics"
}

conversion_attempts_total() {
    local metrics="$1"
    metric_sum "$metrics" 'nginx_markdown_conversion_attempts_total{engine="'
}

converted_terminal_total() {
    local metrics="$1"
    metric_sum "$metrics" 'nginx_markdown_requests_total{outcome="converted",stage="conversion",reason="converted"}'
}

counter_increased() {
    local before="$1"
    local after="$2"
    [[ "$before" =~ ^[0-9]+$ && "$after" =~ ^[0-9]+$ ]] \
        && (( after > before ))
}

# Read the inflight gauge from the module metrics endpoint.
# Returns the integer value, or empty string when unavailable.
inflight_current() {
    local body
    if ! body="$(metrics_snapshot 2>/dev/null)"; then
        echo ""
        return 0
    fi
    if [[ -z "$body" ]]; then
        echo ""
        return
    fi
    # Prometheus text format — extract the runtime inflight gauge.
    echo "$body" | grep -E "^nginx_markdown_inflight_requests" | awk '{print $2}' | head -1
}

# Poll for a bounded interval so the request-side gauge has time to settle
# after a subrequest completes.  A single immediate sample can race the
# metrics update and make a healthy release look like a leak.
wait_for_inflight_baseline() {
    local expected="$1"
    local current=""
    local attempts=40

    [[ "$expected" =~ ^[0-9]+$ ]] || return 1
    while (( attempts > 0 )); do
        current="$(inflight_current)"
        if [[ "$current" == "$expected" ]]; then
            printf '%s\n' "$current"
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 0.25
    done
    printf '%s\n' "$current"
    return 1
}

check_prerequisites

echo "=== Scenario 1: SSI subrequest is converted to Markdown ===" >&2
# The SSI page includes /frag.md as a subrequest.  With subrequest
# support (subrequest option B), the fragment body must be converted even
# though it is delivered via an internal subrequest.
metrics_before=""
metrics_before="$(metrics_snapshot)" || {
    fail "initial SSI metrics snapshot failed"
    metrics_before=""
}
attempts_before="$(conversion_attempts_total "$metrics_before")"
terminals_before="$(converted_terminal_total "$metrics_before")"
if ! body="$(curl -fsS -H "Accept: text/markdown" "${NGINX_URL}${PAGE_PATH}")"; then
    fail "SSI page request failed"
else
    if echo "$body" | grep -qE "^# |^[-*] "; then
        pass "SSI page contains converted fragment output (markdown markers present)"
    elif [[ "$EXPECT_SSI_WRAPPER" == "1" ]] \
        && echo "$body" | grep -qE "# SSI fragment|converted subrequest content"; then
        pass "SSI parent template retained and converted fragment was included"
    elif echo "$body" | grep -qE "<h1|<html|<body|<p[ >]"; then
        fail "SSI page returned unconverted HTML fragment: $(echo "$body" | head -3)"
    else
        fail "SSI page lacks converted fragment output: $(echo "$body" | head -3)"
    fi

    metrics_after=""
    metrics_after="$(metrics_snapshot)" || {
        fail "post-SSI metrics snapshot failed"
        metrics_after=""
    }
    attempts_after="$(conversion_attempts_total "$metrics_after")"
    terminals_after="$(converted_terminal_total "$metrics_after")"
    if counter_increased "$attempts_before" "$attempts_after" \
        && counter_increased "$terminals_before" "$terminals_after"; then
        pass "SSI conversion and terminal counters advanced (${attempts_before}->${attempts_after}, ${terminals_before}->${terminals_after})"
    else
        fail "SSI conversion counters did not prove one conversion and terminal delivery (${attempts_before}->${attempts_after}, ${terminals_before}->${terminals_after})"
    fi
fi

echo "=== Scenario 2: SSI page assembles converted fragment ===" >&2
if ! page="$(curl -fsS "${NGINX_URL}${PAGE_PATH}")"; then
    fail "SSI page assembly request failed"
else
    if echo "$page" | grep -q "<!--# include"; then
        fail "SSI include was not expanded (ssi on missing in fixture?)"
    else
        pass "SSI include expanded in page response"
    fi
    if echo "$page" | grep -qE "^# |^[-*] "; then
        pass "page contains converted subrequest output"
    elif [[ "$EXPECT_SSI_WRAPPER" == "1" ]] \
        && echo "$page" | grep -qE "# SSI fragment|converted subrequest content"; then
        pass "page retained the parent template and included converted subrequest output"
    elif echo "$page" | grep -qE "<h1|<html|<body|<p[ >]"; then
        fail "page returned unconverted HTML fragment: $(echo "$page" | head -3)"
    else
        fail "page lacks converted subrequest output: $(echo "$page" | head -3)"
    fi
fi

echo "=== Scenario 3: inflight counter released at conversion terminal ===" >&2
before="$(inflight_current)"
# Fire the SSI page; the subrequest completes while the main request is
# still assembling.  With active release, the gauge returns to baseline
# after the response finishes (no hold until pool destruction).
if ! curl -fsS "${NGINX_URL}${PAGE_PATH}" >/dev/null; then
    fail "SSI inflight recovery request failed"
else
    after=""
    if after="$(wait_for_inflight_baseline "$before")"; then
        pass "inflight gauge stable after SSI page (${before} -> ${after})"
    else
        fail "inflight gauge did not return to its baseline (${before} -> ${after}); active release may be missing"
    fi
fi

echo "=== Scenario 4: converted subrequest drops representation metadata ===" >&2
if ! headers="$(curl -fsS -D - -o /dev/null "${NGINX_URL}${PAGE_PATH}")"; then
    fail "SSI metadata verification request failed"
else
    if echo "$headers" | grep -qiE "^(Accept-Ranges|Content-MD5|Digest|Content-Digest|Repr-Digest|X-Markdown-Tokens):"; then
        fail "SSI page forwards stale representation metadata after subrequest conversion"
    else
        pass "SSI page clears representation metadata after subrequest conversion"
    fi
fi

echo "=== Scenario 5: auth_request subrequest_in_memory conversion ===" >&2
# auth_request issues an internal subrequest with r->subrequest_in_memory
# set (the response body is captured in memory, not streamed).  When the
# fixture exposes an auth-protected page, the internal subrequest target
# must still be converted and the inflight slot released at its terminal.
auth_metrics_before=""
auth_metrics_before="$(metrics_snapshot)" || {
    fail "initial auth_request metrics snapshot failed"
    auth_metrics_before=""
}
auth_attempts_before="$(conversion_attempts_total "$auth_metrics_before")"
auth_terminals_before="$(converted_terminal_total "$auth_metrics_before")"
# Probe the fixture without curl's fail-on-error flag: any HTTP status is a
# real observation.  Only 404 counts as fixture absence; other error statuses
# (401, 500, ...) mean the fixture exists but misbehaves.
auth_body_file="$(mktemp)"
auth_http_status=""
auth_curl_rc=0
auth_http_status="$(curl -sS -o "${auth_body_file}" -w '%{http_code}' \
    "${NGINX_URL}${AUTH_PAGE_PATH}" 2>/dev/null)" || auth_curl_rc=$?
if [[ "${auth_curl_rc}" -ne 0 ]]; then
    # A transport error (connection refused, timeout, ...) is not fixture
    # absence: fail immediately instead of treating the missing status as
    # an absent fixture.
    rm -f "${auth_body_file}"
    fail "auth_request fixture probe failed with curl exit ${auth_curl_rc} (transport error, not a 404)"
    exit 1
fi
auth_http_status="${auth_http_status:-000}"
auth_body=""
if [[ -n "${auth_http_status}" && -s "${auth_body_file}" ]]; then
    auth_body="$(cat "${auth_body_file}")"
fi
rm -f "${auth_body_file}"
if [[ "${auth_http_status}" == "200" ]]; then
    if echo "$auth_body" | grep -qE "^# |^[-*] "; then
        pass "auth_request-protected page served with converted content"
    elif echo "$auth_body" | grep -qE "<h1|<html|<body|<p[ >]"; then
        fail "auth_request-protected page returned unconverted HTML fragment: $(echo "$auth_body" | head -3)"
    else
        fail "auth_request-protected page lacks converted content: $(echo "$auth_body" | head -3)"
    fi
    # Inflight release check for the internal subrequest path.
    before="$(inflight_current)"
    if ! curl -fsS "${NGINX_URL}${AUTH_PAGE_PATH}" >/dev/null; then
        fail "auth_request inflight recovery request failed"
    else
        after=""
        if after="$(wait_for_inflight_baseline "$before")"; then
            pass "inflight gauge stable after auth_request subrequest (${before} -> ${after})"
        else
            fail "inflight gauge did not return to its baseline (${before} -> ${after})"
        fi
    fi
    auth_metrics_after=""
    auth_metrics_after="$(metrics_snapshot)" || {
        fail "post-auth_request metrics snapshot failed"
        auth_metrics_after=""
    }
    auth_attempts_after="$(conversion_attempts_total "$auth_metrics_after")"
    auth_terminals_after="$(converted_terminal_total "$auth_metrics_after")"
    if counter_increased "$auth_attempts_before" "$auth_attempts_after" \
        && counter_increased "$auth_terminals_before" "$auth_terminals_after"; then
        pass "auth_request conversion and terminal counters advanced"
    else
        fail "auth_request counters did not prove conversion and terminal delivery"
    fi
elif [[ "${auth_http_status}" != "404" && -n "${auth_http_status}" ]]; then
    # A fixture that answers with any status other than 200/404 exists but
    # does not behave like the scenario fixture.
    fail "auth_request fixture at ${AUTH_PAGE_PATH} answered HTTP ${auth_http_status}; expected 200"
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
