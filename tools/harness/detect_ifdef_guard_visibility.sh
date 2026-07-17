#!/usr/bin/env bash
# detect_ifdef_guard_visibility.sh — Detect #ifdef-guarded function references outside guard
#
# Rule (nginx-idioms, build-safety): Functions declared inside #ifdef FEATURE_GUARD
# blocks must not be referenced outside that guard in any .c or .h file. A function
# needed in both feature-enabled and feature-disabled builds must be declared
# outside the #ifdef guard. This detector catches the common mistake of adding
# a function declaration inside an #ifdef but forgetting to move it outside when
# the function is referenced from non-feature-gated code.
#
# Detection strategy:
#   1. Parse the header file to find all function identifiers declared inside
#      #ifdef MARKDOWN_STREAMING_ENABLED blocks.
#   2. For each such function, search all .c and .h files in the src directory
#      for references that appear OUTSIDE #ifdef MARKDOWN_STREAMING_ENABLED blocks.
#   3. Flag any reference found outside the guard as a visibility gap.
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18),
# POSIX ERE via grep -E (Rule 41).
#
# Usage:
#   bash tools/harness/detect_ifdef_guard_visibility.sh [header] [src_dir]
#     header  defaults to components/nginx-module/src/ngx_http_markdown_filter_module.h
#     src_dir  defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no visibility gaps found
#   1 — one or more visibility gaps found

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HEADER_FILE="${REPO_ROOT}/components/nginx-module/src/ngx_http_markdown_filter_module.h"
SRC_DIR="${REPO_ROOT}/components/nginx-module/src"
GUARD_NAME="MARKDOWN_STREAMING_ENABLED"

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            cat <<USAGE
Usage: $0 [header] [src_dir]
  header  defaults to ${HEADER_FILE}
  src_dir  defaults to ${SRC_DIR}
  --guard NAME  feature guard to check (default: ${GUARD_NAME})
  --help   show this help
USAGE
            exit 0
            ;;
        --guard=*)
            GUARD_NAME="${arg#*=}"
            ;;
        *)
            if [[ ! -f "$arg" ]] && [[ ! -d "$arg" ]]; then
                echo "ERROR: not a file or directory: $arg" >&2
                exit 1
            fi
            if [[ -f "$arg" ]]; then
                HEADER_FILE="$arg"
            else
                SRC_DIR="$arg"
            fi
            ;;
    esac
done

if [[ ! -f "$HEADER_FILE" ]]; then
    echo "ERROR: header not found: $HEADER_FILE" >&2
    exit 1
fi

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: src directory not found: $SRC_DIR" >&2
    exit 1
fi

# Step 1: Extract function names declared inside #ifdef GUARD_NAME blocks in the header
# These are prototype-style declarations (end with ;)
guarded_funcs=$(python3 -c "
import re
import sys

with open('$HEADER_FILE') as f:
    lines = f.readlines()

in_guard = False
depth = 0
funcs = set()
for line in lines:
    stripped = line.strip()
    if stripped.startswith('#ifdef $GUARD_NAME'):
        in_guard = True
        depth += 1
        continue
    if in_guard and '#endif' in stripped:
        depth -= 1
        if depth == 0:
            in_guard = False
        continue
    if in_guard:
        # Find function declarations: return_type name(args);
        m = re.search(r'\b(ngx_http_markdown_\w+)\s*\(', stripped)
        if m and ';' in line:
            funcs.add(m.group(1))

for f in sorted(funcs):
    print(f)
" 2>/dev/null)

if [[ -z "$guarded_funcs" ]]; then
    echo "OK: no functions found inside #ifdef ${GUARD_NAME} blocks"
    exit 0
fi

# Step 2: For each guarded function, search for references outside #ifdef blocks
# in all .c and .h files in SRC_DIR
findings=0

for func in $guarded_funcs; do
    # Search all .c and .h files for this function name
    while IFS= read -r -d '' file; do
        rel_path="${file#${REPO_ROOT}/}"

        # Use Python to check if the function is referenced outside the guard
        result=$(python3 -c "
import sys

func_name = '$func'
guard_name = '$GUARD_NAME'

with open('$file') as f:
    lines = f.readlines()

in_guard = False
depth = 0
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith('#ifdef ' + guard_name):
        in_guard = True
        depth += 1
        continue
    if in_guard and '#endif' in stripped:
        depth -= 1
        if depth == 0:
            in_guard = False
        continue
    if not in_guard:
        # Check for function reference (not a declaration in the header)
        # Skip comment lines
        if stripped.startswith('*') or stripped.startswith('/*'):
            continue
        # Check for the function name followed by (
        if func_name + '(' in line:
            # Skip if it's just the header declaration itself
            if '$HEADER_FILE' == '$file' and ';' in line:
                continue
            print(f'{i}:{line.strip()[:80]}')
" 2>/dev/null)

        if [[ -n "$result" ]]; then
            while IFS= read -r match_line; do
                [[ -z "$match_line" ]] && continue
                line_num="${match_line%%:*}"
                line_text="${match_line#*:}"
                echo "ERROR: ${rel_path}:${line_num}: ${func}() referenced outside #ifdef ${GUARD_NAME}" >&2
                echo "  ${line_text}" >&2
                findings=$((findings + 1))
            done <<< "$result"
        fi
    done < <(find "$SRC_DIR" -type f \( -name '*.c' -o -name '*.h' \) -print0)
done

if [[ $findings -gt 0 ]]; then
    echo "FAIL: found ${findings} #ifdef guard visibility gap(s)" >&2
    exit 1
fi

echo "OK: no #ifdef guard visibility gaps found"
exit 0