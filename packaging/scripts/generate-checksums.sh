#!/usr/bin/env bash
# generate-checksums.sh — Generate SHA256SUMS file for release artifacts.
#
# Usage:
#   generate-checksums.sh [-d DIR] [-o OUTPUT] [-h]
#
# Options:
#   -d DIR      Directory containing release artifacts (required)
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
export LC_ALL=C

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

if [[ -z "$OUTPUT_FILE" || "$OUTPUT_FILE" != "${OUTPUT_FILE##*/}" \
    || "$OUTPUT_FILE" == *..* ]]; then
    die "Output must be a safe filename within the artifact directory."
fi

##############################################################################
# Collect artifacts
##############################################################################

cd "$ARTIFACT_DIR"

# Gather package/archive artifacts and release-manifest.json; handle cases
# where one type may be absent.
DEB_COUNT=0
RPM_COUNT=0
TARBALL_COUNT=0
INSTALLER_COUNT=0
RELEASE_KEY_COUNT=0
MANIFEST_FILE=""
ALL_FILES=()

# Keep traversal NUL-safe and sort explicitly without relying on GNU sort -z.
# The artifact directory is flat by contract; nested files are ignored.
append_sorted_file() {
    local value="$1"
    local index=${#ALL_FILES[@]}

    while (( index > 0 )); do
        if [[ "${ALL_FILES[index - 1]}" < "$value" ]]; then
            break
        fi
        ALL_FILES[index]="${ALL_FILES[index - 1]}"
        index=$((index - 1))
    done
    ALL_FILES[index]="$value"
    return 0
}

while IFS= read -r -d '' f; do
    name="${f#./}"
    case "$name" in
        */*)
            continue
            ;;
        *.deb)
            DEB_COUNT=$((DEB_COUNT + 1))
            ;;
        *.rpm)
            RPM_COUNT=$((RPM_COUNT + 1))
            ;;
        *.tar.gz)
            TARBALL_COUNT=$((TARBALL_COUNT + 1))
            ;;
        nginx-markdown-for-agents-installer-*.sh)
            INSTALLER_COUNT=$((INSTALLER_COUNT + 1))
            ;;
        nginx-markdown-for-agents-release.asc)
            RELEASE_KEY_COUNT=$((RELEASE_KEY_COUNT + 1))
            ;;
        release-manifest.json)
            MANIFEST_FILE="$name"
            ;;
        *)
            continue
            ;;
    esac
    append_sorted_file "$name"
done < <(find . -type f \( \
    -name '*.deb' -o -name '*.rpm' -o -name '*.tar.gz' \
    -o -name 'nginx-markdown-for-agents-installer-*.sh' \
    -o -name 'nginx-markdown-for-agents-release.asc' \
    -o -name 'release-manifest.json' \
    \) -print0)

if [[ ${#ALL_FILES[@]} -eq 0 ]]; then
    die "No release artifacts found in '$ARTIFACT_DIR'."
fi

if [[ "$DEB_COUNT" -eq 0 && "$RPM_COUNT" -eq 0 && "$TARBALL_COUNT" -eq 0 ]]; then
    die "No package artifacts (.deb/.rpm/.tar.gz) found in '$ARTIFACT_DIR'; refusing to generate checksums for release-manifest.json alone."
fi

for artifact in "${ALL_FILES[@]}"; do
    if [[ "$OUTPUT_FILE" == "$artifact" ]]; then
        die "Output filename '$OUTPUT_FILE' would overwrite an artifact."
    fi
done

info "Found ${DEB_COUNT} .deb, ${RPM_COUNT} .rpm, ${TARBALL_COUNT} .tar.gz, ${INSTALLER_COUNT} installer, ${RELEASE_KEY_COUNT} release key, and manifest=${MANIFEST_FILE:-none}"

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
