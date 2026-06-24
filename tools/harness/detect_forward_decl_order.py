#!/usr/bin/env python3
"""Forward-Declaration Order Detection Script (Rule 24).

Scans C header files for forward declarations (function prototypes)
that reference types defined by typedefs appearing LATER in the same
file.  Per AGENTS.md Rule 24, forward declarations must not reference
types that have not yet been defined in the translation unit — this
causes silent type errors and compiler warnings under C99 strict mode.

Detection strategy:
  1. Parse each .h file for typedef declarations of the form
     ``typedef struct foo_s foo_t``, ``typedef enum bar_e bar_t``,
     or closing-brace typedefs like ``} foo_t;``.
  2. Record the line number where each typedef name is defined.
  3. Parse for function declarations matching a function-like pattern:
     ``return_type function_name(args);``
  4. For each function declaration, check if any parameter type name
     matches a typedef that appears LATER in the file.
  5. Report violations as WARNING (advisory).

This is a heuristic detector — it uses pattern matching, not a full
C parser.  Findings should be reviewed by a human.

Usage:
    python3 tools/harness/detect_forward_decl_order.py [directory]
    python3 tools/harness/detect_forward_decl_order.py components/nginx-module/src

Exit codes:
    0 — no findings, or findings reported as warnings (advisory)
    1 — directory validation error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Typedef patterns:
#   typedef struct foo_s foo_t;        → captures "foo_t"
#   typedef struct foo_s { ... } foo_t; → captured via closing-brace pattern
#   typedef enum bar_e { ... } bar_t;   → captured via closing-brace pattern
#   } foo_t;                            → captures "foo_t" (closing-brace typedef)
TYPEDEF_STRUCT_RE = re.compile(
    r"^\s*typedef\s+struct\s+\w+\s+(\w+_t)\s*;",
)
TYPEDEF_ENUM_RE = re.compile(
    r"^\s*typedef\s+enum\s+\w+\s+(\w+_t)\s*;",
)
TYPEDEF_CLOSING_BRACE_RE = re.compile(
    r"^\s*\}\s*(\w+_t)\s*;",
)
# Also catch: typedef struct foo_s foo_t;  (inline, no braces)
# and typedef enum bar_e bar_t;
TYPEDEF_SIMPLE_RE = re.compile(
    r"^\s*typedef\s+(?:struct|enum)\s+\w+\s+(\w+_t)\s*;",
)

# Function declaration pattern (applied to a logical single line that
# may have been assembled by joining physical continuation lines):
#   return_type function_name(args);
# Heuristic: a line ending in ");" that contains an identifier followed
# by "(" and at least one parameter.  The groups below capture, in order,
# the (optionally const-qualified) return type, the function name, and the
# raw parameter list text.
FUNC_DECL_RE = re.compile(
    r"^\s*"
    r"(?:static\s+|extern\s+|inline\s+)*"
    r"((?:const\s+)?[\w\s\*]+?)\s*"
    r"(\w+)\s*"
    r"\(([^;]*)\)\s*;"
)

# Parameter type extraction: pull out type names that end in _t
PARAM_TYPE_RE = re.compile(
    r"\b(\w+_t)\b",
)

# Comment lines (skip)
COMMENT_RE = re.compile(
    r"^\s*/\*|^\s*\*\s|^\s*//",
)

# Pattern that looks like the start of a function declaration: a line
# containing an identifier followed by "(" that does NOT yet end in ");".
# Used to detect multi-line prototypes for joining.
FUNC_DECL_START_RE = re.compile(
    r"^\s*"
    r"(?:static\s+|extern\s+|inline\s+)*"
    r"(?:const\s+)?[\w\s\*]+\s*"
    r"\w+\s*\([^;]*$"
)


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _parse_typedef_lines(lines: list[str]) -> dict[str, int]:
    """Extract typedef names and their defining line numbers.

    Args:
        lines: Source lines of the header file.

    Returns:
        Dict mapping typedef name → 1-indexed line number where defined.
    """
    typedefs: dict[str, int] = {}
    for idx, line in enumerate(lines, start=1):
        if COMMENT_RE.search(line):
            continue
        for pattern in (
            TYPEDEF_SIMPLE_RE,
            TYPEDEF_STRUCT_RE,
            TYPEDEF_ENUM_RE,
            TYPEDEF_CLOSING_BRACE_RE,
        ):
            m = pattern.search(line)
            if m:
                name = m.group(1)
                if name not in typedefs:
                    typedefs[name] = idx
                break
    return typedefs


def _join_multiline_prototype(
    lines: list[str], start: int, n: int,
) -> tuple[int, str] | None:
    """Join a multi-line prototype starting at 'start' into a single string.

    Returns (end_index, joined_line) or None if not a real prototype.
    The end_index is the line after the last continuation line.
    """
    joined = lines[start].rstrip()
    j = start + 1
    while j < n:
        cont = lines[j].rstrip()
        if COMMENT_RE.search(cont):
            j += 1
            continue
        joined += " " + cont
        if ");" in cont or joined.rstrip().endswith(");"):
            break
        j += 1
        if j - start > 40:
            break
    m = FUNC_DECL_RE.search(joined)
    if m:
        return (j + 1, joined)
    return None


def _extract_decl_from_match(
    m: re.Match[str], lineno: int,
) -> tuple[int, str, list[str]]:
    """Build a (lineno, func_name, param_type_names) tuple from a regex
    match on FUNC_DECL_RE.
    """
    func_name = m.group(2)
    params = m.group(3)
    param_types = list(PARAM_TYPE_RE.findall(params))
    return (lineno, func_name, param_types)


def _is_skippable_line(line: str) -> bool:
    """Return True for comment and preprocessor lines that should not be
    inspected for function declarations.
    """
    if COMMENT_RE.search(line):
        return True
    return line.lstrip().startswith("#")


def _parse_func_decls(
    lines: list[str],
) -> list[tuple[int, str, list[str]]]:
    """Extract function declarations and their parameter types.

    Handles both single-line and multi-line prototypes.  A multi-line
    prototype is detected when a line looks like the start of a
    declaration (qualified return type + name + open paren, no closing
    ``;``) and is joined with following lines until a ``);`` terminator
    is reached.  The recorded line number is the first physical line of
    the prototype.

    Args:
        lines: Source lines of the header file.

    Returns:
        List of (lineno, func_name, param_type_names) tuples.
        param_type_names are the _t-suffixed type names found in params.
    """
    decls: list[tuple[int, str, list[str]]] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if _is_skippable_line(line):
            i += 1
            continue

        m = FUNC_DECL_RE.search(line)
        if m:
            decls.append(_extract_decl_from_match(m, i + 1))
            i += 1
            continue

        if FUNC_DECL_START_RE.search(line):
            result = _join_multiline_prototype(lines, i, n)
            if result:
                j, joined = result
                m = FUNC_DECL_RE.search(joined)
                if m:
                    decls.append(_extract_decl_from_match(m, i + 1))
                    i = j
                    continue
            i += 1
            continue

        i += 1
    return decls


def check_file(filepath: Path) -> list[str]:
    """Check a single header file for forward-declaration-order issues.

    Args:
        filepath: Path to the .h file to check.

    Returns:
        List of warning message strings.
    """
    warnings: list[str] = []
    rel = _display_path(filepath)

    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return warnings

    if not source.strip():
        return warnings

    lines = source.splitlines()

    typedefs = _parse_typedef_lines(lines)
    func_decls = _parse_func_decls(lines)

    for lineno, func_name, param_types in func_decls:
        for ptype in param_types:
            if ptype not in typedefs:
                # Type not defined in this file at all (may be from
                # another header); skip rather than false-positive.
                continue
            typedef_line = typedefs[ptype]
            if typedef_line > lineno:
                warnings.append(
                    f"  WARNING {rel}:{lineno} — function '{func_name}' "
                    f"references type '{ptype}' (typedef at line "
                    f"{typedef_line}) before it is defined "
                    f"(Rule 24: forward decl must not precede typedef)"
                )

    return warnings


def main() -> int:
    """Main entry point.

    Returns:
        Exit code: 0 for pass (advisory), 1 for directory error.
    """
    parser = argparse.ArgumentParser(
        description="Detect forward declarations that precede their "
                    "typedef definitions (Rule 24)",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="components/nginx-module/src",
        help="Directory to scan (default: components/nginx-module/src); "
             "trusted input only",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any forward-declaration order violation",
    )
    args = parser.parse_args()

    # Validate the scan directory through the project's path validation
    # helper (Rule 12/33 compliance — the detector itself must not bypass
    # path validation).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.path_validation import validate_read_path
        scan_dir = Path(validate_read_path(
            args.directory, purpose="scan directory",
        ))
    except (ImportError, FileNotFoundError):
        # Fallback: if path_validation is unavailable, resolve manually.
        # ValueError from validate_read_path propagates (path traversal rejected).
        scan_dir_raw = str(args.directory)
        scan_dir = Path(scan_dir_raw)
        if not scan_dir.is_absolute():
            scan_dir = REPO_ROOT / scan_dir
        try:
            scan_dir = scan_dir.resolve()
        except OSError:
            pass

    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory", file=sys.stderr)
        return 1

    print("=== Forward-Declaration Order Detection (Rule 24) ===", file=sys.stderr)
    print(f"Scanning: {scan_dir}", file=sys.stderr)
    print(f"Strict: {1 if args.strict else 0}", file=sys.stderr)
    print("", file=sys.stderr)

    all_warnings: list[str] = []

    h_files = sorted(scan_dir.rglob("*.h"))
    for filepath in h_files:
        file_warnings = check_file(filepath)
        all_warnings.extend(file_warnings)

    for w in all_warnings:
        print(w, file=sys.stderr)

    print("", file=sys.stderr)
    print("=== Summary ===", file=sys.stderr)
    print(f"  Warnings: {len(all_warnings)}", file=sys.stderr)
    print("", file=sys.stderr)

    if all_warnings:
        print(
            f"PASS with warnings: {len(all_warnings)} forward-declaration "
            f"order issue(s) — review recommended (advisory per Rule 24)",
            file=sys.stderr,
        )
    else:
        print(
            "PASS: no forward-declaration order issues found",
            file=sys.stderr,
        )

    if args.strict and all_warnings:
        print(
            f"FAIL (strict): {len(all_warnings)} forward-declaration "
            f"order violation(s) — fix before merge (Rule 24)",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())