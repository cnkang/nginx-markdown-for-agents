#!/usr/bin/env bash
# validate-version.sh — Validate a version string for use in the release
# workflow. Rejects invalid formats before any shell interpolation occurs.
#
# Usage:
#   validate-version.sh <version>
#
# Arguments:
#   version    Semantic version string to validate
#
# Valid examples:
#   0.7.0, 1.0.0, 1.2.3
#
# Invalid examples:
#   v0.7.0 (no 'v' prefix allowed), 0.7 (missing patch), 0.7.0- (trailing dash),
#   0.7.0-rc.1 (prerelease suffixes not allowed for package releases)
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
# Exit codes:
#   0  Version is valid
#   1  Version is invalid or missing
#
# Requirements: 9.3, 9.7

set -euo pipefail

##############################################################################
# Constants
##############################################################################

readonly VERSION_REGEX='^[0-9]+\.[0-9]+\.[0-9]+$'
readonly SCRIPT_NAME="${0##*/}"

##############################################################################
# Helpers
##############################################################################

usage() {
    printf 'Usage: %s <version>\n' "${SCRIPT_NAME}" >&2
    printf 'Example: %s 0.7.0\n' "${SCRIPT_NAME}" >&2
    printf 'Example: %s 1.2.3\n' "${SCRIPT_NAME}" >&2
    return 1
}

err() {
    printf 'ERROR: %s\n' "$1" >&2
}

##############################################################################
# Main
##############################################################################

main() {
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
        *)
            local version="$1"

            if ! printf '%s' "${version}" | grep -qE "${VERSION_REGEX}"; then
                err "Invalid version format: '${version}'"
                printf 'Expected: MAJOR.MINOR.PATCH (no prerelease suffix)\n' >&2
                printf 'Pattern: %s\n' "${VERSION_REGEX}" >&2
                return 1
            fi

            # Output validated version to stdout
            printf '%s\n' "${version}"
            return 0
            ;;
    esac
}

main "$@"
