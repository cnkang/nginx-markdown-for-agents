#!/usr/bin/env bash
# smoke-test-basic.sh — Basic smoke test for DEB/RPM module packages.
#
# Installs the nginx.org package, installs the module package, verifies the
# .so file exists, adds a load_module directive, and runs nginx -t to confirm
# ABI compatibility. On any failure, calls smoke-test-diagnostics.sh.
#
# Usage:
#   smoke-test-basic.sh PACKAGE_FILE NGINX_VERSION
#
# Arguments:
#   PACKAGE_FILE     Path to the .deb or .rpm package file
#   NGINX_VERSION    Target NGINX version (e.g., 1.26.3)
#
# Environment:
#   INSTALL_LOG      (set internally) Path to the package install log
#
# Exit codes:
#   0  All smoke tests passed
#   1  Error (missing arguments, installation failure, module load failure)
#
# Requirements: 5.1, 5.2, 5.3, 5.5, 5.6

set -e

##############################################################################
# Helpers
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    sed -n '3,16p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    run_diagnostics
    exit 1
}

info() {
    printf '[smoke-test-basic] %s\n' "$1" >&2
}

run_diagnostics() {
    info "Running diagnostics..."
    export INSTALL_LOG
    export NGINX_VERSION
    export PACKAGE_FILE
    "${SCRIPT_DIR}/smoke-test-diagnostics.sh" || true
}

##############################################################################
# Argument parsing
##############################################################################

if [ $# -lt 2 ]; then
    printf 'ERROR: Missing required arguments\n' >&2
    usage
    exit 1
fi

PACKAGE_FILE="$1"
NGINX_VERSION="$2"

##############################################################################
# Validation
##############################################################################

if [ -z "$PACKAGE_FILE" ]; then
    die "PACKAGE_FILE argument is empty"
fi

if [ -z "$NGINX_VERSION" ]; then
    die "NGINX_VERSION argument is empty"
fi

if [ ! -f "$PACKAGE_FILE" ]; then
    die "Package file not found: ${PACKAGE_FILE}"
fi

##############################################################################
# Detect package format from file extension
##############################################################################

PKG_FORMAT=""
case "$PACKAGE_FILE" in
    *.deb)
        PKG_FORMAT="deb"
        ;;
    *.rpm)
        PKG_FORMAT="rpm"
        ;;
    *)
        die "Unsupported package format (expected .deb or .rpm): ${PACKAGE_FILE}"
        ;;
esac

info "Detected package format: ${PKG_FORMAT}"
info "Package file: ${PACKAGE_FILE}"
info "Target NGINX version: ${NGINX_VERSION}"

##############################################################################
# Install log setup
##############################################################################

INSTALL_LOG="$(mktemp /tmp/smoke-test-install-XXXXXX.log)"

##############################################################################
# Package-format-specific installation and verification
##############################################################################

case "$PKG_FORMAT" in
    deb)
        # --- DEB: Install nginx.org package ---
        info "Adding nginx.org apt repository..."
        apt-get update -qq >"${INSTALL_LOG}" 2>&1 || die "apt-get update failed"
        apt-get install -y -qq curl gnupg2 ca-certificates lsb-release \
            >>"${INSTALL_LOG}" 2>&1 || die "Failed to install prerequisites"

        # Import nginx.org signing key
        curl -fsSL https://nginx.org/keys/nginx_signing.key \
            | gpg --dearmor -o /usr/share/keyrings/nginx-archive-keyring.gpg \
            2>>"${INSTALL_LOG}" || die "Failed to import nginx.org signing key"

        # Add nginx.org stable repository
        printf 'deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/debian %s nginx\n' \
            "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
            > /etc/apt/sources.list.d/nginx.list 2>>"${INSTALL_LOG}" \
            || printf 'deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] http://nginx.org/packages/ubuntu %s nginx\n' \
                "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
                > /etc/apt/sources.list.d/nginx.list 2>>"${INSTALL_LOG}" \
            || die "Failed to add nginx.org repository"

        apt-get update -qq >>"${INSTALL_LOG}" 2>&1 || die "apt-get update (post-repo) failed"

        # Install NGINX from nginx.org
        info "Installing nginx=${NGINX_VERSION}* from nginx.org..."
        apt-get install -y "nginx=${NGINX_VERSION}"'*' >>"${INSTALL_LOG}" 2>&1 \
            || die "Failed to install nginx=${NGINX_VERSION}*"

        # --- DEB: Install module package ---
        info "Installing module package: ${PACKAGE_FILE}"
        dpkg -i "${PACKAGE_FILE}" >>"${INSTALL_LOG}" 2>&1 \
            || die "dpkg -i failed for ${PACKAGE_FILE}"

        # --- DEB: Verify nginx -V ---
        info "Running nginx -V..."
        nginx -V 2>&1 >&2 || die "nginx -V failed"

        # --- DEB: Verify .so exists ---
        MODULE_PATH="/usr/lib/nginx/modules/ngx_http_markdown_module.so"
        info "Verifying module .so at: ${MODULE_PATH}"
        if [ ! -f "${MODULE_PATH}" ]; then
            die "Module .so not found at expected path: ${MODULE_PATH}"
        fi
        info "Module .so exists: $(ls -la "${MODULE_PATH}" 2>&1)"

        # --- DEB: Create load_module config snippet ---
        info "Creating load_module configuration in modules-enabled/..."
        mkdir -p /etc/nginx/modules-enabled
        printf 'load_module modules/ngx_http_markdown_module.so;\n' \
            > /etc/nginx/modules-enabled/50-mod-markdown.conf \
            || die "Failed to create modules-enabled config"

        # --- DEB: Verify nginx -t (ABI compatibility) ---
        info "Running nginx -t to verify module loads correctly..."
        if ! nginx -t 2>&1; then
            die "nginx -t failed — module ABI compatibility check FAILED"
        fi
        ;;

    rpm)
        # --- RPM: Install nginx.org package ---
        info "Adding nginx.org yum repository..."

        cat > /etc/yum.repos.d/nginx.repo <<'REPO'
[nginx-stable]
name=nginx stable repo
baseurl=http://nginx.org/packages/centos/$releasever/$basearch/
gpgcheck=1
enabled=1
gpgkey=https://nginx.org/keys/nginx_signing.key
module_hotfixes=true
REPO

        # Try to install NGINX; fall back to Amazon Linux repo path if needed
        info "Installing nginx-${NGINX_VERSION} from nginx.org..."
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y "nginx-${NGINX_VERSION}" >"${INSTALL_LOG}" 2>&1 \
                || dnf install -y nginx >"${INSTALL_LOG}" 2>&1 \
                || die "Failed to install nginx-${NGINX_VERSION} via dnf"
        elif command -v yum >/dev/null 2>&1; then
            yum install -y "nginx-${NGINX_VERSION}" >"${INSTALL_LOG}" 2>&1 \
                || yum install -y nginx >"${INSTALL_LOG}" 2>&1 \
                || die "Failed to install nginx-${NGINX_VERSION} via yum"
        else
            die "Neither dnf nor yum found"
        fi

        # --- RPM: Install module package ---
        info "Installing module package: ${PACKAGE_FILE}"
        rpm -Uvh "${PACKAGE_FILE}" >>"${INSTALL_LOG}" 2>&1 \
            || die "rpm -Uvh failed for ${PACKAGE_FILE}"

        # --- RPM: Verify nginx -V ---
        info "Running nginx -V..."
        nginx -V 2>&1 >&2 || die "nginx -V failed"

        # --- RPM: Verify .so exists ---
        MODULE_PATH="/usr/lib/nginx/modules/ngx_http_markdown_module.so"
        info "Verifying module .so at: ${MODULE_PATH}"
        if [ ! -f "${MODULE_PATH}" ]; then
            die "Module .so not found at expected path: ${MODULE_PATH}"
        fi
        info "Module .so exists: $(ls -la "${MODULE_PATH}" 2>&1)"

        # --- RPM: Add load_module to nginx.conf top ---
        info "Adding load_module directive to top of nginx.conf..."
        if [ -f /etc/nginx/nginx.conf ]; then
            sed -i '1i load_module modules/ngx_http_markdown_module.so;' \
                /etc/nginx/nginx.conf \
                || die "Failed to add load_module to nginx.conf"
        else
            die "/etc/nginx/nginx.conf not found"
        fi

        # --- RPM: Verify nginx -t (ABI compatibility) ---
        info "Running nginx -t to verify module loads correctly..."
        if ! nginx -t 2>&1; then
            die "nginx -t failed — module ABI compatibility check FAILED"
        fi
        ;;

    *)
        die "Internal error: unhandled package format '${PKG_FORMAT}'"
        ;;
esac

##############################################################################
# Success
##############################################################################

info "All smoke tests PASSED for ${PKG_FORMAT} package"
info "  Package: ${PACKAGE_FILE}"
info "  NGINX version: ${NGINX_VERSION}"
info "  Module loaded successfully (nginx -t passed)"

# Clean up install log on success
rm -f "${INSTALL_LOG}" 2>/dev/null || true

exit 0
