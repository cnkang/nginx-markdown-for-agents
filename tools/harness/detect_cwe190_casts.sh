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
# lack a nearby "< 0" or "NGX_ERROR" guard within 5 preceding lines.

readonly PATTERN_SSIZE_TO_SIZE='\(size_t\)[[:space:]]*(parsed|raw|rc|len|n)[^a-zA-Z]'

# ── Pattern (b): narrowing casts to smaller integer types ──
#
# (uint32_t), (uint8_t), (uInt), (int) applied to size_t or
# ngx_uint_t values without an upper-bound comparison.

readonly NARROW_TYPES='uint32_t|uint8_t|uInt|int'
readonly WIDE_TYPES='size_t|ngx_uint_t|off_t|ngx_int_t'

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
    # Check if the same file uses the safe wrapper nearby
    if grep -qn 'dynconf_parse_size_safe' "$file" 2>/dev/null; then
        echo "  WARNING ${file}:${line} — ngx_parse_size used; safe wrapper exists in file, verify this callsite uses it" >&2
        warnings=$((warnings + 1))
    else
        echo "  ERROR   ${file}:${line} — ngx_parse_size without dynconf_parse_size_safe wrapper" >&2
        errors=$((errors + 1))
    fi
    parse_size_hits=$((parse_size_hits + 1))
done < <(grep -rn 'ngx_parse_size' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null || true)

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
# Look for (size_t) casts of variables that are ssize_t-typed
# (common names: parsed, raw, result, n, rc, len when assigned from ssize_t funcs)
# This is heuristic; we flag the most dangerous common pattern:
# (size_t) applied to a variable immediately after ngx_parse_size
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    # Check if there is a "< 0" or "NGX_ERROR" guard in preceding 5 lines
    context_start=$((line - 5))
    if [[ "$context_start" -lt 1 ]]; then
        context_start=1
    fi
    has_guard=0
    if sed -n "${context_start},${line}p" "$file" 2>/dev/null | grep -qiE '< 0|>= 0|!= NGX_ERROR|== NGX_ERROR|NGX_OK'; then
        has_guard=1
    fi
    if [[ "$has_guard" -eq 1 ]]; then
        echo "  OK      ${file}:${line} — ssize_t→size_t cast with guard" >&2
    else
        echo "  WARNING ${file}:${line} — ssize_t→size_t cast without visible guard" >&2
        warnings=$((warnings + 1))
    fi
    ssize_hits=$((ssize_hits + 1))
done < <(grep -rnE '=\s*ngx_parse_size' "$SRC_DIR" --include='*.c' --include='*.h' 2>/dev/null || true)

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
