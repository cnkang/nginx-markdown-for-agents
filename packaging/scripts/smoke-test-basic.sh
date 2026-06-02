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

set -euo pipefail

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

detect_rpm_repo_baseurl() {
    if [[ ! -f /etc/os-release ]]; then
        die "/etc/os-release not found; cannot select nginx.org RPM repository"
    fi

    . /etc/os-release

    local channel
    channel="$(nginx_repo_channel "$NGINX_VERSION")"

    case "${ID:-}" in
        amzn)
            printf 'http://nginx.org/packages/%samzn/%s/$basearch/\n' \
                "$channel" "${VERSION_ID%%.*}"
            ;;
        almalinux|centos|rocky|rhel)
            printf 'http://nginx.org/packages/%scentos/$releasever/$basearch/\n' \
                "$channel"
            ;;
        *)
            die "Unsupported RPM smoke-test distribution: ID=${ID:-unknown}"
            ;;
    esac
    return 0
}

# Select the nginx.org package channel based on the minor-version parity of
# the target NGINX version.  nginx.org publishes even-minor releases (1.26,
# 1.28, ...) in the stable repository and odd-minor releases (1.27, 1.31, ...)
# in the mainline repository.  Emitting the wrong channel makes the package
# un-installable (AGENTS.md Rule 13).
#
# Outputs the channel path segment: empty string for stable, or "mainline/"
# for mainline.  Callers interpolate it directly into the repository base URL.
nginx_repo_channel() {
    local version="$1"
    local minor

    minor="$(printf '%s\n' "$version" | cut -d. -f2)"

    if [[ -z "$minor" || ! "$minor" =~ ^[0-9]+$ ]]; then
        die "cannot parse NGINX minor version from \"${version}\""
    fi

    if [[ $((minor % 2)) -eq 0 ]]; then
        printf ''
    else
        printf 'mainline/'
    fi
    return 0
}

detect_nginx_modules_path() {
    local modules_path

    modules_path="$(nginx -V 2>&1 \
        | tr ' ' '\n' \
        | sed -n 's/^--modules-path=//p' \
        | head -n 1)"

    if [[ -n "$modules_path" ]]; then
        printf '%s\n' "$modules_path"
        return 0
    fi

    printf '/usr/lib/nginx/modules\n'
    return 0
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

if [[ $# -lt 2 ]]; then
    printf 'ERROR: Missing required arguments\n' >&2
    usage
    exit 1
fi

PACKAGE_FILE="$1"
NGINX_VERSION="$2"

##############################################################################
# Validation
##############################################################################

if [[ -z "$PACKAGE_FILE" ]]; then
    die "PACKAGE_FILE argument is empty"
fi

if [[ -z "$NGINX_VERSION" ]]; then
    die "NGINX_VERSION argument is empty"
fi

if [[ ! -f "$PACKAGE_FILE" ]]; then
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

INSTALL_LOG="$(mktemp -t smoke-test-install.XXXXXX)"

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

        # Add nginx.org repository — select path based on distro ID and
        # channel (stable vs mainline) by NGINX minor-version parity.
        . /etc/os-release
        case "${ID:-}" in
            ubuntu)
                NGINX_REPO_DIST="ubuntu"
                ;;
            debian)
                NGINX_REPO_DIST="debian"
                ;;
            *)
                die "Unsupported DEB smoke-test distribution: ID=${ID:-unknown}"
                ;;
        esac
        NGINX_REPO_CHANNEL="$(nginx_repo_channel "$NGINX_VERSION")"
        printf 'deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] https://nginx.org/packages/%s%s %s nginx\n' \
            "$NGINX_REPO_CHANNEL" "$NGINX_REPO_DIST" "$VERSION_CODENAME" \
            > /etc/apt/sources.list.d/nginx.list \
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
        MODULE_PATH="$(detect_nginx_modules_path)/ngx_http_markdown_filter_module.so"
        info "Verifying module .so at: ${MODULE_PATH}"
        if [[ ! -f "${MODULE_PATH}" ]]; then
            die "Module .so not found at expected path: ${MODULE_PATH}"
        fi
        info "Module .so exists: $(ls -la "${MODULE_PATH}" 2>&1)"

        # --- DEB: Create load_module config snippet ---
        info "Creating load_module configuration in modules-enabled/..."
        mkdir -p /etc/nginx/modules-enabled
        printf 'load_module %s;\n' "${MODULE_PATH}" \
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
        NGINX_REPO_BASEURL="$(detect_rpm_repo_baseurl)"

        cat > /etc/yum.repos.d/nginx.repo <<REPO
[nginx-stable]
name=nginx stable repo
baseurl=${NGINX_REPO_BASEURL}
gpgcheck=1
enabled=1
gpgkey=https://nginx.org/keys/nginx_signing.key
module_hotfixes=true
REPO

        # Try to install NGINX — no fallback to unversioned package (F-03)
        info "Installing nginx-${NGINX_VERSION} from nginx.org..."
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y "nginx-${NGINX_VERSION}" >"${INSTALL_LOG}" 2>&1 \
                || die "Failed to install nginx-${NGINX_VERSION} via dnf"
        elif command -v yum >/dev/null 2>&1; then
            yum install -y "nginx-${NGINX_VERSION}" >"${INSTALL_LOG}" 2>&1 \
                || die "Failed to install nginx-${NGINX_VERSION} via yum"
        else
            die "Neither dnf nor yum found"
        fi

        # Verify installed NGINX version matches the target
        installed_version="$(nginx -v 2>&1 || true)"
        case "$installed_version" in
            *"nginx/${NGINX_VERSION}"*)
                info "Verified installed NGINX version: ${installed_version}"
                ;;
            *)
                die "Installed NGINX version does not match ${NGINX_VERSION}: ${installed_version}"
                ;;
        esac

        # --- RPM: Install module package ---
        info "Installing module package: ${PACKAGE_FILE}"
        rpm -Uvh "${PACKAGE_FILE}" >>"${INSTALL_LOG}" 2>&1 \
            || die "rpm -Uvh failed for ${PACKAGE_FILE}"

        # --- RPM: Verify nginx -V ---
        info "Running nginx -V..."
        nginx -V 2>&1 >&2 || die "nginx -V failed"

        # --- RPM: Verify .so exists ---
        MODULE_PATH="$(detect_nginx_modules_path)/ngx_http_markdown_filter_module.so"
        info "Verifying module .so at: ${MODULE_PATH}"
        if [[ ! -f "${MODULE_PATH}" ]]; then
            die "Module .so not found at expected path: ${MODULE_PATH}"
        fi
        info "Module .so exists: $(ls -la "${MODULE_PATH}" 2>&1)"

        # --- RPM: Add load_module to nginx.conf top ---
        info "Adding load_module directive to top of nginx.conf..."
        if [[ -f /etc/nginx/nginx.conf ]]; then
            _tmp="$(mktemp /etc/nginx/nginx.conf.XXXXXX)" \
                || die "Failed to create temp file for nginx.conf prepend"
            printf 'load_module %s;\n' "${MODULE_PATH}" \
                > "$_tmp" \
                || die "Failed to write load_module line to temp file"
            cat /etc/nginx/nginx.conf >> "$_tmp" \
                || die "Failed to append original nginx.conf to temp file"
            mv -f "$_tmp" /etc/nginx/nginx.conf \
                || die "Failed to move temp file over nginx.conf"
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
