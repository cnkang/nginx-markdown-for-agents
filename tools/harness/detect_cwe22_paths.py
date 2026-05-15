#!/usr/bin/env python3
"""CWE-22 Path Traversal Detection Script.

Scans Python source files in tools/ for open() calls that receive
paths from CLI arguments, function parameters, or environment variables
without passing them through validate_read_path() from
tools/lib/path_validation.py first.

Hard-coded paths within the repo (e.g. REPO_ROOT / "known-file") are
exempt.  Files that have already integrated path_validation are noted
as OK.

CAVEAT: This script uses heuristic AST/pattern matching, not a full
taint-flow analysis.  It may produce false positives (flagging safe
paths that happen to match open() patterns) and false negatives
(missing traversal risks in dynamic path construction it cannot
statically resolve).  Findings should be reviewed by a human before
acting on them.  A clean exit (code 0) does not guarantee the absence
of CWE-22 vulnerabilities — it only means no heuristic patterns were
matched.

Usage:
    python3 tools/harness/detect_cwe22_paths.py [directory]
    python3 tools/harness/detect_cwe22_paths.py tools/

Exit codes:
    0 — no findings (or only allowlisted patterns)
    1 — one or more findings requiring review
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]

VALIDATION_IMPORT_RE = re.compile(
    r"from\s+tools\.lib\.path_validation\s+import"
    r"|from\s+path_validation\s+import"
    r"|import\s+tools\.lib\.path_validation"
    r"|def\s+_resolve_repo_write_path",
)

VALIDATED_VAR_RE = re.compile(
    r"validate_read_path\s*\(\s*(\w+)"
    r"|validate_write_path_within_root\s*\(\s*(\w+)"
    r"|_resolve_repo_write_path\s*\(\s*(\w+)",
)

VALIDATED_ASSIGN_RE = re.compile(
    r"(\w+)\s*=\s*validate_read_path\s*\("
    r"|(\w+)\s*=\s*validate_write_path_within_root\s*\("
    r"|(\w+)\s*=\s*_resolve_repo_write_path\s*\(",
)

OPEN_CALL_RE = re.compile(
    r"(?:os\.)?open\s*\(",
)

OPEN_ARG_RE = re.compile(
    r"(?:os\.)?open\s*\(\s*(\w+)",
)


DIR_FD_RE = re.compile(
    r"dir_fd\s*=",
)

HARDCODED_PATH_RE = re.compile(
    r'REPO_ROOT\s*/\s*"'
    r'|Path\s*\(\s*__file__\s*\)'
    r'|"/'
    r"|[A-Z_]+_DIR\s*/\s*\"",
)

SAFE_OPEN_ARG_RE = re.compile(
    r"^(self|cls)$",
)

PYTEST_FIXTURE_RE = re.compile(
    r"\btmp_path\b",
)

DEF_LINE_RE = re.compile(
    r"^\s*def\s+",
)

REPO_ROOT_DERIVED_RE = re.compile(
    r"repo_root|REPO_ROOT",
)


_TEMPFILE_VAR_RE = re.compile(
    r"tempfile\b",
)


# Variables that are file descriptors (int), not paths
FD_VAR_RE = re.compile(
    r"^(fd|file_fd|parent_fd|sock|pipe_fd)$",
)

# Imports/functions that are not the builtin open()
NON_FILE_OPEN_RE = re.compile(
    r"urlopen|webbrowser\.open",
)

# Lines that are Python comments or docstrings
COMMENT_RE = re.compile(
    r"^\s*#|^\s*\"\"\"|^\s*'''"
)

EXEMPT_FILES = {
    "tools/lib/path_validation.py",
    "tools/harness/detect_cwe22_paths.py",
}

# Pattern (d): Path() construction from user-derived variables without
# validation.  Per commit 847479c, constructing Path from user input
# before validation enables path traversal even if validate_read_path
# is called later.  The safe pattern is: resolved = validate_read_path(arg)
# then Path(resolved).
_PATH_CONSTRUCTION_RE = re.compile(
    r"Path\s*\(\s*(args?\.\w+|sys\.argv\[\d+\]|options?\.\w+|(?:cli_|user_)\w+)\s*\)",
)

# User-derived variable names commonly used in CLI/path construction
_USER_DERIVED_VAR_RE = re.compile(
    r"^(args|options|cli_|user_|input_|untrusted_)",
)


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _emit_finding(
    strict: bool, errors: list[str], warnings: list[str],
    error_msg: str, warning_msg: str,
) -> None:
    """Append a finding as error (strict) or warning."""
    if strict:
        errors.append(error_msg)
    else:
        warnings.append(warning_msg)


def _is_safe_open_context(
    first_arg: str,
    line: str,
    lines: list[str],
    lineno: int,
    validated_vars: set[str],
    hardcoded_vars: set[str],
) -> bool:
    """Return True if this open() call is in a known-safe context."""
    if _is_safe_open_arg(first_arg, validated_vars, hardcoded_vars):
        return True
    path_open_receiver = _extract_path_open_receiver(line)
    if path_open_receiver and path_open_receiver in hardcoded_vars:
        return True
    return _is_safe_open_line_context(line, lines, lineno)


def _is_safe_open_arg(
    first_arg: str, validated_vars: set[str], hardcoded_vars: set[str],
) -> bool:
    """Return True when the open() first argument is already trusted."""
    if SAFE_OPEN_ARG_RE.match(first_arg):
        return True
    if FD_VAR_RE.match(first_arg):
        return True
    return first_arg in validated_vars or first_arg in hardcoded_vars


def _is_safe_open_line_context(
    line: str, lines: list[str], lineno: int,
) -> bool:
    """Return True when open() appears in a known-safe source context."""
    if DEF_LINE_RE.search(line):
        return True
    if HARDCODED_PATH_RE.search(line):
        return True
    if PYTEST_FIXTURE_RE.search(line):
        return True
    if REPO_ROOT_DERIVED_RE.search(line):
        return True
    if DIR_FD_RE.search(line):
        return True
    if lineno < len(lines) and DIR_FD_RE.search(lines[lineno]):
        return True
    return False


def _extract_assignment_lhs(line: str) -> str | None:
    """Extract assignment variable on the left side of '='."""
    if "=" not in line:
        return None
    lhs = line.split("=", 1)[0].strip()
    if lhs.isidentifier():
        return lhs
    return None


def _extract_path_open_receiver(line: str) -> str | None:
    """Extract `foo` from `foo.open(` using deterministic parsing."""
    needle = ".open("
    idx = line.find(needle)
    if idx <= 0:
        return None
    receiver = line[:idx].strip()
    if receiver.isidentifier():
        return receiver
    return None


def _is_file_derived_assignment(line: str) -> bool:
    if "=" not in line:
        return False
    rhs = line.split("=", 1)[1]
    return (
        "__file__" in rhs
        or "repo_root" in rhs
        or "REPO_ROOT" in rhs
    )


def _is_tempfile_assignment(line: str) -> bool:
    if "=" not in line:
        return False
    rhs = line.split("=", 1)[1]
    return (
        "_write_tmp_json" in rhs
        or "_temp_output_path" in rhs
        or "tempfile." in rhs
    )


def _classify_open_call(
    first_arg: str,
    line: str,
    lines: list[str],
    lineno: int,
    has_validation_import: bool,
    validated_vars: set[str],
    hardcoded_vars: set[str],
    filepath: Path,
    rel: str,
    strict: bool,
) -> tuple[list[str], list[str]]:
    """Classify a single open() call and return (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    if _is_safe_open_context(
        first_arg, line, lines, lineno, validated_vars, hardcoded_vars,
    ):
        return errors, warnings

    if "test_" in filepath.name:
        warnings.append(
            f"  WARNING {rel}:{lineno} — open({first_arg}) in test file; "
            f"verify path source is controlled"
        )
        return errors, warnings

    if not has_validation_import:
        _emit_finding(strict, errors, warnings,
            f"  ERROR   {rel}:{lineno} — open({first_arg}) without "
            f"path_validation import; variable '{first_arg}' not validated",
            f"  WARNING {rel}:{lineno} — open({first_arg}) without "
            f"path_validation import; variable '{first_arg}' not validated "
            f"(use --strict to promote to error)",
        )
    elif first_arg not in validated_vars:
        _emit_finding(strict, errors, warnings,
            f"  ERROR   {rel}:{lineno} — open({first_arg}) but "
            f"'{first_arg}' not passed through validate_read_path()",
            f"  WARNING {rel}:{lineno} — open({first_arg}) but "
            f"'{first_arg}' not passed through validate_read_path() "
            f"(use --strict to promote to error)",
        )

    return errors, warnings


def _extract_regex_groups(match: re.Match, target: set[str]) -> None:
    """Add all non-None groups from a regex match to the target set."""
    for group_idx in range(1, len(match.groups()) + 1):
        value = match.group(group_idx)
        if value:
            target.add(value)


_PATH_WRAPPED_RE = re.compile(
    r"(\w+)\s*=\s*Path\s*\(\s*(\w+)\s*\)",
)


def _collect_validated_vars(lines: list[str]) -> set[str]:
    """Extract variable names assigned from validation calls (LHS only).

    Also propagates validation through Path() wrappers: if
    ``resolved = validate_read_path(...)`` and later
    ``path = Path(resolved)``, then ``path`` is also considered validated.
    """
    validated_vars: set[str] = set()
    for line in lines:
        for m in VALIDATED_ASSIGN_RE.finditer(line):
            _extract_regex_groups(m, validated_vars)

    for line in lines:
        for m in _PATH_WRAPPED_RE.finditer(line):
            lhs = m.group(1)
            rhs = m.group(2)
            if rhs in validated_vars:
                validated_vars.add(lhs)

    return validated_vars


def _collect_hardcoded_vars(lines: list[str]) -> set[str]:
    """Extract variable names assigned from __file__/repo_root/REPO_ROOT.

    Also collects variables assigned from tempfile helpers and
    repo-root-derived helper functions.
    """
    hardcoded_vars: set[str] = set()
    hardcoded_vars.add("repo_root")
    hardcoded_vars.add("REPO_ROOT")

    _add_assignment_lhs_by_predicate(
        lines, _is_file_derived_assignment, hardcoded_vars,
    )
    _add_assignment_lhs_by_predicate(
        lines, _is_tempfile_assignment, hardcoded_vars,
    )

    for line in lines:
        for m in _PATH_WRAPPED_RE.finditer(line):
            lhs = m.group(1)
            rhs = m.group(2)
            if rhs in hardcoded_vars:
                hardcoded_vars.add(lhs)

    return hardcoded_vars


def _add_assignment_lhs_by_predicate(
    lines: list[str],
    predicate: Callable[[str], bool],
    target: set[str],
) -> None:
    """Add assignment LHS names for lines matching a predicate."""
    for line in lines:
        if not predicate(line):
            continue
        lhs = _extract_assignment_lhs(line)
        if lhs:
            target.add(lhs)


def _scan_open_calls(
    lines: list[str],
    has_validation_import: bool,
    validated_vars: set[str],
    hardcoded_vars: set[str],
    filepath: Path,
    rel: str,
    strict: bool,
) -> tuple[list[str], list[str]]:
    """Scan lines for open() calls and classify each."""
    errors: list[str] = []
    warnings: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        if not OPEN_CALL_RE.search(line):
            continue

        if NON_FILE_OPEN_RE.search(line):
            continue

        m = OPEN_ARG_RE.search(line)
        if not m:
            continue

        first_arg = m.group(1)

        call_errors, call_warnings = _classify_open_call(
            first_arg, line, lines, lineno,
            has_validation_import,
            validated_vars, hardcoded_vars, filepath, rel, strict,
        )
        errors.extend(call_errors)
        warnings.extend(call_warnings)

    return errors, warnings


def _scan_path_constructions(
    lines: list[str],
    rel: str,
    strict: bool,
) -> tuple[list[str], list[str]]:
    """Scan for Path() construction from user input before validation.

    Per commit 847479c, Path(user_input) before validate_read_path
    enables path traversal.  This catches direct Path(args.x),
    Path(sys.argv[N]), etc.

    Args:
        lines: Source lines.
        rel: Display path for reporting.
        strict: If True, promote warnings to errors.

    Returns:
        Tuple of (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        if COMMENT_RE.search(line):
            continue

        for m in _PATH_CONSTRUCTION_RE.finditer(line):
            user_expr = m.group(1)

            msg_prefix = "WARNING" if not strict else "ERROR"
            detail = (
                f"Path({user_expr}) from user input before validation — "
                f"pass through validate_read_path() first "
                f"(per commit 847479c / AGENTS.md Rule 33)"
            )
            full_msg = f"  {msg_prefix} {rel}:{lineno} — {detail}"

            if strict:
                errors.append(full_msg)
            else:
                warnings.append(full_msg)

    return errors, warnings


def check_file(
    filepath: Path, *, strict: bool = False,
) -> tuple[list[str], list[str]]:
    """Check a single Python file for CWE-22 violations.

    Args:
        filepath: Path to the Python file to check.

    Returns:
        Tuple of (errors, warnings) lists.
    """
    errors: list[str] = []
    warnings: list[str] = []

    rel = _display_path(filepath)
    if rel in EXEMPT_FILES:
        return errors, warnings

    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return errors, warnings

    lines = source.splitlines()

    has_validation_import = any(VALIDATION_IMPORT_RE.search(line) for line in lines)

    validated_vars = _collect_validated_vars(lines)
    hardcoded_vars = _collect_hardcoded_vars(lines)

    call_errors, call_warnings = _scan_open_calls(
        lines, has_validation_import, validated_vars, hardcoded_vars,
        filepath, rel, strict,
    )
    errors.extend(call_errors)
    warnings.extend(call_warnings)

    # ── Pattern (d): Path() from user input before validation ──
    # Per commit 847479c: Path(user_input) before validate_read_path
    # enables path traversal even if validated later.
    path_errors, path_warnings = _scan_path_constructions(
        lines, rel, strict,
    )
    errors.extend(path_errors)
    warnings.extend(path_warnings)

    return errors, warnings


def main() -> int:
    """Main entry point.

    Returns:
        Exit code: 0 for pass, 1 for findings.
    """
    parser = argparse.ArgumentParser(
        description="Detect CWE-22 path traversal risks in Python tooling scripts",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="tools",
        help="Directory to scan (default: tools/); trusted input only",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat all findings as errors (default: warn for existing unpatched sites)",
    )
    args = parser.parse_args()
    strict = args.strict

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.lib import path_validation
        scan_dir = Path(path_validation.validate_read_path(
            args.directory, purpose="scan directory",
        ))
    except (ImportError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: directory validation failed: {exc}", file=sys.stderr)
        return 1
    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory", file=sys.stderr)
        return 1

    print("=== CWE-22 Path Traversal Detection ===", file=sys.stderr)
    print(f"Scanning: {scan_dir}", file=sys.stderr)
    print("", file=sys.stderr)

    all_errors: list[str] = []
    all_warnings: list[str] = []

    py_files = sorted(scan_dir.rglob("*.py"))
    for filepath in py_files:
        file_errors, file_warnings = check_file(filepath, strict=strict)
        all_errors.extend(file_errors)
        all_warnings.extend(file_warnings)

    for e in all_errors:
        print(e, file=sys.stderr)
    for w in all_warnings:
        print(w, file=sys.stderr)

    print("", file=sys.stderr)
    print("=== Summary ===", file=sys.stderr)
    print(f"  Errors:   {len(all_errors)}", file=sys.stderr)
    print(f"  Warnings: {len(all_warnings)}", file=sys.stderr)
    print("", file=sys.stderr)

    if all_errors:
        print(
            f"FAIL: {len(all_errors)} error(s) found — fix before merge",
            file=sys.stderr,
        )
        return 1

    if all_warnings:
        print(
            f"PASS with warnings: {len(all_warnings)} warning(s) — review recommended",
            file=sys.stderr,
        )
        return 0

    print("PASS: no CWE-22 findings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
