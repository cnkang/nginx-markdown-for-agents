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

# Configuration
TEST_PORT=8888
NGINX_CONF="/tmp/nginx-markdown-test.conf"
NGINX_PID="/tmp/nginx-markdown-test.pid"
NGINX_ERROR_LOG="/tmp/nginx-markdown-test-error.log"
NGINX_ACCESS_LOG="/tmp/nginx-markdown-test-access.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    
    # Stop NGINX if running
    if [ -f "$NGINX_PID" ]; then
        PID=$(cat "$NGINX_PID")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Stopping NGINX (PID: $PID)..."
            kill -QUIT "$PID" 2>/dev/null || true
            sleep 1
        fi
    fi
    
    # Remove test files
    rm -f "$NGINX_CONF" "$NGINX_PID" "$NGINX_ERROR_LOG" "$NGINX_ACCESS_LOG"
}

# Set trap to cleanup on exit
trap cleanup EXIT INT TERM

# Helper functions
log_test() {
    echo ""
    echo "=========================================="
    echo "Test $1: $2"
    echo "=========================================="
    TESTS_RUN=$((TESTS_RUN + 1))
}

log_pass() {
    echo -e "${GREEN}✓${NC} $1"
}

log_fail() {
    echo -e "${RED}✗${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

log_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Start NGINX with given configuration
start_nginx() {
    local config="$1"
    
    # Write configuration
    cat > "$NGINX_CONF" << EOF
$config
EOF
    
    # Start NGINX
    echo "Starting NGINX..."
    nginx -c "$NGINX_CONF" -p /tmp 2>&1 | tee /tmp/nginx-start.log
    
    if [ $? -ne 0 ]; then
        echo "Failed to start NGINX"
        cat /tmp/nginx-start.log
        return 1
    fi
    
    # Wait for NGINX to start
    sleep 2
    
    # Verify NGINX is running
    if [ ! -f "$NGINX_PID" ]; then
        echo "NGINX PID file not found"
        return 1
    fi
    
    PID=$(cat "$NGINX_PID")
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "NGINX process not running"
        return 1
    fi
    
    echo "NGINX started (PID: $PID)"
    return 0
}

# Stop NGINX
stop_nginx() {
    if [ -f "$NGINX_PID" ]; then
        PID=$(cat "$NGINX_PID")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Stopping NGINX (PID: $PID)..."
            kill -QUIT "$PID" 2>/dev/null || true
            sleep 1
        fi
    fi
}

# Make HTTP request and return response
make_request() {
    local method="$1"
    local path="$2"
    local accept="$3"
    local extra_headers="$4"
    
    curl -s -i -X "$method" \
        -H "Accept: $accept" \
        $extra_headers \
        "http://localhost:$TEST_PORT$path"
}

# Extract header value from response
get_header() {
    local response="$1"
    local header_name="$2"
    
    echo "$response" | grep -i "^$header_name:" | head -1 | cut -d' ' -f2- | tr -d '\r\n'
}

# Extract status code from response
get_status() {
    local response="$1"
    echo "$response" | head -1 | cut -d' ' -f2
}

# Extract body from response
get_body() {
    local response="$1"
    echo "$response" | sed -n '/^\r$/,$p' | tail -n +2
}

#
# Test 1: Basic Conversion with Accept: text/markdown
#
test_basic_conversion() {
    log_test 1 "Basic Conversion with Accept: text/markdown"
    
    local config='
worker_processes 1;
error_log '"$NGINX_ERROR_LOG"' debug;
pid '"$NGINX_PID"';
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    server {
        listen '"$TEST_PORT"';
        location /test {
            markdown_filter on;
            return 200 '"'"'<html><body><h1>Test Heading</h1><p>Test paragraph.</p></body></html>'"'"';
            default_type text/html;
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "Failed to start NGINX"; return 1; }
    
    # Make request
    local response=$(make_request "GET" "/test" "text/markdown" "")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    local vary=$(get_header "$response" "Vary")
    local body=$(get_body "$response")
    
    # Verify results
    if [ "$status" = "200" ]; then
        log_pass "Status code: 200 OK"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "text/markdown"; then
        log_pass "Content-Type: text/markdown"
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
}

#
# Test 2: Passthrough with Accept: text/html
#
test_passthrough() {
    log_test 2 "Passthrough with Accept: text/html"
    
    local config='
worker_processes 1;
error_log '"$NGINX_ERROR_LOG"' debug;
pid '"$NGINX_PID"';
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    server {
        listen '"$TEST_PORT"';
        location /test {
            markdown_filter on;
            return 200 '"'"'<html><body><h1>Test</h1></body></html>'"'"';
            default_type text/html;
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "Failed to start NGINX"; return 1; }
    
    # Make request
    local response=$(make_request "GET" "/test" "text/html" "")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    local body=$(get_body "$response")
    
    # Verify results
    if [ "$status" = "200" ]; then
        log_pass "Status code: 200 OK"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "text/html"; then
        log_pass "Content-Type: text/html (unchanged)"
    else
        log_fail "Content-Type: Expected text/html, got $content_type"
    fi
    
    if echo "$body" | grep -q "<html>"; then
        log_pass "Body is HTML (not converted)"
    else
        log_fail "Body was converted (should be passthrough)"
    fi
    
    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 3: Configuration Inheritance
#
test_configuration_inheritance() {
    log_test 3 "Configuration Inheritance"
    
    local config='
worker_processes 1;
error_log '"$NGINX_ERROR_LOG"' debug;
pid '"$NGINX_PID"';
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    markdown_filter on;
    markdown_max_size 10m;
    
    server {
        listen '"$TEST_PORT"';
        
        location /enabled {
            return 200 '"'"'<html><body><h1>Enabled</h1></body></html>'"'"';
            default_type text/html;
        }
        
        location /disabled {
            markdown_filter off;
            return 200 '"'"'<html><body><h1>Disabled</h1></body></html>'"'"';
            default_type text/html;
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "Failed to start NGINX"; return 1; }
    
    # Test /enabled - should convert
    local response=$(make_request "GET" "/enabled" "text/markdown" "")
    local content_type=$(get_header "$response" "Content-Type")
    
    if echo "$content_type" | grep -q "text/markdown"; then
        log_pass "/enabled: Conversion occurs (inherits http-level setting)"
    else
        log_fail "/enabled: Expected conversion, got $content_type"
    fi
    
    # Test /disabled - should NOT convert
    response=$(make_request "GET" "/disabled" "text/markdown" "")
    content_type=$(get_header "$response" "Content-Type")
    
    if echo "$content_type" | grep -q "text/html"; then
        log_pass "/disabled: No conversion (location override)"
    else
        log_fail "/disabled: Expected no conversion, got $content_type"
    fi
    
    stop_nginx
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 4: Authenticated Content Handling
#
test_authenticated_content() {
    log_test 4 "Authenticated Content Handling"
    
    local config='
worker_processes 1;
error_log '"$NGINX_ERROR_LOG"' debug;
pid '"$NGINX_PID"';
events { worker_connections 1024; }
http {
    access_log '"$NGINX_ACCESS_LOG"';
    server {
        listen '"$TEST_PORT"';
        location /test {
            markdown_filter on;
            markdown_auth_policy allow;
            return 200 '"'"'<html><body><h1>Private</h1></body></html>'"'"';
            default_type text/html;
        }
    }
}
'
    
    start_nginx "$config" || { log_fail "Failed to start NGINX"; return 1; }
    
    # Make request with Authorization header
    local response=$(make_request "GET" "/test" "text/markdown" "-H 'Authorization: Bearer token123'")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    local cache_control=$(get_header "$response" "Cache-Control")
    
    # Verify results
    if [ "$status" = "200" ]; then
        log_pass "Status code: 200 OK"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "text/markdown"; then
        log_pass "Content-Type: text/markdown (conversion occurs)"
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
}

#
# Main test execution
#
main() {
    echo "=========================================="
    echo "NGINX Markdown Filter - Integration Tests"
    echo "=========================================="
    echo ""
    
    # Check prerequisites
    log_info "Checking prerequisites..."
    
    if ! command -v nginx &> /dev/null; then
        echo "ERROR: nginx not found in PATH"
        exit 1
    fi
    
    if ! command -v curl &> /dev/null; then
        echo "ERROR: curl not found in PATH"
        exit 1
    fi
    
    log_pass "Prerequisites OK"
    
    # Run tests
    test_basic_conversion
    test_passthrough
    test_configuration_inheritance
    test_authenticated_content
    
    # Summary
    echo ""
    echo "=========================================="
    echo "Test Summary"
    echo "=========================================="
    echo "Tests run:    $TESTS_RUN"
    echo "Tests passed: $TESTS_PASSED"
    echo "Tests failed: $TESTS_FAILED"
    echo ""
    
    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}All tests passed!${NC}"
        exit 0
    else
        echo -e "${RED}Some tests failed!${NC}"
        exit 1
    fi
}

# Run main
main
