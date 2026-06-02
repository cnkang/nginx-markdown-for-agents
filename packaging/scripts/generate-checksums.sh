#!/usr/bin/env bash
# generate-checksums.sh — Generate SHA256SUMS file for release artifacts.
#
# Usage:
#   generate-checksums.sh [-d DIR] [-o OUTPUT] [-h]
#
# Options:
#   -d DIR      Directory containing .deb and .rpm artifacts (required)
#   -o OUTPUT   Output filename (default: SHA256SUMS)
#   -h          Show this help message
#
# The script generates a SHA256SUMS file with relative paths inside the
# artifact directory. The output file is written into the same directory.
#
# Exit codes:
#   0  SHA256SUMS generated and verified successfully
#   1  Error (missing directory, no artifacts, format validation failure)

set -euo pipefail

##############################################################################
# Helpers
##############################################################################

usage() {
    sed -n '3,16p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    return 1
}

info() {
    printf '[generate-checksums] %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

ARTIFACT_DIR=""
OUTPUT_FILE="SHA256SUMS"

while getopts "d:o:h" opt; do
    case "$opt" in
        d) ARTIFACT_DIR="$OPTARG" ;;
        o) OUTPUT_FILE="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

# Support positional argument as artifact directory for convenience
if [[ -z "$ARTIFACT_DIR" ]] && [[ $# -ge 1 ]]; then
    ARTIFACT_DIR="$1"
fi

##############################################################################
# Validation
##############################################################################

if [[ -z "$ARTIFACT_DIR" ]]; then
    die "Artifact directory not specified. Use -d DIR or pass as first argument."
fi

if [[ ! -d "$ARTIFACT_DIR" ]]; then
    die "Artifact directory '$ARTIFACT_DIR' does not exist."
fi

##############################################################################
# Collect artifacts
##############################################################################

cd "$ARTIFACT_DIR"

# Gather .deb and .rpm files; handle cases where one type may be absent
DEB_FILES=()
RPM_FILES=()

for f in *.deb; do
    [[ -e "$f" ]] || continue
    DEB_FILES+=("$f")
done

for f in *.rpm; do
    [[ -e "$f" ]] || continue
    RPM_FILES+=("$f")
done

ALL_FILES=(${DEB_FILES[@]+"${DEB_FILES[@]}"} ${RPM_FILES[@]+"${RPM_FILES[@]}"})

if [[ ${#ALL_FILES[@]} -eq 0 ]]; then
    die "No .deb or .rpm files found in '$ARTIFACT_DIR'."
fi

info "Found ${#DEB_FILES[@]} .deb and ${#RPM_FILES[@]} .rpm file(s)"

##############################################################################
# Generate SHA256SUMS
##############################################################################

sha256sum "${ALL_FILES[@]}" > "$OUTPUT_FILE"

info "Generated $OUTPUT_FILE with ${#ALL_FILES[@]} entries"

##############################################################################
# Verify format
##############################################################################

# SHA256SUMS must be non-empty
if [[ ! -s "$OUTPUT_FILE" ]]; then
    die "Generated $OUTPUT_FILE is empty."
fi

# Each line must match: 64 hex chars, two spaces, filename
LINE_COUNT=0
while IFS= read -r line; do
    LINE_COUNT=$((LINE_COUNT + 1))
    case "$line" in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]\ \ *)
            # Valid format: 64 hex + two spaces + filename
            ;;
        *)
            die "Line $LINE_COUNT in $OUTPUT_FILE has invalid format: $line"
            ;;
    esac
done < "$OUTPUT_FILE"

if [[ "$LINE_COUNT" -ne ${#ALL_FILES[@]} ]]; then
    die "Expected ${#ALL_FILES[@]} entries but found $LINE_COUNT lines in $OUTPUT_FILE."
fi

info "Format verification passed ($LINE_COUNT entries)"

##############################################################################
# Output result
##############################################################################

FULL_PATH="$(pwd)/$OUTPUT_FILE"
info "SHA256SUMS written to: $FULL_PATH"
printf '%s\n' "$FULL_PATH"

exit 0
