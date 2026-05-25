#!/bin/bash
# ---------------------------------------------------------------------------
# nginx-markdown-compat-check.sh — Compatibility check for prebuilt packages
#
# PURPOSE:
#   Detects the local NGINX installation and reports whether a prebuilt
#   nginx-markdown-for-agents package is available for the environment.
#   Outputs machine-readable KEY=VALUE pairs to stdout and human-readable
#   diagnostic messages to stderr.
#
# USAGE:
#   nginx-markdown-compat-check.sh [OPTIONS]
#   nginx-markdown-compat-check.sh --help
#
# OPTIONS:
#   -h, --help    Show this help message
#   --nginx PATH  Path to nginx binary (default: auto-detect from PATH)
#
# EXIT CODES:
#   0  Compatible — prebuilt package available
#   1  Incompatible — no prebuilt package for this environment
#   2  Unable to determine — missing nginx or parse failure
#
# OUTPUT (stdout, machine-readable):
#   STATUS=supported|unsupported|unknown
#   NGINX_VERSION=<version>
#   ARCH=<architecture>
#   WITH_COMPAT=yes|no
#   NGINX_SOURCE=nginx.org|distro|openresty|tengine|unknown
#   EXPECTED_PACKAGE=<filename>  (only when STATUS=supported)
#
# OUTPUT (stderr, human-readable):
#   [INFO]  informational messages
#   [WARN]  warnings
#   [ERROR] errors
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Diagnostic messages go to stderr
#   - No unsanitized path interpolation (AGENTS.md Rule 12)
#
# SEE ALSO:
#   - .kiro/specs/31-0.7.0-release-package-compatibility/requirements.md §8
#   - .kiro/specs/31-0.7.0-release-package-compatibility/design.md §Components 4
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
PROJECT_VERSION="0.7.0"

# Supported NGINX versions (nginx.org stable)
SUPPORTED_NGINX_VERSIONS="1.26.3"

# Supported architectures
SUPPORTED_ARCHS="x86_64 aarch64"
OPENRESTY_SOURCE="openresty"
STATUS_UNKNOWN="unknown"
STATUS_UNSUPPORTED="unsupported"
STATUS_SUPPORTED="supported"
NGINX_ORG_SOURCE="nginx.org"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    printf 'Usage: %s [OPTIONS]\n' "$SCRIPT_NAME" >&2
    printf '       %s --help\n' "$SCRIPT_NAME" >&2
    printf '\n' >&2
    printf 'Check compatibility of the local NGINX installation with\n' >&2
    printf 'nginx-markdown-for-agents prebuilt packages.\n' >&2
    printf '\n' >&2
    printf 'Options:\n' >&2
    printf '  -h, --help    Show this help message\n' >&2
    printf '  --nginx PATH  Path to nginx binary (default: auto-detect)\n' >&2
    printf '\n' >&2
    printf 'Exit codes:\n' >&2
    printf '  0  Compatible — prebuilt package available\n' >&2
    printf '  1  Incompatible — no prebuilt package for this environment\n' >&2
    printf '  2  Unable to determine\n' >&2
    return 0
}

log_info() {
    local message="$1"

    printf '[INFO]  %s\n' "$message" >&2
}

log_warn() {
    local message="$1"

    printf '[WARN]  %s\n' "$message" >&2
}

log_error() {
    local message="$1"

    printf '[ERROR] %s\n' "$message" >&2
}

# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

# detect_nginx_binary — locate the nginx binary
# Arguments: $1 = optional explicit path
# Sets: NGINX_BIN
# Returns: 0 on success, 1 on failure
detect_nginx_binary() {
    local explicit_path="$1"

    if [[ -n "$explicit_path" ]]; then
        # Validate the explicit path (Rule 12: no unsanitized interpolation)
        case "$explicit_path" in
            *[!a-zA-Z0-9_./-]*)
                log_error "Invalid characters in nginx path"
                return 1
                ;;
            *)
                ;;
        esac
        if [[ ! -x "$explicit_path" ]]; then
            log_error "nginx binary not found or not executable: $explicit_path"
            return 1
        fi
        NGINX_BIN="$explicit_path"
    else
        NGINX_BIN="$(command -v nginx 2>/dev/null || true)"
        if [[ -z "$NGINX_BIN" ]]; then
            log_error "nginx not found in PATH"
            return 1
        fi
    fi
    return 0
}

# detect_nginx_version — extract version from nginx -v output
# Sets: NGINX_VERSION
# Returns: 0 on success, 1 on failure
detect_nginx_version() {
    local version_output
    version_output="$("$NGINX_BIN" -v 2>&1 || true)"

    if [[ -z "$version_output" ]]; then
        log_error "Failed to execute nginx -v"
        return 1
    fi

    # Extract version number: nginx/X.Y.Z or openresty/X.Y.Z or Tengine/X.Y.Z
    NGINX_VERSION="$(printf '%s\n' "$version_output" | \
        sed -n 's/.*nginx\/\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | \
        head -1)"

    if [[ -z "$NGINX_VERSION" ]]; then
        log_warn "Could not parse NGINX version from: $version_output"
        return 1
    fi

    log_info "Detected NGINX version: $NGINX_VERSION"
    return 0
}

# detect_with_compat — check for --with-compat in nginx -V output
# Sets: WITH_COMPAT (yes|no)
# Returns: 0 always
detect_with_compat() {
    local config_output
    config_output="$("$NGINX_BIN" -V 2>&1 || true)"

    if printf '%s\n' "$config_output" | grep -q -- '--with-compat'; then
        WITH_COMPAT="yes"
        log_info "--with-compat flag: present"
    else
        WITH_COMPAT="no"
        log_warn "--with-compat flag: NOT present"
    fi
    return 0
}

# detect_architecture — get system architecture from uname -m
# Sets: ARCH
# Returns: 0 always
detect_architecture() {
    ARCH="$(uname -m)"
    log_info "Architecture: $ARCH"
    return 0
}

# detect_nginx_source — determine nginx origin
# Sets: NGINX_SOURCE (nginx.org|distro|openresty|tengine|unknown)
# Returns: 0 always
detect_nginx_source() {
    local version_output
    local config_output

    version_output="$("$NGINX_BIN" -v 2>&1 || true)"
    config_output="$("$NGINX_BIN" -V 2>&1 || true)"

    # Check for OpenResty
    if printf '%s\n' "$version_output" | grep -qi "$OPENRESTY_SOURCE"; then
        NGINX_SOURCE="$OPENRESTY_SOURCE"
        log_info "NGINX source: OpenResty"
        return 0
    fi
    if printf '%s\n' "$config_output" | grep -q -- '--with-luajit'; then
        NGINX_SOURCE="$OPENRESTY_SOURCE"
        log_info "NGINX source: OpenResty (detected via --with-luajit)"
        return 0
    fi

    # Check for Tengine
    if printf '%s\n' "$version_output" | grep -qi 'tengine'; then
        NGINX_SOURCE="tengine"
        log_info "NGINX source: Tengine"
        return 0
    fi

    # Check for distro-provided (Ubuntu, Debian, etc.)
    if printf '%s\n' "$version_output" | grep -qiE '\(Ubuntu\)|\(Debian\)'; then
        NGINX_SOURCE="distro"
        log_info "NGINX source: distribution package"
        return 0
    fi
    # Distro heuristic: configure paths typical of distro builds
    if printf '%s\n' "$config_output" | grep -qE '/usr/share/nginx|--with-cc-opt=.*-fstack-protector-strong'; then
        NGINX_SOURCE="distro"
        log_info "NGINX source: distribution package (detected via configure paths)"
        return 0
    fi

    # Check for nginx.org: plain nginx/X.Y.Z without distro suffix
    if printf '%s\n' "$version_output" | grep -qE '^nginx version: nginx/[0-9]+\.[0-9]+\.[0-9]+$'; then
        NGINX_SOURCE="$NGINX_ORG_SOURCE"
        log_info "NGINX source: $NGINX_ORG_SOURCE"
        return 0
    fi

    NGINX_SOURCE="$STATUS_UNKNOWN"
    log_warn "NGINX source: unknown (could not determine origin)"
    return 0
}

# ---------------------------------------------------------------------------
# Compatibility evaluation
# ---------------------------------------------------------------------------

# arch_to_deb — map uname -m architecture to DEB architecture name
# Arguments: $1 = uname -m output
# Prints: DEB arch name or empty string
arch_to_deb() {
    local arch="$1"

    case "$arch" in
        x86_64)
            printf 'amd64'
            ;;
        aarch64)
            printf 'arm64'
            ;;
        *)
            printf ''
            ;;
    esac
    return 0
}

# is_version_supported — check if version is in supported list
# Arguments: $1 = version string
# Returns: 0 if supported, 1 if not
is_version_supported() {
    local version="$1"
    local supported

    for supported in $SUPPORTED_NGINX_VERSIONS; do
        if [[ "$version" = "$supported" ]]; then
            return 0
        fi
    done
    return 1
}

# is_arch_supported — check if architecture is in supported list
# Arguments: $1 = architecture (uname -m)
# Returns: 0 if supported, 1 if not
is_arch_supported() {
    local arch="$1"
    local supported

    for supported in $SUPPORTED_ARCHS; do
        if [[ "$arch" = "$supported" ]]; then
            return 0
        fi
    done
    return 1
}

# evaluate_compatibility — determine overall status
# Sets: STATUS, EXPECTED_PACKAGE
# Returns: exit code matching STATUS
evaluate_compatibility() {
    local deb_arch

    # If source is not nginx.org, unsupported
    if [[ "$NGINX_SOURCE" != "$NGINX_ORG_SOURCE" ]]; then
        STATUS="$STATUS_UNSUPPORTED"
        log_info "Status: UNSUPPORTED — prebuilt packages are only for nginx.org builds"
        if [[ "$NGINX_SOURCE" = "distro" ]]; then
            log_info "  Your NGINX appears to be from a Linux distribution package."
            log_info "  Please build from source or install nginx.org packages."
        elif [[ "$NGINX_SOURCE" = "$OPENRESTY_SOURCE" ]]; then
            log_info "  OpenResty is not supported. Please build from source."
        elif [[ "$NGINX_SOURCE" = "tengine" ]]; then
            log_info "  Tengine is not supported. Please build from source."
        else
            log_info "  Could not determine NGINX origin. Please verify your installation."
        fi
        return 1
    fi

    # Check architecture
    if ! is_arch_supported "$ARCH"; then
        STATUS="$STATUS_UNSUPPORTED"
        log_info "Status: UNSUPPORTED — architecture $ARCH is not in the build matrix"
        return 1
    fi

    # Check version
    if ! is_version_supported "$NGINX_VERSION"; then
        STATUS="$STATUS_UNSUPPORTED"
        log_info "Status: UNSUPPORTED — NGINX version $NGINX_VERSION is not in the build matrix"
        log_info "  Supported versions: $SUPPORTED_NGINX_VERSIONS"
        return 1
    fi

    # All checks passed
    STATUS="$STATUS_SUPPORTED"
    deb_arch="$(arch_to_deb "$ARCH")"
    EXPECTED_PACKAGE="nginx-module-markdown-for-agents_${PROJECT_VERSION}_nginx-${NGINX_VERSION}_${deb_arch}.deb"
    log_info "Status: SUPPORTED — prebuilt package available"
    return 0
}

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

# emit_results — print machine-readable results to stdout
emit_results() {
    printf 'STATUS=%s\n' "$STATUS"
    printf 'NGINX_VERSION=%s\n' "$NGINX_VERSION"
    printf 'ARCH=%s\n' "$ARCH"
    printf 'WITH_COMPAT=%s\n' "$WITH_COMPAT"
    printf 'NGINX_SOURCE=%s\n' "$NGINX_SOURCE"
    if [[ -n "$EXPECTED_PACKAGE" ]]; then
        printf 'EXPECTED_PACKAGE=%s\n' "$EXPECTED_PACKAGE"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    local nginx_path=""
    local current_arg

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        current_arg="$1"
        case "$current_arg" in
            -h|--help)
                usage
                return 0
                ;;
            --nginx)
                if [[ $# -lt 2 ]]; then
                    log_error "--nginx requires a path argument"
                    usage
                    return 2
                fi
                nginx_path="$2"
                shift 2
                ;;
            -*)
                log_error "Unknown option: $current_arg"
                usage
                return 2
                ;;
            *)
                log_error "Unexpected argument: $current_arg"
                usage
                return 2
                ;;
        esac
    done

    # Initialize result variables
    NGINX_BIN=""
    NGINX_VERSION=""
    ARCH=""
    WITH_COMPAT=""
    NGINX_SOURCE=""
    STATUS=""
    EXPECTED_PACKAGE=""

    # Step 1: Detect nginx binary
    if ! detect_nginx_binary "$nginx_path"; then
        STATUS="$STATUS_UNKNOWN"
        printf 'STATUS=%s\n' "$STATUS"
        return 2
    fi

    # Step 2: Detect NGINX version
    if ! detect_nginx_version; then
        STATUS="$STATUS_UNKNOWN"
        ARCH="$(uname -m)"
        printf 'STATUS=%s\n' "$STATUS"
        printf 'ARCH=%s\n' "$ARCH"
        return 2
    fi

    # Step 3: Detect --with-compat
    detect_with_compat

    # Step 4: Detect architecture
    detect_architecture

    # Step 5: Detect NGINX source
    detect_nginx_source

    # Step 6: Evaluate compatibility
    local exit_code=0
    evaluate_compatibility || exit_code=$?

    # Step 7: Emit results
    emit_results

    return "$exit_code"
}

main "$@"
