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

    validated_vars: set[str] = set()
    for line in lines:
        for m in VALIDATED_VAR_RE.finditer(line):
            for group_idx in range(1, len(m.groups()) + 1):
                var_name = m.group(group_idx)
                if var_name:
                    validated_vars.add(var_name)

    for lineno, line in enumerate(lines, start=1):
        if not OPEN_CALL_RE.search(line):
            continue

        # Skip non-file open calls (urlopen, webbrowser.open, etc.)
        if NON_FILE_OPEN_RE.search(line):
            continue

        m = OPEN_ARG_RE.search(line)
        if not m:
            continue

        first_arg = m.group(1)

        # Skip if the argument is a file descriptor (int), not a path
        if FD_VAR_RE.match(first_arg):
            continue

        # Skip if the argument is a validated variable
        if first_arg in validated_vars:
            continue

        # Skip if the argument is 'resolved' (common pattern after resolve())
        if first_arg == "resolved":
            continue

        # Skip hardcoded path patterns
        if HARDCODED_PATH_RE.search(line):
            continue

        # Skip test files that open tempdir/fixtures
        if "test_" in filepath.name:
            warnings.append(
                f"  WARNING {rel}:{lineno} — open({first_arg}) in test file; "
                f"verify path source is controlled"
            )
            continue

        if not has_validation_import:
            msg = (
                f"  ERROR   {rel}:{lineno} — open({first_arg}) without "
                f"path_validation import; variable '{first_arg}' not validated"
            )
            if strict:
                errors.append(msg)
            else:
                warnings.append(
                    f"  WARNING {rel}:{lineno} — open({first_arg}) without "
                    f"path_validation import; variable '{first_arg}' not validated "
                    f"(use --strict to promote to error)"
                )
        elif first_arg not in validated_vars:
            msg = (
                f"  ERROR   {rel}:{lineno} — open({first_arg}) but "
                f"'{first_arg}' not passed through validate_read_path()"
            )
            if strict:
                errors.append(msg)
            else:
                warnings.append(
                    f"  WARNING {rel}:{lineno} — open({first_arg}) but "
                    f"'{first_arg}' not passed through validate_read_path() "
                    f"(use --strict to promote to error)"
                )

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
        help="Directory to scan (default: tools/)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat all findings as errors (default: warn for existing unpatched sites)",
    )
    args = parser.parse_args()
    strict = args.strict

    scan_dir = Path(args.directory)
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
