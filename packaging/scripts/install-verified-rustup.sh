#!/usr/bin/env bash
# install-verified-rustup.sh -- Install rustup-init after checksum verification.
#
# Usage:
#   install-verified-rustup.sh --arch ARCH --toolchain TOOLCHAIN [--os OS] [--libc LIBC] [--checksums FILE] [--version VER]
#
# ARCH accepts the package workflow architecture names amd64 and arm64
# (mapped to x86_64/aarch64 targets). OS accepts linux or darwin and defaults
# to linux. LIBC accepts gnu or musl for Linux and defaults to gnu.
# CHECKSUMS defaults to packaging/checksums.sha256.
# VERSION defaults to rustup-init 1.28.2.

set -euo pipefail

RUSTUP_VERSION="1.28.2"
ARCH=""
OS_NAME="linux"
LIBC="gnu"
RUST_TOOLCHAIN=""
CHECKSUMS_FILE=""

usage() {
    sed -n '2,9p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [install-verified-rustup] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[install-verified-rustup] %s\n' "$1" >&2
}

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --arch)
            if [[ "$#" -lt 2 ]]; then
                die "--arch requires a value"
            fi
            ARCH="$2"
            shift 2
            ;;
        --toolchain)
            if [[ "$#" -lt 2 ]]; then
                die "--toolchain requires a value"
            fi
            RUST_TOOLCHAIN="$2"
            shift 2
            ;;
        --libc)
            if [[ "$#" -lt 2 ]]; then
                die "--libc requires a value"
            fi
            LIBC="$2"
            shift 2
            ;;
        --os)
            if [[ "$#" -lt 2 ]]; then
                die "--os requires a value"
            fi
            OS_NAME="$2"
            shift 2
            ;;
        --checksums)
            if [[ "$#" -lt 2 ]]; then
                die "--checksums requires a value"
            fi
            CHECKSUMS_FILE="$2"
            shift 2
            ;;
        --version)
            if [[ "$#" -lt 2 ]]; then
                die "--version requires a value"
            fi
            RUSTUP_VERSION="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            die "unknown argument: $1"
            ;;
    esac
done

if [[ -z "$ARCH" ]]; then
    die "architecture is required"
fi

if [[ -z "$RUST_TOOLCHAIN" ]]; then
    die "toolchain is required"
fi

if [[ "$OS_NAME" = "linux" ]]; then
    case "$LIBC" in
        gnu|musl)
            ;;
        *)
            die "unsupported libc: $LIBC"
            ;;
    esac
elif [[ "$OS_NAME" != "darwin" ]]; then
    die "unsupported operating system: $OS_NAME"
elif [[ "$LIBC" != "gnu" ]]; then
    die "--libc is not supported with Darwin targets"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "$CHECKSUMS_FILE" ]]; then
    CHECKSUMS_FILE="${SCRIPT_DIR}/../checksums.sha256"
fi

case "${OS_NAME}:${ARCH}" in
    linux:amd64|linux:x86_64)
        RUSTUP_TARGET="x86_64-unknown-linux-${LIBC}"
        ;;
    linux:arm64|linux:aarch64)
        RUSTUP_TARGET="aarch64-unknown-linux-${LIBC}"
        ;;
    darwin:amd64|darwin:x86_64)
        RUSTUP_TARGET="x86_64-apple-darwin"
        ;;
    darwin:arm64|darwin:aarch64)
        RUSTUP_TARGET="aarch64-apple-darwin"
        ;;
    *)
        die "unsupported operating system/architecture: ${OS_NAME}/${ARCH}"
        ;;
esac

IDENTIFIER="rustup-init-${RUSTUP_VERSION}-${RUSTUP_TARGET}"
RUSTUP_URL="https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${RUSTUP_TARGET}/rustup-init"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rustup-init.XXXXXX")"
RUSTUP_INIT="${TMP_DIR}/rustup-init"

cleanup() {
    rm -rf "$TMP_DIR"
    return 0
}
trap cleanup EXIT

info "downloading ${IDENTIFIER}"
curl --proto '=https' --tlsv1.2 -fsSL -o "$RUSTUP_INIT" "$RUSTUP_URL"

"${SCRIPT_DIR}/verify-checksum.sh" \
    -f "$RUSTUP_INIT" \
    -i "$IDENTIFIER" \
    -c "$CHECKSUMS_FILE"

chmod 0755 "$RUSTUP_INIT"
"$RUSTUP_INIT" -y --profile minimal --no-modify-path \
    --default-toolchain "$RUST_TOOLCHAIN"
