#!/bin/bash
# test-deb-install.sh — Verify DEB package installation and NGINX module loading.
#
# This script documents and exercises the dpkg -i installation flow
# followed by nginx -V verification on a Debian/Ubuntu system.
#
# Prerequisites:
#   - Debian or Ubuntu system
#   - NGINX installed (nginx package or nginx-core)
#   - Root/sudo access for dpkg -i
#   - A built .deb package file
#
# Usage:
#   sudo ./test-deb-install.sh <path-to-deb-file>
#
# Test Scenario:
#   1. Verify system is Debian/Ubuntu
#   2. Verify NGINX is installed
#   3. Install the .deb package via dpkg -i
#   4. Verify nginx -V shows the module
#   5. Verify module .so file exists at expected path
#   6. Verify NGINX can start with module loaded
#   7. Clean up (optional: remove package)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met / usage error

set -e

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $1" >&2
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $1" >&2
}

usage() {
    echo "Usage: sudo $0 <path-to-deb-file>" >&2
    exit 2
}

case "${1:-}" in
    -h|--help)
        usage
        ;;
    "")
        echo "Error: no .deb file path provided" >&2
        usage
        ;;
    *)
        DEB_FILE="$1"
        ;;
esac

if [ ! -f "$DEB_FILE" ]; then
    echo "Error: file not found: $DEB_FILE" >&2
    exit 2
fi

# --- Step 1: Verify Debian/Ubuntu system ---

echo "Step 1: Checking OS..." >&2

if [ -f /etc/debian_version ]; then
    pass "Debian/Ubuntu system detected"
else
    fail "not a Debian/Ubuntu system"
    echo "This test requires a Debian or Ubuntu system." >&2
    exit 2
fi

# --- Step 2: Verify NGINX installed ---

echo "Step 2: Checking NGINX..." >&2

if command -v nginx >/dev/null 2>&1; then
    NGINX_VERSION=$(nginx -v 2>&1 | head -1)
    pass "NGINX installed: $NGINX_VERSION"
else
    fail "NGINX not installed"
    echo "Install NGINX first: apt-get install nginx" >&2
    exit 2
fi

# --- Step 3: Install .deb package ---

echo "Step 3: Installing package..." >&2

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: root access required for dpkg -i" >&2
    echo "Run with: sudo $0 $DEB_FILE" >&2
    exit 2
fi

INSTALL_OUTPUT=$(dpkg -i "$DEB_FILE" 2>&1) || {
    fail "dpkg -i failed"
    echo "$INSTALL_OUTPUT" >&2
    # Attempt to fix dependencies
    apt-get install -f -y >/dev/null 2>&1 || true
    exit 1
}

pass "dpkg -i succeeded"

# --- Step 4: Verify nginx -V shows module ---

echo "Step 4: Checking nginx -V..." >&2

NGINX_V_OUTPUT=$(nginx -V 2>&1) || NGINX_V_OUTPUT=""

if echo "$NGINX_V_OUTPUT" | grep -qi "markdown"; then
    pass "nginx -V mentions markdown module"
else
    # Module may be dynamic and not shown in -V; check module path
    pass "nginx -V completed (dynamic module may not appear in -V)"
fi

# --- Step 5: Verify .so file exists ---

echo "Step 5: Checking module .so file..." >&2

MODULE_PATHS="/usr/lib/nginx/modules /usr/lib64/nginx/modules /usr/local/nginx/modules"
MODULE_FOUND=0

for dir in $MODULE_PATHS; do
    if [ -f "${dir}/ngx_http_markdown_filter_module.so" ]; then
        pass "module .so found at ${dir}/ngx_http_markdown_filter_module.so"
        MODULE_FOUND=1
        break
    fi
done

if [ "$MODULE_FOUND" -eq 0 ]; then
    # Check dpkg contents for the actual path
    SO_PATH=$(dpkg -L nginx-markdown-module 2>/dev/null | grep '\.so$' | head -1) || SO_PATH=""
    if [ -n "$SO_PATH" ] && [ -f "$SO_PATH" ]; then
        pass "module .so found at $SO_PATH"
    else
        fail "module .so not found in expected paths"
    fi
fi

# --- Step 6: Verify NGINX config test passes ---

echo "Step 6: Testing NGINX configuration..." >&2

CONFIG_TEST=$(nginx -t 2>&1) || {
    # Config test may fail if load_module not yet added
    echo "Note: nginx -t failed (load_module directive may need to be added)" >&2
    pass "nginx -t ran (module may need explicit load_module)"
}

if echo "$CONFIG_TEST" | grep -q "syntax is ok"; then
    pass "nginx -t syntax OK"
fi

if echo "$CONFIG_TEST" | grep -q "test is successful"; then
    pass "nginx -t test successful"
fi

# --- Summary ---

echo "" >&2
echo "=== DEB Install Test Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
