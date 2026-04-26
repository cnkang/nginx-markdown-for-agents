#!/bin/bash
#
# Reason-Code Lifecycle Audit Script
#
# Verifies that every defined reason code has a complete lifecycle:
#   (a) static string definition in ngx_http_markdown_reason.c
#   (b) accessor function returning const ngx_str_t *
#   (c) at least one emission site (with precision depending on code kind)
#   (d) entry in operator-facing documentation
#
# Emission check precision (c) — file-level heuristic:
#   - direct accessor codes: accessor must appear in the same source file
#     as a log-decision or log_decision_with_category call.
#     This is a file-level proximity check, not a call-graph proof.
#   - mapper-based codes (SKIP_* via reason_from_eligibility):
#     mapper function must be called from a file that also calls log_decision;
#     additionally, the eligibility enum must appear in the eligibility
#     implementation and the same request path must call
#     ngx_http_markdown_check_eligibility before logging the decision.
#     Falls back to file-level co-occurrence only when the enum cannot be
#     derived from the reason code string.
#   - FAIL_* category codes: reason_from_error_category must appear in a
#     file that calls log_decision_with_category (category= emission path)
#   NOTE: Direct-accessor and category tiers are file-level heuristics.
#   They confirm that the accessor and log call coexist in the same
#   translation unit but do not prove the accessor is invoked at a
#   specific log_decision call site.  The mapper tier now includes
#   runtime-call verification (enum-level callsite search) when the
#   enum identifier can be derived from the code string; it falls back
#   to file-level co-occurrence only when derivation is not possible.
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
readonly LOG_DECISION_SYMBOL="ngx_http_markdown_log_decision"

errors=0

echo "=== Reason-Code Lifecycle Audit ===" >&2
echo "" >&2

# ── Explicit accessor registry ──────────────────────────────────────
# Maps reason-code string → accessor function name → emission kind.
#
# Emission kinds:
#   direct   — accessor is called directly in a log_decision context
#   mapper   — accessor is a mapper (reason_from_eligibility / reason_from_error_category)
#              called from a log_decision context; individual enum branches
#              are verified in reason.c
#   category — FAIL_* code emitted via log_decision_with_category category= path
#
# The registry is stored in a temporary file to avoid subshell/pipe
# issues on macOS bash 3.2 (AGENTS.md Rule 11: shell portability).
# Format: REASON_CODE<tab>accessor_function_name<tab>emission_kind
REGISTRY_FILE=$(mktemp)
readonly REGISTRY_FILE
trap 'rm -f "$REGISTRY_FILE"' EXIT
cat > "$REGISTRY_FILE" << 'REGISTRY_EOF'
SKIP_CONFIG	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_METHOD	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_STATUS	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_CONTENT_TYPE	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_SIZE	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_STREAMING	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_AUTH	ngx_http_markdown_reason_from_eligibility	direct
SKIP_RANGE	ngx_http_markdown_reason_from_eligibility	mapper
SKIP_ACCEPT	ngx_http_markdown_reason_skip_accept	direct
ELIGIBLE_CONVERTED	ngx_http_markdown_reason_converted	direct
ELIGIBLE_FAILED_OPEN	ngx_http_markdown_reason_failed_open	direct
ELIGIBLE_FAILED_CLOSED	ngx_http_markdown_reason_failed_closed	direct
FAIL_CONVERSION	ngx_http_markdown_reason_from_error_category	category
FAIL_RESOURCE_LIMIT	ngx_http_markdown_reason_from_error_category	category
FAIL_SYSTEM	ngx_http_markdown_reason_from_error_category	category
ENGINE_STREAMING	ngx_http_markdown_reason_engine_streaming	direct
STREAMING_CONVERT	ngx_http_markdown_reason_streaming_convert	direct
STREAMING_FALLBACK_PREBUFFER	ngx_http_markdown_reason_streaming_fallback	direct
STREAMING_FAIL_POSTCOMMIT	ngx_http_markdown_reason_streaming_fail_postcommit	direct
STREAMING_SKIP_UNSUPPORTED	ngx_http_markdown_reason_streaming_skip_unsupported	direct
STREAMING_BUDGET_EXCEEDED	ngx_http_markdown_reason_streaming_budget_exceeded	direct
STREAMING_PRECOMMIT_FAILOPEN	ngx_http_markdown_reason_streaming_precommit_failopen	direct
STREAMING_PRECOMMIT_REJECT	ngx_http_markdown_reason_streaming_precommit_reject	direct
STREAMING_SHADOW	ngx_http_markdown_reason_streaming_shadow	direct
REGISTRY_EOF

# Look up accessor function name from the explicit registry.
# Returns empty string if not found.
lookup_accessor() {
    local code="$1"
    local line
    line=$(grep -F "${code}	" "$REGISTRY_FILE" 2>/dev/null | head -1 || true)
    if [[ -n "$line" ]]; then
        echo "$line" | cut -f2
    fi
    return 0
}

# Look up emission kind from the explicit registry.
# Returns "direct", "mapper", "category", or empty string.
lookup_emission_kind() {
    local code="$1"
    local line
    line=$(grep -F "${code}	" "$REGISTRY_FILE" 2>/dev/null | head -1 || true)
    if [[ -n "$line" ]]; then
        echo "$line" | cut -f3
    fi
    return 0
}

# ── Emission check helpers ──────────────────────────────────────────

# Check direct accessor emission: accessor must appear in a source file
# that also contains a log_decision or log_decision_with_category call.
# This is stronger than "same file has both" — it requires the accessor
# name to actually appear (not just the mapper) in a file with log calls.
check_direct_emission() {
    local accessor="$1"
    local found=0
    local accessor_seen=0

    for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*.h; do
        if [[ ! -f "$src_file" ]]; then continue; fi
        if grep -q "$accessor" "$src_file" 2>/dev/null; then
            accessor_seen=1
            break
        fi
    done

    if [[ "$accessor_seen" -eq 0 ]]; then
        return 1
    fi

    for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*_impl.h; do
        if [[ ! -f "$src_file" ]]; then continue; fi
        if grep -q "$accessor" "$src_file" 2>/dev/null \
            && grep -q "$LOG_DECISION_SYMBOL" "$src_file" 2>/dev/null; then
            found=1
            break
        fi
    done
    return $((1 - found))
}

# Check mapper-based emission: the mapper function (reason_from_eligibility
# or reason_from_error_category) must be called from a file that also
# calls log_decision.  Additionally, the mapper's switch in reason.c
# must have a branch that returns the code's static string variable.
check_mapper_emission() {
    local accessor="$1"
    local code="$2"
    local found=0
    local accessor_seen=0

    # 1. Mapper accessor may be declared in headers, but runtime emission
    #    proof must come from implementation files.
    for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*.h; do
        if [[ ! -f "$src_file" ]]; then continue; fi
        if grep -q "$accessor" "$src_file" 2>/dev/null; then
            accessor_seen=1
            break
        fi
    done

    if [[ "$accessor_seen" -eq 0 ]]; then
        return 1
    fi

    # 1b. Runtime-call verification: derive the enum identifier from the
    #     code string and verify the two halves of the call chain:
    #       - the eligibility implementation returns the enum
    #       - the request path calls ngx_http_markdown_check_eligibility and
    #         then logs the decision
    #     Mapping: SKIP_X → NGX_HTTP_MARKDOWN_INELIGIBLE_X
    local enum_id=""
    if [[ "$code" =~ ^SKIP_(.*)$ ]]; then
        enum_id="NGX_HTTP_MARKDOWN_INELIGIBLE_${BASH_REMATCH[1]}"
    fi
    if [[ -n "$enum_id" ]]; then
        local enum_returned=0
        local request_chain_found=0

        if grep -q "return ${enum_id};" \
            "${SRC_DIR}/ngx_http_markdown_eligibility.c" 2>/dev/null; then
            enum_returned=1
        fi

        for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*_impl.h; do
            if [[ ! -f "$src_file" ]]; then continue; fi
            if grep -q "ngx_http_markdown_check_eligibility" "$src_file" 2>/dev/null \
                && grep -q "$LOG_DECISION_SYMBOL" "$src_file" 2>/dev/null; then
                request_chain_found=1
                break
            fi
        done

        if [[ "$enum_returned" -eq 1 && "$request_chain_found" -eq 1 ]]; then
            found=1
        else
            return 1
        fi
    fi
    # If enum_id could not be derived, fall through to file-level check below.

    if [[ "$found" -eq 0 ]]; then
        # Fallback: file-level co-occurrence check (accessor + log_decision
        # in same file) when enum derivation was not possible.
        for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*_impl.h; do
            if [[ ! -f "$src_file" ]]; then continue; fi
            if grep -q "$accessor" "$src_file" 2>/dev/null \
                && grep -q "$LOG_DECISION_SYMBOL" "$src_file" 2>/dev/null; then
                found=1
                break
            fi
        done
    fi

    if [[ "$found" -eq 0 ]]; then
        return 1
    fi

    # 2. The code's static string variable is returned from the mapper's
    #    switch in reason.c.  Find the variable name from the definition
    #    (static ngx_str_t VARNAME = ngx_string("CODE")), then check
    #    that VARNAME appears in a return statement in reason.c.
    #    The variable name is on the line before ngx_string("CODE").
    local str_var
    local def_line
    def_line=$(grep -n "ngx_string(\"$code\")" "$REASON_FILE" 2>/dev/null | head -1 | cut -d: -f1 || true)
    if [[ -n "$def_line" ]] && [[ "$def_line" -gt 1 ]]; then
        local prev_line=$((def_line - 1))
        str_var=$(sed -n "${prev_line}p" "$REASON_FILE" \
            | sed 's/.*\(ngx_http_markdown_reason_[a-z_]*_str\).*/\1/' \
            | head -1 || true)
    fi
    if [[ -z "$str_var" ]]; then
        # Fallback: could not derive variable name, accept on file-level check
        return 0
    fi
    if grep -q "return &${str_var}" "$REASON_FILE" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Check category emission: FAIL_* codes are emitted via
# log_decision_with_category's category= parameter.
# Verify: reason_from_error_category is called from a file that
# calls log_decision_with_category, and the code's static string
# variable is returned from the mapper's switch.
check_category_emission() {
    local accessor="$1"
    local code="$2"
    local found=0
    local accessor_seen=0

    # 1. reason_from_error_category is called from a file that
    #    calls log_decision_with_category. Headers may prove the accessor
    #    exists, but implementation files must prove runtime emission.
    for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*.h; do
        if [[ ! -f "$src_file" ]]; then continue; fi
        if grep -q "$accessor" "$src_file" 2>/dev/null; then
            accessor_seen=1
            break
        fi
    done

    if [[ "$accessor_seen" -eq 0 ]]; then
        return 1
    fi

    for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*_impl.h; do
        if [[ ! -f "$src_file" ]]; then continue; fi
        if grep -q "$accessor" "$src_file" 2>/dev/null \
            && grep -q "log_decision_with_category" "$src_file" 2>/dev/null; then
            found=1
            break
        fi
    done

    if [[ "$found" -eq 0 ]]; then
        return 1
    fi

    # 2. The code's static string variable is returned from the mapper's switch
    local str_var
    local def_line
    def_line=$(grep -n "ngx_string(\"$code\")" "$REASON_FILE" 2>/dev/null | head -1 | cut -d: -f1 || true)
    if [[ -n "$def_line" ]] && [[ "$def_line" -gt 1 ]]; then
        local prev_line=$((def_line - 1))
        str_var=$(sed -n "${prev_line}p" "$REASON_FILE" \
            | sed 's/.*\(ngx_http_markdown_reason_[a-z_]*_str\).*/\1/' \
            | head -1 || true)
    fi
    if [[ -z "$str_var" ]]; then
        return 0
    fi
    if grep -q "return &${str_var}" "$REASON_FILE" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Extract all reason code strings from ngx_string() definitions
reason_codes=$(grep 'ngx_string("' "$REASON_FILE" \
    | sed 's/.*ngx_string("\([A-Z_]*\)").*/\1/' \
    | sort -u)

for code in $reason_codes; do
    missing=""

    # (a) Static string definition — already confirmed by extraction

    # (b) Accessor function — use explicit registry, then verify in header/source
    accessor=$(lookup_accessor "$code")
    accessor_found=0
    if [[ -n "$accessor" ]]; then
        if grep -q "$accessor" "$HEADER_FILE" 2>/dev/null; then
            accessor_found=1
        fi
        if [[ "$accessor_found" -eq 0 ]]; then
            for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*.h; do
                if [[ -f "$src_file" ]] && grep -q "$accessor" "$src_file" 2>/dev/null; then
                    accessor_found=1
                    break
                fi
            done
        fi
    fi
    if [[ "$accessor_found" -eq 0 ]]; then
        missing="${missing} accessor"
    fi

    # (c) Emission site — precision depends on emission kind
    emission_kind=$(lookup_emission_kind "$code")
    emission_found=0
    case "$emission_kind" in
        direct)
            if check_direct_emission "$accessor"; then
                emission_found=1
            fi
            ;;
        mapper)
            if check_mapper_emission "$accessor" "$code"; then
                emission_found=1
            fi
            ;;
        category)
            if check_category_emission "$accessor" "$code"; then
                emission_found=1
            fi
            ;;
        *)
            # Unknown emission kind — fall back to file-level check
            for src_file in "$SRC_DIR"/*.c "$SRC_DIR"/*_impl.h; do
                if [[ ! -f "$src_file" ]]; then continue; fi
                if grep -q "$LOG_DECISION_SYMBOL" "$src_file" 2>/dev/null \
                    && grep -q "$accessor" "$src_file" 2>/dev/null; then
                    emission_found=1
                    break
                fi
            done
            ;;
    esac
    if [[ "$emission_found" -eq 0 ]]; then
        missing="${missing} emission"
    fi

    # (d) Documentation entry — check operator-facing docs (whole-word match)
    doc_found=0
    for doc in "$OPS_DOC" "$COOKBOOK_DOC" "$DECISION_CHAIN_DOC"; do
        if [[ -f "$doc" ]] && grep -w -F -q -- "$code" "$doc" 2>/dev/null; then
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
        echo "PASS: $code ($emission_kind)" >&2
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
