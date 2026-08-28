#!/usr/bin/env bash
# smoke-test-basic.sh — Basic smoke test for DEB/RPM module packages.
#
# Installs the nginx.org package, installs the module package, verifies the
# .so file exists, loads the module in a temporary config, serves a real
# Markdown request, and verifies a negative control without load_module fails.
# On any failure, calls smoke-test-diagnostics.sh.
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
NGINX_BIN=""
REMOVAL_CONFIG_PATH=""
REMOVAL_CONFIG_BACKUP=""
REMOVAL_CONFIG_TEMP=""
REMOVAL_CONFIG_MUTATED=0

usage() {
    sed -n '3,16p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

clear_removal_config_state() {
    REMOVAL_CONFIG_PATH=""
    REMOVAL_CONFIG_BACKUP=""
    REMOVAL_CONFIG_TEMP=""
    REMOVAL_CONFIG_MUTATED=0
    return 0
}

restore_removal_config() {
    local restore_status=0

    if [[ "$REMOVAL_CONFIG_MUTATED" -eq 1 ]]; then
        if [[ -z "$REMOVAL_CONFIG_PATH" ]] \
            || [[ -z "$REMOVAL_CONFIG_BACKUP" ]] \
            || [[ ! -f "$REMOVAL_CONFIG_BACKUP" ]] \
            || ! cp -p "$REMOVAL_CONFIG_BACKUP" "$REMOVAL_CONFIG_PATH";
        then
            restore_status=1
        else
            REMOVAL_CONFIG_MUTATED=0
        fi
    fi

    if [[ -n "$REMOVAL_CONFIG_TEMP" ]]; then
        rm -f "$REMOVAL_CONFIG_TEMP" || restore_status=1
    fi

    if [[ "$restore_status" -eq 0 ]] \
        && [[ "$REMOVAL_CONFIG_MUTATED" -eq 0 ]] \
        && [[ -n "$REMOVAL_CONFIG_BACKUP" ]]; then
        rm -f "$REMOVAL_CONFIG_BACKUP" || restore_status=1
    fi

    if [[ "$restore_status" -eq 0 ]]; then
        clear_removal_config_state
    fi
    return "$restore_status"
}

die() {
    if ! restore_removal_config; then
        printf 'ERROR: failed to restore the NGINX configuration fixture\n' >&2
    fi
    printf 'ERROR: %s\n' "$1" >&2
    run_diagnostics
    exit 1
}

info() {
    printf '[smoke-test-basic] %s\n' "$1" >&2
}

# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap.
cleanup() {
    if ! restore_removal_config; then
        printf 'ERROR: failed to restore the NGINX configuration fixture during cleanup\n' >&2
    fi
    return 0
}
trap cleanup EXIT

detect_rpm_repo_baseurl() {
    if [[ ! -f /etc/os-release ]]; then
        die "/etc/os-release not found; cannot select nginx.org RPM repository"
    fi

    # shellcheck disable=SC1091 # Runtime distro metadata, only available in target image.
    . /etc/os-release

    local channel
    channel="$(nginx_repo_channel "$NGINX_VERSION")"

    case "${ID:-}" in
        amzn)
            # shellcheck disable=SC2016 # $basearch is expanded by dnf/yum.
            printf 'https://nginx.org/packages/%samzn/%s/$basearch/\n' \
                "$channel" "${VERSION_ID%%.*}"
            ;;
        almalinux|centos|rocky|rhel)
            # shellcheck disable=SC2016 # $releasever/$basearch are expanded by dnf/yum.
            printf 'https://nginx.org/packages/%scentos/$releasever/$basearch/\n' \
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

remove_module_package() {
    case "$PKG_FORMAT" in
        deb)
            dpkg --remove nginx-module-markdown-for-agents
            ;;
        rpm)
            if command -v dnf >/dev/null 2>&1; then
                dnf remove -y nginx-module-markdown-for-agents
            elif command -v yum >/dev/null 2>&1; then
                yum remove -y nginx-module-markdown-for-agents
            else
                die "Neither dnf nor yum found for RPM module removal"
            fi
            ;;
        *)
            die "Unsupported package format for module removal: ${PKG_FORMAT}"
            ;;
    esac
    return 0
}

run_package_removal_lifecycle() {
    local module_path="$1"
    local config_path="/etc/nginx/nginx.conf"
    local backup_path=""
    local temp_path=""
    local removal_status=0

    if [[ ! -f "$config_path" ]]; then
        die "${config_path} is required for the package removal lifecycle"
    fi

    backup_path="$(mktemp "${TMPDIR:-/tmp}/nginx-markdown-config.XXXXXX")" \
        || die "Failed to create an NGINX configuration backup"
    REMOVAL_CONFIG_PATH="$config_path"
    REMOVAL_CONFIG_BACKUP="$backup_path"
    cp -p "$config_path" "$backup_path" \
        || die "Failed to preserve the original NGINX configuration"
    temp_path="$(mktemp "${config_path}.XXXXXX")" \
        || die "Failed to create an NGINX configuration fixture"
    REMOVAL_CONFIG_TEMP="$temp_path"
    {
        printf 'load_module "%s";\n' "$module_path"
        cat "$config_path"
    } > "$temp_path" || die "Failed to enable the module in the NGINX configuration"
    # Copy through the destination instead of replacing it.  Some
    # distributions make nginx.conf a symlink; replacing it would destroy
    # that packaging invariant and the cleanup path could not restore it.
    cp -p "$temp_path" "$config_path" \
        || die "Failed to enable the module in the NGINX configuration"
    REMOVAL_CONFIG_TEMP=""
    REMOVAL_CONFIG_MUTATED=1

    info "Checking the enabled module configuration before removal..."
    if ! "$NGINX_BIN" -t -c "$config_path" >"${INSTALL_LOG}" 2>&1; then
        die "nginx -t failed after enabling the module for removal testing"
    fi

    info "Confirming package removal is blocked while load_module is active..."
    if remove_module_package >>"${INSTALL_LOG}" 2>&1; then
        removal_status=0
    else
        removal_status=$?
    fi
    if [[ "$removal_status" -eq 0 ]]; then
        die "Package removal unexpectedly succeeded while the module was loaded"
    fi

    info "Disabling the module and retrying package removal..."
    restore_removal_config \
        || die "Failed to disable the module in the NGINX configuration"
    if ! "$NGINX_BIN" -t -c "$config_path" >"${INSTALL_LOG}" 2>&1; then
        die "nginx -t failed after disabling the module"
    fi
    if ! remove_module_package >>"${INSTALL_LOG}" 2>&1; then
        die "Package removal failed after disabling the module"
    fi
    if ! "$NGINX_BIN" -t -c "$config_path" >"${INSTALL_LOG}" 2>&1; then
        die "nginx -t failed after the module package was removed"
    fi
    info "Package removal lifecycle passed: active load blocked removal, disabled load removed cleanly"
    return 0
}

detect_nginx_modules_path() {
    local modules_path

    modules_path="$("$NGINX_BIN" -V 2>&1 \
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

run_module_behavior_smoke() {
    local module_path="$1"
    local curl_bin=""
    local smoke_root=""
    local smoke_prefix=""
    local smoke_conf=""
    local negative_conf=""
    local headers_file=""
    local body_file=""
    local diagnostics_file=""
    local negative_log=""
    local nginx_pid=""
    local response_ok=0
    local content_type_ok=0
    local vary_ok=0
    local body_ok=0
    local diagnostics_ok=0
    local i=0

    curl_bin="$(command -v curl 2>/dev/null || true)"
    if [[ -z "$curl_bin" ]]; then
        die "curl is required for the positive module request smoke test"
    fi

    smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/markdown-smoke-root.XXXXXX")" \
        || die "Failed to create module smoke document root"
    smoke_prefix="$(mktemp -d "${TMPDIR:-/tmp}/markdown-smoke-prefix.XXXXXX")" \
        || die "Failed to create module smoke NGINX prefix"
    smoke_conf="${smoke_prefix}/nginx.conf"
    negative_conf="${smoke_prefix}/negative.conf"
    headers_file="${smoke_prefix}/response.headers"
    body_file="${smoke_prefix}/response.body"
    diagnostics_file="${smoke_prefix}/diagnostics.json"
    negative_log="${smoke_prefix}/negative.log"

    cat > "$smoke_root/index.html" <<'HTML'
<!doctype html>
<html><body><h1>Package smoke fixture</h1><p>module request</p></body></html>
HTML

    cat > "$smoke_conf" <<CONF
load_module "${module_path}";
pid ${smoke_prefix}/nginx.pid;
error_log ${smoke_prefix}/error.log notice;
daemon off;
worker_processes 1;
events { worker_connections 64; }
http {
    default_type text/html;
    markdown_filter on;
    server {
        listen 127.0.0.1:19999;
        root ${smoke_root};
        location / { index index.html; }
        location /nginx-markdown/diagnostics {
            markdown_diagnostics on;
            allow 127.0.0.1;
            deny all;
        }
    }
}
CONF

    cat > "$negative_conf" <<CONF
pid ${smoke_prefix}/negative.pid;
error_log ${negative_log} notice;
daemon off;
worker_processes 1;
events { worker_connections 64; }
http {
    default_type text/html;
    markdown_filter on;
    server { listen 127.0.0.1:19998; }
}
CONF

    info "Starting module-enabled NGINX for a positive Markdown request..."
    "$NGINX_BIN" -p "$smoke_prefix" -c "$smoke_conf" \
        >"${smoke_prefix}/startup.log" 2>&1 &
    nginx_pid=$!

    for ((i = 0; i < 50; i++)); do
        if "$curl_bin" -fsS -D "$headers_file" -o "$body_file" \
            -H 'Accept: text/markdown' http://127.0.0.1:19999/; then
            response_ok=1
            break
        fi
        sleep 0.1
    done

    if [[ "$response_ok" -eq 1 ]]; then
        if grep -Eqi '^Content-Type:[[:space:]]*text/markdown' "$headers_file"; then
            content_type_ok=1
        fi
        if grep -Eqi '^Vary:[[:space:]]*Accept' "$headers_file"; then
            vary_ok=1
        fi
        if grep -q 'Package smoke fixture' "$body_file" \
            && grep -q '^# ' "$body_file"; then
            body_ok=1
        fi
    fi

    if [[ "$response_ok" -ne 1 ]]; then
        kill "$nginx_pid" 2>/dev/null || true
        wait "$nginx_pid" 2>/dev/null || true
        die "Module-enabled NGINX did not serve a Markdown request"
    fi
    if [[ "$content_type_ok" -ne 1 || "$vary_ok" -ne 1 || "$body_ok" -ne 1 ]]; then
        kill "$nginx_pid" 2>/dev/null || true
        wait "$nginx_pid" 2>/dev/null || true
        die "Positive module request did not produce the expected Markdown representation"
    fi
    info "Positive module request verified Content-Type, Vary, and Markdown body"

    info "Reading diagnostics from the loaded package module..."
    if "$curl_bin" -fsS -o "$diagnostics_file" \
        http://127.0.0.1:19999/nginx-markdown/diagnostics; then
        if grep -Fq '"schema_version":2' "$diagnostics_file" \
            && grep -Fq '"diagnostics_recording":"active"' "$diagnostics_file"; then
            diagnostics_ok=1
        fi
        if [[ -n "${EXPECTED_SOURCE_SHA:-}" \
            || -n "${EXPECTED_RUST_VERSION:-}" \
            || -n "${EXPECTED_FEATURE_MANIFEST_DIGEST:-}" ]]; then
            if [[ -z "${EXPECTED_SOURCE_SHA:-}" \
                || -z "${EXPECTED_RUST_VERSION:-}" \
                || -z "${EXPECTED_FEATURE_MANIFEST_DIGEST:-}" ]]; then
                diagnostics_ok=0
            elif ! grep -Fq '"build_kind":"release"' "$diagnostics_file" \
                || ! grep -Fq "\"source_sha\":\"${EXPECTED_SOURCE_SHA}\"" \
                    "$diagnostics_file" \
                || ! grep -Fq "\"rust_version\":\"${EXPECTED_RUST_VERSION}\"" \
                    "$diagnostics_file" \
                || ! grep -Fq \
                    "\"feature_manifest_digest\":\"${EXPECTED_FEATURE_MANIFEST_DIGEST}\"" \
                    "$diagnostics_file"; then
                diagnostics_ok=0
            fi
        fi
    fi

    kill "$nginx_pid" 2>/dev/null || true
    wait "$nginx_pid" 2>/dev/null || true

    if [[ "$diagnostics_ok" -ne 1 ]]; then
        die "Diagnostics response did not match the expected module contract"
    fi
    if [[ -n "${EXPECTED_SOURCE_SHA:-}" ]]; then
        info "Release diagnostics identity matches source, Rust, and feature manifest"
    fi

    info "Running negative control without load_module..."
    if "$NGINX_BIN" -t -p "$smoke_prefix" -c "$negative_conf" \
        >"$negative_log" 2>&1; then
        die "Negative module smoke control unexpectedly passed without load_module"
    fi
    info "Negative control rejected markdown_filter during nginx -t without the module"

    rm -rf "$smoke_root" "$smoke_prefix"
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
        # shellcheck disable=SC1091 # Runtime distro metadata, only available in target image.
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
        NGINX_BIN="$(command -v nginx)" || die "Installed nginx executable not found"

        # --- DEB: Install module package ---
        info "Installing module package: ${PACKAGE_FILE}"
        dpkg -i "${PACKAGE_FILE}" >>"${INSTALL_LOG}" 2>&1 \
            || die "dpkg -i failed for ${PACKAGE_FILE}"

        # --- DEB: Verify nginx -V ---
        info "Running nginx -V..."
        "$NGINX_BIN" -V 2>&1 >&2 || die "nginx -V failed"

        # --- DEB: Verify .so exists ---
        MODULE_PATH="$(detect_nginx_modules_path)/ngx_http_markdown_filter_module.so"
        info "Verifying module .so at: ${MODULE_PATH}"
        if [[ ! -f "${MODULE_PATH}" ]]; then
            die "Module .so not found at expected path: ${MODULE_PATH}"
        fi
        info "Module .so exists: $(ls -la "${MODULE_PATH}" 2>&1)"

        # --- DEB: Real module-load verification ---
        # nginx.org builds do NOT include /etc/nginx/modules-enabled/*.conf
        # by default, so a snippet there would be silently ignored and
        # `nginx -t` would pass without the module ever loading.  Verify
        # loadability with an explicit main-context load_module against a
        # temporary config (the doctor's verification pattern): this
        # proves the module actually loads and markdown_filter parses.
        info "Verifying module loads (load_module + markdown_filter on + nginx -t)..."
        TMP_CONF="$(mktemp "${TMPDIR:-/tmp}/markdown-smoke-XXXXXX.conf")"
        cat > "${TMP_CONF}" <<CONF
load_module "${MODULE_PATH}";
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
        if ! "$NGINX_BIN" -t -c "${TMP_CONF}" 2>&1; then
            rm -f "${TMP_CONF}"
            die "nginx -t with load_module failed — module did not load"
        fi
        rm -f "${TMP_CONF}"
        info "Module loads successfully (load_module + markdown_filter on verified)"
        run_module_behavior_smoke "$MODULE_PATH"
        run_package_removal_lifecycle "$MODULE_PATH"
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
        NGINX_BIN="$(command -v nginx)" || die "Installed nginx executable not found"

        # Verify installed NGINX version matches the target
        installed_version="$("$NGINX_BIN" -v 2>&1 || true)"
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
        # Use the distro package manager so dependencies such as libbrotli
        # are resolved from the configured repositories before installation.
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y "${PACKAGE_FILE}" >>"${INSTALL_LOG}" 2>&1 \
                || die "dnf install failed for ${PACKAGE_FILE}"
        elif command -v yum >/dev/null 2>&1; then
            yum install -y "${PACKAGE_FILE}" >>"${INSTALL_LOG}" 2>&1 \
                || die "yum install failed for ${PACKAGE_FILE}"
        else
            die "Neither dnf nor yum found for RPM module installation"
        fi

        # --- RPM: Verify nginx -V ---
        info "Running nginx -V..."
        "$NGINX_BIN" -V 2>&1 >&2 || die "nginx -V failed"

        # --- RPM: Verify .so exists ---
        MODULE_PATH="$(detect_nginx_modules_path)/ngx_http_markdown_filter_module.so"
        info "Verifying module .so at: ${MODULE_PATH}"
        if [[ ! -f "${MODULE_PATH}" ]]; then
            die "Module .so not found at expected path: ${MODULE_PATH}"
        fi
        info "Module .so exists: $(ls -la "${MODULE_PATH}" 2>&1)"

        run_module_behavior_smoke "$MODULE_PATH"
        run_package_removal_lifecycle "$MODULE_PATH"
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
info "  Module loaded and served a positive Markdown request"

# Clean up install log on success
rm -f "${INSTALL_LOG}" 2>/dev/null || true

exit 0
