#!/usr/bin/env bash
set -euo pipefail

# Run the multi-layer Content-Encoding chain E2E scenario.
#
# Requires NGINX_BIN pointing to a locally-compiled NGINX binary with the
# markdown module loaded (see tools/e2e-harness/README.md).

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

exec cargo run --quiet \
  --manifest-path "${workspace_root}/tools/e2e-harness/Cargo.toml" \
  -- scenario encoding-chain "$@"
