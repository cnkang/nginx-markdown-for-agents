#!/usr/bin/env bash
# sign-packages.sh — Sign .deb and .rpm packages using GPG.
#
# Usage:
#   sign-packages.sh [-k KEY_ID] [-d DIR] [--require-packages] [-h]
#
# Options:
#   -k KEY_ID          GPG key ID to use for signing (default: $GPG_KEY_ID env var)
#   -d DIR             Directory containing packages to sign (default: ./dist)
#   --require-packages  Exit with error if no packages found (instead of warning)
#   -h                 Show this help message
#
# Environment:
#   GPG_KEY_ID  GPG key ID (used if -k not provided)
#
# Exit codes:
#   0  All packages signed successfully
#   1  Error (missing key, missing tools, signing failure)

set -euo pipefail

##############################################################################
# Helpers
##############################################################################

usage() {
    sed -n '3,14p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    return 1
}

info() {
    printf '[sign-packages] %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

KEY_ID="${GPG_KEY_ID:-}"
PKG_DIR="./dist"
REQUIRE_PACKAGES=0

while getopts "k:d:h-:" opt; do
    case "$opt" in
        k) KEY_ID="$OPTARG" ;;
        d) PKG_DIR="$OPTARG" ;;
        -)
            case "${OPTARG}" in
                require-packages) REQUIRE_PACKAGES=1 ;;
                *) usage; exit 1 ;;
            esac
            ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

##############################################################################
# Validation
##############################################################################

if [[ -z "$KEY_ID" ]]; then
    die "GPG key ID not specified. Use -k KEY_ID or set GPG_KEY_ID env var."
fi

# Validate that the GPG key exists in the keyring
if ! gpg --list-keys "$KEY_ID" >/dev/null 2>&1; then
    die "GPG key '$KEY_ID' not found in keyring."
fi

if [[ ! -d "$PKG_DIR" ]]; then
    die "Package directory '$PKG_DIR' does not exist."
fi

##############################################################################
# Sign DEB packages
##############################################################################

sign_deb() {
    local pkg="$1"
    local basename_pkg
    basename_pkg="$(basename "$pkg")"

    if command -v dpkg-sig >/dev/null 2>&1; then
        info "Signing DEB (dpkg-sig): $basename_pkg"
        dpkg-sig --sign builder -k "$KEY_ID" "$pkg"
    elif command -v debsigs >/dev/null 2>&1; then
        info "Signing DEB (debsigs): $basename_pkg"
        debsigs --sign=origin -k "$KEY_ID" "$pkg"
    else
        die "Neither dpkg-sig nor debsigs found. Install one to sign .deb packages."
    fi

    info "OK: $basename_pkg signed successfully"
    return 0
}

##############################################################################
# Sign RPM packages
##############################################################################

sign_rpm() {
    local pkg="$1"
    local basename_pkg
    basename_pkg="$(basename "$pkg")"

    if ! command -v rpm >/dev/null 2>&1; then
        die "rpm command not found. Install rpm-sign to sign .rpm packages."
    fi

    info "Signing RPM: $basename_pkg"

    # Configure RPM macros for GPG signing
    rpm --define "%_gpg_name $KEY_ID" --addsign "$pkg"

    info "OK: $basename_pkg signed successfully"
    return 0
}

##############################################################################
# Main
##############################################################################

FAIL_COUNT=0
SUCCESS_COUNT=0

# Sign .deb packages
for pkg in "$PKG_DIR"/*.deb; do
    [[ -e "$pkg" ]] || continue
    if sign_deb "$pkg"; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

# Sign .rpm packages
for pkg in "$PKG_DIR"/*.rpm; do
    [[ -e "$pkg" ]] || continue
    if sign_rpm "$pkg"; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

##############################################################################
# Summary
##############################################################################

info "Signing complete: $SUCCESS_COUNT succeeded, $FAIL_COUNT failed"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    die "One or more packages failed to sign."
fi

if [[ "$SUCCESS_COUNT" -eq 0 ]]; then
    if [[ "$REQUIRE_PACKAGES" -eq 1 ]]; then
        die "No packages found in '$PKG_DIR' (--require-packages mode)"
    fi
    info "WARNING: No packages found in '$PKG_DIR'"
fi

exit 0
