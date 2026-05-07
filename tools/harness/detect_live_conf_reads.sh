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
#   - log_decision_debug: known P1 gap (reported as WARNING)
#   - is_enabled: known P0 gap (reported as ERROR)
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
    if [ -z "$match" ]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    basename="$(basename "$file")"

    # Skip allowlisted files
    for allowed in "${ALLOWED_FILES[@]}"; do
        if [ "$basename" = "$allowed" ]; then
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
    if [ "$func_start" -lt 1 ]; then
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
    # to direct conf-> reads will be caught here.
    if echo "$context_lines" | grep -q 'log_decision_debug'; then
        echo "  WARNING ${file}:${line} — log_decision_debug() regression: reads live conf->: ${content}" >&2
        warnings=$((warnings + 1))
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

if [ "$hits" -eq 0 ]; then
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
if [ -f "$is_enabled_file" ]; then
    # Find the function body range of ngx_http_markdown_is_enabled
    is_enabled_start="$(grep -n '^ngx_http_markdown_is_enabled(' "$is_enabled_file" 2>/dev/null | head -1 | cut -d: -f1)"
    if [ -n "$is_enabled_start" ]; then
        # Find the closing brace (simple heuristic: next ^} after function start)
        is_enabled_end="$(sed -n "$((is_enabled_start + 1)),\$p" "$is_enabled_file" 2>/dev/null \
            | grep -n '^}' | head -1 | cut -d: -f1)"
        if [ -n "$is_enabled_end" ]; then
            is_enabled_end=$((is_enabled_start + is_enabled_end))
            # Check for direct conf->enabled or conf->enabled_source reads
            # (excluding the ternary fallback pattern "eff != NULL ? eff->... : conf->...")
            while IFS= read -r match; do
                if [ -z "$match" ]; then
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

# ── Summary ──
echo "=== Summary ===" >&2
echo "  Errors:   ${errors}" >&2
echo "  Warnings: ${warnings}" >&2
echo "" >&2

if [ "$errors" -gt 0 ]; then
    echo "FAIL: ${errors} error(s) found — fix before merge" >&2
    exit 1
fi

if [ "$warnings" -gt 0 ]; then
    echo "PASS with warnings: ${warnings} warning(s) — review recommended" >&2
    exit 0
fi

echo "PASS: no live conf read findings" >&2
exit 0
