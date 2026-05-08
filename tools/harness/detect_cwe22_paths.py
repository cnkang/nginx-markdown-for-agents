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

REPO_ROOT = Path(__file__).resolve().parents[2]

VALIDATION_IMPORT_RE = re.compile(
    r"from\s+tools\.lib\.path_validation\s+import"
    r"|from\s+path_validation\s+import"
    r"|import\s+tools\.lib\.path_validation",
)

VALIDATED_VAR_RE = re.compile(
    r"validate_read_path\s*\(\s*(\w+)"
    r"|validate_write_path_within_root\s*\(\s*(\w+)",
)

VALIDATED_ASSIGN_RE = re.compile(
    r"(\w+)\s*=\s*validate_read_path\s*\("
    r"|(\w+)\s*=\s*validate_write_path_within_root\s*\(",
)

OPEN_CALL_RE = re.compile(
    r"open\s*\(",
)

OPEN_ARG_RE = re.compile(
    r"(?<!\w)open\s*\(\s*(\w+)",
)

HARDCODED_PATH_RE = re.compile(
    r'REPO_ROOT\s*/\s*"'
    r'|Path\s*\(\s*__file__\s*\)'
    r'|"/'
    r"|[A-Z_]+_DIR\s*/\s*\"",
)

# Variables that are file descriptors (int), not paths
FD_VAR_RE = re.compile(
    r"^(fd|file_fd|parent_fd|sock|pipe_fd)$",
)

# Imports/functions that are not the builtin open()
NON_FILE_OPEN_RE = re.compile(
    r"urlopen|webbrowser\.open",
)

EXEMPT_FILES = {
    "tools/lib/path_validation.py",
    "tools/harness/detect_cwe22_paths.py",
}


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


def _classify_open_call(
    first_arg: str,
    line: str,
    has_validation_import: bool,
    validated_vars: set[str],
    filepath: Path,
    lineno: int,
    rel: str,
    strict: bool,
) -> tuple[list[str], list[str]]:
    """Classify a single open() call and return (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    if FD_VAR_RE.match(first_arg):
        return errors, warnings

    if first_arg in validated_vars:
        return errors, warnings

    if HARDCODED_PATH_RE.search(line):
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


def _collect_validated_vars(lines: list[str]) -> set[str]:
    """Extract variable names that are validated or assigned from validation calls."""
    validated_vars: set[str] = set()
    for line in lines:
        for m in VALIDATED_VAR_RE.finditer(line):
            _extract_regex_groups(m, validated_vars)
        for m in VALIDATED_ASSIGN_RE.finditer(line):
            _extract_regex_groups(m, validated_vars)
    return validated_vars


def _scan_open_calls(
    lines: list[str],
    has_validation_import: bool,
    validated_vars: set[str],
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
            first_arg, line, has_validation_import,
            validated_vars, filepath, lineno, rel, strict,
        )
        errors.extend(call_errors)
        warnings.extend(call_warnings)

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

    call_errors, call_warnings = _scan_open_calls(
        lines, has_validation_import, validated_vars,
        filepath, rel, strict,
    )
    errors.extend(call_errors)
    warnings.extend(call_warnings)

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

    scan_dir = Path(args.directory)
    if args.directory != "tools":
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
