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
#   4. Verify module .so exists and NGINX version exactly matches package target
#   5. Verify NGINX actually loads the module (load_module + markdown_filter on + nginx -t)
#   6. Clean up (optional: remove package)
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

# --- Step 4: Verify module .so exists and NGINX version matches ---

echo "Step 4: Checking module .so and NGINX version match..." >&2

MODULE_PATHS="/usr/lib/nginx/modules /usr/lib64/nginx/modules /usr/local/nginx/modules"
MODULE_FOUND=0
FOUND_MODULE_PATH=""

for dir in $MODULE_PATHS; do
    if [ -f "${dir}/ngx_http_markdown_filter_module.so" ]; then
        pass "module .so found at ${dir}/ngx_http_markdown_filter_module.so"
        FOUND_MODULE_PATH="${dir}/ngx_http_markdown_filter_module.so"
        MODULE_FOUND=1
        break
    fi
done

if [ "$MODULE_FOUND" -eq 0 ]; then
    # Check dpkg contents for the actual path
    SO_PATH=$(dpkg -L nginx-markdown-module 2>/dev/null | grep '\.so$' | head -1) || SO_PATH=""
    if [ -n "$SO_PATH" ] && [ -f "$SO_PATH" ]; then
        pass "module .so found at $SO_PATH"
        FOUND_MODULE_PATH="$SO_PATH"
    else
        fail "module .so not found in expected paths"
    fi
fi

# The installed NGINX version must EXACTLY match the version this package
# was compiled against (the package filename embeds the target version).
PKG_NGINX_VERSION=""
if [[ "$DEB_FILE" =~ nginx-([0-9]+\.[0-9]+\.[0-9]+) ]]; then
    PKG_NGINX_VERSION="${BASH_REMATCH[1]}"
fi
INSTALLED_NGINX_VERSION="$(nginx -v 2>&1 | sed -n 's|.*nginx/||p')"

if [ -n "$PKG_NGINX_VERSION" ] && [ -n "$INSTALLED_NGINX_VERSION" ]; then
    if [ "$PKG_NGINX_VERSION" = "$INSTALLED_NGINX_VERSION" ]; then
        pass "NGINX version exactly matches package target ($INSTALLED_NGINX_VERSION)"
    else
        fail "NGINX version mismatch: installed=$INSTALLED_NGINX_VERSION package_target=$PKG_NGINX_VERSION (exact match required)"
    fi
else
    fail "could not determine NGINX version (installed='$INSTALLED_NGINX_VERSION', pkg='$PKG_NGINX_VERSION')"
fi

# --- Step 5: Verify NGINX actually loads the module ---

echo "Step 5: Verifying module loads with nginx -t (load_module + markdown_filter on)..." >&2

if [ -n "$FOUND_MODULE_PATH" ]; then
    TMP_CONF=$(mktemp "${TMPDIR:-/tmp}/deb-module-XXXXXX.conf") || {
        fail "could not create temp config file"
        exit 1
    }
    trap 'rm -f "$TMP_CONF"' EXIT

    # Reuse the doctor's verification pattern: a minimal config that
    # explicitly load_module's the .so and enables markdown_filter, then
    # runs nginx -t.  This proves the module is loadable and the
    # directive parses — `nginx -V` alone cannot prove dynamic-module
    # loadability.
    cat > "$TMP_CONF" <<CONF
load_module "${FOUND_MODULE_PATH}";
daemon off;
worker_processes 1;
events { worker_connections 64; }
http {
    markdown_filter on;
    server {
        listen 127.0.0.1:19999;
        location / { return 200 "ok"; }
    }
}
CONF

    if nginx -t -c "$TMP_CONF" >/dev/null 2>&1; then
        pass "nginx -t with load_module + markdown_filter on succeeded (module loads)"
    else
        fail "nginx -t with load_module failed — module did not load"
        echo "Note: if nginx was not compiled with --with-compat or the version differs, loading fails." >&2
    fi
    rm -f "$TMP_CONF"
    trap - EXIT
else
    fail "module .so not found — cannot verify module loading"
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
