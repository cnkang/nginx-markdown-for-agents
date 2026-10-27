#!/usr/bin/env bash
# build-brotli-static.sh -- Build the pinned Brotli decoder for static linking.
#
# Usage:
#   build-brotli-static.sh [-v VERSION] [-p PREFIX] [-c CHECKSUMS_FILE] [-h]
#
# Options:
#   -v VERSION          Brotli version to build (default: 1.2.1)
#   -p PREFIX           Install prefix (default: /usr/local)
#   -c CHECKSUMS_FILE   Checksum table (default: packaging/checksums.sha256)
#   -h                  Show this help message
#
# Exit codes:
#   0  Static Brotli archives installed under PREFIX
#   1  Any failure: missing tool, checksum mismatch, or a shared library
#
# Official Linux release modules bundle the Brotli decoder so the artifact
# does not inherit the host distribution's Brotli package. CVE-2026-111497 is
# an out-of-bounds write in the decoder that is fixed in 1.0.10, 1.1.1, and
# 1.2.1. The AlmaLinux 9 and Alpine 3.20 release bases did not carry a fixed
# Brotli package when this helper was added.
#
# Only static archives and pkg-config metadata are installed. The post-build
# check fails if a shared Brotli library appears under PREFIX, so a later
# link cannot silently fall back to a shared copy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CHECKSUMS="${SCRIPT_DIR}/../checksums.sha256"

VERSION="1.2.1"
PREFIX="/usr/local"
CHECKSUMS_FILE="${DEFAULT_CHECKSUMS}"

usage() {
    sed -n '2,25p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [build-brotli-static] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[build-brotli-static] %s\n' "$1" >&2
}

while getopts "v:p:c:h" opt; do
    case "$opt" in
        v) VERSION="$OPTARG" ;;
        p) PREFIX="$OPTARG" ;;
        c) CHECKSUMS_FILE="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

if [[ -z "$VERSION" ]]; then
    die "version must not be empty"
fi
if [[ -z "$PREFIX" ]]; then
    die "install prefix must not be empty"
fi

for tool in curl tar cmake make; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        die "required tool not found: $tool"
    fi
done

if [[ ! -f "$CHECKSUMS_FILE" ]]; then
    die "checksums file not found: $CHECKSUMS_FILE"
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/brotli-static.XXXXXX")"
cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT INT TERM

TARBALL="${WORK_DIR}/brotli-${VERSION}.tar.gz"
URL="https://github.com/google/brotli/archive/refs/tags/v${VERSION}.tar.gz"

info "Downloading Brotli ${VERSION}"
curl --proto '=https' --tlsv1.2 -fsSL -o "$TARBALL" "$URL" \
    || die "download failed: $URL"

bash "${SCRIPT_DIR}/verify-checksum.sh" \
    -f "$TARBALL" \
    -i "brotli-${VERSION}" \
    -c "$CHECKSUMS_FILE"

tar -xzf "$TARBALL" -C "$WORK_DIR"
SRC_DIR="${WORK_DIR}/brotli-${VERSION}"
if [[ ! -d "$SRC_DIR" ]]; then
    die "unexpected archive layout in ${TARBALL}"
fi

BUILD_DIR="${WORK_DIR}/build"
info "Configuring static build for Brotli ${VERSION}"
cmake -S "$SRC_DIR" -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DBROTLI_DISABLE_TESTS=ON \
    -DBROTLI_BUILD_TOOLS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib

JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
info "Building static archives with ${JOBS} job(s)"
cmake --build "$BUILD_DIR" --parallel "$JOBS"

info "Installing static archives into ${PREFIX}"
cmake --install "$BUILD_DIR"

for archive in libbrotlidec.a libbrotlicommon.a; do
    if [[ ! -f "${PREFIX}/lib/${archive}" ]]; then
        die "static archive missing after install: ${PREFIX}/lib/${archive}"
    fi
done

PACKAGE_CONFIG="${PREFIX}/lib/pkgconfig/libbrotlidec.pc"
if [[ ! -f "$PACKAGE_CONFIG" ]]; then
    die "pkg-config metadata missing after install: ${PACKAGE_CONFIG}"
fi
if ! grep -q "^Version: ${VERSION}$" "$PACKAGE_CONFIG"; then
    die "pkg-config metadata does not report Brotli ${VERSION}"
fi

# A shared library would let the linker prefer the host copy and reintroduce
# the vulnerability this build exists to remove.
for shared in "${PREFIX}"/lib/libbrotli*.so*; do
    if [[ -e "$shared" ]]; then
        die "shared Brotli library installed: $shared"
    fi
done

info "Installed static Brotli ${VERSION} under ${PREFIX}/lib"
