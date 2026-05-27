#!/usr/bin/env bash
#
# detect_ffi_struct_init.sh — Gate: FFI structs with init helpers must not
# be initialized via ngx_memzero/memset in production code.
#
# Rule 15 (ffi-crosslang): Prefer helper functions over literal FFI struct init.
#
# Structs with Rust-provided init helpers:
#   - MarkdownOptions      → markdown_options_init()
#   - MarkdownResult       → markdown_result_init()
#   - FFIAcceptResult      → markdown_accept_result_init()
#   - FFIHeaderPlan        → markdown_header_plan_init()
#   - FFIDecompResult      → markdown_decomp_result_init()
#
# This script scans C production source (not tests) for direct memset/
# ngx_memzero calls on these struct types and reports violations.
#
# Exit codes:
#   0 = no violations found
#   1 = violations detected
#
set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="${SCRIPT_DIR}/../.."
SRC_DIR="${REPO_ROOT}/components/nginx-module/src"

# Structs that have Rust-provided init helpers
GUARDED_STRUCTS=(
    "MarkdownOptions"
    "MarkdownResult"
    "FFIAcceptResult"
    "FFIConditionalResult"
    "FFIDecisionResult"
    "FFIHeaderPlan"
    "FFIDecompResult"
)

violations=0

for struct in "${GUARDED_STRUCTS[@]}"; do
    # Look for ngx_memzero(..., sizeof(struct <Name>)) or
    # ngx_memzero(..., sizeof(<Name>)) patterns in production source
    matches=$(grep -rn "ngx_memzero\|memset" "${SRC_DIR}" 2>/dev/null \
        | grep -v "_test\\.c" \
        | grep -i "${struct}" || true)
    if [[ -n "${matches}" ]]; then
        echo "VIOLATION: Direct memset/ngx_memzero on ${struct} (use init helper instead):" >&2
        echo "${matches}" >&2
        violations=$((violations + 1))
    fi
done

if [[ ${violations} -gt 0 ]]; then
    echo >&2
    echo "FAIL: ${violations} FFI struct init violation(s) found." >&2
    echo "Use the Rust-provided init helpers instead of ngx_memzero/memset." >&2
    exit 1
fi

echo "PASS: No direct memset/ngx_memzero on guarded FFI structs in production code."
exit 0
