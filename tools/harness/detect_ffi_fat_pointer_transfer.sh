#!/bin/bash
#
# detect_ffi_fat_pointer_transfer.sh — FFI Fat-Pointer Ownership Transfer
#                                      Detection (Rule 53 — ffi-crosslang)
#
# Rule 53 (ffi-crosslang — fat-pointer safety):
#   When transferring ownership of a Rust Box<[u8]> slice to C via a raw
#   pointer, do NOT use Box::into_raw() directly, as this truncates the
#   fat pointer (pointer + length) to a thin *mut u8 by relying on
#   implementation-defined layout.  Instead use
#   ffi::memory::leak_boxed_slice_to_raw() which applies the canonical
#   as_mut_ptr + mem::forget pattern.
#
#   Single-object handles allocated via Box::new(...) ARE exempt from
#   this rule — Box::into_raw(Box::new(...)) does not involve a fat
#   pointer.
#
# Detection strategy:
#   1. Scan all .rs files under the FFI source directory.
#   2. Find lines containing Box::into_raw(...).
#   3. Exclude comment-only and doc-comment lines.
#   4. Exclude Box::into_raw(Box::new(...)) (single-object handle exemption).
#   5. Any remaining line is a violation of Rule 53.
#
# Usage:
#   bash tools/harness/detect_ffi_fat_pointer_transfer.sh [directory]
#     directory defaults to components/rust-converter/src/ffi
#
# Exit codes:
#   0 — no violations found
#   1 — violations found (one or more Box::into_raw usages on slices)
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18),
# POSIX ERE via grep -E (Rule 41).

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            cat <<USAGE
Usage: $0 [directory]
  directory defaults to ${REPO_ROOT}/components/rust-converter/src/ffi
  exit 0 = no violations, exit 1 = slice Box::into_raw found
USAGE
            exit 0
            ;;
        *)
            SRC_DIR="$arg"
            ;;
    esac
done

if [[ -z "$SRC_DIR" ]]; then
    SRC_DIR="${REPO_ROOT}/components/rust-converter/src/ffi"
fi

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: directory not found: $SRC_DIR" >&2
    exit 1
fi

# Find .rs files in the scan directory
RS_FILES=()
while IFS= read -r -d '' f; do
    RS_FILES+=("$f")
done < <(find "$SRC_DIR" -name '*.rs' -type f -print0 2>/dev/null || true)

if [[ ${#RS_FILES[@]} -eq 0 ]]; then
    echo "No .rs files found in $SRC_DIR"
    exit 0
fi

violations=0

for rs_file in "${RS_FILES[@]}"; do
    # Find lines with Box::into_raw, excluding:
    #   - Comment-only lines (preceded only by whitespace + //)
    #   - Doc comment lines (///, //!)
    #   - Lines containing Box::into_raw(Box::new( (single-object exemption)
    #
    # Strip leading whitespace, then check first non-whitespace tokens.
    # we use grep -n and process each match manually.
    while IFS= read -r line; do
        # Parse line number and content: "123:   let x = ..."
        line_num="${line%%:*}"
        line_content="${line#*:}"

        # Skip blank lines (shouldn't happen, but defensive)
        if [[ -z "${line_content}" ]]; then
            continue
        fi

        # Trim leading whitespace for comment detection (macOS bash 3.2 safe).
        trimmed="${line_content#"${line_content%%[![:space:]]*}"}"

        # Skip doc-comment and comment-only lines
        if [[ "$trimmed" == "///"* || "$trimmed" == "//!"* || "$trimmed" == "// "* ]]; then
            continue
        fi
        if [[ "$trimmed" == "//"* ]]; then
            # Other comment forms (shy of doc-comment markers)
            continue
        fi

        # Skip single-object handle pattern: Box::into_raw(Box::new(
        if [[ "$trimmed" == *'Box::into_raw(Box::new('* ]]; then
            continue
        fi

        # Skip single-object handle pattern split across statements:
        # let handle = Box::new(...);
        # Box::into_raw(handle)
        boxed_var="$(
            printf '%s\n' "$trimmed" \
                | sed -n 's/.*Box::into_raw(\([A-Za-z_][A-Za-z0-9_]*\)).*/\1/p'
        )"
        if [[ -n "$boxed_var" ]] && sed -n "1,${line_num}p" "$rs_file" \
            | grep -qE "let[[:space:]]+(mut[[:space:]]+)?${boxed_var}[[:space:]]*=[[:space:]]*Box::new[[:space:]]*\\("; then
            continue
        fi

        # Skip SAFETY comment continuation lines that mention Box::into_raw
        if [[ "$trimmed" == *'SAFETY:'*'Box::into_raw'* ]]; then
            continue
        fi
        if [[ "$trimmed" == *'SAFETY:'*'into_raw'* ]]; then
            continue
        fi

        # If we reach here, this is a non-comment, non-whitelisted
        # Box::into_raw usage — potential fat-pointer violation.
        rel_path="${rs_file#$REPO_ROOT/}"
        echo "VIOLATION:${rel_path}:${line_num}: ${line_content}"
        violations=$((violations + 1))
    done < <(grep -n 'Box::into_raw' "$rs_file" 2>/dev/null || true)
done

if [[ $violations -eq 0 ]]; then
    echo "OK: no fat-pointer transfer violations in ${SRC_DIR#$REPO_ROOT/}"
    exit 0
else
    echo ""
    echo "FAIL: ${violations} fat-pointer transfer violation(s) found."
    echo "Use ffi::memory::leak_boxed_slice_to_raw() instead of"
    echo "Box::into_raw(boxed_slice) as *mut u8 for slice ownership transfer."
    echo "Box::into_raw(Box::new(...)) is allowed for single-object handles."
    exit 1
fi
