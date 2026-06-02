#!/usr/bin/env bash
# build-deb.sh — Build a .deb package for nginx-module-markdown using dpkg-deb.
#
# Usage:
#   build-deb.sh --version VERSION --arch ARCH --nginx-version VERSION \
#                --output-dir DIR --module-so PATH [-h]
#
# Options:
#   --version VERSION         Package version (e.g., 0.7.0)
#   --arch ARCH               Architecture: amd64 or arm64
#   --nginx-version VERSION   Target NGINX version for ABI dependency (e.g., 1.24.0)
#   --output-dir DIR          Directory to place the output .deb file
#   --module-so PATH          Path to the compiled .so module file
#   -h, --help                Show this help message
#
# Environment:
#   BUILD_DIR   Override the intermediate build directory (default: ./build/deb)
#
# Exit codes:
#   0  Package built successfully
#   1  Error (missing arguments, missing files, build failure)

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
    exit 1
}

info() {
    printf '[build-deb] %s\n' "$1" >&2
}

##############################################################################
# Argument parsing
##############################################################################

PKG_VERSION=""
PKG_ARCH=""
NGINX_VERSION=""
OUTPUT_DIR=""
MODULE_SO=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            [[ -n "${2:-}" ]] || die "--version requires a value"
            PKG_VERSION="$2"
            shift 2
            ;;
        --arch)
            [[ -n "${2:-}" ]] || die "--arch requires a value"
            PKG_ARCH="$2"
            shift 2
            ;;
        --nginx-version)
            [[ -n "${2:-}" ]] || die "--nginx-version requires a value"
            NGINX_VERSION="$2"
            shift 2
            ;;
        --output-dir)
            [[ -n "${2:-}" ]] || die "--output-dir requires a value"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --module-so)
            [[ -n "${2:-}" ]] || die "--module-so requires a value"
            MODULE_SO="$2"
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

if [[ -z "$PKG_VERSION" ]]; then
    die "Missing required option: --version"
fi

if [[ -z "$PKG_ARCH" ]]; then
    die "Missing required option: --arch"
fi

if [[ -z "$NGINX_VERSION" ]]; then
    die "Missing required option: --nginx-version"
fi

if [[ -z "$OUTPUT_DIR" ]]; then
    die "Missing required option: --output-dir"
fi

if [[ -z "$MODULE_SO" ]]; then
    die "Missing required option: --module-so"
fi

# Validate architecture
case "$PKG_ARCH" in
    amd64|arm64) ;;
    *) die "Unsupported architecture: $PKG_ARCH (must be amd64 or arm64)" ;;
esac

# Validate version format (basic semver check)
case "$PKG_VERSION" in
    [0-9]*.[0-9]*.[0-9]*)  ;;
    *) die "Invalid version format: $PKG_VERSION (expected X.Y.Z)" ;;
esac

# Validate module .so exists
if [[ ! -f "$MODULE_SO" ]]; then
    die "Module .so file not found: $MODULE_SO"
fi

# Derive NGINX ABI version (major.minor from nginx version)
NGINX_ABI_VERSION="${NGINX_VERSION%.*}"

# Locate project root (relative to this script)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEBIAN_DIR="${PROJECT_ROOT}/packaging/debian"

# Validate debian template files exist
for tmpl_file in postinst postrm conffiles; do
    if [[ ! -f "${DEBIAN_DIR}/${tmpl_file}" ]]; then
        die "Missing debian template: ${DEBIAN_DIR}/${tmpl_file}"
    fi
done

##############################################################################
# Build directory setup
##############################################################################

BUILD_DIR="${BUILD_DIR:-./build/deb}"

info "Cleaning build directory: ${BUILD_DIR}"
rm -rf "${BUILD_DIR}"

# Create dpkg-deb directory structure
mkdir -p "${BUILD_DIR}/DEBIAN"
mkdir -p "${BUILD_DIR}/usr/lib/nginx/modules"
mkdir -p "${BUILD_DIR}/etc/nginx/modules-available"

info "Build directory structure created"

##############################################################################
# Install module .so
##############################################################################

info "Installing module: $(basename "$MODULE_SO")"
cp "$MODULE_SO" "${BUILD_DIR}/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
chmod 0644 "${BUILD_DIR}/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"

##############################################################################
# Install configuration snippet
##############################################################################

info "Installing module configuration snippet"
cat > "${BUILD_DIR}/etc/nginx/modules-available/mod-markdown.conf" <<'CONF'
# nginx-module-markdown: Uncomment the line below to enable the module.
# After enabling, run: nginx -t && systemctl reload nginx
#load_module modules/ngx_http_markdown_filter_module.so;
CONF
chmod 0644 "${BUILD_DIR}/etc/nginx/modules-available/mod-markdown.conf"

##############################################################################
# Generate control file
##############################################################################

info "Generating control file (version=${PKG_VERSION}, arch=${PKG_ARCH}, nginx-abi=${NGINX_ABI_VERSION})"

INSTALLED_SIZE=$(du -sk "${BUILD_DIR}" 2>/dev/null | cut -f1)

cat > "${BUILD_DIR}/DEBIAN/control" <<CONTROL
Package: nginx-module-markdown
Version: ${PKG_VERSION}
Architecture: ${PKG_ARCH}
Maintainer: Kang Liu <liukang@noreply.github.com>
Depends: nginx (>= ${NGINX_ABI_VERSION}.0), nginx-abi-${NGINX_ABI_VERSION}
Installed-Size: ${INSTALLED_SIZE}
Section: web
Priority: optional
Homepage: https://github.com/cnkang/nginx-markdown-for-agents
Description: NGINX module for serving Markdown to AI agents
 An NGINX dynamic filter module that converts HTML responses to Markdown
 format for AI agent consumption. Features include:
 .
  * Streaming and full-buffer conversion engines
  * Content negotiation via Accept headers (q-value based)
  * Automatic decompression with bounded budget
  * GFM/MDX flavor support
  * Noise pruning and token estimation
  * Conditional request support (ETag, If-Modified-Since)
  * Prometheus metrics endpoint
 .
 The module is installed as a dynamic module (.so) and must be explicitly
 enabled via load_module directive in nginx.conf.
CONTROL

##############################################################################
# Copy maintainer scripts
##############################################################################

info "Copying maintainer scripts"
cp "${DEBIAN_DIR}/postinst" "${BUILD_DIR}/DEBIAN/postinst"
cp "${DEBIAN_DIR}/postrm" "${BUILD_DIR}/DEBIAN/postrm"
cp "${DEBIAN_DIR}/conffiles" "${BUILD_DIR}/DEBIAN/conffiles"

chmod 0755 "${BUILD_DIR}/DEBIAN/postinst"
chmod 0755 "${BUILD_DIR}/DEBIAN/postrm"
chmod 0644 "${BUILD_DIR}/DEBIAN/conffiles"

##############################################################################
# Generate md5sums
##############################################################################

info "Computing md5sums"

# Generate md5sums for all installed files (paths relative to package root)
(
    cd "${BUILD_DIR}"
    find . -type f ! -path './DEBIAN/*' -print0 | sort -z | while IFS= read -r -d '' file; do
        # Strip leading ./
        rel_path="${file#./}"
        md5sum "$file" | sed "s|  \\./|  |"
    done
) > "${BUILD_DIR}/DEBIAN/md5sums"

chmod 0644 "${BUILD_DIR}/DEBIAN/md5sums"

##############################################################################
# Build .deb package
##############################################################################

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

DEB_FILENAME="nginx-module-markdown_${PKG_VERSION}_${PKG_ARCH}.deb"
DEB_OUTPUT="${OUTPUT_DIR}/${DEB_FILENAME}"

info "Building .deb package: ${DEB_FILENAME}"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    die "dpkg-deb not found. Install dpkg or dpkg-dev to build .deb packages."
fi

dpkg-deb --build "${BUILD_DIR}" "${DEB_OUTPUT}"

##############################################################################
# Verification
##############################################################################

if [[ ! -f "${DEB_OUTPUT}" ]]; then
    die "Build failed: output file not created at ${DEB_OUTPUT}"
fi

DEB_SIZE=$(wc -c < "${DEB_OUTPUT}" | tr -d ' ')
info "Package built successfully: ${DEB_OUTPUT} (${DEB_SIZE} bytes)"

# Print package info summary
info "Package info:"
dpkg-deb --info "${DEB_OUTPUT}" >&2 2>/dev/null || true

exit 0
