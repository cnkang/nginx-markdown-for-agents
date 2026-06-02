#!/usr/bin/env bash
# build-apt-repo.sh — Generate a standard APT repository structure from .deb packages.
#
# Usage:
#   build-apt-repo.sh --input-dir DIR --output-dir DIR [--codename NAME] [-h]
#
# Options:
#   --input-dir DIR     Directory containing .deb packages to include
#   --output-dir DIR    Output directory for the APT repository structure
#   --codename NAME     Distribution codename (default: stable)
#   -h, --help          Show this help message
#
# The script creates the following structure under OUTPUT_DIR:
#   repo/apt/
#   ├── dists/<codename>/
#   │   └── main/
#   │       ├── binary-amd64/
#   │       │   ├── Packages
#   │       │   └── Packages.gz
#   │       ├── binary-arm64/
#   │       │   ├── Packages
#   │       │   └── Packages.gz
#   │       └── Release
#   ├── pool/
#   │   └── main/
#   │       └── *.deb
#   └── README.md
#
# Requirements:
#   dpkg-scanpackages (from dpkg-dev)
#   apt-ftparchive (from apt-utils) — optional, falls back to manual Release
#   gzip
#
# Exit codes:
#   0  Repository built successfully
#   1  Error (missing arguments, missing tools, build failure)

set -euo pipefail

##############################################################################
# Helpers
##############################################################################

usage() {
    sed -n '3,30p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[build-apt-repo] %s\n' "$1" >&2
}

warn() {
    printf '[build-apt-repo] WARNING: %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

INPUT_DIR=""
OUTPUT_DIR=""
CODENAME="stable"

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
        --codename)
            [[ -n "${2:-}" ]] || die "--codename requires a value"
            CODENAME="$2"
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

# Validate codename contains only safe characters (alphanumeric, dash, dot)
case "$CODENAME" in
    *[!a-zA-Z0-9._-]*)
        die "Invalid codename: $CODENAME (only alphanumeric, dash, dot allowed)"
        ;;
    "")
        die "Codename must not be empty"
        ;;
    *)
        ;;
esac

# Check for required tools
if ! command -v dpkg-scanpackages >/dev/null 2>&1; then
    die "dpkg-scanpackages not found. Install dpkg-dev package."
fi

if ! command -v gzip >/dev/null 2>&1; then
    die "gzip not found."
fi

# Check for .deb files in input directory
DEB_COUNT=0
for f in "$INPUT_DIR"/*.deb; do
    [[ -e "$f" ]] || continue
    DEB_COUNT=$((DEB_COUNT + 1))
done

if [[ "$DEB_COUNT" -eq 0 ]]; then
    die "No .deb packages found in: $INPUT_DIR"
fi

info "Found $DEB_COUNT .deb package(s) in $INPUT_DIR"

##############################################################################
# Supported architectures
##############################################################################

ARCHITECTURES="amd64 arm64"

##############################################################################
# Create repository directory structure
##############################################################################

REPO_ROOT="${OUTPUT_DIR}/repo/apt"
POOL_DIR="${REPO_ROOT}/pool/main"
DISTS_DIR="${REPO_ROOT}/dists/${CODENAME}"

info "Creating APT repository structure at: $REPO_ROOT"
info "Codename: $CODENAME"

# Clean previous output if it exists
if [[ -d "$REPO_ROOT" ]]; then
    info "Removing existing repository at $REPO_ROOT"
    rm -rf "$REPO_ROOT"
fi

mkdir -p "$POOL_DIR"
mkdir -p "$DISTS_DIR/main"

for arch in $ARCHITECTURES; do
    mkdir -p "${DISTS_DIR}/main/binary-${arch}"
done

##############################################################################
# Copy .deb packages to pool
##############################################################################

info "Copying .deb packages to pool/main/"

for pkg in "$INPUT_DIR"/*.deb; do
    [[ -e "$pkg" ]] || continue
    cp "$pkg" "$POOL_DIR/"
    info "  Added: $(basename "$pkg")"
done

##############################################################################
# Generate Packages files per architecture
##############################################################################

info "Generating Packages index files..."

for arch in $ARCHITECTURES; do
    BINARY_DIR="${DISTS_DIR}/main/binary-${arch}"
    PACKAGES_FILE="${BINARY_DIR}/Packages"

    info "  Scanning for arch=$arch..."

    # dpkg-scanpackages scans the pool relative to the repo root
    # Filter packages by architecture (including 'all' which applies to every arch)
    (
        cd "$REPO_ROOT"
        dpkg-scanpackages --arch "$arch" pool/main /dev/null 2>/dev/null || \
            dpkg-scanpackages pool/main /dev/null 2>/dev/null
    ) | while IFS= read -r line; do
        printf '%s\n' "$line"
    done > "$PACKAGES_FILE"

    # If dpkg-scanpackages does not support --arch, filter manually
    if [[ ! -s "$PACKAGES_FILE" ]]; then
        info "  Fallback: scanning all packages for arch=$arch"
        (
            cd "$REPO_ROOT"
            dpkg-scanpackages pool/main /dev/null 2>/dev/null || true
        ) > "${PACKAGES_FILE}.tmp"

        # Filter by Architecture field (keep matching arch and 'all')
        awk -v arch="$arch" '
            BEGIN { RS=""; FS="\n"; ORS="\n\n" }
            {
                found = 0
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^Architecture: /) {
                        sub(/^Architecture: /, "", $i)
                        if ($i == arch || $i == "all") {
                            found = 1
                        }
                    }
                }
                if (found) print $0
            }
        ' "${PACKAGES_FILE}.tmp" > "$PACKAGES_FILE"
        rm -f "${PACKAGES_FILE}.tmp"
    fi

    # Generate compressed version
    gzip -9 -c "$PACKAGES_FILE" > "${PACKAGES_FILE}.gz"

    PKG_COUNT=$(grep -c '^Package:' "$PACKAGES_FILE" 2>/dev/null || true)
    PKG_COUNT="${PKG_COUNT:-0}"
    # Trim whitespace/newlines from count
    PKG_COUNT=$(printf '%s' "$PKG_COUNT" | tr -d '[:space:]')
    info "  binary-${arch}: ${PKG_COUNT} package(s) indexed"
done

##############################################################################
# Generate Release file
##############################################################################

info "Generating Release file..."

RELEASE_FILE="${DISTS_DIR}/Release"
DATE_STR="$(date -u '+%a, %d %b %Y %H:%M:%S UTC')"

# Build architectures and components strings
ARCH_LIST=""
for arch in $ARCHITECTURES; do
    if [[ -n "$ARCH_LIST" ]]; then
        ARCH_LIST="${ARCH_LIST} ${arch}"
    else
        ARCH_LIST="$arch"
    fi
done

# Try apt-ftparchive first (produces proper checksums)
if command -v apt-ftparchive >/dev/null 2>&1; then
    info "  Using apt-ftparchive for Release generation"

    # Generate Release with apt-ftparchive
    (
        cd "$REPO_ROOT"
        apt-ftparchive \
            -o "APT::FTPArchive::Release::Origin=nginx-markdown-for-agents" \
            -o "APT::FTPArchive::Release::Label=nginx-markdown-for-agents" \
            -o "APT::FTPArchive::Release::Suite=${CODENAME}" \
            -o "APT::FTPArchive::Release::Codename=${CODENAME}" \
            -o "APT::FTPArchive::Release::Architectures=${ARCH_LIST}" \
            -o "APT::FTPArchive::Release::Components=main" \
            -o "APT::FTPArchive::Release::Date=${DATE_STR}" \
            release "dists/${CODENAME}"
    ) > "$RELEASE_FILE"
else
    info "  apt-ftparchive not found, generating Release manually"

    # Write Release header
    cat > "$RELEASE_FILE" <<RELEASE_HEADER
Origin: nginx-markdown-for-agents
Label: nginx-markdown-for-agents
Suite: ${CODENAME}
Codename: ${CODENAME}
Date: ${DATE_STR}
Architectures: ${ARCH_LIST}
Components: main
Description: nginx-markdown-for-agents APT repository
RELEASE_HEADER

    # Compute checksums for all index files
    printf 'MD5Sum:\n' >> "$RELEASE_FILE"
    (
        cd "$DISTS_DIR"
        find main -type f \( -name 'Packages' -o -name 'Packages.gz' \) | sort | while IFS= read -r file; do
            md5val=$(md5sum "$file" 2>/dev/null | cut -d' ' -f1 || md5 -q "$file" 2>/dev/null)
            size=$(wc -c < "$file" | tr -d ' ')
            printf ' %s %8s %s\n' "$md5val" "$size" "$file"
        done
    ) >> "$RELEASE_FILE"

    printf 'SHA256:\n' >> "$RELEASE_FILE"
    (
        cd "$DISTS_DIR"
        find main -type f \( -name 'Packages' -o -name 'Packages.gz' \) | sort | while IFS= read -r file; do
            sha256val=$(shasum -a 256 "$file" 2>/dev/null | cut -d' ' -f1 || sha256sum "$file" 2>/dev/null | cut -d' ' -f1)
            size=$(wc -c < "$file" | tr -d ' ')
            printf ' %s %8s %s\n' "$sha256val" "$size" "$file"
        done
    ) >> "$RELEASE_FILE"
fi

info "  Release file created: $RELEASE_FILE"

##############################################################################
# Copy README.md to repository root
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
README_SRC="${PROJECT_ROOT}/packaging/repo/apt/README.md"

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
info "APT repository built successfully!"
info "  Root:       $REPO_ROOT"
info "  Codename:   $CODENAME"
info "  Archs:      $ARCH_LIST"
info "  Pool:       $POOL_DIR ($DEB_COUNT packages)"
info ""
info "Next steps:"
info "  1. Sign the Release file: packaging/scripts/sign-repo-metadata.sh -a $REPO_ROOT"
info "  2. Upload the repository to your hosting provider"
info ""

exit 0
