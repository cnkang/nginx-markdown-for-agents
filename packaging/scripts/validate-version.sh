#!/usr/bin/env bash
# validate-version.sh — Validate a release version string before it reaches a
# shell command, a package path, or package metadata.
#
# Usage:
#   validate-version.sh <version>
#   validate-version.sh --stable-only <version>
#
# Arguments:
#   version    Release version: MAJOR.MINOR.PATCH with an optional SemVer
#              prerelease suffix.
#
# Valid examples:
#   0.7.0, 1.0.0, 1.2.3, 1.0.0-rc.1, 1.0.0-beta2
#
# Invalid examples:
#   v0.7.0 (no 'v' prefix allowed), 0.7 (missing patch), 01.0.0 (leading zero),
#   1.0.0- (trailing dash), 1.0.0-rc..1 (empty identifier), 1.0.0+build (build
#   metadata is not part of the package identity)
#
# Options:
#   --stable-only   Reject any prerelease suffix.  Workflows that cannot map a
#                   prerelease onto their package format must use this flag so
#                   they fail closed instead of emitting a mis-versioned
#                   package.
#
# On valid input:
#   Outputs the validated version to stdout, exits 0
#
# On invalid input:
#   Outputs error message to stderr, exits 1
#
# On missing argument:
#   Outputs usage to stderr, exits 1
#
# Security: This script is called by workflow_dispatch as the first step to
# sanitize user-provided version input before it is used in any shell commands
# (Requirements 9.3, 9.7).
#
# The canonical Debian and RPM mapping lives in packaging/scripts/
# release_version.py.  This script only decides whether the version is
# well-formed; it never rewrites the value it prints.
#
# Exit codes:
#   0  Version is valid
#   1  Version is invalid or missing
#
# Requirements: 9.3, 9.7

set -euo pipefail

##############################################################################
# Constants
##############################################################################

readonly VERSION_REGEX='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$'
readonly NUMERIC_LEADING_ZERO_REGEX='(^|\.)0[0-9]+(\.|$)'
readonly SCRIPT_NAME="${0##*/}"

##############################################################################
# Helpers
##############################################################################

usage() {
    printf 'Usage: %s [--stable-only] <version>\n' "${SCRIPT_NAME}" >&2
    printf 'Example: %s 0.7.0\n' "${SCRIPT_NAME}" >&2
    printf 'Example: %s 1.0.0-rc.1\n' "${SCRIPT_NAME}" >&2
    return 0
}

err() {
    printf 'ERROR: %s\n' "$1" >&2
    return 0
}

##############################################################################
# Main
##############################################################################

main() {
    local stable_only=0
    local version=""

    case "${1:-}" in
        "")
            err "No version argument provided"
            usage
            return 1
            ;;
        -h|--help)
            usage
            return 0
            ;;
        --stable-only)
            stable_only=1
            shift
            version="${1:-}"
            if [[ -z "${version}" ]]; then
                err "No version argument provided"
                usage
                return 1
            fi
            ;;
        *)
            version="$1"
            ;;
    esac

    if ! printf '%s' "${version}" | grep -qE "${VERSION_REGEX}"; then
        err "Invalid version format: '${version}'"
        printf 'Expected: MAJOR.MINOR.PATCH with an optional prerelease suffix\n' >&2
        printf 'Pattern: %s\n' "${VERSION_REGEX}" >&2
        return 1
    fi

    local prerelease=""
    case "${version}" in
        *-*) prerelease="${version#*-}" ;;
    esac

    if [[ -n "${prerelease}" ]]; then
        if printf '%s' "${prerelease}" | grep -qE "${NUMERIC_LEADING_ZERO_REGEX}"; then
            err "Prerelease identifier has a leading zero: '${prerelease}'"
            return 1
        fi
        if [[ "${stable_only}" == "1" ]]; then
            err "This workflow accepts stable releases only: '${version}'"
            printf 'Prerelease versions must be built by the canonical release pipeline.\n' >&2
            return 1
        fi
    fi

    printf '%s\n' "${version}"
    return 0
}

main "$@"
