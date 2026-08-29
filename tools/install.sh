#!/bin/bash
set -euo pipefail

# NGINX Markdown for Agents Install Script
# Usage:
#   VERSION=v0.9.2
#   curl -fsSLo /tmp/nginx-markdown-installer.sh \
#     https://github.com/cnkang/nginx-markdown-for-agents/releases/download/${VERSION}/nginx-markdown-for-agents-installer-${VERSION}.sh
#   sudo env VERSION="${VERSION}" bash /tmp/nginx-markdown-installer.sh
# OR (if using specific release version):
#   VERSION=v0.9.2 sudo -E bash /tmp/nginx-markdown-installer.sh
# OR (in Docker, skip root check):
# SKIP_ROOT_CHECK=1 bash /path/to/install.sh
# OR (auto-disable stale load_module snippets on ABI mismatch):
#   AUTO_DISABLE_STALE_MODULE=1 sudo -E bash /tmp/nginx-markdown-install.sh

REPO="cnkang/nginx-markdown-for-agents"
RELEASE_VERSION="${VERSION:-}"
DOWNLOAD_URL_OVERRIDE="${DOWNLOAD_URL_OVERRIDE:-}"
DOWNLOAD_SHA256="${DOWNLOAD_SHA256:-}"
AUTO_DISABLE_STALE_MODULE="${AUTO_DISABLE_STALE_MODULE:-0}"
# NGINX_BIN overrides PATH discovery with an operator-chosen absolute path to
# the nginx executable; the installer validates it before any invocation.
NGINX_BIN="${NGINX_BIN:-}"
# TRUSTED_FINGERPRINT pins the embedded release signing key.  The default
# matches the checked-in release signing key in
# packaging/nginx-markdown-for-agents-release.asc (signing subkey
# 15C792438EAA762B421E60D21E8D41E7D19A8A75).  Operators may override it with
# an independently authenticated fingerprint for a replacement key embedded
# in their locally maintained installer.
TRUSTED_FINGERPRINT="${TRUSTED_FINGERPRINT:-15C792438EAA762B421E60D21E8D41E7D19A8A75}"
MIN_SUPPORTED_NGINX_VERSION="1.24.0"
SOURCE_BUILD_URL="https://github.com/cnkang/nginx-markdown-for-agents/tree/main/docs/guides/INSTALLATION.md#6-secondary-manual-source-build"
SUPPORTED_ARCHITECTURES="x86_64, aarch64"
readonly SED_STRIP_LEADING_ZEROS='s/^0*//'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/sbin:/opt/homebrew/bin"
readonly TRUSTED_COMMAND_ROOTS=(
  /usr/local/sbin
  /usr/local/bin
  /usr/sbin
  /usr/bin
  /sbin
  /bin
  /opt/homebrew/sbin
  /opt/homebrew/bin
  /opt/homebrew/opt
  /opt/homebrew/Cellar
  /usr/local/opt
  /usr/local/Cellar
)
readonly CATEGORY_FILESYSTEM="filesystem"
readonly MSG_CHECK_PERMS_DISK="check filesystem permissions and disk space"
readonly MSG_CHECK_PERMS_TMP_DISK="check temporary directory permissions and disk space"
readonly CONF_GLOB='*.conf'
readonly SEPARATOR_LINE='=================================================================================='
readonly CATEGORY_CONFIG="config"

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

# Every external executable used by the installer is resolved once from a
# fixed system PATH and then invoked by its absolute path.  This prevents a
# hostile caller-controlled PATH from redirecting a root installation to a
# wrapper or symlink in a writable directory.
AWK_BIN=""
BASENAME_BIN=""
CAT_BIN=""
CHMOD_BIN=""
CP_BIN=""
CURL_BIN=""
CUT_BIN=""
DIRNAME_BIN=""
FIND_BIN=""
GREP_BIN=""
HEAD_BIN=""
LDD_BIN=""
MKDIR_BIN=""
MKTEMP_BIN=""
MV_BIN=""
PYTHON3_BIN=""
RM_BIN=""
SED_BIN=""
SH_BIN=""
STAT_BIN=""
TAR_BIN=""
TR_BIN=""
UNAME_BIN=""
XARGS_BIN=""
READLINK_BIN=""
GPG_BIN=""
JQ_BIN=""
FILE_BIN=""
SHA256SUM_BIN=""
SHASUM_BIN=""
OPENSSL_BIN=""

# --- Structured error helpers ---

# emit_error <category> <message>
# _json_escape_string <dest_var> <value>
# Writes a JSON-safe encoding of value into dest_var: backslash, double
# quote, newline, carriage return, tab, and every other control character
# in U+0000-U+001F (encoded as \u00XX). All JSON string interpolation in
# this script must go through this one escaping rule so every field stays
# byte-for-byte consistent.
_json_escape_string() {
  local __dest_var="$1"
  local __value="${2//\\/\\\\}"
  __value="${__value//\"/\\\"}"
  __value="${__value//$'\n'/\\n}"
  __value="${__value//$'\r'/\\r}"
  __value="${__value//$'\t'/\\t}"
  # Remaining C0 control characters (U+0000-U+001F minus the four above):
  # encode each as a JSON \u00XX escape so the envelope stays valid JSON.
  local __i __ch __hex
  # Native arithmetic loop: avoids a dependency on external `seq`, which is
  # not cached in the trusted-executable set and may be absent from the
  # hardened PATH used after cache_trusted_executables.
  for ((__i = 0; __i < 32; __i++)); do
    case "$__i" in
      9|10|13) continue ;;  # already handled above
    esac
    __ch=$(printf "\\$(printf '%03o' "$__i")")
    __hex=$(printf '%02x' "$__i")
    __value="${__value//$__ch/\\u00$__hex}"
  done
  printf -v "$__dest_var" '%s' "$__value"
  return 0
}

# emit_error prints an error message prefixed with "[ERROR] <category>: " to stderr and records `category` and `message` in the script's JSON state variables `_json_error_category` and `_json_error_message`.
emit_error() {
  local category="$1"
  local message="$2"
  echo "[ERROR] ${category}: ${message}" >&2
  _json_error_category="$category"
  _json_error_message="$message"
  return 0
}

# emit_suggest <suggestion>
# emit_suggest appends a human-readable suggestion to the installer's JSON suggestions list and echoes it to stderr prefixed with "[SUGGEST]".
emit_suggest() {
  local suggestion="$1"
  echo "[SUGGEST] ${suggestion}" >&2
  _json_suggestions+=("$suggestion")
  return 0
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
  local escaped_nginx_version=""
  local escaped_os_type=""
  local escaped_arch=""

  _json_escape_string escaped_nginx_version "$json_nginx_version"
  _json_escape_string escaped_os_type "$json_os_type"
  _json_escape_string escaped_arch "$json_arch"

  # Prefer jq for correct escaping and structure when available
  if [[ -n "$JQ_BIN" ]]; then
    local jq_error="null"
    if [[ -n "$_json_error_category" ]]; then
      jq_error="$("$JQ_BIN" -cn --arg cat "$_json_error_category" --arg msg "$_json_error_message" \
        '{category: $cat, message: $msg}')"
    fi

    local jq_suggestions="[]"
    if [[ "${#_json_suggestions[@]}" -gt 0 ]]; then
      jq_suggestions="$(printf '%s\0' "${_json_suggestions[@]}" | "$JQ_BIN" -Rsc 'split("\u0000") | .[:-1]')"
    fi

    local jq_versions="[]"
    if [[ -n "$_json_available_versions" ]]; then
      jq_versions="$(printf '%s\n' "$_json_available_versions" | "$TR_BIN" ' ' '\n' | "$JQ_BIN" -Rsc 'split("\n") | map(select(length > 0))')"
    fi

    "$JQ_BIN" -cn \
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
    local escaped=""
    for s in "${_json_suggestions[@]}"; do
      if [[ "$first" -eq 1 ]]; then
        first=0
      else
        suggestions_json+=","
      fi
      _json_escape_string escaped "$s"
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
      local escaped_version=""
      _json_escape_string escaped_version "$v"
      versions_json+="\"${escaped_version}\""
    done
    versions_json+="]"
  fi

  # Build error object or null
  local error_json="null"
  if [[ -n "$_json_error_category" ]]; then
    # The category gets exactly the same escaping as the message so a
    # category carrying backslashes or quotes cannot break the envelope.
    local escaped_cat=""
    local escaped_msg=""
    _json_escape_string escaped_cat "${_json_error_category}"
    _json_escape_string escaped_msg "${_json_error_message}"
    error_json="{\"category\":\"${escaped_cat}\",\"message\":\"${escaped_msg}\"}"
  fi

  printf '{"success":%s,"nginx_version":"%s","os_type":"%s","arch":"%s","error":%s,"available_versions":%s,"suggestions":%s}\n' \
    "$json_success" "$escaped_nginx_version" "$escaped_os_type" "$escaped_arch" \
    "$error_json" "$versions_json" "$suggestions_json" >&3
  return 0
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
  exit 1  # Intentional exit; no return needed as this is a terminal function
}

# semver_lt compares two semantic version strings in MAJOR.MINOR.PATCH form and exits with status 0 when the first is less than the second.
# Missing minor or patch components are treated as 0 (for example, "1.2" is equivalent to "1.2.0").
semver_lt() {
  local lhs="$1"
  local rhs="$2"
  local l1 l2 l3 r1 r2 r3

  IFS='.' read -r l1 l2 l3 <<<"$lhs"
  IFS='.' read -r r1 r2 r3 <<<"$rhs"

  l1="${l1:-0}"; l2="${l2:-0}"; l3="${l3:-0}"
  r1="${r1:-0}"; r2="${r2:-0}"; r3="${r3:-0}"

  # Strip leading zeros for arithmetic comparison to avoid
  # octal interpretation; sed removes leading zeros then falls
  # back to 0 for empty strings.  This avoids the 10# prefix
  # which SonarCloud's shell parser cannot handle.
  l1=$(printf '%s\n' "$l1" | "$SED_BIN" "$SED_STRIP_LEADING_ZEROS"); l1=${l1:-0}
  l2=$(printf '%s\n' "$l2" | "$SED_BIN" "$SED_STRIP_LEADING_ZEROS"); l2=${l2:-0}
  l3=$(printf '%s\n' "$l3" | "$SED_BIN" "$SED_STRIP_LEADING_ZEROS"); l3=${l3:-0}
  r1=$(printf '%s\n' "$r1" | "$SED_BIN" "$SED_STRIP_LEADING_ZEROS"); r1=${r1:-0}
  r2=$(printf '%s\n' "$r2" | "$SED_BIN" "$SED_STRIP_LEADING_ZEROS"); r2=${r2:-0}
  r3=$(printf '%s\n' "$r3" | "$SED_BIN" "$SED_STRIP_LEADING_ZEROS"); r3=${r3:-0}

  if ((l1 < r1)); then
    return 0
  elif ((l1 > r1)); then
    return 1
  fi

  if ((l2 < r2)); then
    return 0
  elif ((l2 > r2)); then
    return 1
  fi

  if ((l3 < r3)); then
    return 0
  fi

  return 1
}

# Compute the SHA-256 hash of a file, trying sha256sum, shasum, and openssl in order.
#
# Arguments:
#   $1 - path to the file to hash
#
# Outputs:
#   Writes the hex SHA-256 digest (without filename or prefix) to stdout
#
# Returns:
#   0 on success, 1 if no supported hashing tool is available
sha256_file() {
  local file="$1"

  if [[ -n "$SHA256SUM_BIN" ]]; then
    "$SHA256SUM_BIN" "$file" | "$AWK_BIN" '{print $1}'
    return 0
  fi

  if [[ -n "$SHASUM_BIN" ]]; then
    "$SHASUM_BIN" -a 256 "$file" | "$AWK_BIN" '{print $1}'
    return 0
  fi

  if [[ -n "$OPENSSL_BIN" ]]; then
    "$OPENSSL_BIN" dgst -sha256 "$file" | "$AWK_BIN" '{print $2}'
    return 0
  fi

  return 1
}

# Trusted system directories in which the nginx executable may legitimately
# live.  Both the literal PATH entry and its resolved target must remain under
# these roots so a symlink cannot escape the allowlist.
readonly TRUSTED_NGINX_ROOTS=(
  /usr/sbin
  /usr/bin
  /sbin
  /bin
  /usr/local/sbin
  /usr/local/bin
  /usr/local/nginx/sbin
  /opt/nginx/sbin
  /usr/local/opt/nginx/sbin
  /opt/homebrew/bin
  /opt/homebrew/sbin
  /opt/homebrew/opt/nginx/sbin
  /opt/homebrew/Cellar
  /usr/local/Cellar
  /usr/share/nginx/sbin
  /usr/lib/nginx
)
readonly TRUSTED_NGINX_DESTINATION_ROOTS=(
  /etc/nginx
  /usr/lib/nginx
  /usr/share/nginx
  /usr/local/nginx
  /usr/local/opt/nginx
  /usr/local/etc/nginx
  /opt/nginx
  /opt/homebrew/opt/nginx
  /opt/homebrew/etc/nginx
  /opt/homebrew/Cellar/nginx
)

# canonicalize_path resolves symlinks and prints the canonical absolute path.
#
# Arguments:
#   $1 - path to canonicalize (relative paths are resolved from $PWD)
#
# Outputs:
#   Writes the canonical absolute path to stdout
#
# Returns:
#   0 on success; 1 if the input is empty
canonicalize_path() {
  local path="$1"
  local dir=""
  local file=""
  local target=""
  local i=0
  local basename_bin="${BASENAME_BIN:-/usr/bin/basename}"
  local dirname_bin="${DIRNAME_BIN:-/usr/bin/dirname}"
  local readlink_bin="${READLINK_BIN:-}"

  if [[ -z "$path" ]] || [[ -z "$readlink_bin" ]]; then
    return 1
  fi
  if [[ "$path" != /* ]]; then
    path="$(pwd)/$path"
  fi

  if ! dir="$(cd "$("$dirname_bin" "$path")" 2>/dev/null && pwd -P)"; then
    return 1
  fi
  if ! file="$("$basename_bin" "$path")"; then
    return 1
  fi

  while [[ -L "$dir/$file" ]]; do
    if [[ $i -ge 40 ]]; then
      return 1
    fi
    if ! target="$("$readlink_bin" "$dir/$file" 2>/dev/null)"; then
      return 1
    fi
    [[ -n "$target" ]] || return 1
    if [[ "$target" != /* ]]; then
      target="$dir/$target"
    fi
    if ! dir="$(cd "$("$dirname_bin" "$target")" 2>/dev/null && pwd -P)"; then
      return 1
    fi
    if ! file="$("$basename_bin" "$target")"; then
      return 1
    fi
    i=$((i + 1))
  done

  printf '%s/%s\n' "$dir" "$file"
  return 0
}

# is_trusted_nginx_path returns 0 when the given path lives directly under one
# of the trusted system executable roots (the literal candidate location, so a
# user-writable symlink pointing into a trusted root stays rejected).
#
# Arguments:
#   $1 - path to check
#
# Returns:
#   0 when trusted; 1 otherwise
is_trusted_nginx_path() {
  local path="$1"
  local root=""
  for root in "${TRUSTED_NGINX_ROOTS[@]}"; do
    case "$path" in
      "$root"|"$root"/*)
        return 0
        ;;
      *)
        ;;
    esac
  done
  return 1
}

# bootstrap_system_tool resolves the small set of metadata tools needed before
# the normal executable cache is initialized. Alpine keeps coreutils applets
# under /bin, while Debian and macOS commonly expose them under /usr/bin.
bootstrap_system_tool() {
  local name="$1"
  local candidate=""
  local -a candidates=()

  case "$name" in
    stat|uname)
      candidates=(
        "/usr/bin/${name}"
        "/bin/${name}"
        "/usr/local/bin/${name}"
        "/usr/local/sbin/${name}"
      )
      ;;
    *)
      return 1
      ;;
  esac

  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]] && [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

# stat_owner prints the numeric owner of a file using the host's stat syntax.
# Follow trusted system symlinks so distro-managed command aliases such as
# /usr/bin/awk and Alpine's coreutils applets are checked by their targets.
stat_owner() {
  local path="$1"
  local uname_bin="${UNAME_BIN:-}"
  local stat_bin="${STAT_BIN:-}"

  if [[ -z "$uname_bin" ]] || [[ ! -x "$uname_bin" ]]; then
    uname_bin="$(bootstrap_system_tool uname)" || return 1
  fi
  if [[ -z "$stat_bin" ]] || [[ ! -x "$stat_bin" ]]; then
    stat_bin="$(bootstrap_system_tool stat)" || return 1
  fi

  case "$("$uname_bin" -s 2>/dev/null)" in
    Darwin)
      "$stat_bin" -L -f '%u' "$path"
      return $?
      ;;
    *)
      "$stat_bin" -L -c '%u' "$path"
      return $?
      ;;
  esac
}

# stat_mode prints the numeric permission mode of a file using the host's stat
# syntax. A failed stat is propagated so privileged checks fail closed.
stat_mode() {
  local path="$1"
  local uname_bin="${UNAME_BIN:-}"
  local stat_bin="${STAT_BIN:-}"

  if [[ -z "$uname_bin" ]] || [[ ! -x "$uname_bin" ]]; then
    uname_bin="$(bootstrap_system_tool uname)" || return 1
  fi
  if [[ -z "$stat_bin" ]] || [[ ! -x "$stat_bin" ]]; then
    stat_bin="$(bootstrap_system_tool stat)" || return 1
  fi

  case "$("$uname_bin" -s 2>/dev/null)" in
    Darwin)
      "$stat_bin" -L -f '%Lp' "$path"
      return $?
      ;;
    *)
      "$stat_bin" -L -c '%a' "$path"
      return $?
      ;;
  esac
}

# is_secure_root_file returns 0 only when the file is root-owned and has no
# group/other write bits. Metadata lookup failures are unsafe and return 1.
#
# Arguments:
#   $1 - path to check
#
# Returns:
#   0 when secure; 1 otherwise
is_secure_root_file() {
  local path="$1"
  local owner=""
  local mode=""

  owner="$(stat_owner "$path" 2>/dev/null)" || return 1
  [[ "$owner" == "0" ]] || return 1
  mode="$(stat_mode "$path" 2>/dev/null)" || return 1
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  if (( (8#$mode & 8#22) == 0 )); then
    return 0
  fi
  return 1
}

# is_trusted_command_path returns 0 when a command path is directly under one
# of the fixed system executable roots.  The check is applied to both the
# PATH candidate and its canonical target so a symlink in a writable location
# cannot be used to bypass the allowlist.
is_trusted_command_path() {
  local path="$1"
  local root=""

  for root in "${TRUSTED_COMMAND_ROOTS[@]}"; do
    case "$path" in
      "$root"|"$root"/*)
        return 0
        ;;
      *)
        ;;
    esac
  done
  return 1
}

# is_secure_root_path checks every existing component of an absolute path.
# Checking only the final executable is insufficient: a non-root-writable
# parent can replace a symlink or the executable between validation and use.
is_secure_root_path() {
  local path="$1"
  local current="/"
  local remainder=""
  local component=""

  [[ "$path" = /* ]] || return 1
  remainder="${path#/}"
  while [[ -n "$remainder" ]]; do
    component="${remainder%%/*}"
    if [[ "$remainder" == */* ]]; then
      remainder="${remainder#*/}"
    else
      remainder=""
    fi
    [[ -n "$component" ]] || continue
    current="${current%/}/${component}"
    [[ -e "$current" ]] || return 1
    is_secure_root_file "$current" || return 1
  done
  return 0
}

# is_secure_trusted_path accepts only the root-owned system layout.  A
# privileged installer never writes through a user-owned Homebrew tree: the
# owner can replace a validated component between the check and the write.
is_secure_trusted_path() {
  if is_secure_root_path "$1"; then
    return 0
  fi
  return 1
}

# bootstrap_readlink selects a fixed system readlink before canonicalize_path
# is used.  It intentionally does not consult PATH: canonicalization is part
# of the trust boundary for every later executable and destination check.
bootstrap_readlink() {
  local candidate=""
  local -a candidates=(
    /usr/bin/readlink
    /bin/readlink
    /usr/local/bin/readlink
    /opt/homebrew/bin/readlink
  )

  for candidate in "${candidates[@]}"; do
    if [[ ! -f "$candidate" ]] || [[ ! -x "$candidate" ]]; then
      continue
    fi
    is_trusted_command_path "$candidate" || continue
    if [[ "$EUID" -eq 0 ]] && ! is_secure_root_path "$candidate"; then
      continue
    fi
    READLINK_BIN="$candidate"
    return 0
  done

  return 1
}

# path_has_symlink_component returns 0 when any component of an absolute path
# is a symlink.  Privileged destinations reject symlink traversal entirely so
# an attacker cannot redirect a checked directory after validation.
path_has_symlink_component() {
  local path="$1"
  local current="/"
  local remainder=""
  local component=""

  [[ "$path" = /* ]] || return 1
  remainder="${path#/}"
  while [[ -n "$remainder" ]]; do
    component="${remainder%%/*}"
    if [[ "$remainder" == */* ]]; then
      remainder="${remainder#*/}"
    else
      remainder=""
    fi
    [[ -n "$component" ]] || continue
    [[ "$component" != "." ]] || continue
    [[ "$component" != ".." ]] || return 0
    current="${current%/}/${component}"
    [[ -L "$current" ]] && return 0
  done

  return 1
}

# is_trusted_nginx_destination_path limits installer writes to conventional
# NGINX roots or the exact prefix reported by the validated nginx binary.
is_trusted_nginx_destination_path() {
  local path="$1"
  local root=""

  [[ "$path" = /* ]] || return 1
  for root in "${TRUSTED_NGINX_DESTINATION_ROOTS[@]}"; do
    case "$path" in
      "$root"|"$root"/*)
        return 0
        ;;
      *)
        ;;
    esac
  done
  if [[ -n "${NGINX_PREFIX:-}" ]]; then
    case "$path" in
      "$NGINX_PREFIX"|"$NGINX_PREFIX"/*)
        return 0
        ;;
      *)
        ;;
    esac
  fi
  return 1
}

# validate_privileged_destination validates an existing or not-yet-created
# path before any installer write. For an existing file, its parent is checked;
# for a new path, the nearest existing ancestor is checked. All existing
# components must be root-owned and not group/other writable, and no path
# component may be a symlink.
validate_privileged_destination() {
  local path="$1"
  local label="$2"
  local existing=""
  local parent=""
  local canonical=""

  if [[ "$path" != /* ]] || ! is_trusted_nginx_destination_path "$path"; then
    die_with_error "$CATEGORY_CONFIG" \
      "Refusing to write ${label}: destination is outside trusted NGINX roots: ${path}" \
      "Use an NGINX build whose prefix and paths resolve under a root-owned NGINX installation." \
      "Do not override nginx metadata with a writable or relative destination."
  fi
  if path_has_symlink_component "$path"; then
    die_with_error "$CATEGORY_CONFIG" \
      "Refusing to write ${label}: destination contains a symlink: ${path}" \
      "Use a canonical NGINX prefix, modules path, and configuration path without symlink components."
  fi

  existing="$path"
  if [[ -e "$existing" ]] && [[ ! -d "$existing" ]]; then
    parent="$("$DIRNAME_BIN" "$existing")" || return 1
    [[ "$parent" != "$existing" ]] || return 1
    existing="$parent"
  else
    while [[ ! -e "$existing" ]]; do
      parent="$("$DIRNAME_BIN" "$existing")" || return 1
      [[ "$parent" != "$existing" ]] || return 1
      existing="$parent"
    done
  fi
  [[ -d "$existing" ]] || die_with_error "$CATEGORY_CONFIG" \
    "Refusing to write ${label}: existing parent is not a directory: ${existing}" \
    "$MSG_CHECK_PERMS_DISK"
  is_secure_trusted_path "$existing" || die_with_error "$CATEGORY_CONFIG" \
    "Refusing to write ${label}: parent is not root-owned and non-writable by group/other: ${existing}" \
    "$MSG_CHECK_PERMS_DISK"
  canonical="$(canonicalize_path "$existing")" || die_with_error "$CATEGORY_CONFIG" \
    "Refusing to write ${label}: destination canonicalization failed: ${existing}" \
    "Use a complete, accessible NGINX installation path."
  is_secure_trusted_path "$canonical" || die_with_error "$CATEGORY_CONFIG" \
    "Refusing to write ${label}: canonical parent is not secure: ${canonical}" \
    "$MSG_CHECK_PERMS_DISK"

  if [[ -e "$path" ]]; then
    [[ -f "$path" || -d "$path" ]] || die_with_error "$CATEGORY_CONFIG" \
      "Refusing to write ${label}: destination is not a regular file or directory: ${path}" \
      "$MSG_CHECK_PERMS_DISK"
    is_secure_trusted_path "$path" || die_with_error "$CATEGORY_CONFIG" \
      "Refusing to write ${label}: existing destination is not trusted and non-writable by group/other: ${path}" \
      "$MSG_CHECK_PERMS_DISK"
  fi
  return 0
}

# resolve_trusted_executable resolves a command name using only the fixed
# system PATH. It validates both the literal path and its canonical target but
# returns the literal path for invocation so multi-call binaries retain their
# applet name (for example, Alpine's /usr/bin/cat -> /bin/coreutils link).
# Root callers additionally require root-owned, non-writable path components;
# non-root development runs still get the same allowlist and canonical-target
# checks without requiring system ownership.
resolve_trusted_executable() {
  local name="$1"
  local candidate=""
  local resolved=""

  [[ "$name" =~ ^[A-Za-z0-9._+-]+$ ]] || return 1
  if ! candidate="$(PATH="$TRUSTED_COMMAND_PATH" command -v "$name" 2>/dev/null)"; then
    return 1
  fi
  [[ "$candidate" = /* ]] || return 1
  [[ "$(${BASENAME_BIN:-/usr/bin/basename} "$candidate")" = "$name" ]] || return 1
  [[ -f "$candidate" ]] && [[ -x "$candidate" ]] || return 1

  if ! resolved="$(canonicalize_path "$candidate")"; then
    return 1
  fi
  [[ -f "$resolved" ]] && [[ -x "$resolved" ]] || return 1
  is_trusted_command_path "$candidate" || return 1
  is_trusted_command_path "$resolved" || return 1

  if [[ "$EUID" -eq 0 ]]; then
    is_secure_trusted_path "$candidate" || return 1
    is_secure_trusted_path "$resolved" || return 1
  fi

  printf '%s\n' "$candidate"
  return 0
}

# cache_required_executable stores one validated absolute path in the named
# global variable.  Keeping this operation in one helper makes it difficult
# for a newly added command to accidentally reintroduce bare PATH execution.
cache_required_executable() {
  local variable="$1"
  local name="$2"
  local resolved=""

  if ! resolved="$(resolve_trusted_executable "$name")"; then
    die_with_error "$CATEGORY_CONFIG" \
      "Required executable is missing or untrusted: ${name}" \
      "Install ${name} in a root-owned system executable directory." \
      "Do not run the installer with a PATH entry pointing to a writable directory."
  fi
  printf -v "$variable" '%s' "$resolved"
  return 0
}

# cache_optional_executable records an empty path when an optional utility is
# unavailable.  Callers must branch on the cached value before invoking it.
cache_optional_executable() {
  local variable="$1"
  local name="$2"
  local resolved=""

  if resolved="$(resolve_trusted_executable "$name")"; then
    printf -v "$variable" '%s' "$resolved"
  else
    printf -v "$variable" '%s' ''
  fi
  return 0
}

# Resolve all utilities before any installer-controlled operation.  The
# installer then invokes these absolute paths throughout the privileged path;
# changing PATH after this point cannot redirect execution.
cache_trusted_executables() {
  cache_required_executable AWK_BIN awk
  cache_required_executable BASENAME_BIN basename
  cache_required_executable CAT_BIN cat
  cache_required_executable CHMOD_BIN chmod
  cache_required_executable CP_BIN cp
  cache_required_executable CURL_BIN curl
  cache_required_executable CUT_BIN cut
  cache_required_executable DIRNAME_BIN dirname
  cache_required_executable FIND_BIN find
  cache_required_executable GREP_BIN grep
  cache_required_executable HEAD_BIN head
  cache_required_executable MKDIR_BIN mkdir
  cache_required_executable MKTEMP_BIN mktemp
  cache_required_executable MV_BIN mv
  cache_required_executable RM_BIN rm
  cache_required_executable SED_BIN sed
  cache_required_executable SH_BIN sh
  cache_required_executable STAT_BIN stat
  cache_required_executable TAR_BIN tar
  cache_required_executable TR_BIN tr
  cache_required_executable UNAME_BIN uname
  cache_required_executable XARGS_BIN xargs

  cache_optional_executable FILE_BIN file
  cache_optional_executable GPG_BIN gpg
  cache_optional_executable JQ_BIN jq
  cache_optional_executable LDD_BIN ldd
  cache_optional_executable OPENSSL_BIN openssl
  cache_optional_executable PYTHON3_BIN python3
  cache_optional_executable SHA256SUM_BIN sha256sum
  cache_optional_executable SHASUM_BIN shasum
  return 0
}

# resolve_nginx_binary resolves and validates the nginx executable, storing the
# canonical absolute path in the global NGINX_BIN.
#
# When NGINX_BIN is set it must be an absolute path to an executable file whose
# literal location and resolved target are under the trusted roots.  Otherwise
# PATH discovery is used with the same checks.  When running as root, all
# executable and destination path components must be root-owned and
# non-writable by group/other.
#
# Returns:
#   0 on success with NGINX_BIN set; exits with a structured error otherwise.
resolve_nginx_binary() {
  local candidate=""
  local resolved=""

  if [[ -n "$NGINX_BIN" ]]; then
    if [[ "$NGINX_BIN" != /* ]]; then
      die_with_error "$CATEGORY_CONFIG" "NGINX_BIN must be an absolute path." \
        "Set NGINX_BIN to the absolute path of a trusted nginx executable."
    fi
    if [[ ! -f "$NGINX_BIN" ]] || [[ ! -x "$NGINX_BIN" ]]; then
      die_with_error "$CATEGORY_CONFIG" "NGINX_BIN is not an executable file: ${NGINX_BIN}" \
        "Set NGINX_BIN to the absolute path of a trusted nginx executable."
    fi
    candidate="$NGINX_BIN"
    resolved="$(canonicalize_path "$candidate")"
    if ! is_trusted_nginx_path "$candidate" \
      || ! is_trusted_nginx_path "$resolved" \
      || [[ ! -f "$resolved" ]] || [[ ! -x "$resolved" ]]; then
      die_with_error "$CATEGORY_CONFIG" \
        "NGINX_BIN is outside the trusted nginx executable roots: ${candidate}" \
        "Set NGINX_BIN to the absolute path of a trusted nginx executable."
    fi
    if [[ "$EUID" -eq 0 ]]; then
      if ! is_secure_trusted_path "$candidate" \
        || ! is_secure_trusted_path "$resolved"; then
        die_with_error "$CATEGORY_CONFIG" \
          "NGINX_BIN is not root-owned and non-writable by group/other: ${resolved}" \
          "Set NGINX_BIN to the absolute path of a trusted nginx executable."
      fi
    fi
    NGINX_BIN="$resolved"
    return 0
  fi

  if ! candidate="$(resolve_trusted_executable nginx)"; then
    die_with_error "$CATEGORY_CONFIG" "nginx is not installed or not in PATH." \
      "Install NGINX first: https://nginx.org/en/linux_packages.html" \
      "Ensure the nginx binary is in your PATH."
  fi

  resolved="$(canonicalize_path "$candidate")"
  if [[ -z "$resolved" ]] || [[ ! -f "$resolved" ]] || [[ ! -x "$resolved" ]]; then
    die_with_error "$CATEGORY_CONFIG" "Resolved nginx binary is not an executable file: ${resolved}" \
      "Set NGINX_BIN to the absolute path of a trusted nginx executable."
  fi

  if ! is_trusted_nginx_path "$candidate" \
    || ! is_trusted_nginx_path "$resolved"; then
    die_with_error "$CATEGORY_CONFIG" \
      "nginx was discovered in an untrusted PATH location: ${candidate}" \
      "Set NGINX_BIN to the absolute path of a trusted nginx executable."
  fi

  if [[ "$EUID" -eq 0 ]]; then
    if ! is_secure_trusted_path "$candidate" \
      || ! is_secure_trusted_path "$resolved"; then
      die_with_error "$CATEGORY_CONFIG" \
        "Resolved nginx binary is not root-owned and non-writable by group/other: ${resolved}" \
        "Set NGINX_BIN to the absolute path of a trusted nginx executable."
    fi
  fi

  NGINX_BIN="$resolved"
  return 0
}

# verify_release_signature verifies a detached ASCII-armored GPG signature
# (SHA256SUMS.asc) over a checksum manifest (SHA256SUMS) using a trusted key
# whose signing fingerprint must match the pinned expected fingerprint.
#
# Arguments:
#   $1 - path to the checksum manifest (SHA256SUMS)
#   $2 - path to the detached signature (SHA256SUMS.asc)
#   $3 - path to the ASCII-armored trusted public key
#   $4 - expected signing fingerprint (40 hex chars, case-insensitive)
#
# Outputs:
#   Writes "[+] Release signature verified ..." to stdout on success.
#
# Returns:
#   0 when the signature is valid and the fingerprint matches;
#   1 otherwise (with _json_error_message set).
verify_release_signature() {
  local manifest="$1"
  local signature="$2"
  local key_file="$3"
  local expected_fpr="$4"
  local gpg_home=""
  local verify_out=""
  local validsig=""
  local expected_upper=""

  if [[ -z "$GPG_BIN" ]]; then
    _json_error_message="gpg is required to verify the release signature but was not found."
    return 1
  fi

  if [[ ! "$expected_fpr" =~ ^[A-Fa-f0-9]{40}$ ]]; then
    _json_error_message="TRUSTED_FINGERPRINT must be exactly 40 hexadecimal characters."
    return 1
  fi

  if ! gpg_home="$($MKTEMP_BIN -d)"; then
    _json_error_message="Failed to create a temporary gpg home directory."
    return 1
  fi
  "$CHMOD_BIN" 700 "$gpg_home" 2>/dev/null || true

  if ! GNUPGHOME="$gpg_home" "$GPG_BIN" --batch --import "$key_file" >/dev/null 2>&1; then
    "$RM_BIN" -rf "$gpg_home" || true
    _json_error_message="Failed to import the trusted release signing key."
    return 1
  fi

  verify_out="$(GNUPGHOME="$gpg_home" "$GPG_BIN" --batch --status-fd=1 --verify "$signature" "$manifest" 2>/dev/null || true)"
  "$RM_BIN" -rf "$gpg_home" || true

  validsig="$(printf '%s\n' "$verify_out" | "$AWK_BIN" '$2 == "VALIDSIG" { print toupper($3); exit }')"
  expected_upper="$(printf '%s' "$expected_fpr" | "$TR_BIN" '[:lower:]' '[:upper:]')"
  if [[ -z "$validsig" ]]; then
    _json_error_message="Release signature verification failed; no valid signature was produced."
    return 1
  fi
  if [[ "$validsig" != "$expected_upper" ]]; then
    _json_error_message="Release signature fingerprint mismatch: got ${validsig}, expected ${expected_upper}."
    return 1
  fi

  echo "[+] Release signature verified (fingerprint ${expected_upper})"
  return 0
}

# write_embedded_release_key writes the release public key that is trusted by
# this installer. Keeping the key in the standalone script avoids making the
# signed release depend on an unsigned key fetched from the same release. The
# checked-in packaging key is the source of truth; release validation keeps
# this embedded copy synchronized with it.
write_embedded_release_key() {
  local destination="$1"

  if "$CAT_BIN" > "$destination" <<'EOF'
-----BEGIN PGP PUBLIC KEY BLOCK-----

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
U0DqXogBsapoV1N0APQG
=siJo
-----END PGP PUBLIC KEY BLOCK-----
EOF
  then
    return 0
  fi

  return 1
}

# verify_requested_tag_identity enforces exact release-tag identity when the
# operator pinned a version: the release metadata that produced every URL and
# digest below must describe precisely that tag. The default latest-release
# flow leaves RELEASE_VERSION empty and has no operator-pinned version to
# compare against, so the check is skipped there. A single leading v is
# normalized away so both spellings of the same version compare equal.
#
# Arguments:
#   (none; uses RELEASE_VERSION and RELEASE_TAG global variables)
#
# Returns:
#   0 when no pinned version is set or the pinned version matches the
#   resolved tag; never returns on mismatch (die_with_error exits).
verify_requested_tag_identity() {
  if [[ -z "$RELEASE_VERSION" ]]; then
    return 0
  fi
  local requested_tag_norm="${RELEASE_VERSION#v}"
  local resolved_tag_norm="${RELEASE_TAG#v}"
  if [[ "$requested_tag_norm" != "$resolved_tag_norm" ]]; then
    die_with_error "checksum" \
      "Resolved release tag ${RELEASE_TAG} does not match the requested version ${RELEASE_VERSION}; refusing to mix metadata across releases." \
      "Verify the requested version and retry, or pin the exact tag in the download URL." \
      "Build and install from source if no signed release is available."
  fi
  return 0
}

# manifest_digest_for prints the 64-hex digest for the exact asset name listed
# in a SHA256SUMS manifest.
#
# Arguments:
#   $1 - exact asset name
#   $2 - path to the SHA256SUMS manifest
#
# Outputs:
#   Writes the digest to stdout when the asset is listed.
#
# Returns:
#   0 when found; 1 when the asset is not listed.
manifest_digest_for() {
  local asset_name="$1"
  local manifest_file="$2"
  "$AWK_BIN" -v want="$asset_name" '
    ($2 == want || $2 == "*" want) {
      print tolower($1); found=1; exit
    }
    END { if (!found) exit 1 }
  ' "$manifest_file"
  return $?
}

# Fetch the GitHub release JSON for the project, selecting the latest release or a tagged version.
#
# Arguments:
#   (none; uses RELEASE_VERSION and REPO global variables)
#
# Outputs:
#   Writes the raw GitHub API JSON response to stdout
#
# Returns:
#   0 on success, non-zero if curl fails
fetch_release_json() {
  local response=""

  if [[ -z "$RELEASE_VERSION" ]]; then
    if ! response="$("$CURL_BIN" --proto '=https' --tlsv1.2 -fsSL -H 'Accept: application/vnd.github+json' \
      "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null)"; then
      return 1
    fi
    printf '%s\n' "$response"
    return 0
  fi

  # Operators may spell the tag with or without the leading v while the
  # published tag carries exactly one spelling.  Try the requested spelling
  # first, then its equivalent, so VERSION=0.9.2 resolves release v0.9.2.
  # The exact-identity check below still binds every URL and digest to the
  # resolved tag.
  local -a release_apis=()
  release_apis+=("https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_VERSION}")
  if [[ "$RELEASE_VERSION" == v* ]]; then
    release_apis+=("https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_VERSION#v}")
  else
    release_apis+=("https://api.github.com/repos/${REPO}/releases/tags/v${RELEASE_VERSION}")
  fi

  local release_api
  for release_api in "${release_apis[@]}"; do
    if response="$("$CURL_BIN" --proto '=https' --tlsv1.2 -fsSL -H 'Accept: application/vnd.github+json' "$release_api" 2>/dev/null)"; then
      printf '%s\n' "$response"
      return 0
    fi
  done
  return 1
}

# fetch_dist_index_json fetches the GitHub API JSON listing for the repository's `dist` directory at the specified ref and writes it to stdout.
fetch_dist_index_json() {
  local ref_name="$1"
  local dist_api="https://api.github.com/repos/${REPO}/contents/dist?ref=${ref_name}"
  local response=""
  if ! response="$("$CURL_BIN" --proto '=https' --tlsv1.2 -fsSL -H 'Accept: application/vnd.github+json' "$dist_api" 2>/dev/null)"; then
    return 1
  fi
  printf '%s\n' "$response"
  return 0
}

# resolve_download_info determines the download URL, SHA-256 digest, available prebuilt nginx versions,
# SHA256SUMS manifest URL, SHA256SUMS.asc signature URL, and release tag for a
# requested asset and prints them as six newline-separated lines.
# It accepts: asset_name, os_type, arch, nginx_version, ref_name, and optional release_json and dist_index_json (raw JSON strings).
# Output: line 1 = download URL (empty if not found), line 2 = sha256 digest without any prefix (empty if not present), line 3 = space-separated sorted list of available versions, line 4 = SHA256SUMS manifest URL, line 5 = SHA256SUMS.asc signature URL, line 6 = immutable release tag.
# If DOWNLOAD_URL_OVERRIDE is set, that URL, DOWNLOAD_SHA256, and empty manifest/signature URLs are printed immediately.
# resolve_download_info discovers the download URL, SHA-256 digest (if present), available prebuilt versions, SHA256SUMS manifest URL, SHA256SUMS.asc signature URL, and immutable release tag for the specified asset and prints them as six newline-separated lines (URL, digest, space-separated versions, manifest URL, signature URL, release tag); if DOWNLOAD_URL_OVERRIDE is set it prints that URL, DOWNLOAD_SHA256, and empty manifest/signature/tag fields, and if python3 is unavailable it emits a structured config error and returns non-zero so the caller can fail once from the parent shell.
resolve_download_info() {
  local asset_name="$1"
  local os_type="$2"
  local arch="$3"
  local nginx_version="$4"
  local ref_name="$5"
  local release_json="${6:-}"
  local dist_index_json="${7:-}"
  local parse_result=""

  if [[ -n "$DOWNLOAD_URL_OVERRIDE" ]]; then
    printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$DOWNLOAD_URL_OVERRIDE" "$DOWNLOAD_SHA256" "" "" "" ""
    return 0
  fi

  if [[ -z "$PYTHON3_BIN" ]]; then
    _json_error_category="$CATEGORY_CONFIG"
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
    "$PYTHON3_BIN" - <<'PY'
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
sha256sums_url = ""
sha256sums_asc_url = ""
release_tag = ""

if release_json:
    try:
        release_data = json.loads(release_json)
        release_tag = release_data.get("tag_name", "")
        assets = release_data.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            match = module_pattern.match(name)
            if match:
                versions.add(match.group(1))

            if name == "SHA256SUMS":
                sha256sums_url = asset.get("browser_download_url", "")
            elif name == "SHA256SUMS.asc":
                sha256sums_asc_url = asset.get("browser_download_url", "")

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
                    # The contents API is useful for listing available
                    # versions, but it is not an authenticated release asset
                    # source.  Keep URL resolution release-bound so a mutable
                    # branch cannot become the installer trust anchor.
    except json.JSONDecodeError:
        pass

sorted_versions = sorted(
    versions,
    key=lambda v: tuple(int(part) for part in v.split(".")),
)

print(url)
print(digest)
print(" ".join(sorted_versions))
print(sha256sums_url)
print(sha256sums_asc_url)
print(release_tag)
PY
  )"

  if [[ -n "$parse_result" ]]; then
    printf '%s\n' "$parse_result"
  else
    printf '\n\n\n\n\n\n'
  fi
  return 0
}

# Format a space-separated list of nginx versions grouped by major.minor series for display.
#
# Arguments:
#   $1 - space-separated list of version strings (e.g. "1.24.0 1.26.0 1.26.2")
#
# Outputs:
#   Writes grouped version lines to stdout, one line per major.minor series
#   (e.g. "  1.24.x: 1.24.0" and "  1.26.x: 1.26.0 1.26.2")
#
# Returns:
#   0 on success (also returns 0 if versions is empty or python3 is unavailable)
format_versions_by_series() {
  local versions="$1"

  if [[ -z "$versions" ]]; then
    return 0
  fi

  if [[ -z "$PYTHON3_BIN" ]]; then
    return 0
  fi

  "$PYTHON3_BIN" - "$versions" <<'PY'
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
  return 0
}

# Search matching NGINX config files without GNU grep-only traversal flags.
#
# Arguments:
#   $1 - directory tree to search
#   $2 - filename glob accepted by find (for example, *.conf)
#   $3 - extended regular expression passed to grep
#
# Outputs:
#   None.
#
# Returns:
#   0 when at least one matching file contains the pattern; 1 otherwise.
conf_tree_contains_pattern() {
  local search_dir="$1"
  local file_glob="$2"
  local pattern="$3"
  local match_marker=""

  if [[ ! -d "${search_dir}" ]]; then
    return 1
  fi

  match_marker="$(
      "$FIND_BIN" "${search_dir}" -type f -name "${file_glob}" \
      -exec "$SH_BIN" -c '
        grep_bin="$1"
        pattern="$2"
        shift 2
        for candidate do
          if "$grep_bin" -Eq "$pattern" "$candidate"; then
            printf "%s\n" matched
            break
          fi
        done
      ' _ "$GREP_BIN" "${pattern}" {} + 2>/dev/null
  )"
  [[ -n "${match_marker}" ]]
}

# collect_stale_module_suggestions inspects current nginx config for an already-loaded
# markdown module with ABI mismatch and appends concrete remediation suggestions into
# the provided array variable name.
collect_stale_module_suggestions() {
  local out_var="$1"
  local nginx_conf_dir="$2"
  local module_so="$3"
  local test_log=""
  local -a hints=()

  if [[ -z "$nginx_conf_dir" ]] || [[ -z "$module_so" ]]; then
    return 0
  fi

  if [[ ! -d "$nginx_conf_dir" ]]; then
    return 0
  fi

  if ! conf_tree_contains_pattern "$nginx_conf_dir" "$CONF_GLOB" \
    "^[[:space:]]*load_module[[:space:]]+.*${module_so}[[:space:]]*;"; then
    return 0
  fi

  if ! test_log="$($MKTEMP_BIN)"; then
    return 0
  fi

  if "$NGINX_BIN" -t >"$test_log" 2>&1; then
    "$RM_BIN" -f "$test_log" || true
    return 0
  fi

  if "$GREP_BIN" -Eq "module \".*${module_so}\" version [0-9]+ instead of [0-9]+" "$test_log"; then
    hints+=("Detected an already-enabled stale ${module_so} that does not match current NGINX ABI.")
    hints+=("List loader snippets: sudo find ${nginx_conf_dir} -type f -name '${CONF_GLOB}' -exec grep -l '${module_so}' {} +")
    hints+=("Disable each matched snippet by renaming it to *.disabled (or comment out its load_module line), then run: sudo ${NGINX_BIN} -t")
    hints+=("After cleanup, build from source for this NGINX version: ${SOURCE_BUILD_URL}")
  fi

  "$RM_BIN" -f "$test_log" || true
  if [[ "${#hints[@]}" -gt 0 ]]; then
    local i=0
    while [[ $i -lt ${#hints[@]} ]]; do
      printf -v "${out_var}[$i]" '%s' "${hints[$i]}"
      i=$((i + 1))
    done
  fi
  return 0
}

# auto_disable_stale_module_loaders renames nginx *.conf snippets containing the
# target module load directive to *.disabled when AUTO_DISABLE_STALE_MODULE=1.
auto_disable_stale_module_loaders() {
  local nginx_conf_dir="$1"
  local module_so="$2"
  local disabled_count=0
  local file=""

  if [[ "$AUTO_DISABLE_STALE_MODULE" != "1" ]]; then
    return 0
  fi

  if [[ ! -d "$nginx_conf_dir" ]]; then
    return 0
  fi

  while IFS= read -r file; do
    if [[ -z "$file" ]]; then
      continue
    fi
    if [[ "$file" == *.disabled ]]; then
      continue
    fi
    if "$MV_BIN" "$file" "${file}.disabled"; then
      echo "[+] Disabled stale module snippet: ${file} -> ${file}.disabled"
      disabled_count=$((disabled_count + 1))
    else
      echo "[!] Failed to disable stale module snippet: ${file}" >&2
    fi
  done < <(
    "$FIND_BIN" "$nginx_conf_dir" -type f -name "$CONF_GLOB" -print0 2>/dev/null \
      | "$XARGS_BIN" -0 "$GREP_BIN" -l "load_module .*${module_so}" 2>/dev/null || true
  )

  if [[ "$disabled_count" -gt 0 ]]; then
    if "$NGINX_BIN" -t >/dev/null 2>&1; then
      echo "[+] nginx -t passed after disabling stale module snippets"
    else
      echo "[!] nginx -t still fails after auto-disable; manual review is required" >&2
    fi
  fi
  return 0
}

# Extract the value of a --key=VALUE argument from nginx -V output.
#
# Arguments:
#   $1 - the configure key name (e.g. "prefix", "conf-path", "modules-path")
#   $2 - the full output of `nginx -V`
#
# Outputs:
#   Writes the extracted value to stdout, or nothing if the key is not found
#
# Returns:
#   0 always
extract_configure_arg() {
  local key="$1"
  local nginx_v_output="$2"
  printf '%s\n' "$nginx_v_output" | "$SED_BIN" -n "s/.*--${key}=\\([^ ]*\\).*/\\1/p" | "$HEAD_BIN" -n1
  return 0
}

# Resolve a path value by prepending a prefix if the candidate is relative.
#
# Arguments:
#   $1 - candidate path (may be empty, absolute, or relative)
#   $2 - prefix to prepend for relative paths (may be empty)
#
# Outputs:
#   Writes the resolved absolute or relative path to stdout;
#   an empty line if the candidate is empty
#
# Returns:
#   0 always
resolve_path_with_prefix() {
  local candidate="$1"
  local prefix="$2"

  if [[ -z "$candidate" ]]; then
    printf '\n'
    return 0
  fi

  if [[ "$candidate" = /* ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  if [[ -n "$prefix" ]]; then
    printf '%s/%s\n' "${prefix%/}" "$candidate"
  else
    printf '%s\n' "$candidate"
  fi
  return 0
}

resolve_include_dir() {
  local include_pattern="$1"
  local conf_dir="$2"
  local include_dir

  include_dir="$("$DIRNAME_BIN" "$include_pattern")"
  if [[ "$include_dir" = "." ]]; then
    include_dir="$conf_dir"
  fi

  if [[ "$include_dir" != /* ]]; then
    include_dir="${conf_dir%/}/${include_dir}"
  fi

  printf '%s\n' "$include_dir"
  return 0
}

backup_file_once() {
  local file="$1"
  local backup_file="${file}.bak.nginx-markdown-for-agents"
  validate_privileged_destination "$file" "configuration backup source"
  if [[ -e "$backup_file" ]] && ! validate_privileged_destination \
      "$backup_file" "configuration backup"; then
    return 1
  fi
  if [[ ! -f "$backup_file" ]] && ! "$CP_BIN" "$file" "$backup_file"; then
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to create backup file: ${backup_file}" \
      "$MSG_CHECK_PERMS_DISK"
  fi
  return 0
}

# ensure_main_include_directive ensures the given nginx main configuration file contains the specified include directive, inserting it before the first top-level block (events,http,stream,mail) or appending it if no such block is found and creating a backup via backup_file_once.
ensure_main_include_directive() {
  local conf_file="$1"
  local include_directive="$2"

  if "$GREP_BIN" -Fq "$include_directive" "$conf_file"; then
    return 0
  fi

  backup_file_once "$conf_file"

  local tmp_file
  if ! tmp_file="$("$MKTEMP_BIN")"; then
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to create a temporary file while updating ${conf_file}" \
      "$MSG_CHECK_PERMS_TMP_DISK"
  fi

  if ! "$AWK_BIN" -v include_line="$include_directive" '
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
    "$RM_BIN" -f "$tmp_file" || true
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to update nginx config contents for ${conf_file}" \
      "$MSG_CHECK_PERMS_DISK"
  fi

  if ! "$CAT_BIN" "$tmp_file" > "$conf_file"; then
    "$RM_BIN" -f "$tmp_file" || true
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to write updated nginx config: ${conf_file}" \
      "$MSG_CHECK_PERMS_DISK"
  fi
  "$RM_BIN" -f "$tmp_file" || true
  return 0
}

# insert_markdown_filter_into_http_block inserts `markdown_filter on;` as the first line inside the top-level `http { ... }` block of the specified nginx configuration file.
# insert_markdown_filter_into_http_block inserts `markdown_filter on;` into the top-level `http` block of the given nginx configuration file, creates a backup via `backup_file_once` before overwriting, returns 0 on success and returns 1 (leaving the original file unchanged) if no `http` block is found or the insertion fails.
insert_markdown_filter_into_http_block() {
  local conf_file="$1"

  local tmp_file
  if ! tmp_file="$("$MKTEMP_BIN")"; then
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to create a temporary file while updating ${conf_file}" \
      "$MSG_CHECK_PERMS_TMP_DISK"
  fi

  if ! "$AWK_BIN" '
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
    "$RM_BIN" -f "$tmp_file"
    return 1
  fi

  backup_file_once "$conf_file"
  if ! "$CAT_BIN" "$tmp_file" > "$conf_file"; then
    "$RM_BIN" -f "$tmp_file" || true
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to write updated nginx config: ${conf_file}" \
      "$MSG_CHECK_PERMS_DISK"
  fi
  "$RM_BIN" -f "$tmp_file" || true
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

echo "$SEPARATOR_LINE"
echo " NGINX Markdown for Agents - Binary Module Installer"
echo "$SEPARATOR_LINE"

if [[ "${SKIP_ROOT_CHECK:-0}" != "1" ]] && [[ "$EUID" -ne 0 ]]; then
  die_with_error "$CATEGORY_CONFIG" "This script must be run as root." \
    "Re-run with: sudo bash install.sh" \
    "Or set SKIP_ROOT_CHECK=1 if running inside a container."
fi

if ! bootstrap_readlink; then
  die_with_error "$CATEGORY_CONFIG" \
    "Required executable is missing or untrusted: readlink" \
    "Install readlink in a root-owned system executable directory." \
    "Do not run the installer with a PATH entry pointing to a writable directory."
fi
cache_trusted_executables
export PATH="$TRUSTED_COMMAND_PATH"

# Detect Nginx runtime/build metadata using a validated nginx executable.
resolve_nginx_binary
NGINX_V_OUTPUT="$("$NGINX_BIN" -V 2>&1)"
NGINX_VERSION="$(printf '%s\n' "$NGINX_V_OUTPUT" | "$GREP_BIN" -oE 'nginx/[0-9]+\.[0-9]+\.[0-9]+' | "$CUT_BIN" -d/ -f2)"
if [[ -z "$NGINX_VERSION" ]]; then
  die_with_error "$CATEGORY_CONFIG" "Could not determine NGINX version from '${NGINX_BIN} -V' output." \
    "Verify NGINX is installed correctly: ${NGINX_BIN} -V" \
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

if [[ -z "$NGINX_CONF_PATH" ]]; then
  NGINX_CONF_PATH="/etc/nginx/nginx.conf"
fi
if [[ -n "$NGINX_MODULES_PATH" ]]; then
  validate_privileged_destination "$NGINX_MODULES_PATH" "NGINX modules path"
fi
validate_privileged_destination "$NGINX_CONF_PATH" "NGINX configuration"
NGINX_CONF_DIR="$("$DIRNAME_BIN" "$NGINX_CONF_PATH")"

echo "[+] NGINX conf path: $NGINX_CONF_PATH"
if [[ -n "$NGINX_MODULES_PATH_RAW" ]]; then
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
if [[ -n "$LDD_BIN" ]] && "$LDD_BIN" /bin/sh 2>&1 | "$GREP_BIN" -iq musl; then
  OS_TYPE="musl"
elif [[ -f /etc/alpine-release ]]; then
  OS_TYPE="musl"
fi
echo "[+] Detected OS family: $OS_TYPE"
_json_os_type="$OS_TYPE"

# Detect Architecture
ARCH="$("$UNAME_BIN" -m)"
if [[ "$ARCH" = "aarch64" ]] || [[ "$ARCH" = "arm64" ]]; then
  ARCH="aarch64"
elif [[ "$ARCH" = "x86_64" ]] || [[ "$ARCH" = "amd64" ]]; then
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
if [[ -n "$RELEASE_VERSION" ]]; then
  release_api_hint="$RELEASE_VERSION"
  source_ref="$RELEASE_VERSION"
fi

if [[ -n "$DOWNLOAD_URL_OVERRIDE" ]] && [[ -z "$DOWNLOAD_SHA256" ]]; then
  die_with_error "checksum" \
    "DOWNLOAD_URL_OVERRIDE requires DOWNLOAD_SHA256; checksumless installation is not supported." \
    "Set DOWNLOAD_SHA256 to the trusted SHA-256 digest of the override artifact." \
    "If no independently authenticated digest is available, build and install from source."
fi

RELEASE_JSON=""
if [[ -z "$DOWNLOAD_URL_OVERRIDE" ]] && ! RELEASE_JSON="$(fetch_release_json)"; then
  echo "[!] Warning: Failed to query GitHub release metadata (${release_api_hint}); falling back to repository dist index." >&2
fi

DIST_INDEX_JSON=""
if [[ -z "$DOWNLOAD_URL_OVERRIDE" ]] && ! DIST_INDEX_JSON="$(fetch_dist_index_json "$source_ref")"; then
  echo "[!] Warning: Failed to query repository dist index (${source_ref})." >&2
fi

# Determine target asset name
ASSET_NAME="ngx_http_markdown_filter_module-${NGINX_VERSION}-${OS_TYPE}-${ARCH}.tar.gz"

echo "----------------------------------------------------------------------------------"
echo "Looking for binary: $ASSET_NAME"

if ! RELEASE_INFO_FILE="$("$MKTEMP_BIN")"; then
  die_with_error "$CATEGORY_FILESYSTEM" \
    "Failed to create a temporary file for release metadata." \
    "$MSG_CHECK_PERMS_TMP_DISK"
fi

if ! resolve_download_info "$ASSET_NAME" "$OS_TYPE" "$ARCH" "$NGINX_VERSION" "$source_ref" "$RELEASE_JSON" "$DIST_INDEX_JSON" > "$RELEASE_INFO_FILE"; then
  "$RM_BIN" -f "$RELEASE_INFO_FILE" || true
  die_with_error "${_json_error_category:-config}" \
    "${_json_error_message:-Failed to resolve download metadata.}" \
    "${_json_suggestions[@]:-Install python3: apt-get install python3 / apk add python3}"
fi

mapfile -t RELEASE_INFO < "$RELEASE_INFO_FILE"
"$RM_BIN" -f "$RELEASE_INFO_FILE" || true
DOWNLOAD_URL="${RELEASE_INFO[0]:-}"
EXPECTED_SHA256="${RELEASE_INFO[1]:-}"
AVAILABLE_VERSIONS="${RELEASE_INFO[2]:-}"
SHA256SUMS_URL="${RELEASE_INFO[3]:-}"
SHA256SUMS_ASC_URL="${RELEASE_INFO[4]:-}"
RELEASE_TAG="${RELEASE_INFO[5]:-}"

if [[ -z "$DOWNLOAD_URL" ]]; then
  _json_available_versions="$AVAILABLE_VERSIONS"
  _version_suggestions=("NGINX dynamic modules require an exact version match.")
  _stale_module_suggestions=()
  collect_stale_module_suggestions _stale_module_suggestions "$NGINX_CONF_DIR" "ngx_http_markdown_filter_module.so"
  if [[ "${#_stale_module_suggestions[@]}" -gt 0 ]]; then
    auto_disable_stale_module_loaders "$NGINX_CONF_DIR" "ngx_http_markdown_filter_module.so"
    _version_suggestions+=("${_stale_module_suggestions[@]}")
    if [[ "$AUTO_DISABLE_STALE_MODULE" = "1" ]]; then
      _version_suggestions+=("AUTO_DISABLE_STALE_MODULE=1 enabled: attempted automatic disable of stale loader snippets.")
    fi
  fi
  if [[ -n "$AVAILABLE_VERSIONS" ]]; then
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

if [[ -z "$DOWNLOAD_URL_OVERRIDE" && -n "$RELEASE_TAG"
      && "$DOWNLOAD_URL" != *"/releases/download/${RELEASE_TAG}/"* ]]; then
  die_with_error "checksum" \
    "Asset URL does not belong to the verified release ${RELEASE_TAG}: ${DOWNLOAD_URL}." \
    "Re-run without overrides; if the mismatch persists, report it at https://github.com/${REPO}/issues." \
    "Build and install from source if no signed release is available."
fi

echo "[+] Downloading $DOWNLOAD_URL ..."
if ! TMP_DIR="$("$MKTEMP_BIN" -d)"; then
  die_with_error "$CATEGORY_FILESYSTEM" \
    "Failed to create a temporary working directory." \
    "$MSG_CHECK_PERMS_TMP_DISK"
fi
trap '"$RM_BIN" -rf "$TMP_DIR"' EXIT

if ! "$CURL_BIN" --proto '=https' --tlsv1.2 -fsSL -o "$TMP_DIR/$ASSET_NAME" "$DOWNLOAD_URL"; then
  _json_available_versions="$AVAILABLE_VERSIONS"
  if [[ -n "$AVAILABLE_VERSIONS" ]]; then
    echo "Available pre-built versions for ${OS_TYPE}/${ARCH} (grouped by major.minor):" >&2
    format_versions_by_series "$AVAILABLE_VERSIONS" >&2
  fi
  die_with_error "network" \
    "Failed to download ${ASSET_NAME} from ${DOWNLOAD_URL}." \
    "Check your network connection and try again." \
    "Verify the URL is accessible: curl -fsSL -I '${DOWNLOAD_URL}'" \
    "See ${SOURCE_BUILD_URL}"
fi

# Verification strategy:
#   - DOWNLOAD_URL_OVERRIDE (operator-provided URL + DOWNLOAD_SHA256): the
#     operator-supplied digest is the independent trust anchor.
#   - Release-API path (default): the asset digest from the release API is
#     cross-checked against a signed SHA256SUMS manifest whose signature must
#     verify against the pinned key embedded in this installer. The manifest
#     digest is authoritative; the API digest is a secondary consistency
#     check. Fail closed when the signature, key, manifest, or trust material
#     is missing.
if [[ -n "$DOWNLOAD_URL_OVERRIDE" ]]; then
  if [[ -n "$DOWNLOAD_SHA256" ]]; then
    if ! ACTUAL_SHA256="$(sha256_file "$TMP_DIR/$ASSET_NAME")"; then
      die_with_error "checksum" \
        "No SHA-256 hashing tool is available to verify ${ASSET_NAME}." \
        "Install sha256sum, shasum, or openssl and retry." \
        "Do not install the artifact without checksum verification."
    fi
    EXPECTED_SHA256="$(printf '%s' "$DOWNLOAD_SHA256" | tr '[:upper:]' '[:lower:]')"
    if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
      die_with_error "checksum" \
        "Checksum verification failed for ${ASSET_NAME}. Expected: ${DOWNLOAD_SHA256}, Actual: ${ACTUAL_SHA256}." \
        "Re-download the file and try again." \
        "If the problem persists, the artifact may be corrupted. Report at https://github.com/${REPO}/issues"
    fi
    echo "[+] SHA256 checksum verified (operator-supplied DOWNLOAD_SHA256)"
  else
    die_with_error "checksum" \
      "DOWNLOAD_URL_OVERRIDE requires DOWNLOAD_SHA256; checksumless installation is not supported." \
      "Set DOWNLOAD_SHA256 to the trusted SHA-256 digest of the override artifact." \
      "If no independently authenticated digest is available, build and install from source."
  fi
else
  if [[ -z "$SHA256SUMS_URL" ]] || [[ -z "$SHA256SUMS_ASC_URL" ]]; then
    die_with_error "checksum" \
      "Release does not publish a signed SHA256SUMS manifest (SHA256SUMS/SHA256SUMS.asc); refusing to install unsigned artifact." \
      "Provide DOWNLOAD_URL_OVERRIDE together with DOWNLOAD_SHA256, or build and install from source." \
      "See ${SOURCE_BUILD_URL}"
  fi

  if [[ -z "$RELEASE_TAG" ]] || [[ ! "$RELEASE_TAG" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die_with_error "checksum" \
      "Release metadata did not provide an immutable version tag for signed verification." \
      "Use a published release tag or provide DOWNLOAD_URL_OVERRIDE together with DOWNLOAD_SHA256." \
      "Build and install from source if no signed release is available."
  fi
  # Exact requested-tag identity: when the operator named a release, the
  # API metadata that produced every URL and digest below must describe
  # precisely that tag. The latest-release flow has no pinned version and
  # skips the comparison inside the helper.
  verify_requested_tag_identity
  if ! "$CURL_BIN" --proto '=https' --tlsv1.2 -fsSL -o "$TMP_DIR/SHA256SUMS" "$SHA256SUMS_URL"; then
    die_with_error "network" \
      "Failed to download SHA256SUMS manifest." \
      "Check your network connection and try again." \
      "See ${SOURCE_BUILD_URL}"
  fi
  if ! "$CURL_BIN" --proto '=https' --tlsv1.2 -fsSL -o "$TMP_DIR/SHA256SUMS.asc" "$SHA256SUMS_ASC_URL"; then
    die_with_error "network" \
      "Failed to download SHA256SUMS.asc signature." \
      "Check your network connection and try again." \
      "See ${SOURCE_BUILD_URL}"
  fi
  if ! write_embedded_release_key "$TMP_DIR/release-key.asc"; then
    die_with_error "$CATEGORY_FILESYSTEM" \
      "Failed to materialize the embedded release signing key." \
      "$MSG_CHECK_PERMS_TMP_DISK" \
      "See ${SOURCE_BUILD_URL}"
  fi

  if ! verify_release_signature "$TMP_DIR/SHA256SUMS" "$TMP_DIR/SHA256SUMS.asc" \
      "$TMP_DIR/release-key.asc" "$TRUSTED_FINGERPRINT"; then
    die_with_error "checksum" "${_json_error_message}" \
      "Re-run with the correct TRUSTED_FINGERPRINT or DOWNLOAD_URL_OVERRIDE + DOWNLOAD_SHA256." \
      "See ${SOURCE_BUILD_URL}"
  fi

  MANIFEST_SHA256="$(manifest_digest_for "$ASSET_NAME" "$TMP_DIR/SHA256SUMS")" || true
  if [[ -z "$MANIFEST_SHA256" ]]; then
    die_with_error "checksum" \
      "Verified SHA256SUMS manifest does not list ${ASSET_NAME}." \
      "The release may not contain a module for NGINX ${NGINX_VERSION} (${OS_TYPE} ${ARCH})." \
      "See ${SOURCE_BUILD_URL}"
  fi

  if ! ACTUAL_SHA256="$(sha256_file "$TMP_DIR/$ASSET_NAME")"; then
    die_with_error "checksum" \
      "No SHA-256 hashing tool is available to verify ${ASSET_NAME}." \
      "Install sha256sum, shasum, or openssl and retry." \
      "Do not install the artifact without checksum verification."
  fi
  if [[ "$ACTUAL_SHA256" != "$MANIFEST_SHA256" ]]; then
    die_with_error "checksum" \
      "Checksum verification failed for ${ASSET_NAME} against the signed manifest. Expected: ${MANIFEST_SHA256}, Actual: ${ACTUAL_SHA256}." \
      "Re-download the file and try again." \
      "If the problem persists, the release artifact may be corrupted. Report at https://github.com/${REPO}/issues"
  fi
  echo "[+] SHA256 checksum verified against signed SHA256SUMS manifest"

  if [[ -n "$EXPECTED_SHA256" ]] && [[ "$EXPECTED_SHA256" != "$MANIFEST_SHA256" ]]; then
    die_with_error "checksum" \
      "Release API digest disagrees with the signed manifest for ${ASSET_NAME}. API: ${EXPECTED_SHA256}, manifest: ${MANIFEST_SHA256}." \
      "Report the inconsistency at https://github.com/${REPO}/issues"
  fi
fi

cd "$TMP_DIR"
# --no-same-owner: the archive may carry root ownership from the build
# container; extracting as root would otherwise honor embedded ownership.
# Preflight the tar member list: reject absolute paths, path traversal, and
# unexpected members.  The asset must contain exactly the expected module .so.
echo "[+] Preflight tar member list for ${ASSET_NAME}"
TAR_MEMBERS="$("$TAR_BIN" -tzf "$ASSET_NAME" 2>/dev/null | sort)"
if [[ -z "$TAR_MEMBERS" ]]; then
    die_with_error "extraction" \
        "Archive appears empty or unreadable." \
        "Re-download and try again."
fi
EXPECTED_MEMBER="ngx_http_markdown_filter_module.so"
MEMBER_COUNT=$(echo "$TAR_MEMBERS" | wc -l | tr -d ' ')
if [[ "$MEMBER_COUNT" -ne 1 ]]; then
    die_with_error "extraction" \
        "Archive contains $MEMBER_COUNT member(s), expected exactly 1 ($EXPECTED_MEMBER)." \
        "Found: $TAR_MEMBERS"
fi
if [[ "$TAR_MEMBERS" != "$EXPECTED_MEMBER" ]]; then
    die_with_error "extraction" \
        "Archive member mismatch: expected \"$EXPECTED_MEMBER\", got \"$TAR_MEMBERS\"." \
        "The archive may be corrupted or built for a different purpose."
fi
# Check for absolute paths or path traversal in member names (defense-in-depth)
if echo "$TAR_MEMBERS" | grep -qE '^/|(^|/)\.\./'; then
    die_with_error "extraction" \
        "Archive member name contains absolute path or '..' traversal." \
        "Members: $TAR_MEMBERS"
fi
echo "[+] Tar member list preflight passed: $TAR_MEMBERS"

if ! "$TAR_BIN" --no-same-owner -xzf "$ASSET_NAME"; then
  die_with_error "extraction" \
    "Failed to extract ${ASSET_NAME}." \
    "The archive may be corrupted. Re-download and try again." \
    "Report at https://github.com/${REPO}/issues if the problem persists."
fi

MODULE_SO="ngx_http_markdown_filter_module.so"
if [[ ! -f "$MODULE_SO" ]]; then
  die_with_error "$CATEGORY_CONFIG" \
    "Extraction failed: ${MODULE_SO} not found in the downloaded archive." \
    "The archive may be corrupted. Re-download and try again." \
    "Report at https://github.com/${REPO}/issues if the problem persists."
fi

# Verify the extracted object is an ELF of the expected architecture before
# installing: a checksum-matching but wrong-arch artifact (or a future
# multi-file archive) must not be copied into the modules directory.
# `file` is not part of POSIX; on minimal containers it may be absent.  The
# sha256 check above already guarantees byte-exact content, so skip the ELF
# probe with a warning instead of misreporting a valid archive as broken.
if [[ -n "$FILE_BIN" ]]; then
  MODULE_FILE_DESC="$("$FILE_BIN" "$MODULE_SO" 2>/dev/null || true)"
  case "$MODULE_FILE_DESC" in
    *"ELF 64-bit"*)
      ;;
    *)
      die_with_error "$CATEGORY_CONFIG" \
        "Extracted ${MODULE_SO} is not a 64-bit ELF object (file: ${MODULE_FILE_DESC:-unknown})." \
        "The archive may be corrupted or built for a different platform. Re-download and try again."
      ;;
  esac
  case "$ARCH" in
    x86_64)
      if [[ "$MODULE_FILE_DESC" != *"x86-64"* ]] && [[ "$MODULE_FILE_DESC" != *"x86_64"* ]]; then
        die_with_error "$CATEGORY_CONFIG" \
          "Extracted ${MODULE_SO} is not built for x86_64 (file: ${MODULE_FILE_DESC})." \
          "Requested architecture ${ARCH} does not match the artifact. Re-download the correct asset."
      fi
      ;;
    aarch64)
      if [[ "$MODULE_FILE_DESC" != *"ARM aarch64"* ]] && [[ "$MODULE_FILE_DESC" != *"AArch64"* ]]; then
        die_with_error "$CATEGORY_CONFIG" \
          "Extracted ${MODULE_SO} is not built for aarch64 (file: ${MODULE_FILE_DESC})." \
          "Requested architecture ${ARCH} does not match the artifact. Re-download the correct asset."
      fi
      ;;
    *)
      die_with_error "$CATEGORY_CONFIG" \
        "Unsupported architecture for ELF verification: ${ARCH}." \
        "Supported architectures are x86_64 and aarch64."
      ;;
  esac
  echo "[+] Extracted module verified: 64-bit ELF for ${ARCH}"
else
  echo "[!] file(1) not found; skipping ELF architecture verification (sha256 already verified the archive)." >&2
fi

# Determine NGINX modules directory
# Prefer the build-time path from nginx -V when available.
MODULES_DIR=""
if [[ -n "$NGINX_MODULES_PATH" ]]; then
  MODULES_DIR="$NGINX_MODULES_PATH"
elif [[ -d "/etc/nginx/modules" ]]; then
  MODULES_DIR="/etc/nginx/modules"
elif [[ -d "/usr/lib/nginx/modules" ]]; then
  MODULES_DIR="/usr/lib/nginx/modules"
elif [[ -d "/usr/share/nginx/modules" ]]; then
  MODULES_DIR="/usr/share/nginx/modules"
elif [[ -d "/usr/local/nginx/modules" ]]; then
  MODULES_DIR="/usr/local/nginx/modules"
else
  MODULES_DIR="/etc/nginx/modules"
fi
validate_privileged_destination "$MODULES_DIR" "NGINX modules directory"
if ! "$MKDIR_BIN" -p "$MODULES_DIR"; then
  die_with_error "$CATEGORY_FILESYSTEM" \
    "Failed to create modules directory: ${MODULES_DIR}" \
    "$MSG_CHECK_PERMS_DISK"
fi

echo "[+] Installing module to $MODULES_DIR/"
if ! "$CP_BIN" "$MODULE_SO" "$MODULES_DIR/"; then
  die_with_error "$CATEGORY_FILESYSTEM" \
    "Failed to copy ${MODULE_SO} to ${MODULES_DIR}/" \
    "$MSG_CHECK_PERMS_DISK"
fi
if ! "$CHMOD_BIN" 644 "$MODULES_DIR/$MODULE_SO"; then
  die_with_error "$CATEGORY_FILESYSTEM" \
    "Failed to set permissions on ${MODULES_DIR}/${MODULE_SO}" \
    "Check filesystem permissions."
fi

MODULE_LOAD_PATH="${MODULES_DIR%/}/${MODULE_SO}"

MODULE_CONF_SNIPPET=""
MARKDOWN_CONF_SNIPPET=""
MODULE_ALREADY_CONFIGURED=0
MARKDOWN_ALREADY_CONFIGURED=0
MARKDOWN_INSERTED_IN_MAIN=0
MANUAL_ACTIONS=()

if [[ -f "$NGINX_CONF_PATH" ]]; then
  if conf_tree_contains_pattern "$NGINX_CONF_DIR" "$CONF_GLOB" \
    "^[[:space:]]*load_module[[:space:]]+.*${MODULE_SO}[[:space:]]*;"; then
    MODULE_ALREADY_CONFIGURED=1
  fi

  MODULE_INCLUDE_PATTERN="$("$GREP_BIN" -E '^[[:space:]]*include[[:space:]]+[^;]*(modules|modules-enabled)[^;]*\.conf[[:space:]]*;' "$NGINX_CONF_PATH" | "$SED_BIN" -E 's/^[[:space:]]*include[[:space:]]+([^;]+);/\1/' | "$HEAD_BIN" -n1 || true)"
  if [[ -z "$MODULE_INCLUDE_PATTERN" ]]; then
    MODULE_INCLUDE_PATTERN="${NGINX_CONF_DIR%/}/modules-enabled/*.conf"
    ensure_main_include_directive "$NGINX_CONF_PATH" "include ${MODULE_INCLUDE_PATTERN};"
    echo "[+] Added main-context include: include ${MODULE_INCLUDE_PATTERN};"
  fi

  if [[ "$MODULE_ALREADY_CONFIGURED" -eq 0 ]]; then
    MODULE_INCLUDE_DIR="$(resolve_include_dir "$MODULE_INCLUDE_PATTERN" "$NGINX_CONF_DIR")"
    validate_privileged_destination "$MODULE_INCLUDE_DIR" \
      "module include directory"
    if ! "$MKDIR_BIN" -p "$MODULE_INCLUDE_DIR"; then
      die_with_error "$CATEGORY_FILESYSTEM" \
        "Failed to create module include directory: ${MODULE_INCLUDE_DIR}" \
        "$MSG_CHECK_PERMS_DISK"
    fi
    MODULE_CONF_SNIPPET="${MODULE_INCLUDE_DIR%/}/50-ngx-http-markdown-filter-module.conf"
    validate_privileged_destination "$MODULE_CONF_SNIPPET" \
      "module loader snippet"
    if ! "$CAT_BIN" > "$MODULE_CONF_SNIPPET" <<EOF
# Generated by nginx-markdown-for-agents install.sh
load_module ${MODULE_LOAD_PATH};
EOF
    then
      die_with_error "$CATEGORY_FILESYSTEM" \
        "Failed to write module loader snippet: ${MODULE_CONF_SNIPPET}" \
        "$MSG_CHECK_PERMS_DISK"
    fi
    if ! "$CHMOD_BIN" 644 "$MODULE_CONF_SNIPPET"; then
      die_with_error "$CATEGORY_FILESYSTEM" \
        "Failed to set permissions on ${MODULE_CONF_SNIPPET}" \
        "Check filesystem permissions."
    fi
    echo "[+] Wrote module loader snippet: $MODULE_CONF_SNIPPET"
  else
    echo "[+] Existing load_module directive found for ${MODULE_SO}, skipping snippet creation"
  fi

  if conf_tree_contains_pattern "$NGINX_CONF_DIR" "$CONF_GLOB" \
    "^[[:space:]]*markdown_filter[[:space:]]+on[[:space:]]*;"; then
    MARKDOWN_ALREADY_CONFIGURED=1
  fi

  if [[ "$MARKDOWN_ALREADY_CONFIGURED" -eq 0 ]]; then
    HTTP_INCLUDE_PATTERN="$("$GREP_BIN" -E '^[[:space:]]*include[[:space:]]+[^;]*conf\.d/[^;]*\.conf[[:space:]]*;' "$NGINX_CONF_PATH" | "$SED_BIN" -E 's/^[[:space:]]*include[[:space:]]+([^;]+);/\1/' | "$HEAD_BIN" -n1 || true)"
    if [[ -n "$HTTP_INCLUDE_PATTERN" ]]; then
      HTTP_INCLUDE_DIR="$(resolve_include_dir "$HTTP_INCLUDE_PATTERN" "$NGINX_CONF_DIR")"
      validate_privileged_destination "$HTTP_INCLUDE_DIR" \
        "markdown include directory"
      if ! "$MKDIR_BIN" -p "$HTTP_INCLUDE_DIR"; then
        die_with_error "$CATEGORY_FILESYSTEM" \
          "Failed to create markdown include directory: ${HTTP_INCLUDE_DIR}" \
          "$MSG_CHECK_PERMS_DISK"
      fi
      MARKDOWN_CONF_SNIPPET="${HTTP_INCLUDE_DIR%/}/90-markdown-filter-enable.conf"
      validate_privileged_destination "$MARKDOWN_CONF_SNIPPET" \
        "markdown enable snippet"
      if ! "$CAT_BIN" > "$MARKDOWN_CONF_SNIPPET" <<'EOF'
# Generated by nginx-markdown-for-agents install.sh
markdown_filter on;

# Optional tuning examples:
# markdown_limits conversion_memory=64m conversion_timeout=30s;
# markdown_error_policy pass;
EOF
      then
        die_with_error "$CATEGORY_FILESYSTEM" \
          "Failed to write markdown enable snippet: ${MARKDOWN_CONF_SNIPPET}" \
          "$MSG_CHECK_PERMS_DISK"
      fi
      if ! "$CHMOD_BIN" 644 "$MARKDOWN_CONF_SNIPPET"; then
        die_with_error "$CATEGORY_FILESYSTEM" \
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
if ! NGINX_TEST_LOG="$("$MKTEMP_BIN")"; then
  die_with_error "$CATEGORY_FILESYSTEM" \
    "Failed to create a temporary file for nginx -t output." \
    "$MSG_CHECK_PERMS_TMP_DISK"
fi
if "$NGINX_BIN" -t >"$NGINX_TEST_LOG" 2>&1; then
  NGINX_TEST_RESULT="ok"
else
  NGINX_TEST_RESULT="failed"
fi

echo "$SEPARATOR_LINE"
echo " Installation Complete!"
echo "$SEPARATOR_LINE"
echo "Auto-generated configuration:"
if [[ -n "$MODULE_CONF_SNIPPET" ]]; then
  echo "1. Module loader snippet: $MODULE_CONF_SNIPPET"
  echo "   -> load_module ${MODULE_LOAD_PATH};"
elif [[ "$MODULE_ALREADY_CONFIGURED" -eq 1 ]]; then
  echo "1. Module loader already exists in current nginx config."
else
  echo "1. Module loader snippet was not created automatically."
fi

if [[ -n "$MARKDOWN_CONF_SNIPPET" ]]; then
  echo "2. Markdown enable snippet: $MARKDOWN_CONF_SNIPPET"
  echo "   -> markdown_filter on;"
elif [[ "$MARKDOWN_ALREADY_CONFIGURED" -eq 1 ]]; then
  echo "2. markdown_filter is already enabled in current nginx config."
elif [[ "$MARKDOWN_INSERTED_IN_MAIN" -eq 1 ]]; then
  echo "2. Added 'markdown_filter on;' directly into $NGINX_CONF_PATH (http block)."
else
  echo "2. markdown_filter was not auto-enabled."
fi

if [[ "${#MANUAL_ACTIONS[@]}" -gt 0 ]]; then
  echo ""
  echo "Manual actions required:"
  for action in "${MANUAL_ACTIONS[@]}"; do
    echo " - $action"
  done
fi

echo ""
if [[ "$NGINX_TEST_RESULT" = "ok" ]]; then
  echo "[+] nginx -t passed"
  echo "Run: ${NGINX_BIN} -s reload"
else
  echo "[!] nginx -t failed. Review errors below:" >&2
  "$SED_BIN" -n '1,20p' "$NGINX_TEST_LOG"
  echo "Fix config and run: ${NGINX_BIN} -t && ${NGINX_BIN} -s reload"
fi
"$RM_BIN" -f "$NGINX_TEST_LOG" || true

echo ""
echo "You can continue fine-tuning later (recommended):"
echo "- Scope rollout with server/location-level markdown_filter on/off"
echo "- Adjust markdown_limits conversion_memory= / markdown_error_policy by workload"
echo "$SEPARATOR_LINE"

# Emit JSON output if --json was requested
if [[ "$NGINX_TEST_RESULT" = "failed" ]]; then
  emit_error "$CATEGORY_CONFIG" "nginx -t failed after installation; the generated configuration is not loadable."
  json_output false
  # A failed nginx -t means the install produced a broken
  # config; the exit code must reflect that so automation does not treat a
  # broken install as success.
  exit 1
else
  json_output true
fi
