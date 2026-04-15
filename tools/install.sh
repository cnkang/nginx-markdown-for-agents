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
SOURCE_BUILD_URL="https://github.com/cnkang/nginx-markdown-for-agents/tree/main/docs/guides/INSTALLATION.md#6-secondary-manual-source-build"
SUPPORTED_ARCHITECTURES="x86_64, aarch64"

# --json flag: when set, output structured JSON to stdout at exit
JSON_OUTPUT=0
for arg in "$@"; do
  if [[ "$arg" == "--json" ]]; then
    JSON_OUTPUT=1
  fi
done

# Collected state for JSON output
_json_nginx_version=""
_json_os_type=""
_json_arch=""
_json_error_category=""
_json_error_message=""
_json_available_versions=""
_json_suggestions=()

# --- Structured error helpers ---

# emit_error <category> <message>
# emit_error prints an error message prefixed with "[ERROR] <category>: " to stderr and records `category` and `message` in the script's JSON state variables `_json_error_category` and `_json_error_message`.
emit_error() {
  local category="$1"
  local message="$2"
  echo "[ERROR] ${category}: ${message}" >&2
  _json_error_category="$category"
  _json_error_message="$message"
}

# emit_suggest <suggestion>
# emit_suggest appends a human-readable suggestion to the installer's JSON suggestions list and echoes it to stderr prefixed with "[SUGGEST]".
emit_suggest() {
  local suggestion="$1"
  echo "[SUGGEST] ${suggestion}" >&2
  _json_suggestions+=("$suggestion")
}

# json_output <success>
# json_output prints a structured JSON object to fd 3 when --json is enabled, containing success, nginx_version, os_type, arch, error, available_versions, and suggestions.
# json_output writes a structured JSON payload describing installer state to fd 3 when JSON_OUTPUT is enabled; it accepts one argument (`true`/`false`) to set the `success` field and uses `jq` when available, falling back to a manual JSON construction otherwise.
json_output() {
  local success="$1"
  if [[ "$JSON_OUTPUT" -ne 1 ]]; then
    return 0
  fi

  local json_success="$success"
  local json_nginx_version="${_json_nginx_version}"
  local json_os_type="${_json_os_type}"
  local json_arch="${_json_arch}"

  # Prefer jq for correct escaping and structure when available
  if command -v jq >/dev/null 2>&1; then
    local jq_error="null"
    if [[ -n "$_json_error_category" ]]; then
      jq_error="$(jq -cn --arg cat "$_json_error_category" --arg msg "$_json_error_message" \
        '{category: $cat, message: $msg}')"
    fi

    local jq_suggestions="[]"
    if [[ "${#_json_suggestions[@]}" -gt 0 ]]; then
      jq_suggestions="$(printf '%s\0' "${_json_suggestions[@]}" | jq -Rsc 'split("\u0000") | .[:-1]')"
    fi

    local jq_versions="[]"
    if [[ -n "$_json_available_versions" ]]; then
      jq_versions="$(printf '%s\n' "$_json_available_versions" | tr ' ' '\n' | jq -Rsc 'split("\n") | map(select(length > 0))')"
    fi

    jq -cn \
      --argjson success "$json_success" \
      --arg nginx_version "$json_nginx_version" \
      --arg os_type "$json_os_type" \
      --arg arch "$json_arch" \
      --argjson error "$jq_error" \
      --argjson available_versions "$jq_versions" \
      --argjson suggestions "$jq_suggestions" \
      '{success: $success, nginx_version: $nginx_version, os_type: $os_type, arch: $arch, error: $error, available_versions: $available_versions, suggestions: $suggestions}' >&3
    return 0
  fi

  # Fallback: manual JSON construction when jq is not installed

  # Build suggestions JSON array
  local suggestions_json="[]"
  if [[ "${#_json_suggestions[@]}" -gt 0 ]]; then
    suggestions_json="["
    local first=1
    for s in "${_json_suggestions[@]}"; do
      if [[ "$first" -eq 1 ]]; then
        first=0
      else
        suggestions_json+=","
      fi
      # Escape backslashes, double quotes, and control characters in suggestion text
      local escaped="${s//\\/\\\\}"
      escaped="${escaped//\"/\\\"}"
      escaped="${escaped//$'\n'/\\n}"
      escaped="${escaped//$'\r'/\\r}"
      escaped="${escaped//$'\t'/\\t}"
      suggestions_json+="\"${escaped}\""
    done
    suggestions_json+="]"
  fi

  # Build available_versions JSON array
  local versions_json="[]"
  if [[ -n "$_json_available_versions" ]]; then
    versions_json="["
    local first=1
    for v in $_json_available_versions; do
      if [[ "$first" -eq 1 ]]; then
        first=0
      else
        versions_json+=","
      fi
      versions_json+="\"${v}\""
    done
    versions_json+="]"
  fi

  # Build error object or null
  local error_json="null"
  if [[ -n "$_json_error_category" ]]; then
    local escaped_msg="${_json_error_message//\\/\\\\}"
    escaped_msg="${escaped_msg//\"/\\\"}"
    escaped_msg="${escaped_msg//$'\n'/\\n}"
    escaped_msg="${escaped_msg//$'\r'/\\r}"
    escaped_msg="${escaped_msg//$'\t'/\\t}"
    error_json="{\"category\":\"${_json_error_category}\",\"message\":\"${escaped_msg}\"}"
  fi

  printf '{"success":%s,"nginx_version":"%s","os_type":"%s","arch":"%s","error":%s,"available_versions":%s,"suggestions":%s}\n' \
    "$json_success" "$json_nginx_version" "$json_os_type" "$json_arch" \
    "$error_json" "$versions_json" "$suggestions_json" >&3
}

# die_with_error <category> <message> <suggestion1> [suggestion2] ...
# die_with_error emits a structured error and suggestions, writes a JSON payload when --json is enabled, then exits with status 1.
# die_with_error emits a structured error with a category and human-readable message, records any provided suggestions, emits the JSON failure payload, and exits with status 1.
die_with_error() {
  local category="$1"
  local message="$2"
  shift 2
  emit_error "$category" "$message"
  for suggestion in "$@"; do
    emit_suggest "$suggestion"
  done
  json_output false
  exit 1
}

# semver_lt compares two semantic version strings in MAJOR.MINOR.PATCH form and exits with status 0 when the first is less than the second.
# Missing minor or patch components are treated as 0 (for example, "1.2" is equivalent to "1.2.0").
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

  curl --proto '=https' --tlsv1.2 -fsSL -H 'Accept: application/vnd.github+json' "$release_api" 2>/dev/null
}

# fetch_dist_index_json fetches the GitHub API JSON listing for the repository's `dist` directory at the specified ref and writes it to stdout.
fetch_dist_index_json() {
  local ref_name="$1"
  local dist_api="https://api.github.com/repos/${REPO}/contents/dist?ref=${ref_name}"
  curl --proto '=https' --tlsv1.2 -fsSL -H 'Accept: application/vnd.github+json' "$dist_api" 2>/dev/null
}

# resolve_download_info determines the download URL, SHA-256 digest, and available prebuilt nginx versions for a requested asset and prints them as three newline-separated lines.
# It accepts: asset_name, os_type, arch, nginx_version, ref_name, and optional release_json and dist_index_json (raw JSON strings).
# Output: line 1 = download URL (empty if not found), line 2 = sha256 digest without any prefix (empty if not present), line 3 = space-separated sorted list of available versions.
# If DOWNLOAD_URL_OVERRIDE is set, that URL and DOWNLOAD_SHA256 are printed immediately.
# resolve_download_info discovers the download URL, SHA-256 digest (if present), and available prebuilt versions for the specified asset and prints them as three newline-separated lines (URL, digest, space-separated versions); if DOWNLOAD_URL_OVERRIDE is set it prints that URL, DOWNLOAD_SHA256, and an empty versions field, and if python3 is unavailable it emits a structured config error and returns non-zero so the caller can fail once from the parent shell.
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
    _json_error_category="config"
    _json_error_message="python3 is required by the installer but was not found."
    _json_suggestions=("Install python3: apt-get install python3 / apk add python3")
    return 1
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
    if ! cp "$file" "$backup_file"; then
      die_with_error "filesystem" \
        "Failed to create backup file: ${backup_file}" \
        "Check filesystem permissions and disk space."
    fi
  fi
}

# ensure_main_include_directive ensures the given nginx main configuration file contains the specified include directive, inserting it before the first top-level block (events,http,stream,mail) or appending it if no such block is found and creating a backup via backup_file_once.
ensure_main_include_directive() {
  local conf_file="$1"
  local include_directive="$2"

  if grep -Fq "$include_directive" "$conf_file"; then
    return 0
  fi

  backup_file_once "$conf_file"

  local tmp_file
  if ! tmp_file="$(mktemp)"; then
    die_with_error "filesystem" \
      "Failed to create a temporary file while updating ${conf_file}" \
      "Check filesystem permissions and available temporary disk space."
  fi

  if ! awk -v include_line="$include_directive" '
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
  ' "$conf_file" > "$tmp_file"; then
    rm -f "$tmp_file" || true
    die_with_error "filesystem" \
      "Failed to update nginx config contents for ${conf_file}" \
      "Check filesystem permissions and disk space."
  fi

  if ! cat "$tmp_file" > "$conf_file"; then
    rm -f "$tmp_file" || true
    die_with_error "filesystem" \
      "Failed to write updated nginx config: ${conf_file}" \
      "Check filesystem permissions and disk space."
  fi
  rm -f "$tmp_file" || true
}

# insert_markdown_filter_into_http_block inserts `markdown_filter on;` as the first line inside the top-level `http { ... }` block of the specified nginx configuration file.
# insert_markdown_filter_into_http_block inserts `markdown_filter on;` into the top-level `http` block of the given nginx configuration file, creates a backup via `backup_file_once` before overwriting, returns 0 on success and returns 1 (leaving the original file unchanged) if no `http` block is found or the insertion fails.
insert_markdown_filter_into_http_block() {
  local conf_file="$1"

  local tmp_file
  if ! tmp_file="$(mktemp)"; then
    die_with_error "filesystem" \
      "Failed to create a temporary file while updating ${conf_file}" \
      "Check filesystem permissions and available temporary disk space."
  fi

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
  if ! cat "$tmp_file" > "$conf_file"; then
    rm -f "$tmp_file" || true
    die_with_error "filesystem" \
      "Failed to write updated nginx config: ${conf_file}" \
      "Check filesystem permissions and disk space."
  fi
  rm -f "$tmp_file" || true
  return 0
}

# When --json is set, save original stdout to fd 3 for the JSON payload,
# then redirect stdout to stderr so all informational output goes to stderr.
# When --json is not set, fd 3 is just stdout (no-op).
if [[ "$JSON_OUTPUT" -eq 1 ]]; then
  exec 3>&1 1>&2
else
  exec 3>&1
fi

echo "=================================================================================="
echo " NGINX Markdown for Agents - Binary Module Installer"
echo "=================================================================================="

if [ "${SKIP_ROOT_CHECK:-0}" != "1" ] && [ "$EUID" -ne 0 ]; then
  die_with_error "config" "This script must be run as root." \
    "Re-run with: sudo bash install.sh" \
    "Or set SKIP_ROOT_CHECK=1 if running inside a container."
fi

# Detect Nginx runtime/build metadata
if ! command -v nginx > /dev/null 2>&1; then
  die_with_error "config" "nginx is not installed or not in PATH." \
    "Install NGINX first: https://nginx.org/en/linux_packages.html" \
    "Ensure the nginx binary is in your PATH."
fi

NGINX_V_OUTPUT="$(nginx -V 2>&1)"
NGINX_VERSION="$(printf '%s\n' "$NGINX_V_OUTPUT" | grep -oE 'nginx/[0-9]+\.[0-9]+\.[0-9]+' | cut -d/ -f2)"
if [ -z "$NGINX_VERSION" ]; then
  die_with_error "config" "Could not determine NGINX version from 'nginx -V' output." \
    "Verify NGINX is installed correctly: nginx -V" \
    "Ensure the nginx binary is the expected version."
fi
_json_nginx_version="$NGINX_VERSION"
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
  die_with_error "version_mismatch" \
    "NGINX ${NGINX_VERSION} is below the supported baseline (${MIN_SUPPORTED_NGINX_VERSION}+). Versions older than ${MIN_SUPPORTED_NGINX_VERSION} are unsupported; source builds are not guaranteed compatible." \
    "Upgrade NGINX to ${MIN_SUPPORTED_NGINX_VERSION} or newer." \
    "See supported versions: ${SOURCE_BUILD_URL}"
fi

# Detect OS type (glibc vs musl)
OS_TYPE="glibc"
if command -v ldd >/dev/null 2>&1 && ldd /bin/sh 2>&1 | grep -iq musl; then
  OS_TYPE="musl"
elif [ -f /etc/alpine-release ]; then
  OS_TYPE="musl"
fi
echo "[+] Detected OS family: $OS_TYPE"
_json_os_type="$OS_TYPE"

# Detect Architecture
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
  ARCH="aarch64"
elif [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
  ARCH="x86_64"
else
  die_with_error "arch_unsupported" \
    "Unsupported architecture: ${ARCH}. Supported architectures: ${SUPPORTED_ARCHITECTURES}." \
    "Use a supported architecture (${SUPPORTED_ARCHITECTURES})." \
    "Or build the module from source: ${SOURCE_BUILD_URL}"
fi
_json_arch="$ARCH"
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

if ! RELEASE_INFO_FILE="$(mktemp)"; then
  die_with_error "filesystem" \
    "Failed to create a temporary file for release metadata." \
    "Check filesystem permissions and available temporary disk space."
fi

if ! resolve_download_info "$ASSET_NAME" "$OS_TYPE" "$ARCH" "$NGINX_VERSION" "$source_ref" "$RELEASE_JSON" "$DIST_INDEX_JSON" > "$RELEASE_INFO_FILE"; then
  rm -f "$RELEASE_INFO_FILE" || true
  die_with_error "${_json_error_category:-config}" \
    "${_json_error_message:-Failed to resolve download metadata.}" \
    "${_json_suggestions[@]:-Install python3: apt-get install python3 / apk add python3}"
fi

mapfile -t RELEASE_INFO < "$RELEASE_INFO_FILE"
rm -f "$RELEASE_INFO_FILE" || true
DOWNLOAD_URL="${RELEASE_INFO[0]:-}"
EXPECTED_SHA256="${RELEASE_INFO[1]:-}"
AVAILABLE_VERSIONS="${RELEASE_INFO[2]:-}"

if [ -z "$DOWNLOAD_URL" ]; then
  _json_available_versions="$AVAILABLE_VERSIONS"
  _version_suggestions=("NGINX dynamic modules require an exact version match.")
  if [ -n "$AVAILABLE_VERSIONS" ]; then
    echo "Available pre-built versions for ${OS_TYPE}/${ARCH} (grouped by major.minor):" >&2
    format_versions_by_series "$AVAILABLE_VERSIONS" >&2
    _version_suggestions+=("Switch NGINX to one of the available versions listed above.")
  else
    echo "No pre-built binaries are currently available for ${OS_TYPE}/${ARCH} in ref ${source_ref}." >&2
  fi
  die_with_error "version_mismatch" \
    "No pre-built module found for NGINX ${NGINX_VERSION} (${OS_TYPE} ${ARCH}). Version is >= ${MIN_SUPPORTED_NGINX_VERSION} but not in the release matrix; a source build is supported." \
    "${_version_suggestions[@]}" \
    "Or build the module from source: ${SOURCE_BUILD_URL}"
fi

echo "[+] Downloading $DOWNLOAD_URL ..."
if ! TMP_DIR="$(mktemp -d)"; then
  die_with_error "filesystem" \
    "Failed to create a temporary working directory." \
    "Check filesystem permissions and available temporary disk space."
fi
trap 'rm -rf "$TMP_DIR"' EXIT

if ! curl -fsSL -o "$TMP_DIR/$ASSET_NAME" "$DOWNLOAD_URL"; then
  _json_available_versions="$AVAILABLE_VERSIONS"
  if [ -n "$AVAILABLE_VERSIONS" ]; then
    echo "Available pre-built versions for ${OS_TYPE}/${ARCH} (grouped by major.minor):" >&2
    format_versions_by_series "$AVAILABLE_VERSIONS" >&2
  fi
  die_with_error "network" \
    "Failed to download ${ASSET_NAME} from ${DOWNLOAD_URL}." \
    "Check your network connection and try again." \
    "Verify the URL is accessible: curl -fsSL -I '${DOWNLOAD_URL}'" \
    "See ${SOURCE_BUILD_URL}"
fi

if [ -n "$EXPECTED_SHA256" ]; then
  ACTUAL_SHA256="$(sha256_file "$TMP_DIR/$ASSET_NAME")"
  if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    die_with_error "checksum" \
      "Checksum verification failed for ${ASSET_NAME}. Expected: ${EXPECTED_SHA256}, Actual: ${ACTUAL_SHA256}." \
      "Re-download the file and try again." \
      "If the problem persists, the release artifact may be corrupted. Report at https://github.com/${REPO}/issues"
  fi
  echo "[+] SHA256 checksum verified"
else
  echo "[!] Release asset does not provide a SHA256 digest; skipping checksum verification"
fi

cd "$TMP_DIR"
if ! tar -xzf "$ASSET_NAME"; then
  die_with_error "extraction" \
    "Failed to extract ${ASSET_NAME}." \
    "The archive may be corrupted. Re-download and try again." \
    "Report at https://github.com/${REPO}/issues if the problem persists."
fi

MODULE_SO="ngx_http_markdown_filter_module.so"
if [ ! -f "$MODULE_SO" ]; then
  die_with_error "config" \
    "Extraction failed: ${MODULE_SO} not found in the downloaded archive." \
    "The archive may be corrupted. Re-download and try again." \
    "Report at https://github.com/${REPO}/issues if the problem persists."
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
if ! mkdir -p "$MODULES_DIR"; then
  die_with_error "filesystem" \
    "Failed to create modules directory: ${MODULES_DIR}" \
    "Check filesystem permissions and disk space."
fi

echo "[+] Installing module to $MODULES_DIR/"
if ! cp "$MODULE_SO" "$MODULES_DIR/"; then
  die_with_error "filesystem" \
    "Failed to copy ${MODULE_SO} to ${MODULES_DIR}/" \
    "Check filesystem permissions and disk space."
fi
if ! chmod 644 "$MODULES_DIR/$MODULE_SO"; then
  die_with_error "filesystem" \
    "Failed to set permissions on ${MODULES_DIR}/${MODULE_SO}" \
    "Check filesystem permissions."
fi

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
    if ! mkdir -p "$MODULE_INCLUDE_DIR"; then
      die_with_error "filesystem" \
        "Failed to create module include directory: ${MODULE_INCLUDE_DIR}" \
        "Check filesystem permissions and disk space."
    fi
    MODULE_CONF_SNIPPET="${MODULE_INCLUDE_DIR%/}/50-ngx-http-markdown-filter-module.conf"
    if ! cat > "$MODULE_CONF_SNIPPET" <<EOF
# Generated by nginx-markdown-for-agents install.sh
load_module ${MODULE_LOAD_PATH};
EOF
    then
      die_with_error "filesystem" \
        "Failed to write module loader snippet: ${MODULE_CONF_SNIPPET}" \
        "Check filesystem permissions and disk space."
    fi
    if ! chmod 644 "$MODULE_CONF_SNIPPET"; then
      die_with_error "filesystem" \
        "Failed to set permissions on ${MODULE_CONF_SNIPPET}" \
        "Check filesystem permissions."
    fi
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
      if ! mkdir -p "$HTTP_INCLUDE_DIR"; then
        die_with_error "filesystem" \
          "Failed to create markdown include directory: ${HTTP_INCLUDE_DIR}" \
          "Check filesystem permissions and disk space."
      fi
      MARKDOWN_CONF_SNIPPET="${HTTP_INCLUDE_DIR%/}/90-markdown-filter-enable.conf"
      if ! cat > "$MARKDOWN_CONF_SNIPPET" <<'EOF'
# Generated by nginx-markdown-for-agents install.sh
markdown_filter on;

# Optional tuning examples:
# markdown_max_size 5m;
# markdown_on_error pass;
EOF
      then
        die_with_error "filesystem" \
          "Failed to write markdown enable snippet: ${MARKDOWN_CONF_SNIPPET}" \
          "Check filesystem permissions and disk space."
      fi
      if ! chmod 644 "$MARKDOWN_CONF_SNIPPET"; then
        die_with_error "filesystem" \
          "Failed to set permissions on ${MARKDOWN_CONF_SNIPPET}" \
          "Check filesystem permissions."
      fi
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
if ! NGINX_TEST_LOG="$(mktemp)"; then
  die_with_error "filesystem" \
    "Failed to create a temporary file for nginx -t output." \
    "Check filesystem permissions and available temporary disk space."
fi
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
rm -f "$NGINX_TEST_LOG" || true

echo ""
echo "You can continue fine-tuning later (recommended):"
echo "- Scope rollout with server/location-level markdown_filter on/off"
echo "- Adjust markdown_max_size / markdown_on_error by workload"
echo "=================================================================================="

# Emit JSON output if --json was requested
if [ "$NGINX_TEST_RESULT" = "failed" ]; then
  json_output false
else
  json_output true
fi
