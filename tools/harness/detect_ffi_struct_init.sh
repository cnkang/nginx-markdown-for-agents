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
#   - FFIConditionalResult     → markdown_conditional_result_init()
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
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${1:-${REPO_ROOT}/components/nginx-module/src}"
. "${SCRIPT_DIR}/collect_files.sh"

# Structs that have Rust-provided init helpers
GUARDED_STRUCTS=(
    "MarkdownOptions"
    "MarkdownResult"
    "FFIConditionalResult"
    "FFIHeaderPlan"
    "FFIDecompResult"
)

violations=0

if [[ ! -d "${SRC_DIR}" || ! -r "${SRC_DIR}" ]]; then
    echo "ERROR: source directory is missing or not readable: ${SRC_DIR}" >&2
    exit 2
fi
file_list="$(mktemp "${TMPDIR:-/tmp}/ffi-struct-files.XXXXXX")" || {
    echo "ERROR: cannot create the source file list" >&2
    exit 2
}
trap 'rm -f "$file_list"' EXIT
# Test translation units are excluded at enumeration time: fixture files
# intentionally embed ngx_memzero/memset shapes to exercise this detector, so
# auditing them reports the fixture as a production violation (pre-rc9 the
# exclusion lived on each per-file grep; the NUL-safe collector switch dropped
# it for Phase 2).
if ! harness_collect_find0 "$file_list" "${SRC_DIR}" \
    \( -name '*.c' -o -name '*.h' \) -type f ! -name '*_test.c' 2>/dev/null; then
    echo "ERROR: cannot enumerate source files in ${SRC_DIR}" >&2
    exit 2
fi

# ── Phase 1: Direct struct-name on memzero/memset line ──
for struct in "${GUARDED_STRUCTS[@]}"; do
    matches_raw_rc=0
    matches_raw="$(grep -rn -E "ngx_memzero|memset" "${SRC_DIR}" 2>/dev/null)" \
        || matches_raw_rc=$?
    if [[ "$matches_raw_rc" -gt 1 ]]; then
        echo "ERROR: grep failed scanning ${SRC_DIR}" >&2
        exit 2
    fi
    # The filters run on the captured text, so a scan failure above can no
    # longer be masked by a downstream no-match exit status.
    matches="$(printf '%s\n' "$matches_raw" \
        | grep -v "_test\.c" \
        | grep -vE '(^|:)[0-9]+:[[:space:]]*(/\*|\*|//)' \
        | grep -i "${struct}" || true)"
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
while IFS= read -r -d '' src_file; do
    for struct in "${GUARDED_STRUCTS[@]}"; do
        # Find variable declarations: "struct <Name> <varname>",
        # "struct <Name> *<varname>", or typedef aliases such as
        # "<Name> <varname>".
        # Extract variable names from declarations
        names_raw_rc=0
        names_raw="$(grep -nE "(struct[[:space:]]+)?${struct}[[:space:]]+\**[[:space:]]*[a-zA-Z_][a-zA-Z0-9_]*" "${src_file}" 2>/dev/null)" \
            || names_raw_rc=$?
        if [[ "$names_raw_rc" -gt 1 ]]; then
            echo "ERROR: grep failed reading ${src_file} for ${struct} declarations" >&2
            exit 2
        fi
        var_names="$(printf '%s\n' "$names_raw" \
            | grep -vE '(^|:)[0-9]+:[[:space:]]*(/\*|\*|//)|typedef|#include' \
            | sed -E -n \
                -e 's/.*struct[[:space:]]+'"${struct}"'[[:space:]]+\**[[:space:]]*([a-zA-Z_][a-zA-Z0-9_]*).*/\1/p' \
                -e 's/.*(^|[^a-zA-Z0-9_])'"${struct}"'[[:space:]]+\**[[:space:]]*([a-zA-Z_][a-zA-Z0-9_]*).*/\2/p' \
            | sort -u || true)"
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
            memzero_raw_rc=0
            memzero_raw="$(grep -n -E "ngx_memzero|memset" "${src_file}" 2>/dev/null)" \
                || memzero_raw_rc=$?
            if [[ "$memzero_raw_rc" -gt 1 ]]; then
                echo "ERROR: grep failed reading ${src_file} for ${varname} memzero calls" >&2
                exit 2
            fi
            memzero_hits="$(printf '%s\n' "$memzero_raw" \
                | grep -v "_test\.c" \
                | grep -vE '(^|:)[0-9]+:[[:space:]]*(/\*|\*|//)' \
                | grep -E "[&*][[:space:]]*${varname}([^a-zA-Z0-9_]|$)" \
                | grep "sizeof" || true)"
            if [[ -n "${memzero_hits}" ]]; then
                echo "VIOLATION [phase2]: ngx_memzero/memset on ${struct} variable '${varname}' in ${src_file}:" >&2
                echo "${memzero_hits}" >&2
                violations=$((violations + 1))
            fi
        done <<< "${var_names}"
    done
done < "$file_list"

if [[ ${violations} -gt 0 ]]; then
    echo >&2
    echo "FAIL: ${violations} FFI struct init violation(s) found." >&2
    echo "Use the Rust-provided init helpers instead of ngx_memzero/memset." >&2
    exit 1
fi

echo "PASS: No direct memset/ngx_memzero on guarded FFI structs in production code."
exit 0
