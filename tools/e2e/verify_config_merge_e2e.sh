#!/usr/bin/env bash
set -euo pipefail

# Compatibility entry point for the Rust config-merge scenario.
workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${workspace_root}/tools/lib/nginx_markdown_native_build.sh"
# shellcheck source=tools/e2e/e2e_common.sh
source "${workspace_root}/tools/e2e/e2e_common.sh"

usage() {
  echo "Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]"
  echo "Set NGINX_BIN to reuse an existing module-enabled nginx binary."
  return 0
}

port="${PORT:-18101}"
upstream_port="${UPSTREAM_PORT:-19101}"
export NGINX_VERSION="${NGINX_VERSION:-1.28.3}"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts) args+=(--keep-artifacts); shift ;;
    --nginx-version) markdown_require_flag_value "$1" "${2-}"; NGINX_VERSION="$2"; shift 2 ;;
    --port) markdown_require_flag_value "$1" "${2-}"; port="$2"; shift 2 ;;
    --upstream-port) markdown_require_flag_value "$1" "${2-}"; upstream_port="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

args+=(--port "${port}" --upstream-port "${upstream_port}")
e2e_enter_rust_workspace "${workspace_root}"
exec cargo run --locked --quiet \
  --manifest-path "${workspace_root}/tools/e2e-harness/Cargo.toml" \
  -- scenario config-merge "${args[@]}"
