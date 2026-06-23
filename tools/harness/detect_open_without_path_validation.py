#!/usr/bin/env python3
"""AST-based open() path-validation checker.

Enforces AGENTS.md Rule 12 (CWE-22 path traversal prevention) by scanning
Python files in ``tools/`` for ``open()`` calls whose path argument does
NOT pass through one of the path-validation helpers:

    - validate_read_path()
    - validate_write_path_within_root()
    - _resolve_repo_write_path()

Unlike the sibling ``detect_cwe22_paths.py`` (which uses regex heuristics),
this script parses each file with Python's ``ast`` module for precise
call-site detection.

Classification of the ``open()`` first argument:

    a. Literal string (hardcoded path)         → OK, skip
    b. Direct call to a validation helper      → OK, skip
    c. Variable assigned from a validation
       helper (tracked via ast.Assign)          → OK, skip
    d. Anything else (CLI arg, function param,
       env var, sys.argv, …)                    → WARNING

CAVEAT: This is a static AST scan, not a full taint-flow analysis.
It tracks simple variable assignments (``x = validate_read_path(...)``)
but cannot follow data flow through complex control paths, containers,
or cross-function returns.  False positives and false negatives are
possible — findings should be reviewed by a human.  A clean exit
(code 0) does not guarantee the absence of CWE-22 vulnerabilities.

Usage:
    python3 tools/harness/detect_open_without_path_validation.py
    python3 tools/harness/detect_open_without_path_validation.py --path tools/
    python3 tools/harness/detect_open_without_path_validation.py --strict

Exit codes:
    0 — no findings (or only warnings in non-strict mode)
    1 — one or more findings with --strict, or a usage error
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

VALIDATION_FUNCS = {
    "validate_read_path",
    "validate_write_path_within_root",
    "_resolve_repo_write_path",
}

# Trusted fixture names that pytest provides — paths derived from
# these are safe in test contexts and should not trigger CWE-22 warnings.
PYTEST_FIXTURE_NAMES = {"tmp_path", "tmpdir"}

# Path attribute accesses that derive from a validated variable.
# When the open() argument is `validated_var.parent` or
# `validated_var.name`, we treat it as safe because the parent path
# was already boundary-checked.
DERIVED_ATTRS = {"parent", "name", "with_suffix", "stem", "suffix"}

# Files that are exempt from scanning (the validator itself, this script,
# and the regex-based sibling).
EXEMPT_FILES = {
    "tools/lib/path_validation.py",
    "tools/harness/detect_open_without_path_validation.py",
    "tools/harness/detect_cwe22_paths.py",
}


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for *path*."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _unparse(node: ast.AST) -> str:
    """Best-effort short source rendering of an AST node."""
    try:
        return ast.unparse(node)
    except Exception:
        # Fallback for very old Python or edge cases.
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        return ast.dump(node)


def _func_call_name(node: ast.AST) -> str | None:
    """Return the dotted name of a Call's func, or None.

    Handles ``validate_read_path(...)``, ``path_validation.validate_read_path(...)``,
    and ``self._resolve_repo_write_path(...)`` style calls.
    """
    func = node.func if isinstance(node, ast.Call) else None
    if func is None:
        return None
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        # e.g. module.func or self.method
        return func.attr
    return None


def _collect_validated_vars(tree: ast.AST) -> set[str]:
    """Collect variable names assigned from a validation-helper call.

    Handles::

        resolved = validate_read_path(x)
        out = validate_write_path_within_root(x, root)
        safe = self._resolve_repo_write_path(x)

    Also propagates through simple Path() wrappers::

        p = Path(resolved)   # where resolved is already validated

    Only direct assignments at any scope are tracked; nested expressions
    and conditional reassignments are NOT modelled (conservative).
    """
    validated: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        name = _func_call_name(value)
        if name in VALIDATION_FUNCS:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    validated.add(target.id)

    # Propagate through Path(validated_var) wrappers.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        # Match Path(x) or pathlib.Path(x)
        func = value.func
        is_path_ctor = (
            (isinstance(func, ast.Name) and func.id == "Path")
            or (isinstance(func, ast.Attribute) and func.attr == "Path")
        )
        if not is_path_ctor:
            continue
        if not value.args:
            continue
        arg = value.args[0]
        if isinstance(arg, ast.Name) and arg.id in validated:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    validated.add(target.id)

    return validated


def _collect_hardcoded_vars(tree: ast.AST) -> set[str]:
    """Collect variables assigned from __file__ / REPO_ROOT / repo_root.

    These are treated as safe hardcoded path sources.
    """
    hardcoded: set[str] = {"REPO_ROOT", "repo_root"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        # __file__
        if isinstance(value, ast.Name) and value.id == "__file__":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    hardcoded.add(target.id)
            continue
        # REPO_ROOT / repo_root used directly
        if isinstance(value, ast.Name) and value.id in hardcoded:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    hardcoded.add(target.id)
            continue
        # Path(__file__) or Path(REPO_ROOT) or REPO_ROOT / "x"
        if isinstance(value, ast.Call):
            func = value.func
            is_path_ctor = (
                (isinstance(func, ast.Name) and func.id == "Path")
                or (isinstance(func, ast.Attribute) and func.attr == "Path")
            )
            if is_path_ctor and value.args:
                arg = value.args[0]
                if isinstance(arg, ast.Name) and arg.id in hardcoded:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            hardcoded.add(target.id)
            continue
        # BinOp: REPO_ROOT / "filename"  (ast.Div with REPO_ROOT on left)
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div):
            left = value.left
            if isinstance(left, ast.Name) and left.id in hardcoded:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        hardcoded.add(target.id)

    return hardcoded


def _is_safe_path_expr(
    node: ast.AST,
    validated_vars: set[str],
    hardcoded_vars: set[str],
) -> bool:
    """Return True if the path expression is considered safe.

    Safe means: literal string, direct validation-helper call, a known
    validated variable, a known hardcoded variable, a pytest fixture,
    or a derived attribute (``.parent``, ``.name``) of a validated
    variable.
    """
    # (a) Literal string or any constant.
    if isinstance(node, ast.Constant):
        return True

    # (b) Direct call to a validation helper.
    if isinstance(node, ast.Call):
        name = _func_call_name(node)
        if name in VALIDATION_FUNCS:
            return True
        # Path(validated_var) or Path(hardcoded_var)
        func = node.func
        is_path_ctor = (
            (isinstance(func, ast.Name) and func.id == "Path")
            or (isinstance(func, ast.Attribute) and func.attr == "Path")
        )
        if is_path_ctor and node.args:
            if _is_safe_path_expr(node.args[0], validated_vars, hardcoded_vars):
                return True
        return False

    # (c) Variable that was assigned from a validation helper.
    if isinstance(node, ast.Name):
        if node.id in validated_vars or node.id in hardcoded_vars:
            return True
        # Pytest fixtures (tmp_path, tmpdir) are trusted test paths.
        if node.id in PYTEST_FIXTURE_NAMES:
            return True
        return False

    # (d) Derived attribute of a validated variable:
    #     validated_var.parent, validated_var.name, etc.
    if isinstance(node, ast.Attribute):
        if node.attr in DERIVED_ATTRS:
            if _is_safe_path_expr(node.value, validated_vars, hardcoded_vars):
                return True
        return False

    # (e) BinOp like REPO_ROOT / "file" — safe if left side is hardcoded
    #     or a pytest fixture (tmp_path / "output.json").
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        if _is_safe_path_expr(node.left, validated_vars, hardcoded_vars):
            return True
        return False

    # JoinedStr (f-string) with only literal parts.
    if isinstance(node, ast.JoinedStr):
        return all(
            isinstance(v, ast.Constant)
            for v in node.values
        )

    return False


def _find_open_calls(
    tree: ast.AST,
    validated_vars: set[str],
    hardcoded_vars: set[str],
    rel: str,
    strict: bool,
) -> tuple[list[str], list[str]]:
    """Find all open() calls and classify their path argument.

    Detects both builtin ``open(...)`` and ``Path(...).open(...)`` /
    ``some_var.open(...)``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        is_open = False

        # builtin open(...)
        if isinstance(func, ast.Name) and func.id == "open":
            is_open = True
        # something.open(...) — attribute access
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            is_open = True

        if not is_open:
            continue

        if not node.args:
            # open() with no args — not a file-open we care about.
            continue

        path_arg = node.args[0]

        if _is_safe_path_expr(path_arg, validated_vars, hardcoded_vars):
            continue

        # Not safe → finding.
        expr_str = _unparse(path_arg)
        lineno = node.lineno
        detail = (
            f"open({expr_str}) — path not passed through "
            f"validate_read_path() / validate_write_path_within_root() "
            f"/ _resolve_repo_write_path()"
        )

        if strict:
            errors.append(f"  ERROR   {rel}:{lineno} — {detail}")
        else:
            warnings.append(f"  WARNING {rel}:{lineno} — {detail}")

    return errors, warnings


def check_file(
    filepath: Path, *, strict: bool = False,
) -> tuple[list[str], list[str]]:
    """Check a single Python file for unvalidated open() calls.

    Args:
        filepath: Path to the Python file to check.
        strict: If True, promote warnings to errors.

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

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        warnings.append(
            f"  WARNING {rel}:{exc.lineno or '?'} — "
            f"could not parse file: {exc.msg}"
        )
        return errors, warnings

    validated_vars = _collect_validated_vars(tree)
    hardcoded_vars = _collect_hardcoded_vars(tree)

    file_errors, file_warnings = _find_open_calls(
        tree, validated_vars, hardcoded_vars, rel, strict,
    )
    errors.extend(file_errors)
    warnings.extend(file_warnings)

    return errors, warnings


def main() -> int:
    """Main entry point.

    Returns:
        Exit code: 0 for pass / advisory, 1 for findings under --strict.
    """
    parser = argparse.ArgumentParser(
        description=(
            "AST-based detection of open() calls without path validation "
            "(AGENTS.md Rule 12 / CWE-22)"
        ),
    )
    parser.add_argument(
        "--path",
        default="tools",
        help="Directory to scan recursively (default: tools/); trusted input only",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any finding (default: exit 0 with warnings)",
    )
    args = parser.parse_args()
    strict = args.strict

    # Validate the scan directory itself through the project's helper.
    # We insert tools/ (parents[1]) so ``from lib.path_validation import ...``
    # resolves the same way other tooling scripts do it.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.path_validation import validate_read_path
        scan_dir = Path(validate_read_path(
            args.path, purpose="scan directory",
        ))
    except (ImportError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: directory validation failed: {exc}", file=sys.stderr)
        return 1
    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory", file=sys.stderr)
        return 1

    print("=== open() Path Validation Detection (AST) ===", file=sys.stderr)
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
            f"PASS with warnings: {len(all_warnings)} warning(s) "
            f"— review recommended",
            file=sys.stderr,
        )
        return 0

    print("PASS: no unvalidated open() calls found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())