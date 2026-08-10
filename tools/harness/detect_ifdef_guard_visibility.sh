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
guarded_funcs=$(python3 - "${HEADER_FILE}" "${GUARD_NAME}" <<'PY'
import re
import sys

header_path, guard_name = sys.argv[1:3]

with open(header_path, encoding='utf-8') as f:
    lines = f.readlines()

funcs = set()
stack = []

# MARKDOWN_STREAMING_SHADOW_DEBUG is only defined for a streaming build, so
# code guarded by it also has the visibility of MARKDOWN_STREAMING_ENABLED.
implied_guards = {'MARKDOWN_STREAMING_SHADOW_DEBUG': guard_name}

def guard_enabled():
    return any(name == guard_name and active for name, active, _ in stack)

def guard_expression(expression):
    positive = re.search(
        r'defined\s*\(\s*' + re.escape(guard_name) + r'\s*\)',
        expression,
    )
    negative = re.search(
        r'!\s*defined\s*\(\s*' + re.escape(guard_name) + r'\s*\)',
        expression,
    )
    if positive and not negative:
        return True
    if negative and not positive:
        return False
    return None

for line in lines:
    stripped = line.strip()
    directive = re.match(r'#\s*(ifdef|ifndef|if|else|elif|endif)\b(.*)', stripped)
    if directive:
        kind, expression = directive.groups()
        if kind == 'endif':
            if stack:
                stack.pop()
        elif kind in ('ifdef', 'ifndef'):
            name = expression.strip()
            if name in implied_guards:
                name = implied_guards[name]
            active = name == guard_name and kind == 'ifdef'
            stack.append((name, active, active))
        elif kind == 'if':
            active = guard_expression(expression)
            stack.append((guard_name if active is not None else None,
                          active, active is True))
        elif kind == 'elif' and stack:
            name, active, branch_taken = stack[-1]
            candidate = guard_expression(expression)
            if candidate is not None:
                next_active = not branch_taken and candidate
                stack[-1] = (guard_name, next_active,
                             branch_taken or next_active)
            elif name == guard_name and active is not None:
                next_active = False
                stack[-1] = (name, next_active, branch_taken or next_active)
        elif kind == 'else' and stack:
            name, active, branch_taken = stack[-1]
            if name == guard_name and active is not None:
                next_active = not branch_taken
                stack[-1] = (name, next_active, branch_taken or next_active)
        continue

    if guard_enabled():
        # Find function declarations: return_type name(args);
        m = re.search(r'\b(ngx_http_markdown_\w+)\s*\(', stripped)
        if m and ';' in line:
            funcs.add(m.group(1))

for f in sorted(funcs):
    print(f)
PY
 2>/dev/null)

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

        # Use Python to check if the function is referenced outside the guard.
        # Keep conditional nesting intact: an inner #ifdef must not make the
        # outer streaming guard appear closed.  Shadow debug is a streaming
        # sub-feature and therefore implies the requested guard.
        result=$(python3 - "${file}" "${func}" "${GUARD_NAME}" "${HEADER_FILE}" <<'PY'
import re
import sys

source_path, func_name, guard_name, header_path = sys.argv[1:5]

with open(source_path, encoding='utf-8') as f:
    lines = f.readlines()

stack = []
implied_guards = {'MARKDOWN_STREAMING_SHADOW_DEBUG': guard_name}

def guard_enabled():
    return any(name == guard_name and active for name, active, _ in stack)

def guard_expression(expression):
    positive = re.search(
        r'defined\s*\(\s*' + re.escape(guard_name) + r'\s*\)',
        expression,
    )
    negative = re.search(
        r'!\s*defined\s*\(\s*' + re.escape(guard_name) + r'\s*\)',
        expression,
    )
    if positive and not negative:
        return True
    if negative and not positive:
        return False
    return None

for i, line in enumerate(lines, 1):
    stripped = line.strip()
    directive = re.match(r'#\s*(ifdef|ifndef|if|else|elif|endif)\b(.*)', stripped)
    if directive:
        kind, expression = directive.groups()
        if kind == 'endif':
            if stack:
                stack.pop()
        elif kind in ('ifdef', 'ifndef'):
            name = expression.strip()
            if name in implied_guards:
                name = implied_guards[name]
            active = name == guard_name and kind == 'ifdef'
            stack.append((name, active, active))
        elif kind == 'if':
            active = guard_expression(expression)
            stack.append((guard_name if active is not None else None,
                          active, active is True))
        elif kind == 'elif' and stack:
            name, active, branch_taken = stack[-1]
            candidate = guard_expression(expression)
            if candidate is not None:
                next_active = not branch_taken and candidate
                stack[-1] = (guard_name, next_active,
                             branch_taken or next_active)
            elif name == guard_name and active is not None:
                next_active = False
                stack[-1] = (name, next_active, branch_taken or next_active)
        elif kind == 'else' and stack:
            name, active, branch_taken = stack[-1]
            if name == guard_name and active is not None:
                next_active = not branch_taken
                stack[-1] = (name, next_active, branch_taken or next_active)
        continue

    if not guard_enabled():
        # Skip comments and preprocessor text.  A function definition itself
        # is not a visibility reference; its enclosing guard is validated by
        # the same parser in this script.
        if stripped.startswith('*') or stripped.startswith('/*'):
            continue
        # A call can contain a declaration-like return type (for example
        # ``const ngx_str_t *r = func()``).  Only skip an actual definition;
        # calls must remain visible to the guard check.
        definition_pattern = (
            r'^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
            r'[\w\s*]+\b' + re.escape(func_name)
            + r'\s*\([^;]*\)\s*\{'
        )
        if re.match(definition_pattern, line):
            continue
        if func_name + '(' in line:
            # Skip a prototype in the owning header only.
            if header_path == source_path and ';' in line:
                continue
            print(f'{i}:{line.strip()[:80]}')
PY
 2>/dev/null)

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
