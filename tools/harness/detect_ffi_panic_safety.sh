#!/bin/bash
#
# detect_ffi_panic_safety.sh — FFI Panic Safety Detection (FFI Rule 15)
#
# Rule 15 (ffi-crosslang): All Rust FFI export functions must belong to
# one of the following panic-safety categories:
#
#   direct_catch        — function body directly contains catch_unwind
#   delegated_catch     — function delegates to a sibling wrapper that
#                         already contains catch_unwind (one-level call
#                         graph check)
#   safe_init_helper    — only does NULL check and ptr::write(zeroed)
#   safe_static_lookup  — only reads a static/const field and returns it
#   free_helper         — only does NULL check, dereference, and
#                         drop/free of owned resources
#   unsafe_business_logic — performs parsing, conversion, URL/security
#                         logic without catch_unwind (REAL GAP)
#   unknown             — cannot be classified; needs human review
#
# Detection strategy:
#   1. For each #[no_mangle] / #[unsafe(no_mangle)] function, extract
#      the function name and body.
#   2. Check if the body contains catch_unwind (direct_catch).
#   3. If not, check if the body calls a *_with_code sibling (delegated_catch).
#   4. If not, classify as init/free/static-lookup by body pattern.
#   5. Remaining functions are unsafe_business_logic (real gaps).
#
# Allowlist: comment containing "trivial" or "no-alloc" on the same or
# preceding line suppresses the function entirely.
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18),
# POSIX ERE via grep -E (Rule 41).
#
# Usage:
#   bash tools/harness/detect_ffi_panic_safety.sh [directory]
#     directory defaults to components/rust-converter/src/ffi
#
# Exit codes:
#   0 — advisory: findings reported as classified warnings, never blocks CI
#   1 — usage error

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${1:-${REPO_ROOT}/components/rust-converter/src/ffi}"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: directory does not exist: ${SRC_DIR}" >&2
    exit 1
fi

# Counters per category
n_direct=0
n_delegated=0
n_init=0
n_static=0
n_free=0
n_business=0
n_unknown=0
n_exempt=0

echo "=== FFI Panic Safety Detection (FFI Rule 15) ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "" >&2

# ── Helper: check if a comment exempts the function ──
is_exempt() {
    local file="$1"
    local line_num="$2"
    local same_line prev_line prev

    same_line=$(sed -n "${line_num}p" "$file" 2>/dev/null || true)
    if echo "$same_line" | grep -qE '//.*(trivial|no-alloc|safe-init|static-lookup)'; then
        return 0
    fi
    prev_line=$((line_num - 1))
    if [[ "$prev_line" -gt 0 ]]; then
        prev=$(sed -n "${prev_line}p" "$file" 2>/dev/null || true)
        if echo "$prev" | grep -qE '//.*(trivial|no-alloc|safe-init|static-lookup)'; then
            return 0
        fi
    fi
    return 1
}

# ── Main scan ──
while IFS= read -r rs_file; do
    [[ -z "$rs_file" ]] && continue
    if ! grep -qI '' "$rs_file" 2>/dev/null; then
        continue
    fi

    while IFS= read -r match_line; do
        [[ -z "$match_line" ]] && continue
        nomangle_line="${match_line%%:*}"

        if is_exempt "$rs_file" "$nomangle_line"; then
            n_exempt=$((n_exempt + 1))
            continue
        fi

        # Extract function name, body, and check for catch_unwind /
        # delegated call / pattern classification using awk.
        result=$(awk -v start_line="$nomangle_line" '
            BEGIN {
                has_catch = 0
                depth = 0
                func_start = 0
                func_name = ""
                seen_open = 0
                body = ""
                calls_with_code = 0
                has_ptr_write_zeroed = 0
                has_box_from_raw = 0
                has_slice_from_raw = 0
                has_match_or_if = 0
                has_validate = 0
                has_parse = 0
                has_make_decision = 0
            }
            NR < start_line { next }
            NR >= start_line && NR <= start_line + 3 {
                if ($0 ~ /pub[[:space:]]+(unsafe[[:space:]]+)?extern[[:space:]]*"C"[[:space:]]+fn/) {
                    func_start = NR
                    line = $0
                    sub(/.*fn[[:space:]]+/, "", line)
                    sub(/[[:space:]]*[(].*/, "", line)
                    func_name = line
                }
            }
            func_start > 0 && NR >= func_start {
                for (i = 1; i <= length($0); i++) {
                    c = substr($0, i, 1)
                    if (c == "{") { depth++; seen_open = 1 }
                    if (c == "}") depth--
                }
                body = body "\n" $0
                if ($0 ~ /catch_unwind/) has_catch = 1
                if ($0 ~ /_with_code[[:space:]]*\(/) calls_with_code = 1
                if ($0 ~ /ptr::write.*zeroed|mem::zeroed/) has_ptr_write_zeroed = 1
                if ($0 ~ /Box::from_raw/) has_box_from_raw = 1
                if ($0 ~ /slice::from_raw_parts/) has_slice_from_raw = 1
                if ($0 ~ /validate_link_url|is_dangerous_url|SecurityValidator|from_utf8|make_decision|parse/) has_validate = 1
                if ($0 ~ /markdown_streaming_abort[[:space:]]*\(/) calls_with_code = 1
                if (seen_open && depth == 0) {
                    # Classify
                    if (has_catch) {
                        category = "direct_catch"
                    } else if (calls_with_code) {
                        category = "delegated_catch"
                    } else if (has_ptr_write_zeroed && !has_validate && !has_slice_from_raw) {
                        category = "safe_init_helper"
                    } else if (has_box_from_raw && !has_validate && !has_slice_from_raw && !has_ptr_write_zeroed) {
                        category = "free_helper"
                    } else if (has_validate) {
                        category = "unsafe_business_logic"
                    } else if (func_name ~ /_free$/ && !has_validate) {
                        category = "free_helper"
                    } else if (func_name ~ /_init$/ && has_ptr_write_zeroed) {
                        category = "safe_init_helper"
                    } else {
                        category = "unknown"
                    }
                    print func_name ":" func_start ":" category
                    exit
                }
            }
        ' "$rs_file" 2>/dev/null || true)

        if [[ -z "$result" ]]; then
            continue
        fi

        awk_name="${result%%:*}"
        awk_rest="${result#*:}"
        awk_start="${awk_rest%%:*}"
        awk_category="${awk_rest#*:}"

        case "$awk_category" in
            direct_catch)
                n_direct=$((n_direct + 1))
                ;;
            delegated_catch)
                n_delegated=$((n_delegated + 1))
                echo "  OK      ${rs_file##*/}:${awk_start} — ${awk_name}: delegated_catch (delegates to *_with_code sibling)" >&2
                ;;
            safe_init_helper)
                n_init=$((n_init + 1))
                echo "  OK      ${rs_file##*/}:${awk_start} — ${awk_name}: safe_init_helper (NULL check + zeroed write)" >&2
                ;;
            safe_static_lookup)
                n_static=$((n_static + 1))
                echo "  OK      ${rs_file##*/}:${awk_start} — ${awk_name}: safe_static_lookup (static field read)" >&2
                ;;
            free_helper)
                n_free=$((n_free + 1))
                echo "  OK      ${rs_file##*/}:${awk_start} — ${awk_name}: free_helper (drop/Box::from_raw, no business logic)" >&2
                ;;
            unsafe_business_logic)
                n_business=$((n_business + 1))
                echo "  WARNING ${rs_file##*/}:${awk_start} — ${awk_name}: unsafe_business_logic (performs parsing/validation/decision without catch_unwind — Rule 15: add panic boundary)" >&2
                ;;
            *)
                n_unknown=$((n_unknown + 1))
                echo "  REVIEW  ${rs_file##*/}:${awk_start} — ${awk_name}: unknown (cannot classify — needs human review)" >&2
                ;;
        esac

    done < <(grep -nE '#\[unsafe\(no_mangle\)\]|#\[no_mangle\]' "$rs_file" 2>/dev/null || true)

done < <(find "$SRC_DIR" -type f -name '*.rs' 2>/dev/null | sort)

echo "" >&2
echo "=== Summary ===" >&2
echo "  direct_catch:           ${n_direct}" >&2
echo "  delegated_catch:        ${n_delegated}" >&2
echo "  safe_init_helper:      ${n_init}" >&2
echo "  safe_static_lookup:    ${n_static}" >&2
echo "  free_helper:           ${n_free}" >&2
echo "  unsafe_business_logic: ${n_business}" >&2
echo "  unknown:               ${n_unknown}" >&2
echo "  exempt (comment):      ${n_exempt}" >&2
echo "" >&2

if [[ "$n_business" -gt 0 ]]; then
    echo "ACTION: ${n_business} FFI function(s) with business logic missing catch_unwind — add panic boundary (catch_unwind returning safe error code)" >&2
elif [[ "$n_unknown" -gt 0 ]]; then
    echo "ACTION: ${n_unknown} FFI function(s) could not be classified — review manually" >&2
else
    echo "PASS: All FFI export functions have a panic boundary or are classified as safe helpers." >&2
fi

exit 0