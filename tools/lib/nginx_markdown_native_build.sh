#!/usr/bin/env bash

markdown_need_cmd() {
  local cmd_name="$1"

  command -v "${cmd_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${cmd_name}" >&2
    return 1
  }

  return 0
}

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

markdown_detect_rust_target() {
  case "$(uname -s):$(uname -m)" in
    Darwin:arm64) echo "aarch64-apple-darwin" ;;
    Darwin:x86_64) echo "x86_64-apple-darwin" ;;
    Linux:x86_64) echo "x86_64-unknown-linux-gnu" ;;
    Linux:aarch64) echo "aarch64-unknown-linux-gnu" ;;
    *)
      echo "Unsupported host for Rust target detection: $(uname -s)/$(uname -m)" >&2
      return 1
      ;;
  esac

  return 0
}

markdown_default_macos_deployment_target() {
  local product_version major

  product_version="$(sw_vers -productVersion 2>/dev/null || true)"
  if [[ -z "${product_version}" ]]; then
    product_version="$(xcrun --show-sdk-version 2>/dev/null || true)"
  fi
  if [[ -z "${product_version}" ]]; then
    echo "Failed to resolve a macOS deployment target" >&2
    return 1
  fi

  major="${product_version%%.*}"
  if [[ -z "${major}" ]]; then
    echo "Failed to parse macOS deployment target from: ${product_version}" >&2
    return 1
  fi

  printf '%s.0\n' "${major}"
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
    cd "${workspace_root}/components/rust-converter"
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
}
