#!/bin/bash
# ---------------------------------------------------------------------------
# smoke-test.sh — Kubernetes smoke test for nginx-markdown-for-agents
#
# PURPOSE:
#   Verifies basic functionality of the nginx-markdown-for-agents module
#   after Kubernetes deployment. Tests conversion, Accept negotiation,
#   Prometheus metrics accessibility, and pod health.
#
# REQUIREMENTS:
#   - REQ-0700-K8S-005: K8s/Ingress Smoke and E2E verification
#   - Design reference: design.md §12.3
#
# USAGE:
#   ./smoke-test.sh [OPTIONS]
#
# OPTIONS:
#   -u, --url URL        Service base URL (e.g. http://localhost:8080)
#   -n, --namespace NS   Kubernetes namespace (default: ingress-nginx)
#   -l, --label LABEL    Pod label selector (default: app=nginx-markdown)
#   -p, --port PORT      Local port for port-forward (default: 8080)
#   -t, --timeout SECS   Curl timeout in seconds (default: 10)
#   -h, --help           Show this help message
#
# EXAMPLES:
#   # Use kubectl port-forward (default):
#   ./smoke-test.sh
#
#   # Use an explicit service URL:
#   ./smoke-test.sh --url http://nginx-markdown.example.com
#
#   # Custom namespace and label:
#   ./smoke-test.sh --namespace my-ns --label app.kubernetes.io/name=nginx-markdown
#
# EXIT CODES:
#   0  All tests passed
#   1  One or more tests failed
#   2  Usage error or missing prerequisites
#
# NOTES:
#   - Requires: curl, kubectl (if port-forward mode)
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Self-contained; runnable from outside the cluster
#
# SEE ALSO:
#   - examples/kubernetes/manifest/
#   - docs/guides/KUBERNETES_DEPLOYMENT.md
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
NAMESPACE="ingress-nginx"
LABEL_SELECTOR="app=nginx-markdown"
LOCAL_PORT="8080"
CURL_TIMEOUT="10"
BASE_URL=""
PORT_FORWARD_PID=""
PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    sed -n '/^# USAGE:/,/^# EXIT CODES:/{ /^# EXIT CODES:/d; s/^# \{0,3\}//; p; }' "$0" >&2
    return 0
}

log_info() {
    printf '[INFO]  %s\n' "$1" >&2
}

log_pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS]  %s\n' "$1" >&2
}

log_fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[FAIL]  %s\n' "$1" >&2
}

log_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

cleanup() {
    if [ -n "$PORT_FORWARD_PID" ]; then
        kill "$PORT_FORWARD_PID" 2>/dev/null || true
        wait "$PORT_FORWARD_PID" 2>/dev/null || true
        log_info "Stopped port-forward (PID $PORT_FORWARD_PID)"
    fi
}

check_prerequisites() {
    if ! command -v curl >/dev/null 2>&1; then
        log_error "curl is required but not found in PATH"
        return 2
    fi

    if [ -z "$BASE_URL" ]; then
        if ! command -v kubectl >/dev/null 2>&1; then
            log_error "kubectl is required for port-forward mode (or provide --url)"
            return 2
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Port-forward setup
# ---------------------------------------------------------------------------

setup_port_forward() {
    log_info "Setting up kubectl port-forward (namespace=$NAMESPACE, label=$LABEL_SELECTOR, port=$LOCAL_PORT)"

    # Find the first running pod matching the label selector
    local pod_name
    pod_name="$(kubectl get pods -n "$NAMESPACE" -l "$LABEL_SELECTOR" \
        --field-selector=status.phase=Running \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)" || true

    if [ -z "$pod_name" ]; then
        log_error "No running pod found with label '$LABEL_SELECTOR' in namespace '$NAMESPACE'"
        return 1
    fi

    log_info "Found pod: $pod_name"

    kubectl port-forward -n "$NAMESPACE" "$pod_name" "${LOCAL_PORT}:80" >/dev/null 2>&1 &
    PORT_FORWARD_PID=$!

    # Wait for port-forward to be ready
    local retries=0
    while [ $retries -lt 10 ]; do
        if curl -s -o /dev/null --connect-timeout 1 "http://localhost:${LOCAL_PORT}/" 2>/dev/null; then
            log_info "Port-forward ready on localhost:${LOCAL_PORT}"
            return 0
        fi
        retries=$((retries + 1))
        sleep 1
    done

    log_error "Port-forward failed to become ready after 10 seconds"
    return 1
}

# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------

test_health_check() {
    log_info "Test: Pod health check"

    if [ -z "$BASE_URL" ] && [ -n "$PORT_FORWARD_PID" ]; then
        # Port-forward is active, pod is running
        log_pass "Pod is running and port-forward is active"
        return 0
    fi

    # If using explicit URL, verify connectivity
    local http_code
    http_code="$(curl -s -o /dev/null -w '%{http_code}' \
        --connect-timeout "$CURL_TIMEOUT" \
        "${BASE_URL}/" 2>/dev/null)" || true

    if [ -n "$http_code" ] && [ "$http_code" != "000" ]; then
        log_pass "Service is reachable (HTTP $http_code)"
        return 0
    fi

    log_fail "Service is not reachable at ${BASE_URL}/"
    return 1
}

test_markdown_conversion() {
    log_info "Test: Markdown conversion (Accept: text/markdown)"

    local response
    local http_code
    local content_type

    # Send request with Accept: text/markdown
    response="$(curl -s -D - \
        --connect-timeout "$CURL_TIMEOUT" \
        --max-time "$CURL_TIMEOUT" \
        -H "Accept: text/markdown" \
        "${BASE_URL}/test" 2>/dev/null)" || true

    if [ -z "$response" ]; then
        log_fail "Markdown conversion: no response received"
        return 1
    fi

    # Extract HTTP status code from response headers
    http_code="$(printf '%s\n' "$response" | head -1 | grep -o '[0-9][0-9][0-9]' | head -1)" || true

    # Extract Content-Type header (case-insensitive)
    content_type="$(printf '%s\n' "$response" | grep -i '^content-type:' | head -1)" || true

    # Verify response indicates markdown content
    case "$content_type" in
        *text/markdown*|*text/x-markdown*)
            log_pass "Markdown conversion: response Content-Type contains markdown ($content_type)"
            ;;
        *)
            # Check if the body looks like markdown (contains # headers or markdown syntax)
            local body
            body="$(printf '%s' "$response" | sed -n '/^\r*$/,$p' | tail -n +2)" || true
            case "$body" in
                *"# "*|*"## "*|*"- "*|*"* "*)
                    log_pass "Markdown conversion: response body contains markdown syntax"
                    ;;
                *)
                    log_fail "Markdown conversion: response does not appear to be markdown (Content-Type: $content_type)"
                    return 1
                    ;;
            esac
            ;;
    esac

    return 0
}

test_accept_negotiation() {
    log_info "Test: Accept negotiation (Accept: text/html — should NOT convert)"

    local content_type

    # Send request with Accept: text/html — module should pass-through
    content_type="$(curl -s -o /dev/null \
        --connect-timeout "$CURL_TIMEOUT" \
        --max-time "$CURL_TIMEOUT" \
        -H "Accept: text/html" \
        -w '%{content_type}' \
        "${BASE_URL}/test" 2>/dev/null)" || true

    if [ -z "$content_type" ]; then
        log_fail "Accept negotiation: no response received"
        return 1
    fi

    # Verify response is NOT markdown (should be HTML pass-through)
    case "$content_type" in
        *text/markdown*|*text/x-markdown*)
            log_fail "Accept negotiation: response was converted to markdown despite Accept: text/html"
            return 1
            ;;
        *text/html*|*application/*)
            log_pass "Accept negotiation: response is not markdown (Content-Type: $content_type)"
            ;;
        *)
            # Accept any non-markdown content type as pass-through
            log_pass "Accept negotiation: response is not markdown (Content-Type: $content_type)"
            ;;
    esac

    return 0
}

test_metrics_endpoint() {
    log_info "Test: Prometheus metrics endpoint (/metrics)"

    local http_code
    local body

    # Access the /metrics endpoint
    http_code="$(curl -s -o /tmp/smoke_metrics_body.$$ -w '%{http_code}' \
        --connect-timeout "$CURL_TIMEOUT" \
        --max-time "$CURL_TIMEOUT" \
        "${BASE_URL}/metrics" 2>/dev/null)" || true

    body="$(cat /tmp/smoke_metrics_body.$$ 2>/dev/null)" || true
    rm -f /tmp/smoke_metrics_body.$$

    if [ -z "$http_code" ] || [ "$http_code" = "000" ]; then
        log_fail "Metrics endpoint: no response received"
        return 1
    fi

    case "$http_code" in
        200)
            # Verify response contains Prometheus-style metrics
            case "$body" in
                *"# HELP"*|*"# TYPE"*|*_total*|*nginx_markdown*|*nginx_http*|*process_*|*go_*|*promhttp_*)
                    log_pass "Metrics endpoint: accessible and contains Prometheus metrics (HTTP $http_code)"
                    ;;
                *)
                    # 200 but no obvious metrics format — still pass if accessible
                    log_pass "Metrics endpoint: accessible (HTTP $http_code)"
                    ;;
            esac
            ;;
        404)
            log_fail "Metrics endpoint: not found (HTTP 404) — /metrics may not be configured"
            return 1
            ;;
        *)
            log_fail "Metrics endpoint: unexpected status (HTTP $http_code)"
            return 1
            ;;
    esac

    return 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -u|--url)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                BASE_URL="$2"
                shift 2
                ;;
            -n|--namespace)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                NAMESPACE="$2"
                shift 2
                ;;
            -l|--label)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                LABEL_SELECTOR="$2"
                shift 2
                ;;
            -p|--port)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                LOCAL_PORT="$2"
                shift 2
                ;;
            -t|--timeout)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                CURL_TIMEOUT="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                return 2
                ;;
            *)
                log_error "Unexpected argument: $1"
                usage
                return 2
                ;;
        esac
    done

    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    parse_args "$@" || exit $?
    check_prerequisites || exit $?

    trap cleanup EXIT

    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "nginx-markdown-for-agents K8s Smoke Test"
    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2

    # Set up connectivity
    if [ -z "$BASE_URL" ]; then
        setup_port_forward || exit 1
        BASE_URL="http://localhost:${LOCAL_PORT}"
    fi

    log_info "Target URL: ${BASE_URL}"
    printf '\n' >&2

    # Run tests
    test_health_check || true
    test_markdown_conversion || true
    test_accept_negotiation || true
    test_metrics_endpoint || true

    # Summary
    printf '\n' >&2
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2

    if [ "$FAIL_COUNT" -gt 0 ]; then
        return 1
    fi

    return 0
}

main "$@"
