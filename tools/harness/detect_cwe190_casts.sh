#!/bin/bash
#
# CWE-190 Integer Overflow Detection Script
#
# Scans C source files for potentially unsafe ssize_t->size_t casts and
# narrowing conversions that may cause integer overflow (CWE-190).
#
# Flagged patterns:
#   (a) (size_t) applied to a variable of ssize_t/ngx_int_t/off_t type
#       without a preceding non-negative guard (< 0 check)
#   (b) (uint32_t)/(uint8_t)/(uInt)/(int) applied to a value of
#       size_t/ngx_uint_t/ssize_t type without an upper-bound guard
#   (c) Direct ngx_parse_size() result cast to (size_t) instead of
#       using ngx_http_markdown_dynconf_parse_size_safe()
#
# Usage: bash tools/harness/detect_cwe190_casts.sh [directory]
#   directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no findings (or only known-allowlisted patterns)
#   1 — one or more findings requiring review
#

set -euo pipefail

readonly SRC_DIR="${1:-components/nginx-module/src}"
readonly SCRIPT_NAME="$(basename "$0")"

# Files where (size_t) NGX_ERROR is allowed (config handlers return
# NGX_ERROR as size_t sentinel, which is intentional).
readonly ALLOWED_SIZE_T_NGX_ERROR_FILES=(
    "ngx_http_markdown_config_handlers_impl.h"
)

errors=0
warnings=0

echo "=== CWE-190 Integer Overflow Detection ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "" >&2

# ── Pattern (a): ssize_t/ngx_int_t/off_t → (size_t) without guard ──
#
# Look for (size_t) casts of variables that commonly hold ssize_t values
# (parsed, raw, result from ngx_parse_size, etc.) and flag lines that
# lack a nearby guard within 8 preceding lines.
#
# Recognized guard patterns:
#   - Non-negative check: < 0, >= 0, != NGX_ERROR, == NGX_ERROR
#   - Upper-bound check: > NGX_MAX_SIZE_T_VALUE, > max_size_t,
#     > SIZE_MAX, > UINT_MAX, > ..._MAX, > ..._LIMIT
#   - Range check: NGX_OK return from validation helper
#   - Explicit allowlist annotations: /* CWE-190:guarded */

readonly PATTERN_SSIZE_TO_SIZE='\(size_t\)[[:space:]]*(parsed|raw|rc|len|n)[^a-zA-Z]'

# ── Pattern (b): narrowing casts to smaller integer types ──
#
# (uint32_t), (uint8_t), (uInt), (int) applied to size_t or
# ngx_uint_t values without an upper-bound comparison.

readonly NARROW_TYPES='uint32_t|uint8_t|uInt|int'
readonly WIDE_TYPES='size_t|ngx_uint_t|off_t|ngx_int_t'

# ── Known guarded cast patterns (file:line → guard description) ──
#
# These are casts that ARE guarded but whose guard relationship is
# too complex for the simple line-proximity heuristic to detect.
# Format: "basename:line_number:guard_description"
readonly GUARDED_CAST_ALLOWLIST=(
    "ngx_http_markdown_config_handlers_impl.h:107:raw>NGX_MAX_SIZE_T_VALUE before value=(size_t)raw"
    "ngx_http_markdown_dynconf_impl.h:879:(size_t)parsed>max_size_t before *out=(size_t)parsed"
)

# ── Known safe cast patterns (file:line → safety reason) ──
#
# These are casts that are safe for reasons the detector cannot
# infer from local context alone.  Common reasons:
#   - Cast of a compile-time constant (no runtime overflow possible)
#   - Cast of a value that is already size_t/uintptr_t (identity cast)
#   - Cast used in a guard comparison itself (not a data conversion)
#   - Cast with a guard on the same line (ternary / inline check)
readonly SAFE_CAST_ALLOWLIST=(
    "ngx_http_markdown_otel_impl.h:853:compile-time constant NGX_HTTP_MARKDOWN_OTEL_TRACE_ID_LEN"
    "ngx_http_markdown_otel_impl.h:855:compile-time constant NGX_HTTP_MARKDOWN_OTEL_SPAN_ID_LEN"
    "ngx_http_markdown_config_handlers_impl.h:71:NGX_ERROR sentinel return (not a data cast)"
    "ngx_http_markdown_config_handlers_impl.h:76:NGX_ERROR sentinel return (not a data cast)"
    "ngx_http_markdown_config_handlers_impl.h:104:NGX_ERROR sentinel return (not a data cast)"
    "ngx_http_markdown_config_handlers_impl.h:678:NGX_ERROR sentinel comparison (not a data cast)"
    "ngx_http_markdown_decompression.c:213:UINT_MAX constant clamp guard itself"
    "ngx_http_markdown_decompression.c:214:UINT_MAX constant clamp assignment"
    "ngx_http_markdown_streaming_decomp_impl.h:34:guard comparison val>(size_t)UINT_MAX"
    "ngx_http_markdown_conversion_impl.h:770:guarded by INT_MAX ternary on same line"
    "ngx_http_markdown_conversion_impl.h:1164:size_t+size_t identity cast (feed_len+markdown_len)"
    "ngx_http_markdown_conversion_impl.h:1336:size_t identity cast for debug log format"
    "ngx_http_markdown_conversion_impl.h:1352:size_t+size_t identity cast (out_len+markdown_len)"
    "ngx_http_markdown_conversion_impl.h:1363:size_t identity cast for debug log format"
    "ngx_http_markdown_accept.c:137:bounded ternary produces 0 or 1 (always fits uint8_t)"
    "ngx_http_markdown_diagnostics.c:742:ngx_uint_t(=uintptr_t) to size_t same-width identity cast"
    "ngx_http_markdown_config_handlers_impl.h:72:NGX_ERROR sentinel return (not a data cast)"
    "ngx_http_markdown_config_handlers_impl.h:77:NGX_ERROR sentinel return (not a data cast)"
    "ngx_http_markdown_config_handlers_impl.h:105:NGX_ERROR sentinel return (not a data cast)"
    "ngx_http_markdown_config_handlers_impl.h:108:strtoull result guarded by ERANGE+NGX_MAX_SIZE_T_VALUE check above"
    "ngx_http_markdown_config_handlers_impl.h:679:NGX_ERROR sentinel comparison (not a data cast)"
    "ngx_http_markdown_decompression.c:208:estimated already clamped by decompress_max_size (size_t) above"
    "ngx_http_markdown_payload_impl.h:856:uintptr_t→size_t same-width cast (FFI output_len)"
    "ngx_http_markdown_payload_impl.h:900:uintptr_t→size_t same-width cast (FFI output_len)"
    "ngx_http_markdown_payload_impl.h:906:uintptr_t→size_t same-width cast (FFI output_len)"
    "ngx_http_markdown_header_plan.c:152:uintptr_t→size_t same-width cast (FFI value_len for log)"
    "ngx_http_markdown_header_plan.c:304:uintptr_t→size_t same-width cast (FFI value_len for log)"
    "ngx_http_markdown_header_plan.c:428:uintptr_t→size_t same-width cast (FFI plan->count for log)"
    "ngx_http_markdown_header_plan.c:440:uintptr_t→size_t same-width cast (FFI plan->count for log)"
    "ngx_http_markdown_header_plan.c:484:uintptr_t→size_t same-width cast (loop index for log)"
    "ngx_http_markdown_header_plan.c:504:uintptr_t→size_t same-width cast (plan->count for log)"
    "ngx_http_markdown_conversion_impl.h:892:guarded by INT_MAX ternary on same line"
    "ngx_http_markdown_conversion_impl.h:1282:uintptr_t→size_t same-width cast (FFI feed_len+markdown_len)"
    "ngx_http_markdown_conversion_impl.h:1455:uintptr_t→size_t same-width cast (FFI peak_memory_estimate for log)"
    "ngx_http_markdown_conversion_impl.h:1471:uintptr_t→size_t same-width cast (FFI out_len+markdown_len)"
    "ngx_http_markdown_conversion_impl.h:1482:uintptr_t→size_t same-width cast (FFI markdown_len for log)"
)

# ── Pattern (c): direct ngx_parse_size() + (size_t) ──
# This is the most critical pattern: calling ngx_parse_size() and
# immediately casting the ssize_t return to (size_t) without the safe
# wrapper.

echo "--- Pattern (c): direct ngx_parse_size() + (size_t) cast ---" >&2

# Search for ngx_parse_size followed eventually by (size_t) cast
# without ngx_http_markdown_dynconf_parse_size_safe in between.
parse_size_hits=0
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    # Check if the same file uses the safe wrapper near this callsite (±10 lines)
    # or if this line is inside the safe wrapper implementation itself
    ctx_start=$((line - 10))
    if [[ "$ctx_start" -lt 1 ]]; then
        ctx_start=1
    fi
    ctx_end=$((line + 10))
    if sed -n "${ctx_start},${ctx_end}p" "$file" 2>/dev/null | grep -q 'dynconf_parse_size_safe'; then
        echo "  WARNING ${file}:${line} — ngx_parse_size used; safe wrapper exists nearby, verify this callsite uses it" >&2
        warnings=$((warnings + 1))
    else
        impl_start=$((line - 30))
        if [[ "$impl_start" -lt 1 ]]; then
            impl_start=1
        fi
        if sed -n "${impl_start},${line}p" "$file" 2>/dev/null | grep -qE 'ngx_http_markdown_dynconf_parse_size_safe\b'; then
            echo "  OK      ${file}:${line} — ngx_parse_size inside dynconf_parse_size_safe implementation" >&2
        else
            echo "  ERROR   ${file}:${line} — ngx_parse_size without dynconf_parse_size_safe wrapper" >&2
            errors=$((errors + 1))
        fi
    fi
    parse_size_hits=$((parse_size_hits + 1))
done < <(grep -rnE 'ngx_parse_size.*\(size_t|\(size_t.*ngx_parse_size' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null | grep -vE ':\s*/\*|:\s*\*|:\s*//' || true)

if [[ "$parse_size_hits" -eq 0 ]]; then
    echo "  (none found)" >&2
fi
echo "" >&2

# ── Pattern (b): narrowing casts without upper-bound guard ──
echo "--- Pattern (b): narrowing casts without upper-bound guard ---" >&2

narrow_hits=0
# Match (uint32_t), (uint8_t), (uInt) casts of expressions that look
# like they hold size_t-range values
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    # Check for upper-bound guard in the same line or 3 preceding lines
    context_start=$((line - 3))
    if [[ "$context_start" -lt 1 ]]; then
        context_start=1
    fi
    has_guard=0
    if sed -n "${context_start},${line}p" "$file" 2>/dev/null | grep -qiE 'UINT_MAX|UINT32_MAX|INT_MAX|>.*max|>.*limit|>.*bound'; then
        has_guard=1
    fi
    # Bounded ternary: (type) (expr ? small_const : small_const)
    # where both branches produce values that trivially fit the target type
    if echo "$content" | grep -qE '\? [01] : [01]\)|\? 1U? : 0U?\)|\? 0U? : 1U?\)'; then
        has_guard=1
    fi
    if [[ "$has_guard" -eq 1 ]]; then
        echo "  OK      ${file}:${line} — narrowing cast with guard" >&2
    else
        echo "  WARNING ${file}:${line} — narrowing cast without visible upper-bound guard: ${content}" >&2
        warnings=$((warnings + 1))
    fi
    narrow_hits=$((narrow_hits + 1))
done < <(grep -rnE '\((uint32_t|uint8_t|uInt|int)\)[[:space:]]*\(' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null | grep -vE 'sizeof|offsetof|NGX_HTTP_MARKDOWN_' || true)

# Also check specific known-dangerous patterns: (size_t) NGX_ERROR
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    basename="$(basename "$file")"
    # Skip allowlisted files where (size_t) NGX_ERROR is an intentional sentinel
    skip=0
    for allowed in "${ALLOWED_SIZE_T_NGX_ERROR_FILES[@]}"; do
        if [[ "$basename" == "$allowed" ]]; then
            skip=1
            break
        fi
    done
    if [[ "$skip" -eq 1 ]]; then
        echo "  OK      ${file}:${line} — (size_t) NGX_ERROR in allowlisted config handler" >&2
        narrow_hits=$((narrow_hits + 1))
        continue
    fi
    echo "  ERROR   ${file}:${line} — (size_t) NGX_ERROR: negative ssize_t cast to size_t" >&2
    errors=$((errors + 1))
    narrow_hits=$((narrow_hits + 1))
done < <(grep -rn '(size_t) NGX_ERROR' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null || true)

if [[ "$narrow_hits" -eq 0 ]]; then
    echo "  (none found)" >&2
fi
echo "" >&2

# ── Pattern (a): ssize_t → (size_t) without non-negative guard ──
echo "--- Pattern (a): ssize_t/ngx_int_t → (size_t) without guard ---" >&2

ssize_hits=0
# Detect (size_t) explicit casts of ssize_t-typed variables
# (common names: parsed, raw, rc, n) and also = ngx_parse_size assignments
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    basename="$(basename "$file")"

    # Check explicit allowlist for known guarded patterns
    allowlisted=0
    for entry in "${GUARDED_CAST_ALLOWLIST[@]}"; do
        entry_file="$(echo "$entry" | cut -d: -f1)"
        entry_line="$(echo "$entry" | cut -d: -f2)"
        entry_desc="$(echo "$entry" | cut -d: -f3-)"
        if [[ "$basename" == "$entry_file" && "$line" == "$entry_line" ]]; then
            echo "  OK      ${file}:${line} — ssize_t→size_t cast in allowlisted guarded pattern (${entry_desc})" >&2
            allowlisted=1
            break
        fi
    done
    if [[ "$allowlisted" -eq 1 ]]; then
        ssize_hits=$((ssize_hits + 1))
        continue
    fi

    # Check safe-cast allowlist for known safe patterns
    for entry in "${SAFE_CAST_ALLOWLIST[@]}"; do
        entry_file="$(echo "$entry" | cut -d: -f1)"
        entry_line="$(echo "$entry" | cut -d: -f2)"
        entry_desc="$(echo "$entry" | cut -d: -f3-)"
        if [[ "$basename" == "$entry_file" && "$line" == "$entry_line" ]]; then
            echo "  OK      ${file}:${line} — safe cast (${entry_desc})" >&2
            allowlisted=1
            break
        fi
    done
    if [[ "$allowlisted" -eq 1 ]]; then
        ssize_hits=$((ssize_hits + 1))
        continue
    fi

    # Check for guard in preceding 8 lines (expanded from 5 to capture
    # more complex guard-to-cast relationships like upper-bound checks)
    context_start=$((line - 8))
    if [[ "$context_start" -lt 1 ]]; then
        context_start=1
    fi
    has_guard=0
    context_block="$(sed -n "${context_start},${line}p" "$file" 2>/dev/null)"
    if echo "$context_block" | grep -qiE '< 0|>= 0|!= NGX_ERROR|== NGX_ERROR|NGX_OK'; then
        has_guard=1
    fi
    if echo "$context_block" | grep -qiE '> NGX_MAX_SIZE_T_VALUE|> max_size_t|> SIZE_MAX|> UINT_MAX|> [A-Z_]+_MAX|> [A-Z_]+_LIMIT|\(size_t\)[[:space:]]*\w+[[:space:]]*>[[:space:]]*max'; then
        has_guard=1
    fi
    if echo "$context_block" | grep -qiE '/\* CWE-190:guarded \*/'; then
        has_guard=1
    fi
    if [[ "$has_guard" -eq 1 ]]; then
        echo "  OK      ${file}:${line} — ssize_t→size_t cast with guard" >&2
    else
        echo "  WARNING ${file}:${line} — ssize_t→size_t cast without visible guard" >&2
        warnings=$((warnings + 1))
    fi
    ssize_hits=$((ssize_hits + 1))
done < <(grep -rnE '\(size_t\)[[:space:]]*[A-Za-z_]' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null || true)

if [[ "$ssize_hits" -eq 0 ]]; then
    echo "  (none found)" >&2
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

echo "PASS: no CWE-190 findings" >&2
exit 0
