#!/usr/bin/env bash
# sign-repo-metadata.sh — Sign APT and YUM repository metadata using GPG.
#
# Usage:
#   sign-repo-metadata.sh [-k KEY_ID] [-a APT_DIR] [-y YUM_DIR] [--require-metadata] [-h]
#
# Options:
#   -k KEY_ID           GPG key ID to use for signing (default: $GPG_KEY_ID env var)
#   -a APT_DIR          APT repository root (containing dists/) (default: ./repo/apt)
#   -y YUM_DIR          YUM repository root (containing repodata/) (default: ./repo/yum)
#   --require-metadata   Exit with error if no metadata found (instead of warning)
#   -h                  Show this help message
#
# Environment:
#   GPG_KEY_ID  GPG key ID (used if -k not provided)
#
# Exit codes:
#   0  All metadata signed and verified successfully
#   1  Error (missing key, missing files, signing/verification failure)

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
    printf '[sign-repo-metadata] %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

KEY_ID="${GPG_KEY_ID:-}"
APT_DIR="./repo/apt"
YUM_DIR="./repo/yum"
REQUIRE_METADATA=0

while getopts "k:a:y:h-:" opt; do
    case "$opt" in
        k) KEY_ID="$OPTARG" ;;
        a) APT_DIR="$OPTARG" ;;
        y) YUM_DIR="$OPTARG" ;;
        -)
            case "${OPTARG}" in
                require-metadata) REQUIRE_METADATA=1 ;;
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

##############################################################################
# Sign APT repository metadata
##############################################################################

sign_apt_release() {
    local release_file="$1"
    local release_dir
    release_dir="$(dirname "$release_file")"
    local release_gpg="${release_dir}/Release.gpg"
    local inrelease="${release_dir}/InRelease"

    info "Signing APT Release: $release_file"

    # Create detached signature (Release.gpg)
    rm -f "$release_gpg"
    gpg --batch --yes --armor --detach-sign \
        --default-key "$KEY_ID" \
        --output "$release_gpg" \
        "$release_file"
    info "Created: $release_gpg"

    # Create inline signature (InRelease)
    rm -f "$inrelease"
    gpg --batch --yes --armor --clearsign \
        --default-key "$KEY_ID" \
        --output "$inrelease" \
        "$release_file"
    info "Created: $inrelease"

    # Verify signatures
    info "Verifying Release.gpg..."
    if ! gpg --batch --verify "$release_gpg" "$release_file" 2>/dev/null; then
        die "Verification failed for $release_gpg"
    fi
    info "OK: Release.gpg verified"

    info "Verifying InRelease..."
    if ! gpg --batch --verify "$inrelease" 2>/dev/null; then
        die "Verification failed for $inrelease"
    fi
    info "OK: InRelease verified"

    return 0
}

sign_apt() {
    if [[ ! -d "$APT_DIR" ]]; then
        info "APT directory '$APT_DIR' not found, skipping APT signing."
        return 0
    fi

    local found=0

    # Find all Release files under dists/
    while IFS= read -r release_file; do
        [[ -e "$release_file" ]] || continue
        sign_apt_release "$release_file"
        found=$((found + 1))
    done <<EOF
$(find "$APT_DIR/dists" -name "Release" -not -name "InRelease" 2>/dev/null || true)
EOF

    if [[ "$found" -eq 0 ]]; then
        if [[ "$REQUIRE_METADATA" -eq 1 ]]; then
            die "No Release files found under $APT_DIR/dists/ (--require-metadata mode)"
        fi
        info "WARNING: No Release files found under $APT_DIR/dists/"
    else
        info "APT signing complete: $found Release file(s) signed"
    fi

    return 0
}

##############################################################################
# Sign YUM repository metadata
##############################################################################

sign_yum_repomd() {
    local repomd_file="$1"
    local repomd_asc="${repomd_file}.asc"

    info "Signing YUM repomd.xml: $repomd_file"

    # Create detached ASCII-armored signature
    rm -f "$repomd_asc"
    gpg --batch --yes --armor --detach-sign \
        --default-key "$KEY_ID" \
        --output "$repomd_asc" \
        "$repomd_file"
    info "Created: $repomd_asc"

    # Verify signature
    info "Verifying repomd.xml.asc..."
    if ! gpg --batch --verify "$repomd_asc" "$repomd_file" 2>/dev/null; then
        die "Verification failed for $repomd_asc"
    fi
    info "OK: repomd.xml.asc verified"

    return 0
}

sign_yum() {
    if [[ ! -d "$YUM_DIR" ]]; then
        info "YUM directory '$YUM_DIR' not found, skipping YUM signing."
        return 0
    fi

    local found=0

    # Find all repomd.xml files under repodata/
    while IFS= read -r repomd_file; do
        [[ -e "$repomd_file" ]] || continue
        sign_yum_repomd "$repomd_file"
        found=$((found + 1))
    done <<EOF
$(find "$YUM_DIR" -path "*/repodata/repomd.xml" 2>/dev/null || true)
EOF

    if [[ "$found" -eq 0 ]]; then
        if [[ "$REQUIRE_METADATA" -eq 1 ]]; then
            die "No repomd.xml files found under $YUM_DIR (--require-metadata mode)"
        fi
        info "WARNING: No repomd.xml files found under $YUM_DIR"
    else
        info "YUM signing complete: $found repomd.xml file(s) signed"
    fi

    return 0
}

##############################################################################
# Main
##############################################################################

sign_apt
sign_yum

info "Repository metadata signing complete."
exit 0
