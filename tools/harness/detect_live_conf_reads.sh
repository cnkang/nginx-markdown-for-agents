#!/bin/bash
#
# Effective-Config Live Conf Read Detection Script
#
# Scans C source files in the request path for direct reads of
# dynconf-mutable fields (enabled, enabled_source, prune_noise,
# log_verbosity, memory_budget, streaming_budget) from live conf->
# instead of through effective_conf helpers.
#
# Per AGENTS.md Rule 34, request-path code must read these fields
# through ctx->effective_conf via ngx_http_markdown_effective_*()
# helpers, not directly from conf->.
#
# Allowed (allowlisted) locations:
#   - dynconf_impl.h: effective_* fallback, build_effective_conf,
#     snapshot_from_conf, apply_snapshot
#   - config_core_impl.h, config_handlers_impl.h: config init/merge
#   - config_impl.h: config merge helpers
#   - exports.h: public API wrappers
#   - log_decision_debug: fixed in v0.6.2 (reported as ERROR)
#   - is_enabled: fixed in v0.6.2 (reported as ERROR)
#
# Usage: bash tools/harness/detect_live_conf_reads.sh [directory]
#   directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no findings (or only allowlisted/known patterns)
#   1 — one or more findings requiring review
#

set -euo pipefail

readonly SRC_DIR="${1:-components/nginx-module/src}"

# Dynconf-mutable fields that must be read through effective_conf
# in request-path code.
readonly MUTABLE_FIELDS="enabled|enabled_source|prune_noise|log_verbosity|memory_budget|streaming_budget"
readonly MUTABLE_FIELDS_EXACT="^(enabled|enabled_source|prune_noise|log_verbosity|memory_budget|streaming_budget)$"

# Files where direct conf-> reads of mutable fields are allowed
# (configuration/initialization/snapshot code).
#
# config_core_impl.h: The entire file is allowlisted because it
# contains configuration init (create_conf), merge (merge_conf and
# sub-helpers), and diagnostic functions (log_merged_conf) that
# legitimately read/write live conf.  The request-path function
# is_enabled() is handled separately below via the P0 regression
# guard (it now accepts an eff parameter and reads through effective
# view; any future regression to direct conf-> reads will be caught
# by the is_enabled-specific check below).
readonly ALLOWED_FILES=(
    "ngx_http_markdown_dynconf_impl.h"
    "ngx_http_markdown_config_core_impl.h"
    "ngx_http_markdown_config_handlers_impl.h"
    "ngx_http_markdown_config_impl.h"
    "ngx_http_markdown_exports.h"
    "ngx_http_markdown_filter_module.c"
)

errors=0
warnings=0

echo "=== Effective-Config Live Conf Read Detection ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "Mutable fields: ${MUTABLE_FIELDS}" >&2
echo "" >&2

# Build allowlist basename pattern for grep exclusion
readonly ALLOW_PATTERN="$(IFS='|'; echo "${ALLOWED_FILES[*]}")"

# ── Find all conf-><mutable_field> reads ──
echo "--- Direct conf-><mutable_field> reads ---" >&2

hits=0
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    basename="$(basename "$file")"

    # Skip allowlisted files
    for allowed in "${ALLOWED_FILES[@]}"; do
        if [[ "$basename" == "$allowed" ]]; then
            continue 2
        fi
    done

    # Skip lines that are inside effective_* helper definitions
    # (these are the fallback paths when eff is NULL, which is allowed)
    if echo "$content" | grep -qE 'effective_'; then
        echo "  OK      ${file}:${line} — inside effective_* helper: ${content}" >&2
        hits=$((hits + 1))
        continue
    fi

    # Skip lines that are comments
    if echo "$content" | grep -qE '^\s*/\*|^\s*\*|^\s*//'; then
        continue
    fi

    # Check for regressed P0 gap: ngx_http_markdown_is_enabled
    # Regression guard: is_enabled() must read enabled/enabled_source
    # through eff parameter (fixed in v0.6.2).  Any future regression
    # to direct conf-> reads will be caught here.
    func_start=$((line - 50))
    if [[ "$func_start" -lt 1 ]]; then
        func_start=1
    fi
    context_lines="$(sed -n "${func_start},${line}p" "$file" 2>/dev/null)"
    if echo "$context_lines" | grep -q 'ngx_http_markdown_is_enabled'; then
        echo "  ERROR   ${file}:${line} — is_enabled() regression: reads live conf->: ${content}" >&2
        errors=$((errors + 1))
        hits=$((hits + 1))
        continue
    fi

    # Regression guard: log_decision_debug() must read enabled/enabled_source
    # through eff parameter (fixed in v0.6.2).  Any future regression
    # to direct conf-> reads is an error, not a warning.
    if echo "$context_lines" | grep -q 'log_decision_debug'; then
        echo "  ERROR   ${file}:${line} — log_decision_debug() regression: reads live conf->: ${content}" >&2
        errors=$((errors + 1))
        hits=$((hits + 1))
        continue
    fi

    # Check if the line is inside build_effective_conf or snapshot functions
    # These are legitimate conf-> reads to populate effective/snapshot
    if sed -n "${func_start},${line}p" "$file" 2>/dev/null | grep -qE 'build_effective_conf|dynconf_snapshot_from_conf|dynconf_apply_snapshot'; then
        echo "  OK      ${file}:${line} — inside snapshot/builder function: ${content}" >&2
        hits=$((hits + 1))
        continue
    fi

    # All other direct reads are errors
    echo "  ERROR   ${file}:${line} — request-path reads live conf->: ${content}" >&2
    errors=$((errors + 1))
    hits=$((hits + 1))
done < <(grep -rnE "conf->(enabled|enabled_source|prune_noise|log_verbosity|memory_budget|streaming_budget)[^_a-zA-Z]" "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null || true)

if [[ "$hits" -eq 0 ]]; then
    echo "  (none found)" >&2
fi
echo "" >&2

# ── Targeted regression guard for is_enabled() ──
# Even though config_core_impl.h is file-allowlisted above (because
# its init/merge/diag functions legitimately read live conf), we
# specifically check is_enabled() for regressions: it must read
# enabled/enabled_source through the eff parameter, not directly
# from conf->.
echo "--- Regression guard: is_enabled() must use effective view ---" >&2

is_enabled_file="${SRC_DIR}/ngx_http_markdown_config_core_impl.h"
if [[ -f "$is_enabled_file" ]]; then
    # Find the function body range of ngx_http_markdown_is_enabled
    is_enabled_start="$(grep -n '^ngx_http_markdown_is_enabled(' "$is_enabled_file" 2>/dev/null | head -1 | cut -d: -f1)"
    if [[ -n "$is_enabled_start" ]]; then
        # Find the closing brace (simple heuristic: next ^} after function start)
        is_enabled_end="$(sed -n "$((is_enabled_start + 1)),\$p" "$is_enabled_file" 2>/dev/null \
            | grep -n '^}' | head -1 | cut -d: -f1)"
        if [[ -n "$is_enabled_end" ]]; then
            is_enabled_end=$((is_enabled_start + is_enabled_end))
            # Check for direct conf->enabled or conf->enabled_source reads
            # (excluding the ternary fallback pattern "eff != NULL ? eff->... : conf->...")
            while IFS= read -r match; do
                if [[ -z "$match" ]]; then
                    continue
                fi
                iline="$(echo "$match" | cut -d: -f1)"
                icontent="$(echo "$match" | cut -d: -f2-)"
                # Allow the ternary fallback pattern ": conf->..."
                if echo "$icontent" | grep -qE '^\s*:\s*conf->'; then
                    echo "  OK      ${is_enabled_file}:${iline} — ternary fallback in eff check: ${icontent}" >&2
                    continue
                fi
                echo "  ERROR   ${is_enabled_file}:${iline} — is_enabled() regression: direct conf-> read: ${icontent}" >&2
                errors=$((errors + 1))
            done < <(sed -n "${is_enabled_start},${is_enabled_end}p" "$is_enabled_file" 2>/dev/null \
                | grep -nE 'conf->(enabled|enabled_source)[^_a-zA-Z]' || true)
        else
            echo "  WARNING could not find end of is_enabled() — skipping targeted check" >&2
            warnings=$((warnings + 1))
        fi
    else
        echo "  WARNING is_enabled() not found — skipping targeted check" >&2
        warnings=$((warnings + 1))
    fi
else
    echo "  WARNING ${is_enabled_file} not found — skipping targeted check" >&2
    warnings=$((warnings + 1))
fi
echo "" >&2

# ── Regression guard: active_snapshot must be read only once in header_filter ──
# The header_filter must copy active_snapshot into a function-local snap_copy
# exactly once (at the top), then bind that copy into ctx.  A second read of
# ngx_http_markdown_dynconf_watcher.active_snapshot after the initial capture
# creates a race window where a concurrent timer reload can swap the global
# snapshot between the two reads, causing the request to see inconsistent
# configuration.
echo "--- Regression guard: active_snapshot read-once in header_filter ---" >&2

request_impl="${SRC_DIR}/ngx_http_markdown_request_impl.h"
if [[ -f "$request_impl" ]]; then
    # Find the header_filter function body
    hf_start="$(grep -n 'ngx_http_markdown_header_filter(' "$request_impl" 2>/dev/null \
        | grep -v 'static' | head -1 | cut -d: -f1)"
    if [[ -z "$hf_start" ]]; then
        hf_start="$(grep -n 'ngx_http_markdown_header_filter(' "$request_impl" 2>/dev/null \
            | head -1 | cut -d: -f1)"
    fi
    if [[ -n "$hf_start" ]]; then
        # Find closing brace (next ^} at column 1 after start)
        hf_end="$(sed -n "$((hf_start + 1)),\$p" "$request_impl" 2>/dev/null \
            | grep -n '^}' | head -1 | cut -d: -f1)"
        if [[ -n "$hf_end" ]]; then
            hf_end=$((hf_start + hf_end))
            # Count active_snapshot reads (excluding comments) in the function body
            snap_reads="$(sed -n "${hf_start},${hf_end}p" "$request_impl" 2>/dev/null \
                | grep -vE '^\s*/\*|^\s*\*' \
                | grep -c 'ngx_http_markdown_dynconf_watcher\.active_snapshot' || true)"
            if [[ "$snap_reads" -eq 1 ]]; then
                echo "  OK      single active_snapshot read in header_filter (correct)" >&2
            elif [[ "$snap_reads" -eq 0 ]]; then
                echo "  WARNING no active_snapshot read in header_filter — dynconf may not be initialized" >&2
                warnings=$((warnings + 1))
            else
                echo "  ERROR   ${snap_reads} active_snapshot reads in header_filter — must read exactly once to avoid race window" >&2
                errors=$((errors + 1))
            fi
        else
            echo "  WARNING could not find end of header_filter — skipping active_snapshot check" >&2
            warnings=$((warnings + 1))
        fi
    else
        echo "  WARNING header_filter not found — skipping active_snapshot check" >&2
        warnings=$((warnings + 1))
    fi
else
    echo "  WARNING ${request_impl} not found — skipping active_snapshot check" >&2
    warnings=$((warnings + 1))
fi
echo "" >&2

# ── Regression guard: handle_ctx_alloc_failure must receive eff parameter ──
# After v0.6.2, handle_ctx_alloc_failure takes an eff parameter to avoid
# falling back to live conf in log_decision_with_category.  A call passing
# NULL (or omitting eff) is a regression.
echo "--- Regression guard: handle_ctx_alloc_failure must receive eff ---" >&2

if [[ -f "$request_impl" ]]; then
    # Find calls to handle_ctx_alloc_failure
    while IFS= read -r call_match; do
        if [[ -z "$call_match" ]]; then
            continue
        fi
        call_line="$(echo "$call_match" | cut -d: -f1)"
        call_content="$(echo "$call_match" | cut -d: -f2-)"
        # Check if the call passes NULL as the eff argument
        # Pattern: handle_ctx_alloc_failure(r, conf, NULL) or handle_ctx_alloc_failure(r, conf) (2-arg old form)
        if echo "$call_content" | grep -qE 'handle_ctx_alloc_failure\([^)]*,\s*NULL\s*\)' \
            || echo "$call_content" | grep -qE 'handle_ctx_alloc_failure\([^)]*,\s*conf\s*\)$'; then
            echo "  ERROR   ${request_impl}:${call_line} — handle_ctx_alloc_failure called with NULL/missing eff: ${call_content}" >&2
            errors=$((errors + 1))
        else
            echo "  OK      ${request_impl}:${call_line} — handle_ctx_alloc_failure receives eff: ${call_content}" >&2
        fi
    done < <(grep -n 'ngx_http_markdown_handle_ctx_alloc_failure' "$request_impl" 2>/dev/null \
        | grep -vE '^\s*/\*|^\s*\*|^.*static.*ngx_int_t' || true)
else
    echo "  WARNING ${request_impl} not found — skipping handle_ctx_alloc_failure check" >&2
    warnings=$((warnings + 1))
fi
echo "" >&2

# ── Regression guard: effective_conf must be copied from early_eff, not rebuilt ──
# After v0.6.2, ctx->effective_conf is set by copying early_eff directly
# (*ctx->effective_conf = early_eff) rather than calling build_effective_conf
# again (which would re-read the global active_snapshot or the separately-bound
# snapshot, both of which can drift).  A call to build_effective_conf inside
# header_filter after the initial build is a regression.
echo "--- Regression guard: no build_effective_conf re-invocation in header_filter ---" >&2

if [[ -f "$request_impl" ]]; then
    if [[ -n "$hf_start" ]] && [[ -n "$hf_end" ]]; then
        # Count build_effective_conf calls in header_filter body
        # (the initial build at the top is allowed; any after the snap_copy line are not)
        rebuild_count="$(sed -n "${hf_start},${hf_end}p" "$request_impl" 2>/dev/null \
            | grep -c 'ngx_http_markdown_build_effective_conf' || true)"
        if [[ "$rebuild_count" -le 1 ]]; then
            echo "  OK      at most 1 build_effective_conf call in header_filter (correct)" >&2
        else
            echo "  ERROR   ${rebuild_count} build_effective_conf calls in header_filter — must call at most once to avoid race" >&2
            errors=$((errors + 1))
        fi
    else
        echo "  WARNING header_filter range not determined — skipping build_effective_conf check" >&2
        warnings=$((warnings + 1))
    fi
else
    echo "  WARNING ${request_impl} not found — skipping build_effective_conf check" >&2
    warnings=$((warnings + 1))
fi
echo "" >&2

# ── Regression guard: build_effective_conf snapshot gated by dynconf_enabled ──
# After Finding 1 fix, header_filter must pass NULL snapshot to
# build_effective_conf when dynconf_enabled is false, preventing global
# snapshot values from leaking into non-dynconf locations.
# Pattern: build_effective_conf(..., conf->dynconf_enabled ? &snap_copy : NULL, ...)
echo "--- Regression guard: build_effective_conf snapshot gated by dynconf_enabled ---" >&2

if [[ -f "$request_impl" ]]; then
    if [[ -n "$hf_start" ]] && [[ -n "$hf_end" ]]; then
        # Check that build_effective_conf call includes dynconf_enabled guard.
        # The call may span multiple lines, so extract the entire function
        # body and search for dynconf_enabled near build_effective_conf.
        func_body="$(sed -n "${hf_start},${hf_end}p" "$request_impl" 2>/dev/null)"
        if echo "$func_body" | grep -A3 'ngx_http_markdown_build_effective_conf' | grep -qE 'dynconf_enabled'; then
            echo "  OK      build_effective_conf call gated by dynconf_enabled" >&2
        else
            echo "  ERROR   build_effective_conf not gated by dynconf_enabled — snapshot may leak to non-dynconf locations" >&2
            errors=$((errors + 1))
        fi
    else
        echo "  WARNING header_filter range not determined — skipping dynconf_enabled gate check" >&2
        warnings=$((warnings + 1))
    fi
else
    echo "  WARNING ${request_impl} not found — skipping dynconf_enabled gate check" >&2
    warnings=$((warnings + 1))
fi
echo "" >&2

# ── Regression guard: applied_mtime updated only on successful reload ──
# After Finding 2 fix, timer_handler must update applied_mtime only after
# a successful reload (RELOAD_APPLIED or RELOAD_NO_CHANGE), not on failure.
echo "--- Regression guard: applied_mtime updated only on successful reload ---" >&2

dynconf_impl="${SRC_DIR}/ngx_http_markdown_dynconf_impl.h"
if [[ -f "$dynconf_impl" ]]; then
    # Check that timer_handler contains applied_mtime assignment
    # guarded by success return codes
    if grep -q 'applied_mtime.*=.*last_mtime' "$dynconf_impl" 2>/dev/null; then
        # Verify the guard checks for RELOAD_APPLIED or RELOAD_NO_CHANGE
        if grep -A5 'applied_mtime' "$dynconf_impl" 2>/dev/null \
            | grep -qE 'RELOAD_APPLIED|RELOAD_NO_CHANGE'; then
            echo "  OK      applied_mtime assignment guarded by success return codes" >&2
        else
            echo "  ERROR   applied_mtime assignment not guarded by success codes — failed reload may confirm mtime" >&2
            errors=$((errors + 1))
        fi
    else
        echo "  ERROR   no applied_mtime assignment found — reload retry contract not enforced" >&2
        errors=$((errors + 1))
    fi

    # Check that timer_handler has retry-on-failed-reload logic
    if grep -q 'last_mtime.*!=.*applied_mtime' "$dynconf_impl" 2>/dev/null; then
        echo "  OK      retry-on-failed-reload logic present (last_mtime != applied_mtime)" >&2
    else
        echo "  ERROR   no retry-on-failed-reload logic — failed reloads will not be retried" >&2
        errors=$((errors + 1))
    fi
else
    echo "  WARNING ${dynconf_impl} not found — skipping applied_mtime check" >&2
    warnings=$((warnings + 1))
fi
echo "" >&2

# ── Summary ──
echo "=== Summary ===" >&2
echo "  Errors:   ${errors}" >&2
echo "  Warnings: ${warnings}" >&2
echo "" >&2

if [[ "$errors" -gt 0 ]]; then
    echo "FAIL: ${errors} error(s) found — fix before merge" >&2
    exit 1
fi

if [[ "$warnings" -gt 0 ]]; then
    echo "PASS with warnings: ${warnings} warning(s) — review recommended" >&2
    exit 0
fi

echo "PASS: no live conf read findings" >&2
exit 0
