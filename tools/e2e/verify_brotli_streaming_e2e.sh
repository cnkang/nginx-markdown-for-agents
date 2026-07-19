#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

exec cargo run --quiet \
  --manifest-path "${workspace_root}/tools/e2e-harness/Cargo.toml" \
  -- scenario brotli-streaming "$@"
