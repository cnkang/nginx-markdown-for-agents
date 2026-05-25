#!/usr/bin/env bash
# verify-checksum.sh — Verify a downloaded file against the repository checksum table.
#
# Usage:
#   verify-checksum.sh -f FILE -i IDENTIFIER [-c CHECKSUMS_FILE] [-h]
#
# Options:
#   -f FILE             Path to the downloaded file to verify
#   -i IDENTIFIER       Identifier to look up in the checksums file
#                       (e.g. "nginx-1.26.3", "nfpm-2.46.3-linux-x86_64")
#   -c CHECKSUMS_FILE   Path to checksums file (default: packaging/checksums.sha256)
#   -h                  Show this help message
#
# Exit codes:
#   0  Checksum verified successfully
#   1  Error (missing file, missing checksum, mismatch, placeholder detected)
#
# This script is FAIL-CLOSED: any error condition causes a non-zero exit.
# There is no "skip verification" path.

set -euo pipefail

##############################################################################
# Helpers
##############################################################################

usage() {
    sed -n '3,16p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [verify-checksum] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[verify-checksum] %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

FILE=""
IDENTIFIER=""
CHECKSUMS_FILE=""

while getopts "f:i:c:h" opt; do
    case "$opt" in
        f) FILE="$OPTARG" ;;
        i) IDENTIFIER="$OPTARG" ;;
        c) CHECKSUMS_FILE="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

##############################################################################
# Validation
##############################################################################

if [ -z "$FILE" ]; then
    die "File path not specified (-f FILE)"
fi

if [ -z "$IDENTIFIER" ]; then
    die "Identifier not specified (-i IDENTIFIER)"
fi

if [ ! -f "$FILE" ]; then
    die "File not found: $FILE"
fi

# Locate checksums file
if [ -z "$CHECKSUMS_FILE" ]; then
    # Try relative to script location, then relative to working directory
    SCRIPT_DIR="$(dirname "$0")"
    if [ -f "${SCRIPT_DIR}/../checksums.sha256" ]; then
        CHECKSUMS_FILE="${SCRIPT_DIR}/../checksums.sha256"
    elif [ -f "packaging/checksums.sha256" ]; then
        CHECKSUMS_FILE="packaging/checksums.sha256"
    else
        die "Checksums file not found. Specify with -c or ensure packaging/checksums.sha256 exists."
    fi
fi

if [ ! -f "$CHECKSUMS_FILE" ]; then
    die "Checksums file not found: $CHECKSUMS_FILE"
fi

##############################################################################
# Look up expected checksum
##############################################################################

# Parse the checksums file: skip comments and blank lines, match identifier
EXPECTED_SHA256=""
while IFS= read -r line; do
    # Skip comments and blank lines
    case "$line" in
        '#'*|'') continue ;;
    esac
    # Format: HASH  IDENTIFIER (two spaces)
    line_hash="${line%%  *}"
    line_id="${line#*  }"
    if [ "$line_id" = "$IDENTIFIER" ]; then
        EXPECTED_SHA256="$line_hash"
        break
    fi
done < "$CHECKSUMS_FILE"

if [ -z "$EXPECTED_SHA256" ]; then
    die "No checksum found for identifier '$IDENTIFIER' in $CHECKSUMS_FILE"
fi

# Validate the hash looks like a real SHA256 (64 hex chars, no placeholder text)
if ! printf '%s' "$EXPECTED_SHA256" | grep -qE '^[0-9a-f]{64}$'; then
    die "Invalid SHA256 format for '$IDENTIFIER': '$EXPECTED_SHA256' (must be 64 lowercase hex chars)"
fi

# Reject known placeholder patterns
case "$EXPECTED_SHA256" in
    *PLACEHOLDER*|*placeholder*|*OBTAIN*|*obtain*)
        die "Checksum for '$IDENTIFIER' is a placeholder — replace with actual SHA256"
        ;;
esac

##############################################################################
# Compute and compare
##############################################################################

if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA256=$(sha256sum "$FILE" | cut -d' ' -f1)
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA256=$(shasum -a 256 "$FILE" | cut -d' ' -f1)
else
    die "Neither sha256sum nor shasum found — cannot verify checksum"
fi

if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    die "SHA256 mismatch for $FILE (id: $IDENTIFIER): expected $EXPECTED_SHA256, got $ACTUAL_SHA256"
fi

info "SHA256 verified: $IDENTIFIER ($FILE)"
exit 0
