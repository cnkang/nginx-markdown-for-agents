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
  --guard=NAME  feature guard to check (default: ${GUARD_NAME})
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

# Step 1: Extract function names declared inside #ifdef GUARD_NAME blocks in the
# header (prototype style, ending with ';') plus function definitions inside the
# guard across every .c/.h file in SRC_DIR, so a guarded-only definition behind
# an unguarded declaration is still collected and checked.
if ! guarded_funcs=$(python3 - "${HEADER_FILE}" "${GUARD_NAME}" "${SRC_DIR}" <<'PY'
import re
import sys
import os

header_path, guard_name, src_dir = sys.argv[1:4]

def guard_enabled(stack):
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

def update_stack(stack, kind, expression):
    if kind == 'endif':
        if stack:
            stack.pop()
    elif kind in ('ifdef', 'ifndef'):
        name = expression.strip()
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

def scan_declarations(path):
    """Collect function names declared inside the guard (prototype style)."""
    funcs = set()
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    stack = []
    pending_decl = None
    for line in lines:
        stripped = line.strip()
        directive = re.match(r'#\s*(ifdef|ifndef|if|else|elif|endif)\b(.*)', stripped)
        if directive:
            update_stack(stack, directive.group(1), directive.group(2))
            continue
        if not guard_enabled(stack):
            continue
        if stripped.startswith(('//', '/*', '*')):
            continue
        name = _definition_signature(stripped, line)
        if name is not None:
            if ';' in line:
                funcs.add(name)
            elif re.search(r'\(\s*$', stripped) or re.search(r'\)\s*$', line.rstrip()):
                # Multi-line declaration: name( on this line, arguments
                # and semicolon on the following lines.
                pending_decl = name
        elif pending_decl is not None and ';' in line:
            funcs.add(pending_decl)
            pending_decl = None
    return funcs

def _definition_signature(stripped, line):
    """Return the function name when ``line`` begins an nginx-style
    definition signature, else None.

    The signature must start at column 0 (the optional leading type tokens
    plus the function name are the first tokens of the line).  Control-flow
    statements (``if``, ``for``, ``while``, ``switch``, ``return``, ``do``,
    ``else``) never qualify, so a call ending in ``) {`` is not recorded as
    a definition.  A name buried mid-expression is not a signature.
    """
    if re.match(r'(?:if|for|while|switch|return|do|else)\b', stripped):
        return None
    m = re.match(
        r'(?:[A-Za-z_][A-Za-z0-9_]*\s+(?:\*\s*)?)*'
        r'(ngx_http_markdown_\w+)\s*\(',
        stripped,
    )
    return m.group(1) if m is not None else None


def scan_definitions(path):
    """Collect function names defined inside the guard (body style)."""
    funcs = set()
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    stack = []
    pending_def = None
    for line in lines:
        stripped = line.strip()
        directive = re.match(r'#\s*(ifdef|ifndef|if|else|elif|endif)\b(.*)', stripped)
        if directive:
            update_stack(stack, directive.group(1), directive.group(2))
            continue
        if not guard_enabled(stack):
            continue
        if stripped.startswith(('//', '/*', '*')):
            continue
        if pending_def is not None:
            # Multi-line signature: a definition ends at the opening brace,
            # a prototype at the semicolon.
            if '{' in line:
                funcs.add(pending_def)
                pending_def = None
            elif ';' in line:
                pending_def = None
            continue
        name = _definition_signature(stripped, line)
        if name is not None and re.search(r'\)\s*\{\s*$', line.rstrip()):
            # One-line definition: name(args) {
            funcs.add(name)
        elif name is not None and (
            re.search(r'\(\s*$', stripped)
            or re.search(r'\)\s*$', line.rstrip())
        ):
            # Multi-line signature start; may be a definition or a call.
            # ``name(...)`` closing the parameter list with the opening
            # brace on the following line is the common nginx body style.
            pending_def = name
    return funcs


def scan_definitions_outside_guard(path):
    """Collect function names defined OUTSIDE the guard (feature-disabled
    build must still link them)."""
    funcs = set()
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    stack = []
    pending_def = None
    for line in lines:
        stripped = line.strip()
        directive = re.match(r'#\s*(ifdef|ifndef|if|else|elif|endif)\b(.*)', stripped)
        if directive:
            update_stack(stack, directive.group(1), directive.group(2))
            continue
        if guard_enabled(stack):
            continue
        if stripped.startswith(('//', '/*', '*')):
            continue
        if pending_def is not None:
            if '{' in line:
                funcs.add(pending_def)
                pending_def = None
            elif ';' in line:
                pending_def = None
            continue
        name = _definition_signature(stripped, line)
        if name is not None and re.search(r'\)\s*\{\s*$', line.rstrip()):
            funcs.add(name)
        elif name is not None and (
            re.search(r'\(\s*$', stripped)
            or re.search(r'\)\s*$', line.rstrip())
        ):
            pending_def = name
    return funcs


funcs = set()
funcs |= scan_declarations(header_path)
outside_defs = set()
for dirpath, _dirnames, filenames in os.walk(src_dir):
    for filename in sorted(filenames):
        if not filename.endswith(('.c', '.h')):
            continue
        path = os.path.join(dirpath, filename)
        funcs |= scan_definitions(path)
        outside_defs |= scan_definitions_outside_guard(path)

for f in sorted(outside_defs):
    print('OUTSIDE_DEF\t' + f)
for f in sorted(funcs):
    print(f)
PY
); then
    echo "ERROR: Python parser failed while extracting guarded functions from ${HEADER_FILE} / ${SRC_DIR}" >&2
    exit 1
fi

outside_defs="$(printf '%s\n' "$guarded_funcs" | awk -F'\t' '$1 == "OUTSIDE_DEF" { print $2 }')"
guarded_funcs="$(printf '%s\n' "$guarded_funcs" | awk -F'\t' '$1 != "OUTSIDE_DEF" { print $1 }')"

if [[ -z "$guarded_funcs" ]]; then
    echo "OK: no functions found inside #ifdef ${GUARD_NAME} blocks"
    exit 0
fi

# Step 2: For each guarded function, search for references outside #ifdef blocks
# in all .c and .h files in SRC_DIR.  One Python process scans every file once,
# tracking guard state per file, so the cost is O(files) not O(files x funcs).
findings=0
parse_errors=0

if ! scan_result=$(python3 - "${SRC_DIR}" "${GUARD_NAME}" "${HEADER_FILE}" "${guarded_funcs}" "${outside_defs}" <<'PY'
import re
import sys
import os

src_dir, guard_name, header_path = sys.argv[1:4]
func_names = sys.argv[4].split()
# Functions with an equivalent feature-disabled definition are linkable in
# every build; only calls to a guarded-only symbol are visibility gaps.
outside_defs = set(sys.argv[5].split()) if len(sys.argv) > 5 else set()
func_names = [name for name in func_names if name not in outside_defs]

def same_file(left, right):
    return os.path.realpath(left) == os.path.realpath(right)

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

def guard_enabled(stack):
    return any(name == guard_name and active for name, active, _ in stack)

def push_directive(stack, kind, expression):
    if kind == 'endif':
        if stack:
            stack.pop()
    elif kind in ('ifdef', 'ifndef'):
        name = expression.strip()
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

def scan_file(path):
    """Return (file, [(line_no, text)]) references outside the guard."""
    hits = []
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    stack = []
    pending_definition = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        directive = re.match(r'#\s*(ifdef|ifndef|if|else|elif|endif)\b(.*)', stripped)
        if directive:
            push_directive(stack, directive.group(1), directive.group(2))
            continue
        if guard_enabled(stack):
            continue
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
        for func_name in func_names:
            # Skip a prototype in the owning header only — including
            # multi-line declarations whose parameter list and semicolon
            # span several lines.
            if same_file(header_path, path):
                if func_name + '(' in line and ';' in line:
                    continue
                if re.search(
                    r'^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
                    r'[\w\s*]+\b' + re.escape(func_name) + r'\s*\([^;]*$',
                    line,
                ) and '{' not in line:
                    continue
            definition_pattern = (
                r'^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
                r'[\w\s*]+\b' + re.escape(func_name)
                + r'\s*\([^;]*\)\s*\{'
            )
            if re.match(definition_pattern, line):
                continue
            split_definition_pattern = (
                r'^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?'
                + re.escape(func_name) + r'\s*\([^;]*$'
            )
            if prev_is_type_line and re.match(split_definition_pattern, line):
                pending_definition = True
                break
            if func_name + '(' in line:
                # A signature fragment that ends with the opening paren (no
                # closing paren on this line) can be a multi-line definition
                # whose parameter list and opening brace follow.  Only treat
                # it as such when the preceding line is a return-type line; a
                # multiline call (``fn(\n arg\n);``) must be reported as a
                # reference instead of being skipped.
                if prev_is_type_line and re.search(
                    r'\b' + re.escape(func_name) + r'\s*\(\s*$', line
                ):
                    pending_definition = True
                    break
                hits.append((i, line.strip()[:80], func_name))
    return hits

for dirpath, _dirnames, filenames in os.walk(src_dir):
    for filename in sorted(filenames):
        if not filename.endswith(('.c', '.h')):
            continue
        path = os.path.join(dirpath, filename)
        for line_no, text, func_name in scan_file(path):
            print(f'{path}\t{line_no}\t{func_name}\t{text}')
PY
); then
    echo "ERROR: Python parser failed while scanning guard visibility" >&2
    exit 1
fi

if [[ -n "$scan_result" ]]; then
    while IFS=$'\t' read -r path line_no func_name line_text; do
        [[ -z "$path" ]] && continue
        rel_path="${path#${REPO_ROOT}/}"
        echo "ERROR: ${rel_path}:${line_no}: ${func_name}() referenced outside #ifdef ${GUARD_NAME}" >&2
        echo "  ${line_text}" >&2
        findings=$((findings + 1))
    done <<< "$scan_result"
fi

if [[ $findings -gt 0 || $parse_errors -gt 0 ]]; then
    echo "FAIL: found ${findings} #ifdef guard visibility gap(s), ${parse_errors} parse error(s)" >&2
    exit 1
fi

echo "OK: no #ifdef guard visibility gaps found"
exit 0
