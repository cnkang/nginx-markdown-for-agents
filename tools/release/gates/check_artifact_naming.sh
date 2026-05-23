#!/bin/bash
# ---------------------------------------------------------------------------
# check_artifact_naming.sh — Validate release artifact filenames
#
# PURPOSE:
#   Validates that .deb and .rpm artifact filenames conform to the
#   nginx-markdown-for-agents naming convention, which encodes project
#   version, target NGINX version, and architecture.
#
# NAMING PATTERNS:
#   DEB: nginx-module-markdown-for-agents_{version}_nginx-{nginx_version}_{arch}.deb
#   RPM: nginx-module-markdown-for-agents-{version}-nginx{nginx_version}-1.{arch}.rpm
#
# USAGE:
#   check_artifact_naming.sh <filename> [<filename> ...]
#   check_artifact_naming.sh --help
#
# OPTIONS:
#   -h, --help    Show this help message
#
# EXIT CODES:
#   0  All filenames are valid
#   1  One or more filenames are invalid
#   2  Usage error
#
# EXAMPLES:
#   check_artifact_naming.sh nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb
#   check_artifact_naming.sh *.deb *.rpm
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Diagnostic messages go to stderr
#   - Machine-readable pass/fail per file to stdout
#
# SEE ALSO:
#   - .kiro/specs/31-0.7.0-release-package-compatibility/requirements.md §4
#   - .kiro/specs/31-0.7.0-release-package-compatibility/design.md §Components 1
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
PASS_COUNT=0
FAIL_COUNT=0
FMT_PASS='PASS %s\n'
FMT_FAIL='FAIL %s\n'

# ---------------------------------------------------------------------------
# Patterns (POSIX ERE compatible, macOS bash 3.2 safe via grep -E)
# ---------------------------------------------------------------------------

# Semver: major.minor.patch with optional prerelease suffix.
VERSION_RE='[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?'

# NGINX version: major.minor.patch
NGINX_VERSION_RE='[0-9]+\.[0-9]+\.[0-9]+'

# DEB architectures
DEB_ARCH_RE='(amd64|arm64)'

# RPM architectures
RPM_ARCH_RE='(x86_64|aarch64)'

# Full DEB pattern
DEB_PATTERN="^nginx-module-markdown-for-agents_${VERSION_RE}_nginx-${NGINX_VERSION_RE}_${DEB_ARCH_RE}\.deb\$"

# Full RPM pattern
RPM_PATTERN="^nginx-module-markdown-for-agents-${VERSION_RE}-nginx${NGINX_VERSION_RE}-1\.${RPM_ARCH_RE}\.rpm\$"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    printf 'Usage: %s <filename> [<filename> ...]\n' "$SCRIPT_NAME" >&2
    printf '       %s --help\n' "$SCRIPT_NAME" >&2
    printf '\n' >&2
    printf 'Validates artifact filenames against the naming convention.\n' >&2
    printf '\n' >&2
    printf 'Valid patterns:\n' >&2
    printf '  DEB: nginx-module-markdown-for-agents_{ver}_nginx-{nginx_ver}_{arch}.deb\n' >&2
    printf '  RPM: nginx-module-markdown-for-agents-{ver}-nginx{nginx_ver}-1.{arch}.rpm\n' >&2
    printf '\n' >&2
    printf 'Options:\n' >&2
    printf '  -h, --help    Show this help message\n' >&2
    return 0
}

log_info() {
    local msg="$1"
    printf '[INFO]  %s\n' "$msg" >&2
}

log_pass() {
    local msg="$1"
    printf '[PASS]  %s\n' "$msg" >&2
}

log_fail() {
    local msg="$1"
    printf '[FAIL]  %s\n' "$msg" >&2
}

log_error() {
    local msg="$1"
    printf '[ERROR] %s\n' "$msg" >&2
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# validate_filename — check a single filename against naming patterns
# Arguments: $1 = filename (basename only, no directory path)
# Returns: 0 if valid, 1 if invalid
validate_filename() {
    local filename="$1"
    local basename_only

    # Strip directory components to validate only the filename
    basename_only="$(basename "$filename")"

    # Determine package type by extension
    case "$basename_only" in
        *.deb)
            if printf '%s\n' "$basename_only" | grep -qE "$DEB_PATTERN"; then
                log_pass "$basename_only"
                printf "$FMT_PASS" "$basename_only"
                PASS_COUNT=$((PASS_COUNT + 1))
                return 0
            else
                log_fail "$basename_only — does not match DEB pattern"
                log_info "  Expected: nginx-module-markdown-for-agents_{version}_nginx-{nginx_version}_{amd64|arm64}.deb"
                printf "$FMT_FAIL" "$basename_only"
                FAIL_COUNT=$((FAIL_COUNT + 1))
                return 1
            fi
            ;;
        *.rpm)
            if printf '%s\n' "$basename_only" | grep -qE "$RPM_PATTERN"; then
                log_pass "$basename_only"
                printf "$FMT_PASS" "$basename_only"
                PASS_COUNT=$((PASS_COUNT + 1))
                return 0
            else
                log_fail "$basename_only — does not match RPM pattern"
                log_info "  Expected: nginx-module-markdown-for-agents-{version}-nginx{nginx_version}-1.{x86_64|aarch64}.rpm"
                printf "$FMT_FAIL" "$basename_only"
                FAIL_COUNT=$((FAIL_COUNT + 1))
                return 1
            fi
            ;;
        *)
            log_fail "$basename_only — unrecognized package extension (expected .deb or .rpm)"
            printf "$FMT_FAIL" "$basename_only"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    # Handle no arguments or help flag
    if [ $# -eq 0 ]; then
        log_error "No filenames provided"
        usage
        return 2
    fi

    local first_arg="$1"
    case "$first_arg" in
        -h|--help)
            usage
            return 0
            ;;
        -*)
            log_error "Unknown option: $first_arg"
            usage
            return 2
            ;;
        *)
            ;;
    esac

    # Validate each filename
    local had_failure=0
    for filename in "$@"; do
        validate_filename "$filename" || had_failure=1
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
