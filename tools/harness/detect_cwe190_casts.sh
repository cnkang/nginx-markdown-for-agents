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
readonly NONE_FOUND_MSG="  (none found)"

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

# ── Pattern (b): narrowing casts to smaller integer types ──
#
# (uint32_t), (uint8_t), (uInt), (int) applied to size_t or
# ngx_uint_t values without an upper-bound comparison.

# ── Known guarded cast patterns (file:pattern → guard description) ──
#
# These are casts that ARE guarded but whose guard relationship is
# too complex for the simple line-proximity heuristic to detect.
# Format: "basename<TAB>regex_pattern<TAB>guard_description"
# The regex_pattern is matched against the source line content.
readonly GUARDED_CAST_ALLOWLIST=(
    $'ngx_http_markdown_config_handlers_impl.h\traw>NGX_MAX_SIZE_T_VALUE.*value=.size_t.raw\traw>NGX_MAX_SIZE_T_VALUE before value=(size_t)raw'
    $'ngx_http_markdown_dynconf_impl.h\t.size_t.parsed.>.*max_size_t.*out=.size_t.parsed\t(size_t)parsed>max_size_t before *out=(size_t)parsed'
)

# ── Known safe cast patterns (file:pattern → safety reason) ──
#
# These are casts that are safe for reasons the detector cannot
# infer from local context alone.  Common reasons:
#   - Cast of a compile-time constant (no runtime overflow possible)
#   - Cast of a value that is already size_t/uintptr_t (identity cast)
#   - Cast used in a guard comparison itself (not a data conversion)
#   - Cast with a guard on the same line (ternary / inline check)
# Format: "basename<TAB>regex_pattern<TAB>safety_reason"
# The regex_pattern is matched against the source line content (not line number),
# so it survives code edits that shift line numbers.
readonly SAFE_CAST_ALLOWLIST=(
    # ── NGX_ERROR sentinel returns (not data casts) ──
    $'ngx_http_markdown_config_handlers_impl.h\treturn.*size_t.*NGX_ERROR\tNGX_ERROR sentinel return (not a data cast)'
    $'ngx_http_markdown_config_handlers_impl.h\t==.*size_t.*NGX_ERROR\tNGX_ERROR sentinel comparison (not a data cast)'
    $'ngx_http_markdown_dynconf_impl.h\treturn.*size_t.*NGX_ERROR\tNGX_ERROR sentinel return (not a data cast)'
    $'ngx_http_markdown_dynconf_impl.h\t==.*size_t.*NGX_ERROR\tNGX_ERROR sentinel comparison (not a data cast)'
    $'ngx_http_markdown_decompression.c\t==.*size_t.*NGX_ERROR\tNGX_ERROR sentinel comparison (not a data cast)'
    $'ngx_http_markdown_header_plan.c\t==.*size_t.*NGX_ERROR\tNGX_ERROR sentinel comparison (not a data cast)'
    # ── UINT_MAX clamp guards (the cast IS the guard) ──
    $'ngx_http_markdown_decompression.c\tsize_t.*UINT_MAX\tUINT_MAX constant clamp guard itself'
    $'ngx_http_markdown_streaming_decomp_impl.h\tsize_t.*UINT_MAX\tguard comparison val>(size_t)UINT_MAX'
    # ── INT_MAX ternary guards on same line ──
    $'ngx_http_markdown_conversion_impl.h\tINT_MAX.*ternary\tguarded by INT_MAX ternary on same line'
    $'ngx_http_markdown_conversion_impl.h\terror_len.*INT_MAX\tguarded by INT_MAX ternary on same line'
    # ── uintptr_t→size_t same-width identity casts (FFI fields) ──
    $'ngx_http_markdown_payload_impl.h\tsize_t.*output_len\tuintptr_t→size_t same-width cast (FFI output_len)'
    $'ngx_http_markdown_header_plan.c\tsize_t.*value_len\tuintptr_t→size_t same-width cast (FFI value_len for log)'
    $'ngx_http_markdown_header_plan.c\tsize_t.*plan->count\tuintptr_t→size_t same-width cast (FFI plan->count for log)'
    $'ngx_http_markdown_header_plan.c\tsize_t.*entry->value_len\tuintptr_t→size_t same-width cast (FFI value_len for log)'
    $'ngx_http_markdown_header_plan.c\tsize_t.*i[^a-zA-Z]\tuintptr_t→size_t same-width cast (loop index for log)'
    $'ngx_http_markdown_conversion_impl.h\tsize_t.*feed_len\tuintptr_t→size_t same-width cast (FFI feed_len)'
    $'ngx_http_markdown_conversion_impl.h\tsize_t.*markdown_len\tuintptr_t→size_t same-width cast (FFI markdown_len)'
    $'ngx_http_markdown_conversion_impl.h\tsize_t.*out_len\tuintptr_t→size_t same-width cast (FFI out_len)'
    $'ngx_http_markdown_conversion_impl.h\tsize_t.*base_url_len\tuintptr_t→size_t same-width cast after out_cap guard'
    $'ngx_http_markdown_conversion_impl.h\tsize_t.*peak_memory\tuintptr_t→size_t same-width cast (FFI peak_memory_estimate for log)'
    $'ngx_http_markdown_diagnostics.c\tsize_t.*decision_count\tngx_uint_t→size_t same-width identity cast'
    # ── Bounded ternary (always 0 or 1) ──
    $'ngx_http_markdown_accept.c\tsize_t.*accept.*ternary\tbounded ternary produces 0 or 1 (always fits uint8_t)'
    $'ngx_http_markdown_accept.c\taccept_encoding.*size_t\tbounded ternary produces 0 or 1 (always fits uint8_t)'
    # ── strtoull result already guarded by ERANGE+NGX_MAX_SIZE_T_VALUE ──
    $'ngx_http_markdown_config_handlers_impl.h\tvalue.*size_t.*raw\tstrtoull result guarded by ERANGE+NGX_MAX_SIZE_T_VALUE check above'
    # ── estimated already clamped by decompress_max_size ──
    $'ngx_http_markdown_decompression.c\tsize_t.*estimated\testimated already clamped by decompress_max_size (size_t) above'
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
        if sed -n "${impl_start},${line}p" "$file" 2>/dev/null \
            | grep -qE 'ngx_http_markdown_dynconf_parse_size_safe[[:space:]]*\('; then
            echo "  OK      ${file}:${line} — ngx_parse_size inside dynconf_parse_size_safe implementation" >&2
        else
            echo "  ERROR   ${file}:${line} — ngx_parse_size without dynconf_parse_size_safe wrapper" >&2
            errors=$((errors + 1))
        fi
    fi
    parse_size_hits=$((parse_size_hits + 1))
done < <(grep -rnE 'ngx_parse_size.*\(size_t|\(size_t.*ngx_parse_size' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null | grep -vE ':[[:space:]]*/\*|:[[:space:]]*\*|:[[:space:]]*//' || true)

if [[ "$parse_size_hits" -eq 0 ]]; then
    echo "$NONE_FOUND_MSG" >&2
fi
echo "" >&2

# ── Pattern (b): narrowing casts without upper-bound guard ──
echo "--- Pattern (b): narrowing casts without upper-bound guard ---" >&2

narrow_hits=0
# Match (uint32_t), (uint8_t), (uInt) casts of expressions that look
# like they hold size_t-range values
#
# Pattern-specific safe-cast allowlist (file<TAB>regex_pattern<TAB>safety_reason)
# for narrowing casts whose guard is in the calling function, not nearby.
readonly NARROW_SAFE_ALLOWLIST=(
    $'ngx_http_markdown_decompression.c\tavail_out.*uInt.*output_size.*used\toutput_size already clamped to UINT_MAX by grow_output_buffer upstream'
)
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    basename_n="$(basename "$file")"
    # Check pattern-specific safe-cast allowlist first
    narrow_allowlisted=0
    for entry in "${NARROW_SAFE_ALLOWLIST[@]}"; do
        IFS=$'\t' read -r entry_file entry_pattern entry_desc <<< "$entry"
        if [[ "$basename_n" == "$entry_file" ]] && echo "$content" | grep -qE "$entry_pattern"; then
            echo "  OK      ${file}:${line} — narrowing cast allowlisted (${entry_desc})" >&2
            narrow_allowlisted=1
            break
        fi
    done
    if [[ "$narrow_allowlisted" -eq 1 ]]; then
        narrow_hits=$((narrow_hits + 1))
        continue
    fi
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
    echo "$NONE_FOUND_MSG" >&2
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
    content_line="$(echo "$match" | cut -d: -f3-)"
    basename="$(basename "$file")"

    # Check explicit allowlist for known guarded patterns
    allowlisted=0
    for entry in "${GUARDED_CAST_ALLOWLIST[@]}"; do
        IFS=$'\t' read -r entry_file entry_pattern entry_desc <<< "$entry"
        if [[ "$basename" == "$entry_file" ]] && echo "$content_line" | grep -qE "$entry_pattern"; then
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
        IFS=$'\t' read -r entry_file entry_pattern entry_desc <<< "$entry"
        if [[ "$basename" == "$entry_file" ]] && echo "$content_line" | grep -qE "$entry_pattern"; then
            echo "  OK      ${file}:${line} — safe cast (${entry_desc})" >&2
            allowlisted=1
            break
        fi
    done
    if [[ "$allowlisted" -eq 1 ]]; then
        ssize_hits=$((ssize_hits + 1))
        continue
    fi

    # ── Type awareness: determine source type class within function scope ──
    # Extract every identifier after a (size_t) cast.  A greedy sed match
    # used to retain only the final cast on a line and could hide an earlier
    # risky conversion behind a later safe one.
    cast_idents="$(printf '%s' "$content_line" \
        | grep -oE '\(size_t\)[[:space:]]*[A-Za-z_][A-Za-z0-9_]*' \
        | sed -E 's/^\(size_t\)[[:space:]]*//' || true)"
    # Count every (size_t) cast on the line, including expression and
    # literal casts (e.g. (size_t)(a - b) or (size_t)-1): those carry the
    # same wrap risk as identifier casts but are invisible to the
    # identifier extraction above.  The all-unsigned quick path may skip
    # guard detection only when the counts match and every extracted
    # identifier is unsigned; otherwise continue to guard detection.
    total_cast_count="$(printf '%s' "$content_line" \
        | grep -oE '\(size_t\)' | wc -l | tr -d ' ' || true)"
    idents_cast_count="$(printf '%s\n' "$cast_idents" \
        | grep -cE '[A-Za-z_]' || true)"

    if [[ -n "$cast_idents" && "$total_cast_count" -eq "$idents_cast_count" ]]; then
        # Determine function boundary: find the nearest preceding line
        # matching ^} (NGINX style: function-closing brace at column 0).
        # Window starts from the line after that brace (or file start).
        func_start=1
        if [[ "$line" -gt 1 ]]; then
            # Search backwards from the line before the cast
            func_boundary="$(sed -n "1,$((line - 1))p" "$file" 2>/dev/null \
                | grep -n '^}' | tail -1 | cut -d: -f1 || true)"
            if [[ -n "$func_boundary" ]]; then
                func_start=$((func_boundary + 1))
            fi
        fi

        # Search for each declaration independently.  If every cast on this
        # line is a type that cannot exceed size_t, the line is safe.  Wide
        # unsigned types such as uint64_t and uintptr_t stay fail-closed so
        # the detector remains correct on narrower targets.
        func_window="$(sed -n "${func_start},${line}p" "$file" 2>/dev/null)"
        all_unsigned=1
        while IFS= read -r cast_ident; do
            if [[ -z "$cast_ident" ]]; then
                continue
            fi
            declaration_line="$(printf '%s\n' "$func_window" \
                | grep -nE \
                    "(^|[^[:alnum:]_])(ssize_t|ngx_int_t|off_t|time_t|ptrdiff_t|int|long|short|char|ngx_uint_t|size_t|uint8_t|uint16_t|uint32_t|uint64_t|uintptr_t|u_char|unsigned)[[:space:]]+\\*[[:space:]]*${cast_ident}([^[:alnum:]_]|$)|(^|[^[:alnum:]_])(ssize_t|ngx_int_t|off_t|time_t|ptrdiff_t|int|long|short|char|ngx_uint_t|size_t|uint8_t|uint16_t|uint32_t|uint64_t|uintptr_t|u_char|unsigned)[[:space:]]+${cast_ident}([^[:alnum:]_]|$)" \
                | tail -1 || true)"

            is_unsigned=0
            if printf '%s\n' "$declaration_line" | grep -qE \
                "(^|[^[:alnum:]_])(ngx_uint_t|size_t|uint8_t|uint16_t|uint32_t|u_char|unsigned)[[:space:]]+\\*[[:space:]]*${cast_ident}([^[:alnum:]_]|$)|(^|[^[:alnum:]_])(ngx_uint_t|size_t|uint8_t|uint16_t|uint32_t|u_char|unsigned)[[:space:]]+${cast_ident}([^[:alnum:]_]|$)"; then
                is_unsigned=1
            fi

            is_signed=0
            if printf '%s\n' "$declaration_line" | grep -E \
                "(^|[^[:alnum:]_])(ssize_t|ngx_int_t|off_t|time_t|ptrdiff_t|int|long|short|char)[[:space:]]+\\*[[:space:]]*${cast_ident}([^[:alnum:]_]|$)|(^|[^[:alnum:]_])(ssize_t|ngx_int_t|off_t|time_t|ptrdiff_t|int|long|short|char)[[:space:]]+${cast_ident}([^[:alnum:]_]|$)" \
                | grep -qvE 'u_char|unsigned'; then
                is_signed=1
            fi

            if [[ "$is_unsigned" -eq 1 ]] && [[ "$is_signed" -eq 0 ]]; then
                echo "  OK      ${file}:${line} — unsigned source type for '${cast_ident}' cannot be negative" >&2
            else
                all_unsigned=0
            fi
        done <<< "$cast_idents"

        if [[ "$all_unsigned" -eq 1 ]]; then
            ssize_hits=$((ssize_hits + 1))
            continue
        fi
        # Signed, wide-unsigned, or unknown casts continue to guard detection.
    fi

    # Check for guard in preceding 8 lines (expanded from 5 to capture
    # more complex guard-to-cast relationships like upper-bound checks)
    context_start=$((line - 8))
    if [[ "$context_start" -lt 1 ]]; then
        context_start=1
    fi
    has_guard=0
    context_block="$(sed -n "${context_start},${line}p" "$file" 2>/dev/null)"
    if echo "$context_block" | grep -qiE '< 0|>= 0|!= NGX_ERROR|== NGX_ERROR|(!=|==)[[:space:]]*NGX_OK|NGX_OK[[:space:]]*(!=|==)'; then
        has_guard=1
    fi
    if echo "$context_block" | grep -qiE '> NGX_MAX_SIZE_T_VALUE|> max_size_t|> SIZE_MAX|> UINT_MAX|> [A-Z_]+_MAX|> [A-Z_]+_LIMIT|\(size_t\)[[:space:]]*[[:alnum:]_]+[[:space:]]*>[[:space:]]*max'; then
        has_guard=1
    fi
    # ((size_t) -1) is the standard C idiom for SIZE_MAX
    if echo "$context_block" | grep -qE '>[[:space:]]*\(\(size_t\)[[:space:]]*-1\)'; then
        has_guard=1
    fi
    # Division precheck is the standard C idiom for multiplication overflow:
    # if (a > ((size_t) -1) / b) { error; } — semantically equivalent to
    # if (a * b > SIZE_MAX) { error; } but without the overflow.
    if echo "$context_block" | grep -qE '>[[:space:]]*\(\(size_t\)[[:space:]]*-1\)[[:space:]]*/|>[[:space:]]*(SIZE_MAX|[A-Z_]+_MAX)[[:space:]]*/'; then
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
    echo "$NONE_FOUND_MSG" >&2
fi
echo "" >&2

# ── Pattern (d): (size_t)(ptr - ptr) — pointer subtraction cast to size_t ──
# When pointers are reversed (e.g. last < pos), the ptrdiff_t result is
# negative, and casting to (size_t) wraps to a huge positive value.
# Use ngx_http_markdown_buf_len_safe(buf) to get a safe length instead.
#
# Known safe contexts (not flagged):
#   - Used in a comparison context (>=, <=, ==) that self-guards the bounds
#   - Preceded by an explicit bounds check on the same pointer pair
#   - Allowlisted as known-safe or doc-comment

echo "--- Pattern (d): (size_t)(ptr - ptr) pointer subtraction without guard ---" >&2

# Allowlisted pointer subtraction patterns (file:regex → reason)
readonly PTR_SUB_SAFE_ALLOWLIST=(
    # Doc comments only
    $'ngx_http_markdown_filter_module.h\t\*.*\(size_t\)\tDoc comment (not executable code)'
    # ── Forward-only string/token parsing — end pointer always > start ──
    $'ngx_http_markdown_config_core_impl.h\tend - start\tforward-only string normalization (end guaranteed > start by loop above)'
    $'ngx_http_markdown_stream_postcommit.c\tlast - p\tforward-only buffer scanning, called only when last > p (validated by caller)'
    $'ngx_http_markdown_auth.c\ttoken_end - token_start\tforward-only token scanning within bounds'
    $'ngx_http_markdown_auth.c\tdst - new_value\tforward-only buffer fill (dst monotonically advances)'
    $'ngx_http_markdown_auth.c\tname_end - name_start\tforward-only cookie name scanning within bounds'
)

ptr_sub_hits=0
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content_line="$(echo "$match" | cut -d: -f3-)"
    basename="$(basename "$file")"

    # Check safe-cast allowlist
    allowlisted=0
    for entry in "${PTR_SUB_SAFE_ALLOWLIST[@]}"; do
        IFS=$'\t' read -r entry_file entry_pattern entry_desc <<< "$entry"
        if [[ "$basename" == "$entry_file" ]] && echo "$content_line" | grep -qE "$entry_pattern"; then
            echo "  OK      ${file}:${line} — allowlisted (${entry_desc})" >&2
            allowlisted=1
            break
        fi
    done
    if [[ "$allowlisted" -eq 1 ]]; then
        ptr_sub_hits=$((ptr_sub_hits + 1))
        continue
    fi

    # Check for guard in preceding 5 lines
    context_start=$((line - 5))
    if [[ "$context_start" -lt 1 ]]; then
        context_start=1
    fi
    context_block="$(sed -n "${context_start},${line}p" "$file" 2>/dev/null)"
    has_guard=0
    # Guard: explicit <=/</>=/> comparison on pointer variables
    if echo "$context_block" | grep -qiE '(last|p|pos|ptr|end|start|src|dst|buf|cur|begin)[[:space:]]*[<>]=?[[:space:]]*(last|p|pos|ptr|end|start|src|dst|buf|cur|begin)'; then
        has_guard=1
    fi
    if [[ "$has_guard" -eq 1 ]]; then
        echo "  OK      ${file}:${line} — pointer subtraction with guard" >&2
    else
        echo "  WARNING ${file}:${line} — (size_t)(ptr-ptr) without nearby bound guard: ${content_line}" >&2
        warnings=$((warnings + 1))
    fi
    ptr_sub_hits=$((ptr_sub_hits + 1))
done < <(grep -rnE '\(size_t\)[[:space:]]*\([a-zA-Z_][a-zA-Z0-9_.>]*[[:space:]]*-[[:space:]]*[a-zA-Z_][a-zA-Z0-9_.>]*\)' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null || true)

if [[ "$ptr_sub_hits" -eq 0 ]]; then
    echo "$NONE_FOUND_MSG" >&2
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
