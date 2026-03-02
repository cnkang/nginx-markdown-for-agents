#!/bin/bash
set -euo pipefail

# NGINX Markdown for Agents Install Script
# Usage: curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
# OR (if using specific release version):
# VERSION=v0.1.0 curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
# OR (in Docker, skip root check):
# SKIP_ROOT_CHECK=1 bash /path/to/install.sh

REPO="cnkang/nginx-markdown-for-agents"
RELEASE_VERSION="${VERSION:-}"
DOWNLOAD_URL_OVERRIDE="${DOWNLOAD_URL_OVERRIDE:-}"
DOWNLOAD_SHA256="${DOWNLOAD_SHA256:-}"
MIN_SUPPORTED_NGINX_VERSION="1.24.0"

semver_lt() {
  local lhs="$1"
  local rhs="$2"
  local l1 l2 l3 r1 r2 r3
  local IFS='.'

  read -r l1 l2 l3 <<<"$lhs"
  read -r r1 r2 r3 <<<"$rhs"

  l1="${l1:-0}"; l2="${l2:-0}"; l3="${l3:-0}"
  r1="${r1:-0}"; r2="${r2:-0}"; r3="${r3:-0}"

  if ((10#$l1 < 10#$r1)); then
    return 0
  elif ((10#$l1 > 10#$r1)); then
    return 1
  fi

  if ((10#$l2 < 10#$r2)); then
    return 0
  elif ((10#$l2 > 10#$r2)); then
    return 1
  fi

  if ((10#$l3 < 10#$r3)); then
    return 0
  fi

  return 1
}

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

extract_configure_arg() {
  local key="$1"
  local nginx_v_output="$2"
  printf '%s\n' "$nginx_v_output" | sed -n "s/.*--${key}=\\([^ ]*\\).*/\\1/p" | head -n1
}

resolve_path_with_prefix() {
  local candidate="$1"
  local prefix="$2"

  if [ -z "$candidate" ]; then
    printf '\n'
    return 0
  fi

  if [[ "$candidate" = /* ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  if [ -n "$prefix" ]; then
    printf '%s/%s\n' "${prefix%/}" "$candidate"
  else
    printf '%s\n' "$candidate"
  fi
}

resolve_include_dir() {
  local include_pattern="$1"
  local conf_dir="$2"
  local include_dir

  include_dir="$(dirname "$include_pattern")"
  if [ "$include_dir" = "." ]; then
    include_dir="$conf_dir"
  fi

  if [[ "$include_dir" != /* ]]; then
    include_dir="${conf_dir%/}/${include_dir}"
  fi

  printf '%s\n' "$include_dir"
}

backup_file_once() {
  local file="$1"
  local backup_file="${file}.bak.nginx-markdown-for-agents"
  if [ ! -f "$backup_file" ]; then
    cp "$file" "$backup_file"
  fi
}

ensure_main_include_directive() {
  local conf_file="$1"
  local include_directive="$2"

  if grep -Fq "$include_directive" "$conf_file"; then
    return 0
  fi

  backup_file_once "$conf_file"

  local tmp_file
  tmp_file="$(mktemp)"

  awk -v include_line="$include_directive" '
    BEGIN { inserted = 0 }
    /^[[:space:]]*(events|http|stream|mail)[[:space:]]*\{/ && inserted == 0 {
      print include_line
      inserted = 1
    }
    { print }
    END {
      if (inserted == 0) {
        print include_line
      }
    }
  ' "$conf_file" > "$tmp_file"

  cat "$tmp_file" > "$conf_file"
  rm -f "$tmp_file"
}

insert_markdown_filter_into_http_block() {
  local conf_file="$1"

  local tmp_file
  tmp_file="$(mktemp)"

  if ! awk '
    BEGIN { inserted = 0 }
    /^[[:space:]]*http[[:space:]]*\{/ && inserted == 0 {
      print
      print "    markdown_filter on;"
      inserted = 1
      next
    }
    { print }
    END {
      if (inserted == 0) {
        exit 1
      }
    }
  ' "$conf_file" > "$tmp_file"; then
    rm -f "$tmp_file"
    return 1
  fi

  backup_file_once "$conf_file"
  cat "$tmp_file" > "$conf_file"
  rm -f "$tmp_file"
  return 0
}

echo "=================================================================================="
echo " NGINX Markdown for Agents - Binary Module Installer"
echo "=================================================================================="

if [ "${SKIP_ROOT_CHECK:-0}" != "1" ] && [ "$EUID" -ne 0 ]; then
  echo "Please run as root (or with sudo)."
  exit 1
fi

# Detect Nginx runtime/build metadata
if ! command -v nginx > /dev/null 2>&1; then
  echo "Error: nginx is not installed or not in PATH."
  echo "Please install NGINX first before running this script."
  exit 1
fi

NGINX_V_OUTPUT="$(nginx -V 2>&1)"
NGINX_VERSION="$(printf '%s\n' "$NGINX_V_OUTPUT" | grep -oE 'nginx/[0-9]+\.[0-9]+\.[0-9]+' | cut -d/ -f2)"
if [ -z "$NGINX_VERSION" ]; then
  echo "Error: Could not determine NGINX version."
  exit 1
fi
echo "[+] Detected NGINX version: $NGINX_VERSION"

NGINX_PREFIX_RAW="$(extract_configure_arg "prefix" "$NGINX_V_OUTPUT")"
NGINX_MODULES_PATH_RAW="$(extract_configure_arg "modules-path" "$NGINX_V_OUTPUT")"
NGINX_CONF_PATH_RAW="$(extract_configure_arg "conf-path" "$NGINX_V_OUTPUT")"

NGINX_PREFIX="$(resolve_path_with_prefix "$NGINX_PREFIX_RAW" "")"
NGINX_MODULES_PATH="$(resolve_path_with_prefix "$NGINX_MODULES_PATH_RAW" "$NGINX_PREFIX")"
NGINX_CONF_PATH="$(resolve_path_with_prefix "$NGINX_CONF_PATH_RAW" "$NGINX_PREFIX")"

if [ -z "$NGINX_CONF_PATH" ]; then
  NGINX_CONF_PATH="/etc/nginx/nginx.conf"
fi
NGINX_CONF_DIR="$(dirname "$NGINX_CONF_PATH")"

echo "[+] NGINX conf path: $NGINX_CONF_PATH"
if [ -n "$NGINX_MODULES_PATH_RAW" ]; then
  echo "[+] NGINX modules path from build: $NGINX_MODULES_PATH_RAW"
fi

if semver_lt "$NGINX_VERSION" "$MIN_SUPPORTED_NGINX_VERSION"; then
  echo "Error: NGINX $NGINX_VERSION is not supported."
  echo "Minimum supported NGINX version is $MIN_SUPPORTED_NGINX_VERSION."
  echo "Support baseline was raised to simplify compatibility and release coverage."
  echo "Please upgrade NGINX, or build and maintain your own custom module for older versions."
  exit 1
fi

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
# Prefer the build-time path from nginx -V when available.
MODULES_DIR=""
if [ -n "$NGINX_MODULES_PATH" ]; then
  MODULES_DIR="$NGINX_MODULES_PATH"
elif [ -d "/etc/nginx/modules" ]; then
  MODULES_DIR="/etc/nginx/modules"
elif [ -d "/usr/lib/nginx/modules" ]; then
  MODULES_DIR="/usr/lib/nginx/modules"
elif [ -d "/usr/share/nginx/modules" ]; then
  MODULES_DIR="/usr/share/nginx/modules"
elif [ -d "/usr/local/nginx/modules" ]; then
  MODULES_DIR="/usr/local/nginx/modules"
else
  MODULES_DIR="/etc/nginx/modules"
fi
mkdir -p "$MODULES_DIR"

echo "[+] Installing module to $MODULES_DIR/"
cp "$MODULE_SO" "$MODULES_DIR/"
chmod 644 "$MODULES_DIR/$MODULE_SO"

MODULE_LOAD_PATH="${MODULES_DIR%/}/${MODULE_SO}"
if [ -n "$NGINX_MODULES_PATH_RAW" ]; then
  MODULE_LOAD_PATH="${NGINX_MODULES_PATH_RAW%/}/${MODULE_SO}"
fi

MODULE_CONF_SNIPPET=""
MARKDOWN_CONF_SNIPPET=""
MODULE_ALREADY_CONFIGURED=0
MARKDOWN_ALREADY_CONFIGURED=0
MARKDOWN_INSERTED_IN_MAIN=0
MANUAL_ACTIONS=()

if [ -f "$NGINX_CONF_PATH" ]; then
  if [ -d "$NGINX_CONF_DIR" ] && grep -R --include='*.conf' -Eq "^[[:space:]]*load_module[[:space:]]+.*${MODULE_SO}[[:space:]]*;" "$NGINX_CONF_DIR"; then
    MODULE_ALREADY_CONFIGURED=1
  fi

  MODULE_INCLUDE_PATTERN="$(grep -E '^[[:space:]]*include[[:space:]]+[^;]*(modules|modules-enabled)[^;]*\.conf[[:space:]]*;' "$NGINX_CONF_PATH" | sed -E 's/^[[:space:]]*include[[:space:]]+([^;]+);/\1/' | head -n1 || true)"
  if [ -z "$MODULE_INCLUDE_PATTERN" ]; then
    MODULE_INCLUDE_PATTERN="${NGINX_CONF_DIR%/}/modules-enabled/*.conf"
    ensure_main_include_directive "$NGINX_CONF_PATH" "include ${MODULE_INCLUDE_PATTERN};"
    echo "[+] Added main-context include: include ${MODULE_INCLUDE_PATTERN};"
  fi

  if [ "$MODULE_ALREADY_CONFIGURED" -eq 0 ]; then
    MODULE_INCLUDE_DIR="$(resolve_include_dir "$MODULE_INCLUDE_PATTERN" "$NGINX_CONF_DIR")"
    mkdir -p "$MODULE_INCLUDE_DIR"
    MODULE_CONF_SNIPPET="${MODULE_INCLUDE_DIR%/}/50-ngx-http-markdown-filter-module.conf"
    cat > "$MODULE_CONF_SNIPPET" <<EOF
# Generated by nginx-markdown-for-agents install.sh
load_module ${MODULE_LOAD_PATH};
EOF
    chmod 644 "$MODULE_CONF_SNIPPET"
    echo "[+] Wrote module loader snippet: $MODULE_CONF_SNIPPET"
  else
    echo "[+] Existing load_module directive found for ${MODULE_SO}, skipping snippet creation"
  fi

  if [ -d "$NGINX_CONF_DIR" ] && grep -R --include='*.conf' -Eq "^[[:space:]]*markdown_filter[[:space:]]+on[[:space:]]*;" "$NGINX_CONF_DIR"; then
    MARKDOWN_ALREADY_CONFIGURED=1
  fi

  if [ "$MARKDOWN_ALREADY_CONFIGURED" -eq 0 ]; then
    HTTP_INCLUDE_PATTERN="$(grep -E '^[[:space:]]*include[[:space:]]+[^;]*conf\.d/[^;]*\.conf[[:space:]]*;' "$NGINX_CONF_PATH" | sed -E 's/^[[:space:]]*include[[:space:]]+([^;]+);/\1/' | head -n1 || true)"
    if [ -n "$HTTP_INCLUDE_PATTERN" ]; then
      HTTP_INCLUDE_DIR="$(resolve_include_dir "$HTTP_INCLUDE_PATTERN" "$NGINX_CONF_DIR")"
      mkdir -p "$HTTP_INCLUDE_DIR"
      MARKDOWN_CONF_SNIPPET="${HTTP_INCLUDE_DIR%/}/90-markdown-filter-enable.conf"
      cat > "$MARKDOWN_CONF_SNIPPET" <<'EOF'
# Generated by nginx-markdown-for-agents install.sh
markdown_filter on;

# Optional tuning examples:
# markdown_max_size 5m;
# markdown_on_error pass;
EOF
      chmod 644 "$MARKDOWN_CONF_SNIPPET"
      echo "[+] Wrote markdown enable snippet: $MARKDOWN_CONF_SNIPPET"
    else
      if insert_markdown_filter_into_http_block "$NGINX_CONF_PATH"; then
        MARKDOWN_INSERTED_IN_MAIN=1
        echo "[+] Injected 'markdown_filter on;' into http block of $NGINX_CONF_PATH"
      else
        MANUAL_ACTIONS+=("Add 'markdown_filter on;' into your http/server/location block.")
      fi
    fi
  else
    echo "[+] Existing 'markdown_filter on;' directive found, skipping auto-enable"
  fi
else
  MANUAL_ACTIONS+=("Could not find nginx.conf at $NGINX_CONF_PATH. Add a load_module line and enable markdown_filter manually.")
fi

NGINX_TEST_RESULT="not-run"
NGINX_TEST_LOG="$(mktemp)"
if nginx -t >"$NGINX_TEST_LOG" 2>&1; then
  NGINX_TEST_RESULT="ok"
else
  NGINX_TEST_RESULT="failed"
fi

echo "=================================================================================="
echo " Installation Complete!"
echo "=================================================================================="
echo "Auto-generated configuration:"
if [ -n "$MODULE_CONF_SNIPPET" ]; then
  echo "1. Module loader snippet: $MODULE_CONF_SNIPPET"
  echo "   -> load_module ${MODULE_LOAD_PATH};"
elif [ "$MODULE_ALREADY_CONFIGURED" -eq 1 ]; then
  echo "1. Module loader already exists in current nginx config."
else
  echo "1. Module loader snippet was not created automatically."
fi

if [ -n "$MARKDOWN_CONF_SNIPPET" ]; then
  echo "2. Markdown enable snippet: $MARKDOWN_CONF_SNIPPET"
  echo "   -> markdown_filter on;"
elif [ "$MARKDOWN_ALREADY_CONFIGURED" -eq 1 ]; then
  echo "2. markdown_filter is already enabled in current nginx config."
elif [ "$MARKDOWN_INSERTED_IN_MAIN" -eq 1 ]; then
  echo "2. Added 'markdown_filter on;' directly into $NGINX_CONF_PATH (http block)."
else
  echo "2. markdown_filter was not auto-enabled."
fi

if [ "${#MANUAL_ACTIONS[@]}" -gt 0 ]; then
  echo ""
  echo "Manual actions required:"
  for action in "${MANUAL_ACTIONS[@]}"; do
    echo " - $action"
  done
fi

echo ""
if [ "$NGINX_TEST_RESULT" = "ok" ]; then
  echo "[+] nginx -t passed"
  echo "Run: nginx -s reload"
else
  echo "[!] nginx -t failed. Review errors below:"
  sed -n '1,20p' "$NGINX_TEST_LOG"
  echo "Fix config and run: nginx -t && nginx -s reload"
fi
rm -f "$NGINX_TEST_LOG"

echo ""
echo "You can continue fine-tuning later (recommended):"
echo "- Scope rollout with server/location-level markdown_filter on/off"
echo "- Adjust markdown_max_size / markdown_on_error by workload"
echo "=================================================================================="
