#!/bin/bash
#
# End-to-End Test Runner for NGINX Markdown Filter Module
#
# This script runs comprehensive E2E tests with a real NGINX instance
# and backend server. Tests validate the complete request/response cycle:
#
# Client → NGINX (proxy) → Backend Server → NGINX (filter) → Client
#
# Test Coverage:
# - Complete proxy chain with real backend
# - Header propagation through the chain
# - Error handling when backend fails
# - Performance under load
# - Concurrent requests
# - Large responses
# - Streaming/chunked responses
#
# Requirements:
# - NGINX compiled with markdown filter module
# - Rust converter library built
# - Python 3 (for backend server)
# - curl, ab (Apache Bench) for testing
#
# Usage:
#   ./run_e2e_tests.sh
#

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration
TEST_PORT=8889
BACKEND_PORT=9999
NGINX_CONF="/tmp/nginx-markdown-e2e.conf"
NGINX_PID="/tmp/nginx-markdown-e2e.pid"
NGINX_ERROR_LOG="/tmp/nginx-markdown-e2e-error.log"
NGINX_ACCESS_LOG="/tmp/nginx-markdown-e2e-access.log"
BACKEND_PID="/tmp/backend-server.pid"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Performance baseline storage
declare -A PERF_BASELINES

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
    
    # Stop backend server if running
    if [ -f "$BACKEND_PID" ]; then
        PID=$(cat "$BACKEND_PID")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Stopping backend server (PID: $PID)..."
            kill -TERM "$PID" 2>/dev/null || true
            sleep 1
        fi
    fi
    
    # Remove test files
    rm -f "$NGINX_CONF" "$NGINX_PID" "$NGINX_ERROR_LOG" "$NGINX_ACCESS_LOG" "$BACKEND_PID"
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
    echo -e "${BLUE}ℹ${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Start backend server
start_backend() {
    echo "Starting backend server on port $BACKEND_PORT..."
    
    python3 "$SCRIPT_DIR/test_backend_server.py" --port "$BACKEND_PORT" > /tmp/backend-server.log 2>&1 &
    echo $! > "$BACKEND_PID"
    
    # Wait for backend to start
    sleep 2
    
    # Verify backend is running
    if ! curl -s http://localhost:$BACKEND_PORT/health > /dev/null; then
        echo "Failed to start backend server"
        cat /tmp/backend-server.log
        return 1
    fi
    
    echo "Backend server started (PID: $(cat $BACKEND_PID))"
    return 0
}

# Start NGINX with given configuration
start_nginx() {
    local config="$1"
    
    # Write configuration
    cat > "$NGINX_CONF" << EOF
$config
EOF
    
    # Start NGINX
    echo "Starting NGINX on port $TEST_PORT..."
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

# Measure request latency
measure_latency() {
    local path="$1"
    local accept="$2"
    
    local start=$(date +%s%N)
    curl -s -H "Accept: $accept" "http://localhost:$TEST_PORT$path" > /dev/null
    local end=$(date +%s%N)
    
    local latency_ns=$((end - start))
    local latency_ms=$((latency_ns / 1000000))
    
    echo "$latency_ms"
}

#
# Test 1: Complete Proxy Chain - Basic Conversion
#
test_proxy_chain_basic() {
    log_test 1 "Complete Proxy Chain - Basic Conversion"
    
    # Make request through NGINX proxy to backend
    local response=$(make_request "GET" "/simple" "text/markdown" "")
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
        log_pass "Content-Type: text/markdown (converted)"
    else
        log_fail "Content-Type: Expected text/markdown, got $content_type"
    fi
    
    if echo "$vary" | grep -q "Accept"; then
        log_pass "Vary: Accept header present"
    else
        log_fail "Vary: Accept header missing"
    fi
    
    if echo "$body" | grep -q "# Simple Test Page"; then
        log_pass "Body contains Markdown heading"
    else
        log_fail "Body does not contain expected Markdown"
        log_info "Body preview: $(echo "$body" | head -5)"
    fi
    
    if echo "$body" | grep -q "\[Example\](https://example.com)"; then
        log_pass "Links preserved in Markdown format"
    else
        log_warn "Links may not be correctly formatted"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 2: Header Propagation Through Chain
#
test_header_propagation() {
    log_test 2 "Header Propagation Through Chain"
    
    # Make request and check headers
    local response=$(make_request "GET" "/simple" "text/markdown" "")
    local cache_control=$(get_header "$response" "Cache-Control")
    local etag=$(get_header "$response" "ETag")
    
    # Backend sends Cache-Control: public, max-age=3600
    if echo "$cache_control" | grep -q "max-age"; then
        log_pass "Cache-Control header preserved from backend"
    else
        log_warn "Cache-Control: Expected max-age, got $cache_control"
    fi
    
    # Module should generate new ETag (not preserve backend's)
    if [ -n "$etag" ]; then
        if [ "$etag" != '"simple-v1"' ]; then
            log_pass "ETag generated by module (not backend's ETag)"
        else
            log_fail "ETag: Module should generate new ETag, not preserve backend's"
        fi
    else
        log_warn "ETag header not present"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 3: Complex HTML Conversion
#
test_complex_html() {
    log_test 3 "Complex HTML Conversion"
    
    local response=$(make_request "GET" "/complex" "text/markdown" "")
    local status=$(get_status "$response")
    local body=$(get_body "$response")
    
    if [ "$status" = "200" ]; then
        log_pass "Status code: 200 OK"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    # Check for various Markdown elements
    if echo "$body" | grep -q "# Main Heading"; then
        log_pass "H1 heading converted"
    else
        log_fail "H1 heading not found in Markdown"
    fi
    
    if echo "$body" | grep -q "## Subheading"; then
        log_pass "H2 heading converted"
    else
        log_fail "H2 heading not found in Markdown"
    fi
    
    if echo "$body" | grep -q "\*\*complex\*\*"; then
        log_pass "Bold text converted"
    else
        log_warn "Bold text may not be correctly formatted"
    fi
    
    if echo "$body" | grep -q "\*various\*"; then
        log_pass "Italic text converted"
    else
        log_warn "Italic text may not be correctly formatted"
    fi
    
    if echo "$body" | grep -q '```'; then
        log_pass "Code block converted"
    else
        log_warn "Code block may not be correctly formatted"
    fi
    
    # Scripts should be removed
    if echo "$body" | grep -q "console.log"; then
        log_fail "Script content not removed"
    else
        log_pass "Script content removed"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 4: Backend Error Handling
#
test_backend_error() {
    log_test 4 "Backend Error Handling"
    
    # Backend returns 500 error
    local response=$(make_request "GET" "/error" "text/markdown" "")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    
    # Module should NOT convert non-200 responses
    if [ "$status" = "500" ]; then
        log_pass "Status code: 500 (passed through)"
    else
        log_fail "Status code: Expected 500, got $status"
    fi
    
    if echo "$content_type" | grep -q "text/html"; then
        log_pass "Content-Type: text/html (not converted)"
    else
        log_fail "Content-Type: Expected text/html, got $content_type"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 5: Large Response Handling
#
test_large_response() {
    log_test 5 "Large Response Handling"
    
    local response=$(make_request "GET" "/large" "text/markdown" "")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    local body=$(get_body "$response")
    local body_size=${#body}
    
    if [ "$status" = "200" ]; then
        log_pass "Status code: 200 OK"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    # Check if conversion occurred or was bypassed due to size limit
    if echo "$content_type" | grep -q "text/markdown"; then
        log_pass "Large response converted to Markdown"
        log_info "Converted body size: $body_size bytes"
    else
        log_warn "Large response not converted (may exceed size limit)"
        log_info "Original body size: $body_size bytes"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 6: Chunked Transfer Encoding
#
test_chunked_encoding() {
    log_test 6 "Chunked Transfer Encoding"
    
    local response=$(make_request "GET" "/chunked" "text/markdown" "")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    local body=$(get_body "$response")
    
    if [ "$status" = "200" ]; then
        log_pass "Status code: 200 OK"
    else
        log_fail "Status code: Expected 200, got $status"
    fi
    
    if echo "$content_type" | grep -q "text/markdown"; then
        log_pass "Chunked response converted to Markdown"
    else
        log_fail "Content-Type: Expected text/markdown, got $content_type"
    fi
    
    if echo "$body" | grep -q "# Chunked Response"; then
        log_pass "Complete chunked response converted"
    else
        log_fail "Chunked response incomplete or not converted"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 7: Concurrent Requests
#
test_concurrent_requests() {
    log_test 7 "Concurrent Requests"
    
    log_info "Sending 10 concurrent requests..."
    
    # Send 10 concurrent requests
    local pids=()
    for i in {1..10}; do
        (
            response=$(make_request "GET" "/simple" "text/markdown" "")
            status=$(get_status "$response")
            if [ "$status" = "200" ]; then
                echo "OK" > /tmp/concurrent-$i.result
            else
                echo "FAIL" > /tmp/concurrent-$i.result
            fi
        ) &
        pids+=($!)
    done
    
    # Wait for all requests to complete
    for pid in "${pids[@]}"; do
        wait $pid
    done
    
    # Check results
    local success_count=0
    for i in {1..10}; do
        if [ -f /tmp/concurrent-$i.result ]; then
            result=$(cat /tmp/concurrent-$i.result)
            if [ "$result" = "OK" ]; then
                success_count=$((success_count + 1))
            fi
            rm -f /tmp/concurrent-$i.result
        fi
    done
    
    if [ $success_count -eq 10 ]; then
        log_pass "All 10 concurrent requests succeeded"
    else
        log_fail "Only $success_count/10 concurrent requests succeeded"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 8: Performance Baseline - Latency
#
test_performance_latency() {
    log_test 8 "Performance Baseline - Latency"
    
    log_info "Measuring latency for 10 requests..."
    
    # Measure latency for Markdown conversion
    local total_latency_md=0
    for i in {1..10}; do
        latency=$(measure_latency "/simple" "text/markdown")
        total_latency_md=$((total_latency_md + latency))
    done
    local avg_latency_md=$((total_latency_md / 10))
    
    # Measure latency for passthrough (no conversion)
    local total_latency_html=0
    for i in {1..10}; do
        latency=$(measure_latency "/simple" "text/html")
        total_latency_html=$((total_latency_html + latency))
    done
    local avg_latency_html=$((total_latency_html / 10))
    
    # Calculate overhead
    local overhead=$((avg_latency_md - avg_latency_html))
    local overhead_pct=0
    if [ $avg_latency_html -gt 0 ]; then
        overhead_pct=$((overhead * 100 / avg_latency_html))
    fi
    
    log_info "Average latency (Markdown): ${avg_latency_md}ms"
    log_info "Average latency (HTML passthrough): ${avg_latency_html}ms"
    log_info "Conversion overhead: ${overhead}ms (${overhead_pct}%)"
    
    # Store baselines
    PERF_BASELINES[latency_md]=$avg_latency_md
    PERF_BASELINES[latency_html]=$avg_latency_html
    PERF_BASELINES[overhead_ms]=$overhead
    PERF_BASELINES[overhead_pct]=$overhead_pct
    
    # Pass if overhead is reasonable (< 100ms or < 200%)
    if [ $overhead -lt 100 ] || [ $overhead_pct -lt 200 ]; then
        log_pass "Conversion overhead is acceptable"
    else
        log_warn "Conversion overhead may be high"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 9: Performance Baseline - Throughput
#
test_performance_throughput() {
    log_test 9 "Performance Baseline - Throughput"
    
    # Check if ab (Apache Bench) is available
    if ! command -v ab &> /dev/null; then
        log_warn "Apache Bench (ab) not found, skipping throughput test"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return
    fi
    
    log_info "Running throughput test (100 requests, concurrency 10)..."
    
    # Test Markdown conversion throughput
    ab -n 100 -c 10 -H "Accept: text/markdown" \
        "http://localhost:$TEST_PORT/simple" > /tmp/ab-markdown.txt 2>&1
    
    local rps_md=$(grep "Requests per second" /tmp/ab-markdown.txt | awk '{print $4}')
    local time_per_req_md=$(grep "Time per request.*mean\)" /tmp/ab-markdown.txt | awk '{print $4}')
    
    # Test HTML passthrough throughput
    ab -n 100 -c 10 -H "Accept: text/html" \
        "http://localhost:$TEST_PORT/simple" > /tmp/ab-html.txt 2>&1
    
    local rps_html=$(grep "Requests per second" /tmp/ab-html.txt | awk '{print $4}')
    local time_per_req_html=$(grep "Time per request.*mean\)" /tmp/ab-html.txt | awk '{print $4}')
    
    log_info "Throughput (Markdown): ${rps_md} req/s"
    log_info "Throughput (HTML passthrough): ${rps_html} req/s"
    log_info "Time per request (Markdown): ${time_per_req_md}ms"
    log_info "Time per request (HTML): ${time_per_req_html}ms"
    
    # Store baselines
    PERF_BASELINES[rps_md]=$rps_md
    PERF_BASELINES[rps_html]=$rps_html
    PERF_BASELINES[time_per_req_md]=$time_per_req_md
    PERF_BASELINES[time_per_req_html]=$time_per_req_html
    
    log_pass "Throughput baseline measured"
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Test 10: HEAD Request Through Proxy
#
test_head_request() {
    log_test 10 "HEAD Request Through Proxy"
    
    local response=$(make_request "HEAD" "/simple" "text/markdown" "")
    local status=$(get_status "$response")
    local content_type=$(get_header "$response" "Content-Type")
    local content_length=$(get_header "$response" "Content-Length")
    local body=$(get_body "$response")
    
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
    
    if [ -n "$content_length" ] && [ "$content_length" -gt 0 ]; then
        log_pass "Content-Length header present: $content_length bytes"
    else
        log_warn "Content-Length header missing or zero"
    fi
    
    if [ -z "$body" ]; then
        log_pass "No body in HEAD response"
    else
        log_fail "HEAD response should not have body"
    fi
    
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

#
# Main test execution
#
main() {
    echo "=========================================="
    echo "NGINX Markdown Filter - E2E Tests"
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
    
    if ! command -v python3 &> /dev/null; then
        echo "ERROR: python3 not found in PATH"
        exit 1
    fi
    
    if [ ! -f "$SCRIPT_DIR/test_backend_server.py" ]; then
        echo "ERROR: $SCRIPT_DIR/test_backend_server.py not found"
        exit 1
    fi
    
    log_pass "Prerequisites OK"
    
    # Start backend server
    start_backend || exit 1
    
    # Configure and start NGINX
    local nginx_config='
worker_processes 1;
error_log '"$NGINX_ERROR_LOG"' info;
pid '"$NGINX_PID"';

events {
    worker_connections 1024;
}

http {
    access_log '"$NGINX_ACCESS_LOG"';
    
    # Enable markdown filter
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 5s;
    markdown_on_error pass;
    markdown_flavor commonmark;
    markdown_etag on;
    
    server {
        listen '"$TEST_PORT"';
        server_name localhost;
        
        # Proxy to backend server
        location / {
            proxy_pass http://127.0.0.1:'"$BACKEND_PORT"';
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
'
    
    start_nginx "$nginx_config" || exit 1
    
    # Run tests
    test_proxy_chain_basic
    test_header_propagation
    test_complex_html
    test_backend_error
    test_large_response
    test_chunked_encoding
    test_concurrent_requests
    test_performance_latency
    test_performance_throughput
    test_head_request
    
    # Summary
    echo ""
    echo "=========================================="
    echo "Test Summary"
    echo "=========================================="
    echo "Tests run:    $TESTS_RUN"
    echo "Tests passed: $TESTS_PASSED"
    echo "Tests failed: $TESTS_FAILED"
    echo ""
    
    # Performance baselines
    if [ ${#PERF_BASELINES[@]} -gt 0 ]; then
        echo "=========================================="
        echo "Performance Baselines"
        echo "=========================================="
        echo "Latency (Markdown):       ${PERF_BASELINES[latency_md]}ms"
        echo "Latency (HTML):           ${PERF_BASELINES[latency_html]}ms"
        echo "Conversion overhead:      ${PERF_BASELINES[overhead_ms]}ms (${PERF_BASELINES[overhead_pct]}%)"
        
        if [ -n "${PERF_BASELINES[rps_md]}" ]; then
            echo "Throughput (Markdown):    ${PERF_BASELINES[rps_md]} req/s"
            echo "Throughput (HTML):        ${PERF_BASELINES[rps_html]} req/s"
            echo "Time per request (MD):    ${PERF_BASELINES[time_per_req_md]}ms"
            echo "Time per request (HTML):  ${PERF_BASELINES[time_per_req_html]}ms"
        fi
        echo ""
    fi
    
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
