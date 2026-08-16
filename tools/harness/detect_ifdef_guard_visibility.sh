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
if ! guarded_funcs=$(python3 - "${HEADER_FILE}" "${GUARD_NAME}" <<'PY'
import re
import sys
import os

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

pending_definition = False
pending_decl = None

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

    if guard_enabled():
        # Find function declarations: return_type name(args);
        if stripped.startswith(('//', '/*', '*')):
            continue
        m = re.search(r'\b(ngx_http_markdown_\w+)\s*\(', stripped)
        if m:
            if ';' in line:
                funcs.add(m.group(1))
            elif re.search(r'\(\s*$', stripped):
                # Multi-line declaration: name( on this line, arguments
                # and semicolon on the following lines.
                pending_decl = m.group(1)
            elif pending_decl is not None and ';' in line:
                funcs.add(pending_decl)
                pending_decl = None
        elif pending_decl is not None and ';' in line:
            funcs.add(pending_decl)
            pending_decl = None

for f in sorted(funcs):
    print(f)
PY
); then
    echo "ERROR: Python parser failed while extracting guarded functions from ${HEADER_FILE}" >&2
    exit 1
fi

if [[ -z "$guarded_funcs" ]]; then
    echo "OK: no functions found inside #ifdef ${GUARD_NAME} blocks"
    exit 0
fi

# Step 2: For each guarded function, search for references outside #ifdef blocks
# in all .c and .h files in SRC_DIR
findings=0
parse_errors=0

for func in $guarded_funcs; do
    # Search all .c and .h files for this function name
    while IFS= read -r -d '' file; do
        rel_path="${file#${REPO_ROOT}/}"

        # Use Python to check if the function is referenced outside the guard.
        # Keep conditional nesting intact: an inner #ifdef must not make the
        # outer streaming guard appear closed.  Shadow debug is a streaming
        # sub-feature and therefore implies the requested guard.
if ! result=$(python3 - "${file}" "${func}" "${GUARD_NAME}" "${HEADER_FILE}" <<'PY'
import re
import sys
import os

source_path, func_name, guard_name, header_path = sys.argv[1:5]

def same_file(left, right):
    return os.path.realpath(left) == os.path.realpath(right)

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

pending_definition = False

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
        if stripped.startswith(('//', '/*', '*')):
            continue
        if pending_definition:
            # Inside a multi-line function signature: the definition ends
            # at the opening brace or at a prototype semicolon.  A closing
            # paren without a brace means the candidate was a cross-line
            # call, not a definition: exit pending and re-examine the
            # line normally.
            if re.search(r'\)\s*\{\s*$', line) or ';' in line:
                pending_definition = False
                continue
            if re.match(r'^\s*\{', line):
                # K&R/nginx variants that put the opening brace on its own
                # line after the parameter list.
                pending_definition = False
                continue
            if re.search(r'\)\s*$', line):
                pending_definition = False
            else:
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
        # nginx-style split-line definition: the return type is on the
        # preceding line and the function name starts this line, followed
        # by a parameter list that may span lines before the opening brace.
        # Require the preceding line to be a return-type line (a type
        # declaration ending without ';', '(', '{', or '}') so a multiline
        # *call* such as ``fn(\n arg\n);`` is not misclassified as a
        # definition (which would skip checking an out-of-guard reference).
        prev_line = lines[i - 2] if i >= 2 else ""
        prev_stripped = prev_line.strip()
        prev_is_type_line = bool(
            prev_stripped
            and not re.search(r'[;(){}]', prev_stripped)
            and re.search(
                r'(?:^|\s)(?:static|inline|const|ngx_[a-z_]+_t|u_char|size_t|'
                r'ngx_int_t|ngx_uint_t|ngx_flag_t|void|char|int|unsigned|'
                r'off_t|ssize_t|time_t|struct|enum|union)\s*$',
                prev_stripped,
            )
        )
        split_definition_pattern = (
            r'^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
            + re.escape(func_name) + r'\s*\([^;]*$'
        )
        if prev_is_type_line and re.match(split_definition_pattern, line):
            pending_definition = True
            continue
        if func_name + '(' in line:
            # Skip a prototype in the owning header only.
            if same_file(header_path, source_path) and ';' in line:
                continue
            # A signature fragment that ends with the opening paren (no
            # closing paren on this line) can be a multi-line definition
            # whose parameter list and opening brace follow.  Only treat it
            # as such when the preceding line is a return-type line; a
            # multiline call (``fn(\n arg\n);``) must be reported as a
            # reference instead of being skipped.
            if prev_is_type_line and re.search(
                r'\b' + re.escape(func_name) + r'\s*\(\s*$', line
            ):
                pending_definition = True
                continue
            print(f'{i}:{line.strip()[:80]}')
PY
); then
            echo "ERROR: Python parser failed while checking ${func} visibility in ${file}" >&2
            parse_errors=$((parse_errors + 1))
            continue
        fi

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

if [[ $findings -gt 0 || $parse_errors -gt 0 ]]; then
    echo "FAIL: found ${findings} #ifdef guard visibility gap(s), ${parse_errors} parse error(s)" >&2
    exit 1
fi

echo "OK: no #ifdef guard visibility gaps found"
exit 0
