#!/bin/bash
# ---------------------------------------------------------------------------
# check_install_layout.sh — Validate package install layout
#
# PURPOSE:
#   Verifies that a .deb or .rpm package contains all required file paths
#   defined by the nginx-markdown-for-agents install layout specification.
#
# REQUIRED PATHS:
#   /usr/lib/nginx/modules/ngx_http_markdown_module.so
#   /usr/share/doc/nginx-markdown-for-agents/README.md
#   /usr/share/doc/nginx-markdown-for-agents/INSTALL.md
#   /usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md
#   /usr/share/licenses/nginx-markdown-for-agents/LICENSE
#
# USAGE:
#   check_install_layout.sh <package_file> [<package_file> ...]
#   check_install_layout.sh --help
#
# OPTIONS:
#   -h, --help    Show this help message
#
# EXIT CODES:
#   0  All packages contain all required paths
#   1  One or more packages are missing required paths
#   2  Usage error (no arguments, unknown option, unsupported file type)
#
# EXAMPLES:
#   check_install_layout.sh dist/nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb
#   check_install_layout.sh dist/*.deb dist/*.rpm
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Diagnostic messages go to stderr
#   - Machine-readable pass/fail per package to stdout
#   - Uses dpkg-deb --contents for .deb, rpm -qlp for .rpm
#
# SEE ALSO:
#   - .kiro/specs/31-0.7.0-release-package-compatibility/requirements.md §5
#   - .kiro/specs/31-0.7.0-release-package-compatibility/design.md §Components 2
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Required paths (install layout specification)
# ---------------------------------------------------------------------------
REQUIRED_PATHS="/usr/lib/nginx/modules/ngx_http_markdown_module.so
/usr/share/doc/nginx-markdown-for-agents/README.md
/usr/share/doc/nginx-markdown-for-agents/INSTALL.md
/usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md
/usr/share/licenses/nginx-markdown-for-agents/LICENSE"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    printf 'Usage: %s <package_file> [<package_file> ...]\n' "$SCRIPT_NAME" >&2
    printf '       %s --help\n' "$SCRIPT_NAME" >&2
    printf '\n' >&2
    printf 'Validates that packages contain all required install layout paths.\n' >&2
    printf '\n' >&2
    printf 'Supported package types:\n' >&2
    printf '  .deb    Inspected via dpkg-deb --contents\n' >&2
    printf '  .rpm    Inspected via rpm -qlp\n' >&2
    printf '\n' >&2
    printf 'Options:\n' >&2
    printf '  -h, --help    Show this help message\n' >&2
    return 0
}

log_info() {
    printf '[INFO]  %s\n' "$1" >&2
}

log_pass() {
    printf '[PASS]  %s\n' "$1" >&2
}

log_fail() {
    printf '[FAIL]  %s\n' "$1" >&2
}

log_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

# ---------------------------------------------------------------------------
# Package content listing
# ---------------------------------------------------------------------------

# list_deb_contents — list file paths inside a .deb package
# Arguments: $1 = path to .deb file
# Outputs: file paths to stdout (one per line)
# Returns: 0 on success, 1 on failure
list_deb_contents() {
    local pkg_path="$1"

    if ! command -v dpkg-deb >/dev/null 2>&1; then
        log_error "dpkg-deb not found in PATH"
        return 1
    fi

    dpkg-deb --contents "$pkg_path" 2>/dev/null | awk '{print $NF}'
    return $?
}

# list_rpm_contents — list file paths inside an .rpm package
# Arguments: $1 = path to .rpm file
# Outputs: file paths to stdout (one per line)
# Returns: 0 on success, 1 on failure
list_rpm_contents() {
    local pkg_path="$1"

    if ! command -v rpm >/dev/null 2>&1; then
        log_error "rpm not found in PATH"
        return 1
    fi

    rpm -qlp "$pkg_path" 2>/dev/null
    return $?
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# check_path_present — check if a required path is present in content listing
# Arguments: $1 = required path, $2 = content listing (newline-separated)
# Returns: 0 if found, 1 if not found
check_path_present() {
    local required="$1"
    local contents="$2"

    # Match with or without leading "./" prefix (dpkg-deb uses ./ prefix)
    if printf '%s\n' "$contents" | grep -qF "$required"; then
        return 0
    fi

    return 1
}

# validate_package — validate a single package file
# Arguments: $1 = path to package file
# Returns: 0 if all required paths present, 1 if any missing
validate_package() {
    local pkg_path="$1"
    local pkg_basename
    local contents
    local missing_count=0

    # Validate file exists
    if [ ! -f "$pkg_path" ]; then
        log_error "File not found: $pkg_path"
        printf 'FAIL %s (file not found)\n' "$pkg_path"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    fi

    pkg_basename="$(basename "$pkg_path")"
    log_info "Checking: $pkg_basename"

    # Get package contents based on file extension
    case "$pkg_basename" in
        *.deb)
            contents="$(list_deb_contents "$pkg_path")" || {
                log_error "Failed to read .deb contents: $pkg_basename"
                printf 'FAIL %s (read error)\n' "$pkg_basename"
                FAIL_COUNT=$((FAIL_COUNT + 1))
                return 1
            }
            ;;
        *.rpm)
            contents="$(list_rpm_contents "$pkg_path")" || {
                log_error "Failed to read .rpm contents: $pkg_basename"
                printf 'FAIL %s (read error)\n' "$pkg_basename"
                FAIL_COUNT=$((FAIL_COUNT + 1))
                return 1
            }
            ;;
        *)
            log_error "Unsupported package type: $pkg_basename (expected .deb or .rpm)"
            printf 'FAIL %s (unsupported type)\n' "$pkg_basename"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            return 1
            ;;
    esac

    # Check each required path
    local old_ifs="$IFS"
    IFS='
'
    for required_path in $REQUIRED_PATHS; do
        if check_path_present "$required_path" "$contents"; then
            log_info "  found: $required_path"
        else
            log_fail "  missing: $required_path"
            missing_count=$((missing_count + 1))
        fi
    done
    IFS="$old_ifs"

    # Report result
    if [ "$missing_count" -gt 0 ]; then
        log_fail "$pkg_basename — $missing_count required path(s) missing"
        printf 'FAIL %s\n' "$pkg_basename"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    fi

    log_pass "$pkg_basename — all required paths present"
    printf 'PASS %s\n' "$pkg_basename"
    PASS_COUNT=$((PASS_COUNT + 1))
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    # Handle no arguments
    if [ $# -eq 0 ]; then
        log_error "No package files provided"
        usage
        return 2
    fi

    # Handle options
    case "$1" in
        -h|--help)
            usage
            return 0
            ;;
        -*)
            log_error "Unknown option: $1"
            usage
            return 2
            ;;
        *)
            ;;
    esac

    # Validate each package
    local had_failure=0
    for pkg_file in "$@"; do
        validate_package "$pkg_file" || had_failure=1
    done

    # Summary
    printf '\n' >&2
    log_info "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"

    if [ "$had_failure" -eq 1 ]; then
        return 1
    fi

    return 0
}

main "$@"
