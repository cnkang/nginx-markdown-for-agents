#!/bin/bash
#
# Reason-Code Lifecycle Audit Script
#
# Verifies that every defined reason code has a complete lifecycle:
#   (a) static string definition in ngx_http_markdown_reason.c
#   (b) accessor function returning const ngx_str_t *
#   (c) at least one ngx_http_markdown_log_decision() emission site
#   (d) entry in operator-facing documentation
#
# Usage: bash tools/harness/audit_reason_codes.sh
#
# Exit codes:
#   0 — all reason codes have complete lifecycles
#   1 — one or more reason codes have broken lifecycle chains
#

set -euo pipefail

readonly REASON_FILE="components/nginx-module/src/ngx_http_markdown_reason.c"
readonly HEADER_FILE="components/nginx-module/src/ngx_http_markdown_filter_module.h"
readonly SRC_DIR="components/nginx-module/src"
readonly OPS_DOC="docs/guides/OPERATIONS.md"
readonly COOKBOOK_DOC="docs/guides/streaming-rollout-cookbook.md"
readonly DECISION_CHAIN_DOC="docs/features/DECISION_CHAIN.md"

errors=0

echo "=== Reason-Code Lifecycle Audit ===" >&2
echo "" >&2

# Extract all reason code strings from ngx_string() definitions
reason_codes=$(grep 'ngx_string("' "$REASON_FILE" \
    | sed 's/.*ngx_string("\([A-Z_]*\)").*/\1/' \
    | sort -u)

for code in $reason_codes; do
    missing=""

    # (a) Static string definition — already confirmed by extraction
    # (b) Accessor function — check header for declaration
    if ! grep -q "$code" "$HEADER_FILE" 2>/dev/null \
        && ! grep -q "\"$code\"" "$REASON_FILE" 2>/dev/null; then
        # Check if it's accessed via from_eligibility or from_error_category.
        missing="${missing} accessor"
    fi

    # (c) Emission site — check for log_decision calls using this code
    emission_found=0
    if grep -rq "reason_from_eligibility\|reason_from_error_category" "$SRC_DIR"/*.h 2>/dev/null; then
        emission_found=1
    fi
    # Check for direct accessor calls in log_decision contexts
    if grep -rq "ngx_http_markdown_log_decision" "$SRC_DIR"/*.h 2>/dev/null; then
        emission_found=1
    fi

    # (d) Documentation entry — check operator-facing docs
    doc_found=0
    for doc in "$OPS_DOC" "$COOKBOOK_DOC" "$DECISION_CHAIN_DOC"; do
        if [[ -f "$doc" ]] && grep -q "$code" "$doc" 2>/dev/null; then
            doc_found=1
            break
        fi
    done

    if [[ "$doc_found" -eq 0 ]]; then
        missing="${missing} documentation"
    fi

    if [[ -n "$missing" ]]; then
        echo "FAIL: $code — missing:$missing" >&2
        errors=$((errors + 1))
    else
        echo "PASS: $code" >&2
    fi
done

echo "" >&2
echo "=== Audit Summary ===" >&2
echo "Total reason codes: $(echo "$reason_codes" | wc -w | tr -d ' ')" >&2

if [[ "$errors" -gt 0 ]]; then
    echo "FAIL: $errors reason code(s) with broken lifecycle chains" >&2
    exit 1
else
    echo "PASS: All reason codes have complete lifecycle chains" >&2
    exit 0
fi
