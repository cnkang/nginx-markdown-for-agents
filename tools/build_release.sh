#!/bin/bash
# build_release.sh — Build the nginx-markdown-for-agents dynamic module for a
# specific NGINX version, OS type, and architecture using Docker.
#
# Usage: tools/build_release.sh [nginx_version] [os_type] [arch]
#   nginx_version — nginx version string, "stable", or "mainline" (default: stable)
#   os_type       — "glibc" or "musl" (default: glibc)
#   arch          — "x86_64", "amd64", "aarch64", or "arm64" (default: x86_64)
set -euo pipefail

NGINX_VERSION=${1:-stable}
OS_TYPE=${2:-glibc} # glibc or musl
ARCH=${3:-x86_64} # x86_64/amd64 or aarch64/arm64

# Resolve a requested nginx version string to an exact MAJOR.MINOR.PATCH version.
#
# Arguments:
#   $1 - requested version: "stable" or "mainline" to fetch the latest from
#        nginx.org, or an explicit version string returned as-is
#
# Outputs:
#   Writes the resolved MAJOR.MINOR.PATCH version string to stdout
#
# Returns:
#   0 on success; exits with status 1 if curl or python3 is missing,
#   or if the version cannot be resolved from the nginx.org download page
resolve_nginx_version() {
    local requested="$1"
    local page=""
    local version=""

    case "$requested" in
        stable|mainline)
            if ! command -v curl >/dev/null 2>&1; then
                echo "curl is required to resolve nginx channel: $requested"
                exit 1
            fi
            if ! command -v python3 >/dev/null 2>&1; then
                echo "python3 is required to resolve nginx channel: $requested"
                exit 1
            fi

            page="$(curl --proto '=https' --tlsv1.2 -fsSL https://nginx.org/en/download.html)"
            version="$(
                NGINX_DOWNLOAD_HTML="${page}" CHANNEL="${requested}" python3 - <<'PY'
import os
import re

html = os.environ.get("NGINX_DOWNLOAD_HTML", "")
channel = os.environ.get("CHANNEL", "")

if channel == "mainline":
    pattern = r"Mainline version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
elif channel == "stable":
    pattern = r"Stable version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
else:
    raise SystemExit(1)

match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
if not match:
    raise SystemExit(1)

print(match.group(1))
PY
            )"

            if [[ -z "$version" ]]; then
                echo "Failed to resolve latest ${requested} nginx version."
                exit 1
            fi
            printf '%s\n' "$version"
            ;;
        *)
            printf '%s\n' "$requested"
            ;;
    esac

    return 0
}

case "$OS_TYPE" in
    glibc|musl) ;;
    *)
        echo "OS_TYPE must be 'glibc' or 'musl'."
        exit 1
        ;;
esac

case "$ARCH" in
    x86_64|amd64)
        ARCH="x86_64"
        PLATFORM="linux/amd64"
        ;;
    aarch64|arm64)
        ARCH="aarch64"
        PLATFORM="linux/arm64"
        ;;
    *)
        echo "ARCH must be one of: x86_64, amd64, aarch64, arm64."
        exit 1
        ;;
esac

NGINX_VERSION="$(resolve_nginx_version "$NGINX_VERSION")"

DOCKERFILE="tools/build_release/Dockerfile.$OS_TYPE"
OUT_DIR="dist/${NGINX_VERSION}-${OS_TYPE}-${ARCH}"

if [[ ! -f "$DOCKERFILE" ]]; then
    echo "Dockerfile not found: $DOCKERFILE"
    exit 1
fi

mkdir -p "$OUT_DIR"

echo "Building NGINX Markdown Module v$NGINX_VERSION for $OS_TYPE on $ARCH (platform: $PLATFORM)..."

# Ensure docker buildx is available and can build multi-platform if needed
# Note: For building aarch64 on x86_64 Mac, docker desktop handles this automatically.

docker buildx build \
    --platform "$PLATFORM" \
    --build-arg NGINX_VERSION="$NGINX_VERSION" \
    -f "$DOCKERFILE" \
    -t nginx-markdown-builder \
    --load \
    .

# Extract the built .so
CONTAINER_ID=$(docker create nginx-markdown-builder)
docker cp "${CONTAINER_ID}:/out/ngx_http_markdown_filter_module.so" "$OUT_DIR/"
docker rm "$CONTAINER_ID"

# Clean up image to save space
docker rmi nginx-markdown-builder

# Compress it as a tar.gz for distribution
cd "$OUT_DIR"
TAR_NAME="ngx_http_markdown_filter_module-${NGINX_VERSION}-${OS_TYPE}-${ARCH}.tar.gz"
tar -czf "$TAR_NAME" ngx_http_markdown_filter_module.so
cd - > /dev/null

echo "==> Successfully built: $OUT_DIR/$TAR_NAME"
