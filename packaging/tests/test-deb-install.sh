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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../scripts/verify-module-load.sh"

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

if [[ ! -f "$DEB_FILE" ]]; then
    echo "Error: file not found: $DEB_FILE" >&2
    exit 2
fi

# --- Step 1: Verify Debian/Ubuntu system ---

echo "Step 1: Checking OS..." >&2

if [[ -f /etc/debian_version ]]; then
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

if [[ "$(id -u)" -ne 0 ]]; then
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
    if [[ -f "${dir}/ngx_http_markdown_filter_module.so" ]]; then
        pass "module .so found at ${dir}/ngx_http_markdown_filter_module.so"
        FOUND_MODULE_PATH="${dir}/ngx_http_markdown_filter_module.so"
        MODULE_FOUND=1
        break
    fi
done

if [[ "$MODULE_FOUND" -eq 0 ]]; then
    # Check dpkg contents for the actual path
    PKG_NAME="$(dpkg-deb -f "$DEB_FILE" Package 2>/dev/null || true)"
    if [[ -n "$PKG_NAME" ]]; then
        SO_PATH=$(dpkg -L "$PKG_NAME" 2>/dev/null | grep '\.so$' | head -1) || SO_PATH=""
    else
        SO_PATH=""
    fi
    if [[ -n "$SO_PATH" ]] && [[ -f "$SO_PATH" ]]; then
        pass "module .so found at $SO_PATH"
        FOUND_MODULE_PATH="$SO_PATH"
    else
        fail "module .so not found in expected paths"
    fi
fi

# The installed NGINX version must EXACTLY match the version this package
# was compiled against (the package filename embeds the target version).
PKG_NGINX_VERSION=""
DEB_BASENAME="$(basename "$DEB_FILE")"
if [[ "$DEB_BASENAME" =~ nginx-([0-9]+\.[0-9]+\.[0-9]+) ]]; then
    PKG_NGINX_VERSION="${BASH_REMATCH[1]}"
fi
# Capture only the numeric version token: distro builds append vendor
# suffixes such as "(Ubuntu)" after the version, which must not leak into
# the exact-match comparison against the package target.
INSTALLED_NGINX_VERSION="$(nginx -v 2>&1 | sed -nE 's|.*nginx/([0-9]+\.[0-9]+\.[0-9]+).*|\1|p')"

if [[ -n "$PKG_NGINX_VERSION" ]] && [ -n "$INSTALLED_NGINX_VERSION" ]; then
    if [[ "$PKG_NGINX_VERSION" = "$INSTALLED_NGINX_VERSION" ]]; then
        pass "NGINX version exactly matches package target ($INSTALLED_NGINX_VERSION)"
    else
        fail "NGINX version mismatch: installed=$INSTALLED_NGINX_VERSION package_target=$PKG_NGINX_VERSION (exact match required)"
    fi
else
    fail "could not determine NGINX version (installed='$INSTALLED_NGINX_VERSION', pkg='$PKG_NGINX_VERSION')"
fi

# --- Step 5: Verify NGINX actually loads the module ---

echo "Step 5: Verifying module loads with nginx -t (load_module + markdown_filter on)..." >&2

if [[ -n "$FOUND_MODULE_PATH" ]]; then
    if verify_module_load nginx "$FOUND_MODULE_PATH"; then
        pass "positive load_module configuration succeeded and the directive required the module"
    else
        fail "module load verification failed: positive or negative configuration was unexpected"
    fi
else
    fail "module .so not found — cannot verify module loading"
fi

# --- Summary ---

echo "" >&2
echo "=== DEB Install Test Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
