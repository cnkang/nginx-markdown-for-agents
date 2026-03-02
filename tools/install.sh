#!/bin/bash
set -euo pipefail

# NGINX Markdown for Agents Install Script
# Usage: curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
# OR (if using specific release version):
# VERSION=v1.0.0 curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash

REPO="cnkang/nginx-markdown-for-agents"
RELEASE_VERSION="${VERSION:-}"
DOWNLOAD_URL_OVERRIDE="${DOWNLOAD_URL_OVERRIDE:-}"
DOWNLOAD_SHA256="${DOWNLOAD_SHA256:-}"

sha256_file() {
  local file="$1"

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
    return 0
  fi

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
    return 0
  fi

  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$file" | awk '{print $2}'
    return 0
  fi

  return 1
}

resolve_download_info() {
  local asset_name="$1"
  local release_api=""
  local release_json=""

  if [ -n "$DOWNLOAD_URL_OVERRIDE" ]; then
    printf '%s\n%s\n' "$DOWNLOAD_URL_OVERRIDE" "$DOWNLOAD_SHA256"
    return 0
  fi

  if [ -z "$RELEASE_VERSION" ]; then
    release_api="https://api.github.com/repos/${REPO}/releases/latest"
  else
    release_api="https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_VERSION}"
  fi

  release_json="$(curl -fsSL -H 'Accept: application/vnd.github+json' "$release_api")"

  RELEASE_JSON="$release_json" ASSET_NAME="$asset_name" python3 - <<'PY'
import json
import os
import sys

release_json = os.environ.get("RELEASE_JSON", "")
asset_name = os.environ.get("ASSET_NAME", "")

if not release_json or not asset_name:
    sys.exit(1)

data = json.loads(release_json)
assets = data.get("assets", [])

for asset in assets:
    if asset.get("name") != asset_name:
        continue

    url = asset.get("browser_download_url", "")
    digest = asset.get("digest", "")
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    else:
        digest = ""

    print(url)
    print(digest)
    sys.exit(0)

print("")
print("")
PY
}

echo "=================================================================================="
echo " NGINX Markdown for Agents - Binary Module Installer"
echo "=================================================================================="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (or with sudo)."
  exit 1
fi

# Detect Nginx version
if ! command -v nginx > /dev/null 2>&1; then
  echo "Error: nginx is not installed or not in PATH."
  echo "Please install NGINX first before running this script."
  exit 1
fi

NGINX_VERSION="$(nginx -v 2>&1 | grep -oE 'nginx/[0-9]+\.[0-9]+\.[0-9]+' | cut -d/ -f2)"
if [ -z "$NGINX_VERSION" ]; then
  echo "Error: Could not determine NGINX version."
  exit 1
fi
echo "[+] Detected NGINX version: $NGINX_VERSION"

# Detect OS type (glibc vs musl)
OS_TYPE="glibc"
if command -v ldd >/dev/null 2>&1 && ldd /bin/sh 2>&1 | grep -iq musl; then
  OS_TYPE="musl"
elif [ -f /etc/alpine-release ]; then
  OS_TYPE="musl"
fi
echo "[+] Detected OS family: $OS_TYPE"

# Detect Architecture
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
  ARCH="aarch64"
elif [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
  ARCH="x86_64"
else
  echo "Error: Unsupported architecture $ARCH"
  exit 1
fi
echo "[+] Detected Architecture: $ARCH"

# Determine target asset name
ASSET_NAME="ngx_http_markdown_filter_module-${NGINX_VERSION}-${OS_TYPE}-${ARCH}.tar.gz"

echo "----------------------------------------------------------------------------------"
echo "Looking for binary: $ASSET_NAME"

mapfile -t RELEASE_INFO < <(resolve_download_info "$ASSET_NAME")
DOWNLOAD_URL="${RELEASE_INFO[0]:-}"
EXPECTED_SHA256="${RELEASE_INFO[1]:-}"

if [ -z "$DOWNLOAD_URL" ]; then
  echo "Error: Could not find pre-built module for NGINX $NGINX_VERSION ($OS_TYPE $ARCH)."
  echo "You may need to compile the module from source."
  echo "See https://github.com/${REPO}/tree/main/docs/guides/INSTALLATION.md"
  exit 1
fi

echo "[+] Downloading $DOWNLOAD_URL ..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

curl -fsSL -o "$TMP_DIR/$ASSET_NAME" "$DOWNLOAD_URL"

if [ -n "$EXPECTED_SHA256" ]; then
  ACTUAL_SHA256="$(sha256_file "$TMP_DIR/$ASSET_NAME")"
  if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    echo "Error: Checksum verification failed for $ASSET_NAME"
    echo "Expected: $EXPECTED_SHA256"
    echo "Actual:   $ACTUAL_SHA256"
    exit 1
  fi
  echo "[+] SHA256 checksum verified"
else
  echo "[!] Release asset does not provide a SHA256 digest; skipping checksum verification"
fi

cd "$TMP_DIR"
tar -xzf "$ASSET_NAME"

MODULE_SO="ngx_http_markdown_filter_module.so"
if [ ! -f "$MODULE_SO" ]; then
  echo "Error: Extraction failed, $MODULE_SO not found."
  exit 1
fi

# Determine NGINX modules directory
# Official NGINX packages usually use /usr/lib/nginx/modules or /etc/nginx/modules
MODULES_DIR=""
if [ -d "/etc/nginx/modules" ]; then
  MODULES_DIR="/etc/nginx/modules"
elif [ -d "/usr/lib/nginx/modules" ]; then
  MODULES_DIR="/usr/lib/nginx/modules"
elif [ -d "/usr/share/nginx/modules" ]; then
  MODULES_DIR="/usr/share/nginx/modules"
elif [ -d "/usr/local/nginx/modules" ]; then
  MODULES_DIR="/usr/local/nginx/modules"
else
  # Default fallback
  MODULES_DIR="/etc/nginx/modules"
  mkdir -p "$MODULES_DIR"
fi

echo "[+] Installing module to $MODULES_DIR/"
cp "$MODULE_SO" "$MODULES_DIR/"
chmod 644 "$MODULES_DIR/$MODULE_SO"

echo "=================================================================================="
echo " Installation Complete!"
echo "=================================================================================="
echo "Next steps:"
echo "1. Edit your nginx.conf and add the following at the top (main context):"
echo "   load_module $MODULES_DIR/ngx_http_markdown_filter_module.so;"
echo ""
echo "2. Add 'markdown_filter on;' in your http, server, or location block."
echo ""
echo "3. Test config and reload NGINX:"
echo "   nginx -t && nginx -s reload"
echo "=================================================================================="
