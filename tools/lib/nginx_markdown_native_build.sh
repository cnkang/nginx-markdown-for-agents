#!/usr/bin/env bash
#
# Shared native-build helpers for release and E2E scripts.
#
# Contract:
#   - Functions named markdown_* are shell-library entrypoints.
#   - Data-returning helpers write machine-readable values to stdout.
#   - Diagnostics and prerequisites failures are written to stderr.
#   - Functions return 0 on success and non-zero on validation, discovery,
#     copy, or unsupported-platform failures.
#   - Callers must source this file; it is not intended to run as a script.

# Args:
#   $1 - command name to find in PATH.
# Stdout:
#   None.
# Stderr:
#   Missing-command diagnostic.
# Returns:
#   0 when the command is available; 1 otherwise.
markdown_need_cmd() {
  local cmd_name="$1"

  command -v "${cmd_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${cmd_name}" >&2
    return 1
  }

  return 0
}

# Args:
#   $1 - current script path; remaining arguments are forwarded on re-exec.
# Stdout:
#   None.
# Stderr:
#   Re-exec diagnostic when Rosetta translation is detected.
# Returns:
#   0 when no re-exec is needed. On translated Apple Silicon, replaces the
#   current process with native arm64 /bin/bash.
markdown_ensure_native_apple_silicon() {
  local script_path="$1"
  shift || true

  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  local translated
  translated="$(sysctl -in sysctl.proc_translated 2>/dev/null || echo 0)"
  if [[ "${translated}" == "1" ]]; then
    echo "Re-executing under native arm64 (/bin/bash) to avoid Rosetta..." >&2
    exec arch -arm64 /bin/bash "${script_path}" "$@"
  fi

  return 0
}

# Args:
#   None.
# Stdout:
#   Rust target triple for the current host.
# Stderr:
#   Unsupported-host diagnostic.
# Returns:
#   0 when a supported target is detected; 1 otherwise.
markdown_detect_rust_target() {
  local host_os host_arch libc_variant

  host_os="$(uname -s)"
  host_arch="$(uname -m)"
  libc_variant="gnu"

  if [[ "${host_os}" == "Linux" ]] && { ldd --version 2>&1 | grep -qi musl || compgen -G '/lib/ld-musl*' >/dev/null; }; then
    libc_variant="musl"
  fi

  case "${host_os}:${host_arch}:${libc_variant}" in
    Darwin:arm64:*) echo "aarch64-apple-darwin" ;;
    Darwin:x86_64:*) echo "x86_64-apple-darwin" ;;
    Linux:x86_64:gnu) echo "x86_64-unknown-linux-gnu" ;;
    Linux:aarch64:gnu) echo "aarch64-unknown-linux-gnu" ;;
    Linux:x86_64:musl) echo "x86_64-unknown-linux-musl" ;;
    Linux:aarch64:musl) echo "aarch64-unknown-linux-musl" ;;
    *)
      echo "Unsupported host for Rust target detection: ${host_os}/${host_arch}" >&2
      return 1
      ;;
  esac

  return 0
}

markdown_default_macos_deployment_target() {
  printf '%s\n' "${MACOS_MIN_VERSION:-11.0}"
  return 0
}

markdown_export_native_build_env() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  if [[ -z "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
    export MACOSX_DEPLOYMENT_TARGET
    MACOSX_DEPLOYMENT_TARGET="$(markdown_default_macos_deployment_target)"
  fi

  return 0
}

markdown_append_env_flag() {
  local var_name="$1"
  local flag_value="$2"
  local current_value

  eval "current_value=\"\${${var_name}:-}\""
  case " ${current_value} " in
    *" ${flag_value} "*) ;;
    *) current_value="${current_value:+${current_value} }${flag_value}" ;;
  esac

  printf -v "${var_name}" '%s' "${current_value}"
  export "${var_name?}"
  return 0
}

markdown_homebrew_prefix() {
  local formula="$1"
  local prefix

  if command -v brew >/dev/null 2>&1; then
    prefix="$(brew --prefix "${formula}" 2>/dev/null || true)"
    if [[ -n "${prefix}" && -d "${prefix}" ]]; then
      printf '%s\n' "${prefix}"
      return 0
    fi
  fi

  for prefix in "/opt/homebrew/opt/${formula}" "/usr/local/opt/${formula}"; do
    if [[ -d "${prefix}" ]]; then
      printf '%s\n' "${prefix}"
      return 0
    fi
  done

  return 1
}

markdown_export_nginx_dependency_env() {
  local formula prefix

  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  for formula in "$@"; do
    prefix="$(markdown_homebrew_prefix "${formula}" || true)"
    if [[ -z "${prefix}" ]]; then
      continue
    fi
    if [[ -d "${prefix}/include" ]]; then
      markdown_append_env_flag CPPFLAGS "-I${prefix}/include"
    fi
    if [[ -d "${prefix}/lib" ]]; then
      markdown_append_env_flag LDFLAGS "-L${prefix}/lib"
    fi
  done

  return 0
}

markdown_emit_nginx_configure_env() {
  local cc_opt="$1"
  local ld_opt="$2"
  local opt

  if [[ -n "${NGINX_CONFIGURE_OPTS:-}" ]]; then
    for opt in ${NGINX_CONFIGURE_OPTS}; do
      printf '%s\n' "${opt}"
    done
  fi
  if [[ -n "${cc_opt}" ]]; then
    printf '%s\n' "--with-cc-opt=${cc_opt}"
  fi
  if [[ -n "${ld_opt}" ]]; then
    printf '%s\n' "--with-ld-opt=${ld_opt}"
  fi

  return 0
}

markdown_print_nginx_build_failure_diagnostics() {
  local buildroot="${1:-}"
  local log_file="${2:-}"

  echo "Native NGINX build failed." >&2
  if [[ -n "${buildroot}" ]]; then
    echo "Artifacts kept at: ${buildroot}" >&2
  fi
  if [[ -n "${log_file}" ]]; then
    echo "Build log: ${log_file}" >&2
  fi
  echo "To reuse an existing module-enabled binary:" >&2
  echo "  export NGINX_BIN=/path/to/compiled/nginx" >&2

  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "For Homebrew dependencies, install:" >&2
    echo "  brew install pcre2 zlib openssl@3" >&2
    echo "Homebrew prefixes checked: /opt/homebrew and /usr/local" >&2
    echo "Current CPPFLAGS: ${CPPFLAGS:-<unset>}" >&2
    echo "Current LDFLAGS: ${LDFLAGS:-<unset>}" >&2
    echo "If auto-detection fails, pass paths explicitly, for example:" >&2
    # shellcheck disable=SC2016
    echo '  export CPPFLAGS="-I$(brew --prefix openssl@3)/include -I$(brew --prefix pcre2)/include -I$(brew --prefix zlib)/include ${CPPFLAGS:-}"' >&2
    # shellcheck disable=SC2016
    echo '  export LDFLAGS="-L$(brew --prefix openssl@3)/lib -L$(brew --prefix pcre2)/lib -L$(brew --prefix zlib)/lib ${LDFLAGS:-}"' >&2
  fi

  return 0
}

markdown_sync_converter_header() {
  local workspace_root="$1"
  local header_src="${workspace_root}/components/rust-converter/include/markdown_converter.h"
  local header_dst="${workspace_root}/components/nginx-module/src/markdown_converter.h"

  if [[ ! -f "${header_src}" ]]; then
    echo "Missing generated header: ${header_src}" >&2
    return 1
  fi

  cp "${header_src}" "${header_dst}"
  return 0
}

markdown_copy_rust_release_archive() {
  local workspace_root="$1"
  local rust_target="$2"
  local src_lib="${workspace_root}/components/rust-converter/target/${rust_target}/release/libnginx_markdown_converter.a"
  local dst_lib="${workspace_root}/components/rust-converter/target/release/libnginx_markdown_converter.a"

  if [[ "$(cd "$(dirname "${src_lib}")" && pwd)/$(basename "${src_lib}")" != "$(cd "$(dirname "${dst_lib}")" && pwd)/$(basename "${dst_lib}")" ]]; then
    mkdir -p "$(dirname "${dst_lib}")"
    cp "${src_lib}" "${dst_lib}"
  fi

  return 0
}

markdown_validate_nginx_bin() {
  local nginx_bin="$1"

  if [[ ! -x "${nginx_bin}" ]]; then
    echo "NGINX binary is not executable: ${nginx_bin}" >&2
    return 1
  fi

  return 0
}

markdown_nginx_runtime_conf_dir() {
  local nginx_bin="$1"
  local source_root source_conf

  markdown_validate_nginx_bin "${nginx_bin}" || return 1

  source_root="$(cd "$(dirname "${nginx_bin}")/.." && pwd)"
  source_conf="${source_root}/conf"

  if [[ ! -f "${source_conf}/mime.types" ]]; then
    return 1
  fi

  printf '%s\n' "${source_conf}"
  return 0
}

markdown_can_reuse_nginx_bin() {
  local nginx_bin="$1"

  markdown_nginx_runtime_conf_dir "${nginx_bin}" >/dev/null
  return $?
}

markdown_copy_runtime_conf_from_nginx_bin() {
  local nginx_bin="$1"
  local runtime_dir="$2"
  local source_conf

  source_conf="$(markdown_nginx_runtime_conf_dir "${nginx_bin}")" || {
    echo "Missing mime.types beside reusable nginx binary: ${nginx_bin}" >&2
    return 1
  }

  mkdir -p "${runtime_dir}/conf"
  cp "${source_conf}/mime.types" "${runtime_dir}/conf/mime.types"
  return 0
}

markdown_nginx_modules_dir() {
  local nginx_bin="$1"
  local source_root source_modules modules_path

  markdown_validate_nginx_bin "${nginx_bin}" || return 1

  source_root="$(cd "$(dirname "${nginx_bin}")/.." && pwd)"
  source_modules="${source_root}/modules"
  if [[ -d "${source_modules}" ]]; then
    printf '%s\n' "${source_modules}"
    return 0
  fi

  modules_path="$("${nginx_bin}" -V 2>&1 | tr ' ' '\n' | sed -n 's/^--modules-path=//p' | tail -n1)"
  if [[ -n "${modules_path}" && -d "${modules_path}" ]]; then
    printf '%s\n' "${modules_path}"
    return 0
  fi

  return 1
}

markdown_find_dynamic_markdown_module() {
  local nginx_bin="$1"
  local modules_dir module_path

  modules_dir="$(markdown_nginx_modules_dir "${nginx_bin}")" || return 1
  module_path="$(
    find "${modules_dir}" -maxdepth 1 -type f \( \
      -name 'ngx_http_markdown*.so' -o \
      -name '*markdown*.so' \
    \) | sort | head -n1
  )"

  if [[ -z "${module_path}" ]]; then
    return 1
  fi

  printf '%s\n' "${module_path}"
  return 0
}

markdown_prepare_runtime_reuse() {
  local nginx_bin="$1"
  local runtime_dir="$2"
  local module_path

  markdown_copy_runtime_conf_from_nginx_bin "${nginx_bin}" "${runtime_dir}" || return 1

  module_path="$(markdown_find_dynamic_markdown_module "${nginx_bin}" || true)"
  if [[ -z "${module_path}" ]]; then
    return 0
  fi

  mkdir -p "${runtime_dir}/modules"
  cp "${module_path}" "${runtime_dir}/modules/"
  printf 'load_module modules/%s;\n' "$(basename "${module_path}")"
  return 0
}

markdown_version_gt() {
  local lhs="$1"
  local rhs="$2"
  local lhs_parts rhs_parts idx lhs_value rhs_value

  IFS='.' read -r -a lhs_parts <<< "${lhs}"
  IFS='.' read -r -a rhs_parts <<< "${rhs}"

  for idx in 0 1 2; do
    lhs_value="${lhs_parts[idx]:-0}"
    rhs_value="${rhs_parts[idx]:-0}"
    if (( 10#${lhs_value} > 10#${rhs_value} )); then
      return 0
    fi
    if (( 10#${lhs_value} < 10#${rhs_value} )); then
      return 1
    fi
  done

  return 1
}

markdown_find_newer_macos_archive_member() {
  local archive_path="$1"
  local deployment_target="$2"
  local extract_dir member minos
  archive_path="$(cd "$(dirname "${archive_path}")" && pwd)/$(basename "${archive_path}")"

  extract_dir="$(mktemp -d /tmp/nginx-markdown-archive.XXXXXX)"
  (
    cd "${extract_dir}" || exit 1
    while IFS= read -r member; do
      [[ -z "${member}" || "${member}" == "__.SYMDEF"* ]] && continue
      rm -f "${member}"
      ar -x "${archive_path}" "${member}" >/dev/null 2>&1 || continue
      minos="$(
        otool -l "${member}" 2>/dev/null | awk '
          /LC_BUILD_VERSION/ { in_build_version = 1; next }
          in_build_version && $1 == "minos" { print $2; exit }
        '
      )"
      rm -f "${member}"

      if [[ -n "${minos}" ]] && markdown_version_gt "${minos}" "${deployment_target}"; then
        printf '%s\n' "${member}"
        exit 0
      fi
    done < <(ar -t "${archive_path}")
    exit 1
  )
  local rc=$?
  rm -rf "${extract_dir}"
  return "${rc}"
}

markdown_prepare_rust_converter_release() {
  local workspace_root="$1"
  local rust_target="$2"
  shift 2

  markdown_export_native_build_env || return 1

  (
    cd "${workspace_root}/components/rust-converter" || {
      echo "Failed to enter Rust converter directory under ${workspace_root}" >&2
      exit 1
    }
    cargo build --target "${rust_target}" --release "$@"

    if [[ "$(uname -s)" == "Darwin" ]]; then
      local archive_path="target/${rust_target}/release/libnginx_markdown_converter.a"
      local stale_member=""
      stale_member="$(markdown_find_newer_macos_archive_member "${archive_path}" "${MACOSX_DEPLOYMENT_TARGET}" || true)"
      if [[ -n "${stale_member}" ]]; then
        echo "Rebuilding Rust converter for macOS deployment target ${MACOSX_DEPLOYMENT_TARGET} (stale member: ${stale_member})"
        cargo clean --target "${rust_target}"
        cargo build --target "${rust_target}" --release "$@"
      fi
    fi

    markdown_copy_rust_release_archive "${workspace_root}" "${rust_target}"
    markdown_sync_converter_header "${workspace_root}"
  )
  return 0
}

# Wait for an HTTP endpoint to become reachable.
#
# Args:
#   $1 - URL to poll.
#   $2 - human-readable label for failure diagnostics.
# Stdout:
#   None.
# Stderr:
#   Timeout diagnostics and curl output on failure.
# Returns:
#   0 when the endpoint responds; 1 after ~5s of unreachable attempts
#   (wall-clock can grow if the server accepts but stalls per request).
markdown_wait_for_http() {
  local url="$1"
  local label="$2"
  local curl_output=""
  local _i

  for _i in $(seq 1 50); do
    if curl -sS --max-time 1 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done

  curl_output=$(curl -sS --max-time 2 "$url" 2>&1 || true)
  echo "${label} failed to become ready after 5s" >&2
  if [[ -n "${curl_output}" ]]; then
    echo "curl output: ${curl_output}" >&2
  fi
  return 1
}

# Validate that a CLI flag received a non-empty value.
#
# Intended for use in argument-parsing loops: the caller passes the flag
# name and the prospective value; if the value is missing or empty, an
# error is printed and the function returns 2.  Under set -e, the
# non-zero return will terminate the caller; callers that run without
# set -e should check the return value explicitly.
#
# Args:
#   $1 - flag name (e.g. --nginx-version) for diagnostics.
#   $2 - flag value; must be non-empty.
# Stdout:
#   None.
# Stderr:
#   Missing-value diagnostic on failure.
# Returns:
#   0 when the flag has a value; 2 otherwise.
markdown_require_flag_value() {
  local flag_name="$1"

  if [[ $# -lt 2 || -z "${2:-}" ]]; then
    echo "Missing value for ${flag_name}" >&2
    return 2
  fi

  return 0
}

# Assert that an HTTP status line pattern appears in a header file.
#
# Args:
#   $1 - case label for diagnostics (e.g. "Case 1").
#   $2 - header file path.
#   $3 - grep pattern (e.g. "HTTP/1.1 200").
# Stdout:
#   None on success.
# Stderr:
#   FAIL message on assertion failure.
# Exits:
#   1 when the pattern is not found.
#   Returns 0 on success.
markdown_expect_status() {
  local label="$1"
  local hdr_file="$2"
  local pattern="$3"

  grep -qi "${pattern}" "${hdr_file}" || {
    echo "FAIL: ${label} - expected ${pattern}" >&2
    exit 1
  }
  return 0
}

# Assert that a header pattern appears in a header file.
#
# Args:
#   $1 - case label for diagnostics (e.g. "Case 1").
#   $2 - header file path.
#   $3 - grep pattern (e.g. "^ETag:").
# Stdout:
#   None on success.
# Stderr:
#   FAIL message on assertion failure.
# Exits:
#   1 when the pattern is not found.
#   Returns 0 on success.
markdown_expect_header() {
  local label="$1"
  local hdr_file="$2"
  local pattern="$3"

  grep -qi "${pattern}" "${hdr_file}" || {
    echo "FAIL: ${label} - expected header matching ${pattern}" >&2
    exit 1
  }
  return 0
}

# Extract a header value from a header file.
#
# Uses awk for POSIX portability; avoids GNU-sed /I flag which is
# not available on macOS BSD sed.
#
# Args:
#   $1 - header file path.
#   $2 - header name (e.g. "ETag").
# Stdout:
#   The header value with leading whitespace and trailing CR/LF stripped.
# Stderr:
#   None.
# Returns:
#   0 always.
markdown_extract_header() {
  local hdr_file="$1"
  local header="$2"

  awk -v hdr="${header}" 'BEGIN { lower=tolower(hdr) }
    tolower($1) ~ tolower(hdr) ":" {
      sub(/^[^:]+:[[:space:]]+/, ""); sub(/\r$/, ""); print; exit
    }' "${hdr_file}"
  return 0
}
