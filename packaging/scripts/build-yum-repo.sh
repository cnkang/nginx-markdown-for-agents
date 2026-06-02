#!/usr/bin/env bash
# build-yum-repo.sh — Generate a standard YUM/DNF repository structure from .rpm packages.
#
# Usage:
#   build-yum-repo.sh --input-dir DIR --output-dir DIR [-h]
#
# Options:
#   --input-dir DIR     Directory containing .rpm packages to include
#   --output-dir DIR    Output directory for the YUM repository structure
#   -h, --help          Show this help message
#
# The script creates the following structure under OUTPUT_DIR:
#   repo/yum/
#   ├── packages/
#   │   └── *.rpm
#   ├── repodata/
#   │   ├── repomd.xml
#   │   ├── primary.xml.gz
#   │   ├── filelists.xml.gz
#   │   └── other.xml.gz
#   └── README.md
#
# Requirements:
#   createrepo_c (preferred) or createrepo
#
# Exit codes:
#   0  Repository built successfully
#   1  Error (missing arguments, missing tools, build failure)

set -euo pipefail

##############################################################################
# Helpers
##############################################################################

usage() {
    sed -n '3,22p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[build-yum-repo] %s\n' "$1" >&2
}

warn() {
    printf '[build-yum-repo] WARNING: %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

INPUT_DIR=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir)
            [[ -n "${2:-}" ]] || die "--input-dir requires a value"
            INPUT_DIR="$2"
            shift 2
            ;;
        --output-dir)
            [[ -n "${2:-}" ]] || die "--output-dir requires a value"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

##############################################################################
# Validation
##############################################################################

if [[ -z "$INPUT_DIR" ]]; then
    die "Missing required option: --input-dir"
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    die "Missing required option: --output-dir"
fi

if [[ ! -d "$INPUT_DIR" ]]; then
    die "Input directory does not exist: $INPUT_DIR"
fi

# Validate INPUT_DIR and OUTPUT_DIR contain only safe characters
case "$INPUT_DIR" in
    *[!a-zA-Z0-9/_.,+=-]*)
        die "Input directory path contains unsafe characters: $INPUT_DIR"
        ;;
    *)
        ;;
esac

case "$OUTPUT_DIR" in
    *[!a-zA-Z0-9/_.,+=-]*)
        die "Output directory path contains unsafe characters: $OUTPUT_DIR"
        ;;
    *)
        ;;
esac

# Determine which createrepo tool is available
CREATEREPO_CMD=""
if command -v createrepo_c >/dev/null 2>&1; then
    CREATEREPO_CMD="createrepo_c"
elif command -v createrepo >/dev/null 2>&1; then
    CREATEREPO_CMD="createrepo"
else
    die "Neither createrepo_c nor createrepo found. Install createrepo_c package."
fi

info "Using repository tool: $CREATEREPO_CMD"

# Check for .rpm files in input directory
RPM_COUNT=0
for f in "$INPUT_DIR"/*.rpm; do
    [[ -e "$f" ]] || continue
    RPM_COUNT=$((RPM_COUNT + 1))
done

if [[ "$RPM_COUNT" -eq 0 ]]; then
    die "No .rpm packages found in: $INPUT_DIR"
fi

info "Found $RPM_COUNT .rpm package(s) in $INPUT_DIR"

##############################################################################
# Create repository directory structure
##############################################################################

REPO_ROOT="${OUTPUT_DIR}/repo/yum"
PACKAGES_DIR="${REPO_ROOT}/packages"

info "Creating YUM repository structure at: $REPO_ROOT"

# Clean previous output if it exists
if [[ -d "$REPO_ROOT" ]]; then
    info "Removing existing repository at $REPO_ROOT"
    rm -rf "$REPO_ROOT"
fi

mkdir -p "$PACKAGES_DIR"

##############################################################################
# Copy .rpm packages to packages directory
##############################################################################

info "Copying .rpm packages to packages/"

for pkg in "$INPUT_DIR"/*.rpm; do
    [[ -e "$pkg" ]] || continue
    cp "$pkg" "$PACKAGES_DIR/"
    info "  Added: $(basename "$pkg")"
done

##############################################################################
# Generate repository metadata with createrepo
##############################################################################

info "Generating repository metadata..."

"$CREATEREPO_CMD" --database "$REPO_ROOT"

# Verify repomd.xml was created
if [[ ! -f "${REPO_ROOT}/repodata/repomd.xml" ]]; then
    die "Failed to generate repomd.xml"
fi

info "Repository metadata generated successfully"

##############################################################################
# Copy README.md to repository root
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
README_SRC="${PROJECT_ROOT}/packaging/repo/yum/README.md"

if [[ -f "$README_SRC" ]]; then
    cp "$README_SRC" "${REPO_ROOT}/README.md"
    info "Copied README.md to repository root"
else
    warn "README.md not found at $README_SRC, skipping"
fi

##############################################################################
# Summary
##############################################################################

info ""
info "YUM repository built successfully!"
info "  Root:       $REPO_ROOT"
info "  Packages:   $PACKAGES_DIR ($RPM_COUNT packages)"
info "  Metadata:   ${REPO_ROOT}/repodata/repomd.xml"
info ""
info "Next steps:"
info "  1. Sign repomd.xml: gpg --detach-sign --armor ${REPO_ROOT}/repodata/repomd.xml"
info "  2. Upload the repository to your hosting provider"
info "  3. Users add the repo with: yum-config-manager --add-repo <URL>"
info ""

exit 0
