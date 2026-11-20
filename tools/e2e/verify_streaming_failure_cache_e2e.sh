#!/usr/bin/env bash
set -euo pipefail

# Compatibility entry point for the Rust streaming-failure-cache scenario.
workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${workspace_root}/tools/lib/nginx_markdown_native_build.sh"
# shellcheck source=tools/e2e/e2e_common.sh
source "${workspace_root}/tools/e2e/e2e_common.sh"

usage() {
  echo "Usage: $(basename "$0") [--keep-artifacts] [--nginx-version VERSION] [--port PORT] [--upstream-port PORT]"
  echo "       [--nginx-bin PATH] [--markdown-max-size SIZE] [--plan]"
  echo "Set NGINX_BIN to reuse an existing module-enabled nginx binary."
  return 0
}

port="${PORT:-18102}"
upstream_port="${UPSTREAM_PORT:-19102}"
export NGINX_VERSION="${NGINX_VERSION:-1.28.3}"
plan=0
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-artifacts) args+=(--keep-artifacts); shift ;;
    --nginx-version) markdown_require_flag_value "$1" "${2-}"; NGINX_VERSION="$2"; shift 2 ;;
    --port) markdown_require_flag_value "$1" "${2-}"; port="$2"; shift 2 ;;
    --upstream-port) markdown_require_flag_value "$1" "${2-}"; upstream_port="$2"; shift 2 ;;
    --nginx-bin) markdown_require_flag_value "$1" "${2-}"; export NGINX_BIN="$2"; shift 2 ;;
    --markdown-max-size) markdown_require_flag_value "$1" "${2-}"; export MARKDOWN_MAX_SIZE="$2"; shift 2 ;;
    --plan) plan=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "${plan}" -eq 1 ]]; then
  cat <<'PLAN'
10.1 and 10.1b Streaming conversion and ETag removal
10.2 through 10.4 Fail-open, reject, and aborted upstream
10.5 through 10.8 Full-buffer and streaming validators
10.9 Independent pass and reject policies
10.10 and 10.11 HEAD and conditional 304
10.12, 10.12b, and 10.13 Authenticated and anonymous cache policy
PLAN
  exit 0
fi
if [[ -z "${NGINX_BIN:-}" ]]; then
  echo "Set NGINX_BIN to a streaming-enabled nginx binary, or use --plan." >&2
  exit 1
fi
args+=(--port "${port}" --upstream-port "${upstream_port}")
e2e_enter_rust_workspace "${workspace_root}"
exec cargo run --locked --quiet \
  --manifest-path "${workspace_root}/tools/e2e-harness/Cargo.toml" \
  -- scenario streaming-failure-cache "${args[@]}"
