#!/bin/bash
#
# Integration Test Runner for NGINX Markdown Filter Module
#
# This script runs end-to-end integration tests with a real NGINX instance.
# Tests validate the complete request/response cycle including:
# - Content negotiation
# - HTML to Markdown conversion
# - HTTP header handling
# - Configuration inheritance
# - Conditional requests
# - Authenticated content
#
# Requirements:
# - NGINX compiled with markdown filter module
# - Rust converter library built
# - curl installed
# - Test corpus available
#
# Usage:
#   ./run_integration_tests.sh
#

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
NATIVE_BUILD_HELPER="${REPO_ROOT}/tools/lib/nginx_markdown_native_build.sh"
source "${NATIVE_BUILD_HELPER}"

# Configuration
TEST_PORT=8888
NGINX_CONF="/tmp/nginx-markdown-test.conf"
NGINX_PID="/tmp/nginx-markdown-test.pid"
NGINX_ERROR_LOG="/tmp/nginx-markdown-test-error.log"
NGINX_ACCESS_LOG="/tmp/nginx-markdown-test-access.log"
STATIC_ROOT="/tmp/nginx-markdown-test-root"
RANGE_HTML_PATH="${STATIC_ROOT}/range.html"
NGINX_BIN="${NGINX_BIN:-}"
DELEGATED_NGINX_BIN=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Reused literals
SEPARATOR_LINE="=========================================="
MEDIA_TYPE_HTML="text/html"
MEDIA_TYPE_MARKDOWN="text/markdown"
HEADER_CONTENT_TYPE="Content-Type"
STATUS_CODE_OK_MESSAGE="Status code: 200 OK"
NGINX_START_FAILURE_MSG="Failed to start NGINX"
CONFIG_ERROR_LOG_LINE="error_log ${NGINX_ERROR_LOG} debug;"
CONFIG_PID_LINE="pid ${NGINX_PID};"
VAR_TOGGLE_HTML="<html><body><h1>Var Toggle</h1></body></html>"
AUTH_PRIVATE_HTML="<html><body><h1>Private</h1></body></html>"

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    local pid
    
    # Stop NGINX if running
    if [[ -f "$NGINX_PID" ]]; then
        pid=$(cat "$NGINX_PID")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping NGINX (PID: $pid)..."
            kill -QUIT "$pid" 2>/dev/null || true
            sleep 1
        fi
    fi
    
    # Remove test files
    rm -f "$NGINX_CONF" "$NGINX_PID" "$NGINX_ERROR_LOG" "$NGINX_ACCESS_LOG"
    rm -rf "$STATIC_ROOT"
    return 0
}

# Set trap to cleanup on exit
trap cleanup EXIT INT TERM

resolve_nginx_bin() {
    if [[ -n "$NGINX_BIN" ]]; then
        if [[ ! -x "$NGINX_BIN" ]]; then
            echo "ERROR: NGINX_BIN is not executable: $NGINX_BIN" >&2
            return 1
        fi
        return 0
    fi

    if command -v nginx &> /dev/null; then
        NGINX_BIN="$(command -v nginx)"
        return 0
    fi

    echo "ERROR: nginx not found in PATH and NGINX_BIN is not set" >&2
    return 1
}

resolve_delegated_nginx_bin() {
    if markdown_can_reuse_nginx_bin "$NGINX_BIN"; then
        DELEGATED_NGINX_BIN="$NGINX_BIN"
        log_info "Delegated runtime checks will reuse $DELEGATED_NGINX_BIN"
    else
        DELEGATED_NGINX_BIN=""
        log_info "Delegated runtime checks will self-build NGINX because $NGINX_BIN does not expose reusable runtime assets"
    fi

    return 0
}

# Helper functions
log_test() {
    local test_id="$1"
    local test_name="$2"

    echo ""
    echo "$SEPARATOR_LINE"
    echo "Test $test_id: $test_name"
    echo "$SEPARATOR_LINE"
    TESTS_RUN=$((TESTS_RUN + 1))
    return 0
}

log_pass() {
    local message="$1"
    echo -e "${GREEN}✓${NC} $message"
    return 0
}

log_fail() {
    local message="$1"
    echo -e "${RED}✗${NC} $message"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    return 0
}

log_info() {
    local message="$1"
    echo -e "${YELLOW}ℹ${NC} $message"
    return 0
}

run_external_check() {
    local test_id="$1"
    local test_name="$2"
    shift 2

    log_test "$test_id" "$test_name"

    if [[ -n "$DELEGATED_NGINX_BIN" ]]; then
        if env NGINX_BIN="$DELEGATED_NGINX_BIN" "$@"; then
            log_pass "$test_name"
            TESTS_PASSED=$((TESTS_PASSED + 1))
            return 0
        fi
    elif env -u NGINX_BIN "$@"; then
        log_pass "$test_name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    fi

    log_fail "$test_name"
    return 1
}

write_static_html() {
    local path="$1"
    local content="$2"

    mkdir -p "$STATIC_ROOT"
    printf '%s\n' "$content" > "$path"
    return 0
}

extract_json_number_field() {
    local json="$1"
    local field="$2"
    local compact match

    compact=$(printf "%s" "$json" | tr -d '\n')
    match=$(printf "%s" "$compact" | grep -oE "\"${field}\"[[:space:]]*:[[:space:]]*[0-9]+" | head -n1 || true)
    if [[ -z "$match" ]]; then
        return 1
    fi

    printf '%s\n' "$match" | sed -E 's/.*:[[:space:]]*//'
    return 0
}

# Start NGINX with given configuration
start_nginx() {
    local config="$1"
    local pid
    local nginx_rc=0

    mkdir -p /tmp/logs
    
    # Write configuration
    cat > "$NGINX_CONF" << EOF
$config
EOF
    
    # Start NGINX
    echo "Starting NGINX..."
    set +e
    "$NGINX_BIN" -c "$NGINX_CONF" -p /tmp 2>&1 | tee /tmp/nginx-start.log
    nginx_rc=${PIPESTATUS[0]}
    set -e
    if [[ $nginx_rc -ne 0 ]]; then
        echo "Failed to start NGINX" >&2
        cat /tmp/nginx-start.log >&2
        return 1
    fi
    
    # Wait for NGINX to start
    sleep 2
    
    # Verify NGINX is running
    if [[ ! -f "$NGINX_PID" ]]; then
        echo "NGINX PID file not found" >&2
        return 1
    fi
    
    pid=$(cat "$NGINX_PID")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "NGINX process not running" >&2
        return 1
    fi
    
    echo "NGINX started (PID: $pid)"
    return 0
}

# Stop NGINX
stop_nginx() {
    local pid
    if [[ -f "$NGINX_PID" ]]; then
        pid=$(cat "$NGINX_PID")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping NGINX (PID: $pid)..."
            kill -QUIT "$pid" 2>/dev/null || true
            sleep 1
        fi
    fi
    return 0
}

# Make HTTP request and return response
make_request() {
    local method="$1"
    local path="$2"
    local accept="$3"
    shift 3
    
    curl -s -i -X "$method" \
        -H "Accept: $accept" \
        "$@" \
        "http://localhost:$TEST_PORT$path"
    return 0
}

# Extract header value from response
get_header() {
    local response="$1"
    local header_name="$2"
    
    echo "$response" | grep -i "^$header_name:" | head -1 | cut -d' ' -f2- | tr -d '\r\n'
    return 0
}

# Extract status code from response
get_status() {
    local response="$1"
    echo "$response" | head -1 | cut -d' ' -f2
    return 0
}

# Extract body from response
get_body() {
    local response="$1"
    echo "$response" | sed -n '/^\r$/,$p' | tail -n +2
    return 0
}

#
# Test 1: Basic Conversion with Accept: text/markdown
#
test_basic_conversion() {
    log_test 1 "Basic Conversion with Accept: text/markdown"

    write_static_html "${STATIC_ROOT}/basic.html" \
        '<html><body><h1>Test Heading</h1><p>Test paragraph.</p></body></html>'
    
    local config='
worker_processes 1;
'"$CONFIG_ERROR_LOG_LINE"'
'"$CONFIG_PID_LINE"'
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    server {
        listen '"$TEST_PORT"';
        location = /test {
            alias '"${STATIC_ROOT}"'/basic.html;
            markdown_filter on;
            default_type '"${MEDIA_TYPE_HTML}"';
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }
    
    # Make request
    local response=$(make_request "GET" "/test" "$MEDIA_TYPE_MARKDOWN")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    local vary=$(get_header "$response" "Vary")
    local body=$(get_body "$response")
    
    # Verify results
    if [[ "$status" == "200" ]]; then
        log_pass "$STATUS_CODE_OK_MESSAGE"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "$MEDIA_TYPE_MARKDOWN"; then
        log_pass "Content-Type: ${MEDIA_TYPE_MARKDOWN}"
    else
        log_fail "Content-Type: Expected text/markdown, got $content_type"
    fi
    
    if echo "$vary" | grep -q "Accept"; then
        log_pass "Vary: Accept header present"
    else
        log_fail "Vary: Accept header missing"
    fi
    
    if echo "$body" | grep -q "# Test Heading"; then
        log_pass "Body contains Markdown heading"
    else
        log_fail "Body does not contain expected Markdown"
        log_info "Body: $body"
    fi
    
    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Test 2: Passthrough with Accept: text/html
#
test_passthrough() {
    log_test 2 "Passthrough with Accept: text/html"

    write_static_html "${STATIC_ROOT}/passthrough.html" \
        '<html><body><h1>Test</h1></body></html>'
    
    local config='
worker_processes 1;
'"$CONFIG_ERROR_LOG_LINE"'
'"$CONFIG_PID_LINE"'
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    server {
        listen '"$TEST_PORT"';
        location = /test {
            alias '"${STATIC_ROOT}"'/passthrough.html;
            markdown_filter on;
            default_type '"${MEDIA_TYPE_HTML}"';
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }
    
    # Make request
    local response=$(make_request "GET" "/test" "$MEDIA_TYPE_HTML")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    local body=$(get_body "$response")
    
    # Verify results
    if [[ "$status" == "200" ]]; then
        log_pass "$STATUS_CODE_OK_MESSAGE"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "$MEDIA_TYPE_HTML"; then
        log_pass "Content-Type: ${MEDIA_TYPE_HTML} (unchanged)"
    else
        log_fail "Content-Type: Expected ${MEDIA_TYPE_HTML}, got $content_type"
    fi
    
    if echo "$body" | grep -q "<html>"; then
        log_pass "Body is HTML (not converted)"
    else
        log_fail "Body was converted (should be passthrough)"
    fi
    
    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Test 3: Configuration Inheritance
#
test_configuration_inheritance() {
    log_test 3 "Configuration Inheritance"

    write_static_html "${STATIC_ROOT}/enabled.html" \
        '<html><body><h1>Enabled</h1></body></html>'
    write_static_html "${STATIC_ROOT}/disabled.html" \
        '<html><body><h1>Disabled</h1></body></html>'
    
    local config='
worker_processes 1;
'"$CONFIG_ERROR_LOG_LINE"'
'"$CONFIG_PID_LINE"'
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    markdown_filter on;
    markdown_max_size 10m;
    
    server {
        listen '"$TEST_PORT"';
        
        location = /enabled {
            alias '"${STATIC_ROOT}"'/enabled.html;
            default_type '"${MEDIA_TYPE_HTML}"';
        }
        
        location = /disabled {
            markdown_filter off;
            alias '"${STATIC_ROOT}"'/disabled.html;
            default_type '"${MEDIA_TYPE_HTML}"';
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }
    
    # Test /enabled - should convert
    local response=$(make_request "GET" "/enabled" "$MEDIA_TYPE_MARKDOWN")
    local content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    
    if echo "$content_type" | grep -q "$MEDIA_TYPE_MARKDOWN"; then
        log_pass "/enabled: Conversion occurs (inherits http-level setting)"
    else
        log_fail "/enabled: Expected conversion, got $content_type"
    fi
    
    # Test /disabled - should NOT convert
    response=$(make_request "GET" "/disabled" "$MEDIA_TYPE_MARKDOWN")
    content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    
    if echo "$content_type" | grep -q "$MEDIA_TYPE_HTML"; then
        log_pass "/disabled: No conversion (location override)"
    else
        log_fail "/disabled: Expected no conversion, got $content_type"
    fi
    
    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Test 4: Authenticated Content Handling
#
test_authenticated_content() {
    log_test 4 "Authenticated Content Handling"
    local config

    write_static_html "${STATIC_ROOT}/auth.html" "${AUTH_PRIVATE_HTML}"

    config="$(cat <<EOF
worker_processes 1;
${CONFIG_ERROR_LOG_LINE}
${CONFIG_PID_LINE}
events { worker_connections 1024; }
http {
    access_log ${NGINX_ACCESS_LOG};
    server {
        listen ${TEST_PORT};
        location = /test {
            alias ${STATIC_ROOT}/auth.html;
            markdown_filter on;
            markdown_auth_policy allow;
            add_header Cache-Control private always;
            default_type ${MEDIA_TYPE_HTML};
        }
    }
}
EOF
)"
    
    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }
    
    # Make request with Authorization header
    local response=$(make_request "GET" "/test" "$MEDIA_TYPE_MARKDOWN" -H "Authorization: Bearer token123")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    local cache_control=$(get_header "$response" "Cache-Control")
    
    # Verify results
    if [[ "$status" == "200" ]]; then
        log_pass "$STATUS_CODE_OK_MESSAGE"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "$MEDIA_TYPE_MARKDOWN"; then
        log_pass "Content-Type: ${MEDIA_TYPE_MARKDOWN} (conversion occurs)"
    else
        log_fail "Content-Type: Expected text/markdown, got $content_type"
    fi
    
    if echo "$cache_control" | grep -q "private"; then
        log_pass "Cache-Control: private header present"
    else
        log_fail "Cache-Control: Expected private, got $cache_control"
    fi
    
    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Test 5: Variable-Driven markdown_filter
#
test_variable_driven_markdown_filter() {
    log_test 5 "Variable-driven markdown_filter resolution"

    local config
    write_static_html "${STATIC_ROOT}/variable.html" "${VAR_TOGGLE_HTML}"
    config="$(cat <<EOF
worker_processes 1;
${CONFIG_ERROR_LOG_LINE}
${CONFIG_PID_LINE}
events { worker_connections 1024; }
http {
    access_log ${NGINX_ACCESS_LOG};

    map \$arg_md \$markdown_enabled {
        default "maybe";
        "1" " on ";
        "0" "off";
        "true" "yes";
    }

    server {
        listen ${TEST_PORT};
        location = /test {
            alias ${STATIC_ROOT}/variable.html;
            markdown_filter \$markdown_enabled;
            default_type ${MEDIA_TYPE_HTML};
        }
    }
}
EOF
)"

    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }

    local response
    local content_type

    response=$(make_request "GET" "/test?md=1" "$MEDIA_TYPE_MARKDOWN")
    content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    if echo "$content_type" | grep -q "$MEDIA_TYPE_MARKDOWN"; then
        log_pass "md=1 enables conversion (trimmed \" on \" value)"
    else
        log_fail "md=1 should convert, got $content_type"
    fi

    response=$(make_request "GET" "/test?md=0" "$MEDIA_TYPE_MARKDOWN")
    content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    if echo "$content_type" | grep -q "$MEDIA_TYPE_HTML"; then
        log_pass "md=0 disables conversion"
    else
        log_fail "md=0 should not convert, got $content_type"
    fi

    response=$(make_request "GET" "/test?md=true" "$MEDIA_TYPE_MARKDOWN")
    content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    if echo "$content_type" | grep -q "$MEDIA_TYPE_MARKDOWN"; then
        log_pass "md=true enables conversion (yes/true mapping)"
    else
        log_fail "md=true should convert, got $content_type"
    fi

    response=$(make_request "GET" "/test?md=bad" "$MEDIA_TYPE_MARKDOWN")
    content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    if echo "$content_type" | grep -q "$MEDIA_TYPE_HTML"; then
        log_pass "Invalid variable value safely disables conversion"
    else
        log_fail "Invalid value should not convert, got $content_type"
    fi

    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Test 6: Real NGINX Range bypass
#
test_range_bypass() {
    log_test 6 "Range request bypass with real NGINX file serving"

    write_static_html "$RANGE_HTML_PATH" \
        '<html><body><h1>Range Content</h1><p>This body should stay HTML when Range is used.</p></body></html>'

    local config
    config="$(cat <<EOF
worker_processes 1;
${CONFIG_ERROR_LOG_LINE}
${CONFIG_PID_LINE}
events { worker_connections 1024; }
http {
    access_log ${NGINX_ACCESS_LOG};
    server {
        listen ${TEST_PORT};
        location /range.html {
            root ${STATIC_ROOT};
            markdown_filter on;
            default_type ${MEDIA_TYPE_HTML};
        }
    }
}
EOF
)"

    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }

    local status
    local content_type
    local body

    local response
    response=$(make_request "GET" "/range.html" "$MEDIA_TYPE_MARKDOWN" -H "Range: bytes=0-31")
    status=$(get_status "$response")
    content_type=$(get_header "$response" "$HEADER_CONTENT_TYPE")
    body=$(get_body "$response")

    if [[ "$status" == "206" ]]; then
        log_pass "Status code: 206 Partial Content"
    else
        log_fail "Status code: Expected 206, got $status"
    fi

    if echo "$content_type" | grep -q "$MEDIA_TYPE_HTML"; then
        log_pass "Content-Type remains ${MEDIA_TYPE_HTML} for Range bypass"
    else
        log_fail "Content-Type: Expected ${MEDIA_TYPE_HTML}, got $content_type"
    fi

    if echo "$body" | grep -q "<html><body><h1>Range"; then
        log_pass "Body remains original HTML"
    else
        log_fail "Body should remain HTML for Range bypass"
        log_info "Body: $body"
    fi

    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Test 7: Shared metrics aggregation across workers
#
test_metrics_shared_aggregation() {
    log_test 7 "Shared metrics aggregation across workers"

    local config
    local total_requests=40
    local metrics
    local attempted
    local completed

    write_static_html "${STATIC_ROOT}/metrics.html" \
        '<html><body><h1>Shared Metrics</h1><p>Aggregation path.</p></body></html>'

    config="$(cat <<EOF
worker_processes 2;
${CONFIG_ERROR_LOG_LINE}
${CONFIG_PID_LINE}
events {
    worker_connections 1024;
    accept_mutex off;
}
http {
    access_log ${NGINX_ACCESS_LOG};
    server {
        listen ${TEST_PORT};
        location = /test {
            alias ${STATIC_ROOT}/metrics.html;
            markdown_filter on;
            default_type ${MEDIA_TYPE_HTML};
        }
        location /markdown-metrics {
            markdown_metrics;
        }
    }
}
EOF
)"

    start_nginx "$config" || { log_fail "$NGINX_START_FAILURE_MSG"; return 1; }

    seq "$total_requests" | xargs -I{} -P 8 sh -c \
        "curl -s -H 'Accept: ${MEDIA_TYPE_MARKDOWN}' 'http://localhost:${TEST_PORT}/test?req={}' > /dev/null" || true

    metrics=$(curl -s -H "Accept: application/json" "http://localhost:${TEST_PORT}/markdown-metrics")
    if ! attempted=$(extract_json_number_field "$metrics" "conversions_attempted"); then
        log_fail "Failed to parse conversions_attempted from metrics JSON"
        log_info "Metrics: $metrics"
        stop_nginx
        return 1
    fi
    if ! completed=$(extract_json_number_field "$metrics" "conversion_completed"); then
        log_fail "Failed to parse conversion_completed from metrics JSON"
        log_info "Metrics: $metrics"
        stop_nginx
        return 1
    fi

    if [[ "$attempted" == "$total_requests" ]]; then
        log_pass "Shared metrics aggregate all worker attempts (${attempted})"
    else
        log_fail "Expected conversions_attempted=${total_requests}, got ${attempted}"
        log_info "Metrics: $metrics"
    fi

    if [[ "$completed" == "$total_requests" ]]; then
        log_pass "Shared metrics report completed conversions (${completed})"
    else
        log_fail "Expected conversion_completed=${total_requests}, got ${completed}"
        log_info "Metrics: $metrics"
    fi

    if echo "$metrics" | grep -q '"conversion_latency_buckets"'; then
        log_pass "Metrics JSON exposes latency buckets"
    else
        log_fail "Metrics JSON missing conversion_latency_buckets"
        log_info "Metrics: $metrics"
    fi

    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
}

#
# Main test execution
#
main() {
    echo "$SEPARATOR_LINE"
    echo "NGINX Markdown Filter - Integration Tests"
    echo "$SEPARATOR_LINE"
    echo ""
    
    # Check prerequisites
    log_info "Checking prerequisites..."
    
    if ! resolve_nginx_bin; then
        return 1
    fi
    resolve_delegated_nginx_bin
    
    if ! command -v curl &> /dev/null; then
        echo "ERROR: curl not found in PATH" >&2
        return 1
    fi
    
    log_pass "Prerequisites OK"
    
    # Run tests
    test_basic_conversion
    test_passthrough
    test_configuration_inheritance
    test_authenticated_content
    test_variable_driven_markdown_filter
    test_range_bypass
    test_metrics_shared_aggregation
    if ! run_external_check 8 "Delegated If-Modified-Since runtime validation" \
        "${REPO_ROOT}/tools/ci/verify_real_nginx_ims.sh"; then
        :
    fi
    if ! run_external_check 9 "Chunked streaming native smoke validation" \
        "${REPO_ROOT}/tools/e2e/verify_chunked_streaming_native_e2e.sh" --profile smoke; then
        :
    fi
    if ! run_external_check 10 "Large response native validation" \
        "${REPO_ROOT}/tools/e2e/verify_large_markdown_response_e2e.sh"; then
        :
    fi
    
    # Summary
    echo ""
    echo "$SEPARATOR_LINE"
    echo "Test Summary"
    echo "$SEPARATOR_LINE"
    echo "Tests run:    $TESTS_RUN"
    echo "Tests passed: $TESTS_PASSED"
    echo "Tests failed: $TESTS_FAILED"
    echo ""
    
    if [[ $TESTS_FAILED -eq 0 ]]; then
        echo -e "${GREEN}All tests passed!${NC}"
        return 0
    else
        echo -e "${RED}Some tests failed!${NC}"
        return 1
    fi
}

# Run main
main "$@"
exit $?
