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

fetch_release_json() {
  local release_api=""

  if [ -z "$RELEASE_VERSION" ]; then
    release_api="https://api.github.com/repos/${REPO}/releases/latest"
  else
    release_api="https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_VERSION}"
  fi

  curl -fsSL -H 'Accept: application/vnd.github+json' "$release_api" 2>/dev/null
}

fetch_dist_index_json() {
  local ref_name="$1"
  local dist_api="https://api.github.com/repos/${REPO}/contents/dist?ref=${ref_name}"
  curl -fsSL -H 'Accept: application/vnd.github+json' "$dist_api" 2>/dev/null
}

resolve_download_info() {
  local asset_name="$1"
  local os_type="$2"
  local arch="$3"
  local nginx_version="$4"
  local ref_name="$5"
  local release_json="${6:-}"
  local dist_index_json="${7:-}"
  local parse_result=""

  if [ -n "$DOWNLOAD_URL_OVERRIDE" ]; then
    printf '%s\n%s\n%s\n' "$DOWNLOAD_URL_OVERRIDE" "$DOWNLOAD_SHA256" ""
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required by the installer."
    exit 1
  fi

  parse_result="$(
    RELEASE_JSON="$release_json" \
    DIST_INDEX_JSON="$dist_index_json" \
    ASSET_NAME="$asset_name" \
    OS_TYPE="$os_type" \
    ARCH="$arch" \
    NGINX_VERSION="$nginx_version" \
    REPO_NAME="$REPO" \
    REF_NAME="$ref_name" \
    python3 - <<'PY'
import json
import os
import re
import sys

release_json = os.environ.get("RELEASE_JSON", "")
dist_index_json = os.environ.get("DIST_INDEX_JSON", "")
asset_name = os.environ.get("ASSET_NAME", "")
os_type = os.environ.get("OS_TYPE", "")
arch = os.environ.get("ARCH", "")
nginx_version = os.environ.get("NGINX_VERSION", "")
repo_name = os.environ.get("REPO_NAME", "")
ref_name = os.environ.get("REF_NAME", "main")

if not all([asset_name, os_type, arch, nginx_version, repo_name]):
    print("")
    print("")
    print("")
    sys.exit(0)

module_pattern = re.compile(
    r"^ngx_http_markdown_filter_module-([0-9]+\.[0-9]+\.[0-9]+)-"
    + re.escape(os_type)
    + r"-"
    + re.escape(arch)
    + r"\.tar\.gz$"
)
dist_dir_pattern = re.compile(
    r"^([0-9]+\.[0-9]+\.[0-9]+)-"
    + re.escape(os_type)
    + r"-"
    + re.escape(arch)
    + r"$"
)

url = ""
digest = ""
versions = set()

if release_json:
    try:
        release_data = json.loads(release_json)
        assets = release_data.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            match = module_pattern.match(name)
            if match:
                versions.add(match.group(1))

            if name == asset_name:
                url = asset.get("browser_download_url", "")
                digest = asset.get("digest", "")
                if digest.startswith("sha256:"):
                    digest = digest.split(":", 1)[1]
                else:
                    digest = ""
    except json.JSONDecodeError:
        pass

if dist_index_json:
    try:
        dist_entries = json.loads(dist_index_json)
        if isinstance(dist_entries, list):
            for entry in dist_entries:
                name = entry.get("name", "")
                match = dist_dir_pattern.match(name)
                if match:
                    version = match.group(1)
                    versions.add(version)
                    if version == nginx_version and not url:
                        url = (
                            f"https://raw.githubusercontent.com/{repo_name}/{ref_name}"
                            f"/dist/{name}/{asset_name}"
                        )
    except json.JSONDecodeError:
        pass

sorted_versions = sorted(
    versions,
    key=lambda v: tuple(int(part) for part in v.split(".")),
)

print(url)
print(digest)
print(" ".join(sorted_versions))
PY
  )"

  if [ -n "$parse_result" ]; then
    printf '%s\n' "$parse_result"
  else
    printf '\n\n\n'
  fi
}

format_versions_by_series() {
  local versions="$1"

  if [ -z "$versions" ]; then
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    return 0
  fi

  python3 - "$versions" <<'PY'
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
versions = [v for v in raw.split() if v]
if not versions:
    raise SystemExit(0)

def key(v: str):
    return tuple(int(p) for p in v.split("."))

groups = {}
for version in sorted(set(versions), key=key):
    major_minor = ".".join(version.split(".")[:2])
    groups.setdefault(major_minor, []).append(version)

for series in sorted(groups.keys(), key=lambda s: tuple(int(p) for p in s.split("."))):
    print(f"  {series}.x: {' '.join(groups[series])}")
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

release_api_hint="latest"
source_ref="main"
if [ -n "$RELEASE_VERSION" ]; then
  release_api_hint="$RELEASE_VERSION"
  source_ref="$RELEASE_VERSION"
fi

RELEASE_JSON=""
if [ -z "$DOWNLOAD_URL_OVERRIDE" ]; then
  if ! RELEASE_JSON="$(fetch_release_json)"; then
    echo "[!] Warning: Failed to query GitHub release metadata (${release_api_hint}); falling back to repository dist index."
  fi
fi

DIST_INDEX_JSON=""
if [ -z "$DOWNLOAD_URL_OVERRIDE" ]; then
  if ! DIST_INDEX_JSON="$(fetch_dist_index_json "$source_ref")"; then
    echo "[!] Warning: Failed to query repository dist index (${source_ref})."
  fi
fi

# Determine target asset name
ASSET_NAME="ngx_http_markdown_filter_module-${NGINX_VERSION}-${OS_TYPE}-${ARCH}.tar.gz"

echo "----------------------------------------------------------------------------------"
echo "Looking for binary: $ASSET_NAME"

mapfile -t RELEASE_INFO < <(resolve_download_info "$ASSET_NAME" "$OS_TYPE" "$ARCH" "$NGINX_VERSION" "$source_ref" "$RELEASE_JSON" "$DIST_INDEX_JSON")
DOWNLOAD_URL="${RELEASE_INFO[0]:-}"
EXPECTED_SHA256="${RELEASE_INFO[1]:-}"
AVAILABLE_VERSIONS="${RELEASE_INFO[2]:-}"

if [ -z "$DOWNLOAD_URL" ]; then
  echo "Error: Could not find pre-built module for NGINX $NGINX_VERSION ($OS_TYPE $ARCH)."
  echo "NGINX dynamic modules require an exact NGINX version match."
  if [ -n "$AVAILABLE_VERSIONS" ]; then
    echo "Available pre-built versions for ${OS_TYPE}/${ARCH} (grouped by major.minor):"
    format_versions_by_series "$AVAILABLE_VERSIONS"
  else
    echo "No pre-built binaries are currently available for ${OS_TYPE}/${ARCH} in ref ${source_ref}."
  fi
  echo "Use the exact patch version shown above, or compile the module from source for your current NGINX version."
  echo "See https://github.com/${REPO}/tree/main/docs/guides/INSTALLATION.md"
  exit 1
fi

echo "[+] Downloading $DOWNLOAD_URL ..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! curl -fsSL -o "$TMP_DIR/$ASSET_NAME" "$DOWNLOAD_URL"; then
  echo "Error: Failed to download $ASSET_NAME from:"
  echo "       $DOWNLOAD_URL"
  echo "This usually means the exact NGINX version binary does not exist for your platform."
  if [ -n "$AVAILABLE_VERSIONS" ]; then
    echo "Available pre-built versions for ${OS_TYPE}/${ARCH} (grouped by major.minor):"
    format_versions_by_series "$AVAILABLE_VERSIONS"
  fi
  echo "See https://github.com/${REPO}/tree/main/docs/guides/INSTALLATION.md"
  exit 1
fi

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
