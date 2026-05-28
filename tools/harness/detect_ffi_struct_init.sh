#!/usr/bin/env bash
#
# detect_ffi_struct_init.sh — Gate: FFI structs with init helpers must not
# be initialized via ngx_memzero/memset in production code.
#
# Rule 15 (ffi-crosslang): Prefer helper functions over literal FFI struct init.
#
# Structs with Rust-provided init helpers:
#   - MarkdownOptions          → markdown_options_init()
#   - MarkdownResult           → markdown_result_init()
#   - FFIAcceptResult          → markdown_accept_result_init()
#   - FFIConditionalResult     → markdown_conditional_result_init()
#   - FFIDecisionResult        → markdown_decision_result_init()
#   - FFIHeaderPlan            → markdown_header_plan_init()
#   - FFIDecompResult          → markdown_decomp_result_init()
#
# Detection strategy (two phases):
#   Phase 1: Direct struct-name match on the memzero/memset line
#            e.g. ngx_memzero(&opts, sizeof(struct MarkdownOptions));
#   Phase 2: Variable-name tracking — find declarations of guarded
#            structs, collect variable names, then check if those
#            variables appear in ngx_memzero/memset calls
#            e.g. struct MarkdownResult result;
#                 ngx_memzero(&result, sizeof(result));
#
# Exit codes:
#   0 = no violations found
#   1 = violations detected
#
set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="${SCRIPT_DIR}/../.."
SRC_DIR="${1:-${REPO_ROOT}/components/nginx-module/src}"

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

# ── Phase 1: Direct struct-name on memzero/memset line ──
for struct in "${GUARDED_STRUCTS[@]}"; do
    matches=$(grep -rn -E "ngx_memzero|memset" "${SRC_DIR}" 2>/dev/null \
        | grep -v "_test\\.c" \
        | grep -vE '(^|:)[0-9]+:[[:space:]]*(/\*|\*|//)' \
        | grep -i "${struct}" || true)
    if [[ -n "${matches}" ]]; then
        echo "VIOLATION [phase1]: Direct memset/ngx_memzero on ${struct}:" >&2
        echo "${matches}" >&2
        violations=$((violations + 1))
    fi
done

# ── Phase 2: Variable-name tracking ──
# For each production source file, find declarations of guarded structs,
# extract variable names, then check if those variables are passed to
# ngx_memzero/memset anywhere in the same file.
while IFS= read -r src_file; do
    for struct in "${GUARDED_STRUCTS[@]}"; do
        # Find variable declarations: "struct <Name> <varname>",
        # "struct <Name> *<varname>", or typedef aliases such as
        # "<Name> <varname>".
        # Extract variable names from declarations
        var_names=$(grep -nE "(struct[[:space:]]+)?${struct}[[:space:]]+\**[[:space:]]*[a-zA-Z_][a-zA-Z0-9_]*" "${src_file}" 2>/dev/null \
            | grep -vE '(^|:)[0-9]+:[[:space:]]*(/\*|\*|//)|typedef|#include' \
            | sed -E -n \
                -e 's/.*struct[[:space:]]+'"${struct}"'[[:space:]]+\**[[:space:]]*([a-zA-Z_][a-zA-Z0-9_]*).*/\1/p' \
                -e 's/.*(^|[^a-zA-Z0-9_])'"${struct}"'[[:space:]]+\**[[:space:]]*([a-zA-Z_][a-zA-Z0-9_]*).*/\2/p' \
            | sort -u \
            || true)
        if [[ -z "${var_names}" ]]; then
            continue
        fi
        # For each variable name, check if it appears in a memzero/memset call
        while IFS= read -r varname; do
            if [[ -z "${varname}" ]]; then
                continue
            fi
            # Skip common false positives: function parameters, pointer types
            # Look for: ngx_memzero(&varname, sizeof(varname)) or
            #           ngx_memzero(&varname, sizeof(*varname)) or
            #           memset(&varname, 0, sizeof(varname))
            memzero_hits=$(grep -n -E "ngx_memzero|memset" "${src_file}" 2>/dev/null \
                | grep -v "_test\\.c" \
                | grep -vE '(^|:)[0-9]+:[[:space:]]*(/\*|\*|//)' \
                | grep -E "[&*][[:space:]]*${varname}([^a-zA-Z0-9_]|$)" \
                | grep "sizeof" || true)
            if [[ -n "${memzero_hits}" ]]; then
                echo "VIOLATION [phase2]: ngx_memzero/memset on ${struct} variable '${varname}' in ${src_file}:" >&2
                echo "${memzero_hits}" >&2
                violations=$((violations + 1))
            fi
        done <<< "${var_names}"
    done
done < <(find "${SRC_DIR}" -name '*.c' -o -name '*.h' | grep -v "_test\\.c" | sort)

if [[ ${violations} -gt 0 ]]; then
    echo >&2
    echo "FAIL: ${violations} FFI struct init violation(s) found." >&2
    echo "Use the Rust-provided init helpers instead of ngx_memzero/memset." >&2
    exit 1
fi

echo "PASS: No direct memset/ngx_memzero on guarded FFI structs in production code."
exit 0
