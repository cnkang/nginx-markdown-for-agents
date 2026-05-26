#!/usr/bin/env bash
# smoke-test-diagnostics.sh — Output comprehensive diagnostic information when
# a smoke test fails.
#
# Usage:
#   smoke-test-diagnostics.sh
#
# Environment:
#   INSTALL_LOG      Path to the package manager install log file
#   NGINX_VERSION    The NGINX version used for compilation
#   PACKAGE_FILE     The package artifact filename
#
# All diagnostic output goes to stderr. Uses set -x for debug tracing.
#
# Exit codes:
#   0  Diagnostics collected (regardless of findings)
#
# Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9

set -x

##############################################################################
# Helpers
##############################################################################

diag() {
    printf '%s\n' "$1" >&2
}

section() {
    printf '\n=== %s ===\n' "$1" >&2
}

##############################################################################
# Module .so paths (DEB and RPM layouts)
##############################################################################

MODULE_PATH_DEB="/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
MODULE_PATH_RPM="/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"

##############################################################################
# Diagnostics collection
##############################################################################

section "Smoke Test Diagnostic Information"

# Requirement 8.1: nginx -V output
section "nginx -V"
if command -v nginx >/dev/null 2>&1; then
    nginx -V 2>&1 >&2 || diag "nginx -V exited with code $?"
else
    diag "nginx binary not found in PATH"
fi

# Requirement 8.2: nginx -t full error
section "nginx -t"
if command -v nginx >/dev/null 2>&1; then
    nginx -t 2>&1 >&2 || true
else
    diag "nginx binary not found in PATH"
fi

# Requirement 8.3: Module .so path check (file existence + ls -la)
section "Module .so Path Check"

MODULE_FOUND=""

diag "Checking DEB path: ${MODULE_PATH_DEB}"
if [ -f "${MODULE_PATH_DEB}" ]; then
    diag "  File EXISTS"
    ls -la "${MODULE_PATH_DEB}" >&2
    MODULE_FOUND="${MODULE_PATH_DEB}"
else
    diag "  File NOT FOUND"
fi

diag "Checking RPM path: ${MODULE_PATH_RPM}"
if [ -f "${MODULE_PATH_RPM}" ]; then
    diag "  File EXISTS"
    ls -la "${MODULE_PATH_RPM}" >&2
    MODULE_FOUND="${MODULE_PATH_RPM}"
else
    diag "  File NOT FOUND"
fi

if [ -z "${MODULE_FOUND}" ]; then
    diag "WARNING: Module .so not found at either expected path"
fi

# Requirement 8.4: ldd dependency check
section "ldd Dependency Check"
if [ -n "${MODULE_FOUND}" ]; then
    if command -v ldd >/dev/null 2>&1; then
        ldd "${MODULE_FOUND}" >&2 || diag "ldd exited with code $?"
    else
        diag "ldd not available on this system"
    fi
else
    diag "Cannot run ldd: module .so file not found at either path"
fi

# Requirement 8.5: Package manager install log
section "Package Manager Install Log"
if [ -n "${INSTALL_LOG:-}" ] && [ -f "${INSTALL_LOG}" ]; then
    diag "Install log (${INSTALL_LOG}):"
    cat "${INSTALL_LOG}" >&2
elif [ -n "${INSTALL_LOG:-}" ]; then
    diag "INSTALL_LOG set to '${INSTALL_LOG}' but file does not exist"
else
    diag "INSTALL_LOG not set; no install log available"
fi

# Requirement 8.6: Compilation NGINX version
section "Build NGINX Version"
diag "NGINX_VERSION=${NGINX_VERSION:-unknown}"

# Requirement 8.7: Package artifact filename
section "Package Artifact"
diag "PACKAGE_FILE=${PACKAGE_FILE:-unknown}"

# Requirement 8.8: System architecture information
section "System Architecture"
diag "uname -m: $(uname -m 2>&1)"

if command -v dpkg >/dev/null 2>&1; then
    diag "dpkg --print-architecture: $(dpkg --print-architecture 2>&1)"
elif command -v rpm >/dev/null 2>&1; then
    diag "rpm --eval '%%{_arch}': $(rpm --eval '%{_arch}' 2>&1)"
else
    diag "Neither dpkg nor rpm found; cannot determine package architecture"
fi

section "End of Diagnostics"

exit 0
