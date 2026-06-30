#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# E2E Smoke: Profile system (spec 50, task 9.12)
#
# Validates that all three profiles (strict_cache, balanced,
# streaming_first) load correctly in a running NGINX instance and
# produce the expected effective configuration behavior.
#
# Requires:
#   NGINX_BIN — path to a locally-compiled NGINX binary with the
#               markdown filter module loaded.
#
# Test plan:
#   1. Start NGINX with a config containing all three profiles in
#      separate locations.
#   2. For each profile location, request the diagnostics endpoint
#      and verify the "profile" field in the JSON response.
#   3. Verify that strict_cache returns streaming=off in diagnostics.
#   4. Verify that streaming_first returns streaming=force.
#   5. Verify that balanced returns streaming=auto.
#   6. Verify that an explicit override (e.g. streaming=force on a
#      strict_cache location) triggers a startup error.
#   7. Shut down NGINX cleanly.
#
# Exit codes:
#   0 — all checks passed
#   1 — test failure
#   2 — NGINX_BIN not set (deferred)
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/e2e_common.sh" 2>/dev/null || true

if [[ -z "${NGINX_BIN:-}" ]]; then
    echo "SKIP: NGINX_BIN not set — profile E2E smoke deferred"
    echo ""
    echo "To run this test:"
    echo "  export NGINX_BIN=/path/to/nginx-with-module"
    echo "  bash $0"
    exit 2
fi

echo "=== Profile E2E Smoke (spec 50, task 9.12) ==="
echo "NGINX_BIN=${NGINX_BIN}"
echo ""
echo "NOTE: Full implementation pending — this is a placeholder"
echo "      documenting the test plan for when the E2E environment"
echo "      is available."
echo ""
echo "DEFERRED: requires running NGINX instance with module loaded."
exit 2
