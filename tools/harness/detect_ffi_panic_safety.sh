#!/bin/bash
#
# detect_ffi_panic_safety.sh — FFI Panic Safety Detection (FFI Rule 15)
#
# Rule 15 (ffi-crosslang): All Rust FFI export functions must belong to
# one of the following panic-safety categories:
#
#   direct_catch        — function body directly contains catch_unwind
#   delegated_catch     — function delegates to a sibling FFI export
#                         whose body directly contains catch_unwind
#                         (one-level call graph check: the callee must
#                         itself be a #[no_mangle]/#[unsafe(no_mangle)]
#                         export function in the scan tree and must
#                         contain catch_unwind in its body)
#   safe_init_helper    — only does NULL check and ptr::write(zeroed)
#   safe_static_lookup  — only reads a static/const field and returns it
#                         (no allocation, no parsing, no FFI input
#                         dereference)
#   free_helper         — only does NULL check, dereference, and
#                         drop/free of owned resources; must either
#                         contain catch_unwind OR be a pure
#                         drop-in-place of an FFI handle with no
#                         business logic.  Without catch_unwind a
#                         free_helper is flagged REVIEW in strict mode.
#   unsafe_business_logic — performs parsing, conversion, URL/security
#                         logic without catch_unwind (REAL GAP)
#   unknown             — cannot be classified; needs human review
#
# Detection strategy:
#   1. For each #[no_mangle] / #[unsafe(no_mangle)] function, extract
#      the function name and body.
#   2. Check if the body contains catch_unwind (direct_catch).
#   3. If not, identify any call to another FFI-export-named function in
#      the scan tree; if the callee body contains catch_unwind,
#      classify as delegated_catch (one-level delegation only).
#   4. If not, classify as init/free/static-lookup by body pattern.
#   5. Remaining functions are unsafe_business_logic (real gaps) or
#      unknown.
#
# Allowlist: comment containing "trivial", "no-alloc", "safe-init", or
# "static-lookup" on the same or preceding line suppresses the function
# entirely (exempt category).
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18),
# POSIX ERE via grep -E (Rule 41).
#
# Usage:
#   bash tools/harness/detect_ffi_panic_safety.sh [directory] [--strict]
#     directory defaults to components/rust-converter/src
#
# Exit codes:
#   0 — no actionable findings (advisory: warnings reported, never blocks)
#   1 — usage error, OR one or more unsafe_business_logic / unknown /
#       adjacent-duplicate free_helper_without_catch findings in
#       --strict mode
#
# Makefile harness-security-checks invokes this detector with --strict.

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR=""
STRICT=0

for arg in "$@"; do
    case "$arg" in
        --strict)
            STRICT=1
            ;;
        --help|-h)
            cat <<USAGE
Usage: $0 [directory] [--strict]
  directory defaults to ${REPO_ROOT}/components/rust-converter/src
  --strict  exit 1 on unsafe_business_logic / unknown / free_helper_without_catch
USAGE
            exit 0
            ;;
        *)
            if [[ -z "$SRC_DIR" ]]; then
                SRC_DIR="$arg"
            else
                echo "ERROR: unexpected argument: $arg" >&2
                exit 1
            fi
            ;;
    esac
done

if [[ -z "$SRC_DIR" ]]; then
    SRC_DIR="${REPO_ROOT}/components/rust-converter/src"
fi

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
n_free_no_catch=0
n_business=0
n_unknown=0
n_exempt=0

# Actionable findings (only relevant in strict mode)
n_actionable=0

echo "=== FFI Panic Safety Detection (FFI Rule 15) ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "Strict: ${STRICT}" >&2
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

# ── Stage 1: collect all FFI export function names and whether their
#    body contains catch_unwind.  We do this with a single awk pass per
#    file, emitting "<file>:<line>:<func_name>:<has_catch>" records.
#    This map is reused by the delegated_catch check.

map_file="${TMPDIR:-/tmp}/ffi_panic_map.$$"
trap 'rm -f "$map_file" >/dev/null 2>&1' EXIT

: >"$map_file"

# Collect the list of rs files first so we can pass them all to a
# single awk invocation that builds the global map in one pass.
rs_files=$(find "$SRC_DIR" -type f -name '*.rs' 2>/dev/null | sort)
if [[ -z "$rs_files" ]]; then
    echo "PASS: no Rust source files found in ${SRC_DIR}" >&2
    exit 0
fi

# Build a global function map across all files: name → has_catch (0/1)
# We only track #[no_mangle]/#[unsafe(no_mangle)] exports.
printf '%s\n' "$rs_files" | awk '
    BEGIN {
        depth = 0
        func_start = 0
        func_name = ""
        seen_open = 0
        has_catch = 0
    }
    FNR == 1 {
        # new file: flush any in-progress function
        if (func_start > 0 && seen_open) {
            print func_name "\t" has_catch
        }
        depth = 0
        func_start = 0
        func_name = ""
        seen_open = 0
        has_catch = 0
    }
    {
        line = $0
        # Detect no_mangle attribute lines
        if (line ~ /#\[unsafe\(no_mangle\)\]/ || line ~ /#\[no_mangle\]/) {
            # flush previous function if open
            if (func_start > 0 && seen_open) {
                print func_name "\t" has_catch
            }
            depth = 0
            func_start = 0
            func_name = ""
            seen_open = 0
            has_catch = 0
            nomangle_pending = 1
            next
        }
        if (nomangle_pending) {
            if (line ~ /pub[[:space:]]+(unsafe[[:space:]]+)?extern[[:space:]]*"C"[[:space:]]+fn/) {
                l = line
                sub(/.*fn[[:space:]]+/, "", l)
                sub(/[[:space:]]*[(].*/, "", l)
                func_name = l
                func_start = NR
                nomangle_pending = 0
            } else if (line ~ /^[[:space:]]*$/ || line ~ /^[[:space:]]*\/\// || line ~ /^[[:space:]]*#/ || line ~ /^[[:space:]]*\/\*/) {
                # doc comment / blank / attribute between no_mangle and fn
                # keep waiting
                next
            } else {
                nomangle_pending = 0
            }
        }
        if (func_start > 0) {
            n = length(line)
            for (i = 1; i <= n; i++) {
                c = substr(line, i, 1)
                if (c == "{") { depth++; seen_open = 1 }
                if (c == "}") depth--
            }
            if (line ~ /catch_unwind/) has_catch = 1
            if (seen_open && depth == 0) {
                print func_name "\t" has_catch
                func_start = 0
                func_name = ""
                seen_open = 0
                has_catch = 0
            }
        }
    }
    END {
        if (func_start > 0 && seen_open) {
            print func_name "\t" has_catch
        }
    }
' $(printf '%s\n' "$rs_files" | tr '\n' ' ') >"$map_file" 2>/dev/null || true

# ── Stage 2: per-function classification ──
# For delegated_catch we re-open the global map and look up the callee
# name.  We pass the map file into awk via a preload.

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

        # Extract function name, body, and classification using awk.
        # We pass the map file as a second input stream so awk can build
        # a name→has_catch lookup table before processing the main file.
        result=$(awk -v map_file="$map_file" -v start_line="$nomangle_line" '
            BEGIN {
                # Preload the global map: name -> has_catch
                while ((getline line < map_file) > 0) {
                    split(line, parts, "\t")
                    name = parts[1]
                    hc = parts[2]
                    if (name != "") {
                        global_has_catch[name] = hc
                    }
                }
                close(map_file)
                depth = 0
                func_start = 0
                func_name = ""
                seen_open = 0
                has_catch = 0
                calls_with_code = 0
                calls_sibling_with_catch = 0
                has_ptr_write_zeroed = 0
                has_box_from_raw = 0
                has_slice_from_raw = 0
                has_validate = 0
                has_drop = 0
                has_static_read = 0
                has_alloc = 0
                has_deref_ffi_input = 0
                body = ""
                # Track set of sibling names called in the body
                delete called_names
                called_count = 0
            }
            # Skip the map file entirely (it is the first input file).
            # Use FNR so line numbers match the source file being scanned.
            FNR < start_line { next }
            FNR >= start_line && FNR <= start_line + 10 {
                if ($0 ~ /pub[[:space:]]+(unsafe[[:space:]]+)?extern[[:space:]]*"C"[[:space:]]+fn/) {
                    func_start = FNR
                    line = $0
                    sub(/.*fn[[:space:]]+/, "", line)
                    sub(/[[:space:]]*[(].*/, "", line)
                    func_name = line
                }
            }
            func_start > 0 && FNR >= func_start {
                n = length($0)
                for (i = 1; i <= n; i++) {
                    c = substr($0, i, 1)
                    if (c == "{") { depth++; seen_open = 1 }
                    if (c == "}") depth--
                }
                # Strip // line comments and /* ... */ block-comment
                # fragments so flag regexes do not fire on doc prose.
                codeline = $0
                sub(/\/\/.*/, "", codeline)
                sub(/\/\*.*\*\//, "", codeline)
                body = body "\n" $0
                if (codeline ~ /catch_unwind/) has_catch = 1
                if (codeline ~ /_with_code[[:space:]]*\(/) calls_with_code = 1
                if (codeline ~ /ptr::write.*zeroed|mem::zeroed/) has_ptr_write_zeroed = 1
                if (codeline ~ /Box::from_raw/) has_box_from_raw = 1
                if (codeline ~ /slice::from_raw_parts/) has_slice_from_raw = 1
                if (codeline ~ /validate_link_url|is_dangerous_url|SecurityValidator|from_utf8|make_decision|parse/) has_validate = 1
                if (codeline ~ /markdown_streaming_abort[[:space:]]*\(/) calls_sibling = 1
                if (codeline ~ /drop[[:space:]]*\(/) has_drop = 1
                if (codeline ~ /REASON_CODE_COUNT|\.as_ptr\(\)|\.as_str\(\)/) has_static_read = 1
                # Const/static field reads: ALL_CAPS identifiers are
                # compile-time constants; a function that only returns
                # such a value is a safe static lookup.
                if (codeline ~ /[A-Z_][A-Z0-9_]+/) has_static_read = 1
                if (codeline ~ /Box::new|Box::into_raw|Vec::with_capacity|Vec::new|alloc/) has_alloc = 1
                if (codeline ~ /unsafe[[:space:]]*\{[[:space:]]*\*/) has_deref_ffi_input = 1
                # Detect calls to any other known FFI export name
                line = codeline
                for (sib in global_has_catch) {
                    if (sib == func_name) continue
                    if (index(line, sib "(") > 0) {
                        called_names[sib] = 1
                        called_count++
                    }
                }
                if (seen_open && depth == 0) {
                    # Evaluate delegation: any called sibling whose body
                    # has catch_unwind
                    for (sib in called_names) {
                        if (sib in global_has_catch && global_has_catch[sib] == "1") {
                            calls_sibling_with_catch = 1
                        }
                    }
                    # Classify
                    if (has_catch) {
                        category = "direct_catch"
                    } else if (calls_sibling_with_catch) {
                        category = "delegated_catch"
                    } else if (has_ptr_write_zeroed && !has_validate && !has_slice_from_raw && !has_alloc) {
                        category = "safe_init_helper"
                    } else if (func_name ~ /_count$/ && !has_validate && !has_alloc && !has_deref_ffi_input && has_static_read) {
                        category = "safe_static_lookup"
                    } else if (func_name ~ /_str$/ && !has_validate && !has_alloc && has_static_read) {
                        category = "safe_static_lookup"
                    } else if (func_name ~ /_metric_key$/ && !has_validate && !has_alloc && has_static_read) {
                        category = "safe_static_lookup"
                    } else if (has_box_from_raw && !has_validate && !has_slice_from_raw && !has_ptr_write_zeroed && !has_alloc) {
                        category = "free_helper"
                    } else if (func_name ~ /_free$/ && !has_validate && !has_alloc) {
                        category = "free_helper"
                    } else if (func_name ~ /_abort$/ && !has_validate && !has_alloc) {
                        # abort that only drops is a free helper
                        category = "free_helper"
                    } else if (has_validate) {
                        category = "unsafe_business_logic"
                    } else {
                        category = "unknown"
                    }
                    print func_name ":" func_start ":" category
                    exit
                }
            }
        ' "$map_file" "$rs_file" 2>/dev/null || true)

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
                echo "  OK      ${rs_file##*/}:${awk_start} — ${awk_name}: delegated_catch (callee has catch_unwind)" >&2
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
                if [[ "$STRICT" -eq 1 ]]; then
                    n_free_no_catch=$((n_free_no_catch + 1))
                    n_actionable=$((n_actionable + 1))
                    echo "  REVIEW  ${rs_file##*/}:${awk_start} — ${awk_name}: free_helper_without_catch (drop/free of owned resource without catch_unwind — add panic boundary for Rule 15)" >&2
                else
                    echo "  OK      ${rs_file##*/}:${awk_start} — ${awk_name}: free_helper (drop/Box::from_raw, no business logic)" >&2
                fi
                ;;
            unsafe_business_logic)
                n_business=$((n_business + 1))
                n_actionable=$((n_actionable + 1))
                echo "  WARNING ${rs_file##*/}:${awk_start} — ${awk_name}: unsafe_business_logic (performs parsing/validation/decision without catch_unwind — Rule 15: add panic boundary)" >&2
                ;;
            *)
                n_unknown=$((n_unknown + 1))
                n_actionable=$((n_actionable + 1))
                echo "  REVIEW  ${rs_file##*/}:${awk_start} — ${awk_name}: unknown (cannot classify — needs human review)" >&2
                ;;
        esac

    done < <(grep -nE '#\[unsafe\(no_mangle\)\]|#\[no_mangle\]' "$rs_file" 2>/dev/null || true)

done < <(printf '%s\n' "$rs_files")

echo "" >&2
echo "=== Summary ===" >&2
echo "  direct_catch:           ${n_direct}" >&2
echo "  delegated_catch:        ${n_delegated}" >&2
echo "  safe_init_helper:      ${n_init}" >&2
echo "  safe_static_lookup:    ${n_static}" >&2
echo "  free_helper:           ${n_free} (no-catch flagged in strict: ${n_free_no_catch})" >&2
echo "  unsafe_business_logic: ${n_business}" >&2
echo "  unknown:               ${n_unknown}" >&2
echo "  exempt (comment):      ${n_exempt}" >&2
echo "" >&2

if [[ "$n_business" -gt 0 ]]; then
    echo "ACTION: ${n_business} FFI function(s) with business logic missing catch_unwind — add panic boundary (catch_unwind returning safe error code)" >&2
elif [[ "$n_unknown" -gt 0 ]]; then
    echo "ACTION: ${n_unknown} FFI function(s) could not be classified — review manually" >&2
fi

if [[ "$STRICT" -eq 1 && "$n_actionable" -gt 0 ]]; then
    echo "FAIL (strict): ${n_actionable} actionable finding(s) — fix before merge" >&2
    exit 1
fi

if [[ "$n_business" -eq 0 && "$n_unknown" -eq 0 && "$n_actionable" -eq 0 ]]; then
    echo "PASS: All FFI export functions have a panic boundary or are classified as safe helpers." >&2
else
    echo "PASS with warnings: advisory findings reported (run with --strict to gate CI)" >&2
fi
exit 0