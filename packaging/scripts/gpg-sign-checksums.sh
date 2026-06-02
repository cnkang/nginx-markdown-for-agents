#!/usr/bin/env bash
# gpg-sign-checksums.sh — Create a detached GPG signature for SHA256SUMS.
#
# Usage:
#   gpg-sign-checksums.sh <SHA256SUMS_FILE> <GPG_KEY_ID>
#
# Arguments:
#   SHA256SUMS_FILE   Path to the SHA256SUMS file to sign
#   GPG_KEY_ID        GPG key ID to use for signing
#
# Environment:
#   GPG_PRIVATE_KEY   ASCII-armored GPG private key (required)
#   GPG_PASSPHRASE    Passphrase for the GPG private key (required)
#
# Exit codes:
#   0  Signature created and verified successfully
#   1  Error (missing arguments, import failure, signing failure)
#
# Security:
#   - GPG private key material is NEVER output to stdout or stderr
#   - Imported key is removed from the keyring after signing
#   - All GPG operations use --batch and --pinentry-mode loopback

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
    printf '[gpg-sign-checksums] %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    "")
        die "Missing required argument: SHA256SUMS file path. Use -h for help."
        ;;
    *)
        CHECKSUMS_FILE="$1"
        ;;
esac

case "${2:-}" in
    "")
        die "Missing required argument: GPG_KEY_ID. Use -h for help."
        ;;
    *)
        GPG_KEY_ID="$2"
        ;;
esac

##############################################################################
# Validation
##############################################################################

if [[ ! -f "$CHECKSUMS_FILE" ]]; then
    die "SHA256SUMS file not found: $CHECKSUMS_FILE"
fi

if [[ -z "${GPG_PRIVATE_KEY:-}" ]]; then
    die "GPG_PRIVATE_KEY environment variable is not set or empty."
fi

if [[ -z "${GPG_PASSPHRASE:-}" ]]; then
    die "GPG_PASSPHRASE environment variable is not set or empty."
fi

if ! command -v gpg >/dev/null 2>&1; then
    die "gpg command not found. Install gnupg to sign checksums."
fi

##############################################################################
# GPG key import (suppress all output to prevent key material leakage)
##############################################################################

info "Importing GPG key..."

# Import the private key from environment variable.
# All output is suppressed to prevent any key material from leaking.
if ! printf '%s' "$GPG_PRIVATE_KEY" | gpg --batch --yes --import >/dev/null 2>&1; then
    die "Failed to import GPG private key. Check GPG_PRIVATE_KEY format."
fi

info "GPG key imported successfully."

##############################################################################
# Create detached signature
##############################################################################

SIGNATURE_FILE="${CHECKSUMS_FILE}.asc"

info "Signing $CHECKSUMS_FILE with key $GPG_KEY_ID..."

# Sign using passphrase via stdin (fd 0) for non-interactive operation.
# --batch and --pinentry-mode loopback ensure no interactive prompts.
if ! printf '%s' "$GPG_PASSPHRASE" | gpg \
    --batch \
    --yes \
    --pinentry-mode loopback \
    --passphrase-fd 0 \
    --detach-sign \
    --armor \
    --local-user "$GPG_KEY_ID" \
    --output "$SIGNATURE_FILE" \
    "$CHECKSUMS_FILE" 2>/dev/null; then
    die "GPG signing failed. Check GPG_KEY_ID and GPG_PASSPHRASE."
fi

info "Signature created: $SIGNATURE_FILE"

##############################################################################
# Verify signature
##############################################################################

info "Verifying signature..."

if ! gpg --batch --verify "$SIGNATURE_FILE" "$CHECKSUMS_FILE" >/dev/null 2>&1; then
    die "Signature verification failed for $SIGNATURE_FILE"
fi

info "Signature verified successfully."

##############################################################################
# Cleanup: remove imported private key from keyring
##############################################################################

info "Cleaning up GPG keyring..."

# Get the keygrip(s) for the imported key to delete from the agent
FINGERPRINT=""
FINGERPRINT="$(gpg --batch --with-colons --list-secret-keys "$GPG_KEY_ID" 2>/dev/null \
    | grep '^fpr:' | head -n1 | cut -d: -f10)" || true

if [[ -n "$FINGERPRINT" ]]; then
    # Delete the secret key first, then the public key
    gpg --batch --yes --delete-secret-keys "$FINGERPRINT" >/dev/null 2>&1 || true
    gpg --batch --yes --delete-keys "$FINGERPRINT" >/dev/null 2>&1 || true
    info "GPG key material removed from keyring."
else
    # Fallback: try deleting by key ID directly
    gpg --batch --yes --delete-secret-keys "$GPG_KEY_ID" >/dev/null 2>&1 || true
    gpg --batch --yes --delete-keys "$GPG_KEY_ID" >/dev/null 2>&1 || true
    info "GPG key material cleanup attempted (fallback method)."
fi

##############################################################################
# Done
##############################################################################

info "GPG signing complete: $SIGNATURE_FILE"
exit 0
