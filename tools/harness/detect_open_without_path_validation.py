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

# Scope label used for the module-level scope (no enclosing function or
# class).  Referenced by _find_scope, _is_safe_path_expr, and
# _is_safe_name_expr.
MODULE_SCOPE = "<module>"

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


def _collect_validated_vars(tree: ast.AST) -> dict[str, set[str]]:
    """Collect variable names assigned from a validation-helper call,
    keyed by enclosing scope.

    Handles::

        resolved = validate_read_path(x)
        out = validate_write_path_within_root(x, root)
        safe = self._resolve_repo_write_path(x)

    Also propagates through simple Path() wrappers::

        p = Path(resolved)   # where resolved is already validated

    Only direct assignments at any scope are tracked; nested expressions
    and conditional reassignments are NOT modelled (conservative).

    Scope-qualified names ("scope:var") prevent a validated variable in
    one function from incorrectly validating an unrelated variable with
    the same name in a different function or scope.
    """
    parent_map: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    scope_validated: dict[str, set[str]] = {}

    _collect_direct_validation_assignments(tree, parent_map, scope_validated)
    _propagate_path_wrappers(tree, parent_map, scope_validated)

    return scope_validated


def _collect_direct_validation_assignments(
    tree: ast.AST,
    parent_map: dict[int, ast.AST],
    scope_validated: dict[str, set[str]],
) -> None:
    """Collect direct assignments from validation helpers."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        name = _func_call_name(value)
        if name in VALIDATION_FUNCS:
            scope = _find_scope(node, parent_map)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scope_validated.setdefault(scope, set()).add(target.id)


_PATH_CTOR_NAMES = {"Path"}


def _is_path_constructor(func: ast.expr) -> bool:
    """Return True if *func* is a ``Path`` constructor call target."""
    if isinstance(func, ast.Name) and func.id in _PATH_CTOR_NAMES:
        return True
    return isinstance(func, ast.Attribute) and func.attr in _PATH_CTOR_NAMES


# Trusted module-level path roots treated as safe sources for Path()
# wrappers when propagating validated status.
_TRUSTED_ROOT_NAMES = {"REPO_ROOT", "repo_root"}


def _maybe_propagate_path_wrapper(
    node: ast.Assign,
    parent_map: dict[int, ast.AST],
    scope_validated: dict[str, set[str]],
) -> None:
    """If *node* is ``x = Path(validated_var)``, mark the wrapper target
    as validated in its enclosing scope.  Otherwise do nothing.
    """
    value = node.value
    if not isinstance(value, ast.Call) or not _is_path_constructor(value.func):
        return
    if not value.args:
        return
    arg = value.args[0]
    if not isinstance(arg, ast.Name):
        return
    src_scope = _find_scope(arg, parent_map)
    src_vars = scope_validated.get(src_scope, set())
    if arg.id in src_vars or arg.id in _TRUSTED_ROOT_NAMES:
        dst_scope = _find_scope(node, parent_map)
        scope_validated.setdefault(dst_scope, set()).add(arg.id)


def _propagate_path_wrappers(
    tree: ast.AST,
    parent_map: dict[int, ast.AST],
    scope_validated: dict[str, set[str]],
) -> None:
    """Propagate validated status through Path(validated_var) wrappers."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            _maybe_propagate_path_wrapper(node, parent_map, scope_validated)


def _find_scope(node: ast.AST, parent_map: dict[int, ast.AST]) -> str:
    """Find the enclosing scope name for a node."""
    cur = node
    while id(cur) in parent_map:
        cur = parent_map[id(cur)]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return f"func:{cur.name}"
        if isinstance(cur, ast.ClassDef):
            return f"class:{cur.name}"
    return MODULE_SCOPE


def _expr_derives_from_hardcoded(
    node: ast.AST, hardcoded: set[str],
) -> bool:
    """Return True if *node* is an expression where ALL Name nodes are
    trusted hardcoded path sources.

    Recognizes ``__file__`` and names already in *hardcoded*.  Returns
    False if ANY Name node in the expression tree is not trusted,
    ensuring mixed expressions like ``REPO_ROOT / user_input`` are
    properly rejected.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            if sub.id != "__file__" and sub.id not in hardcoded:
                return False
    return True


def _collect_hardcoded_vars(tree: ast.AST) -> set[str]:
    """Collect variables assigned from safe (trusted) path sources.

    Trusted sources include:
      - ``__file__`` / ``REPO_ROOT`` / ``repo_root`` (module-relative roots)
      - string literals (``out = "output.json"``) — literal strings are
        compile-time constants and cannot carry runtime taint
      - ``Path(__file__)``, ``Path(REPO_ROOT)`` wrappers
      - ``REPO_ROOT / "filename"`` joins with a hardcoded left side

    Variables holding untrusted runtime values (function parameters,
    ``sys.argv``, ``os.environ``, CLI args) are NOT included.
    """
    hardcoded: set[str] = {"REPO_ROOT", "repo_root"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        _try_add_hardcoded(node, hardcoded)

    return hardcoded


def _try_add_hardcoded(node: ast.Assign, hardcoded: set[str]) -> None:
    """Try to add assignment targets to hardcoded set if value is safe."""
    value = node.value

    if isinstance(value, ast.Name) and value.id in ("__file__", *hardcoded):
        _add_targets(node, hardcoded)
        return

    if _expr_derives_from_hardcoded(value, hardcoded):
        _add_targets(node, hardcoded)
        return

    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        _add_targets(node, hardcoded)
        return

    if isinstance(value, ast.Call):
        _try_add_path_call_hardcoded(node, value, hardcoded)
        return

    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Div):
        if _is_safe_path_expr(value, {}, hardcoded):
            _add_targets(node, hardcoded)


def _try_add_path_call_hardcoded(
    node: ast.Assign, value: ast.Call, hardcoded: set[str],
) -> None:
    """Add targets if Call is Path(hardcoded_var)."""
    if _is_path_constructor(value.func) and value.args:
        arg = value.args[0]
        if isinstance(arg, ast.Name) and arg.id in hardcoded:
            _add_targets(node, hardcoded)


def _add_targets(node: ast.Assign, target_set: set[str]) -> None:
    """Add all assignment target names to the set."""
    for target in node.targets:
        if isinstance(target, ast.Name):
            target_set.add(target.id)


def _is_safe_path_expr(
    node: ast.AST,
    validated_vars: dict[str, set[str]],
    hardcoded_vars: set[str],
    scope: str = MODULE_SCOPE,
) -> bool:
    """Return True if the path expression is considered safe.

    Safe means: literal string, direct validation-helper call, a known
    validated variable (in the same scope), a known hardcoded variable,
    a pytest fixture, or a derived attribute (``.parent``, ``.name``) of
    a validated variable.
    """
    if isinstance(node, ast.Constant):
        return True

    if isinstance(node, ast.Call):
        return _is_safe_call_expr(node, validated_vars, hardcoded_vars, scope)

    if isinstance(node, ast.Name):
        return _is_safe_name_expr(node, validated_vars, hardcoded_vars, scope)

    if isinstance(node, ast.Attribute):
        return _is_safe_attr_expr(node, validated_vars, hardcoded_vars, scope)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return (
            _is_safe_path_expr(node.left, validated_vars, hardcoded_vars, scope)
            and _is_safe_path_expr(node.right, validated_vars, hardcoded_vars, scope)
        )

    if isinstance(node, ast.JoinedStr):
        return all(
            isinstance(v, ast.Constant)
            for v in node.values
        )

    return False


def _is_safe_call_expr(
    node: ast.Call,
    validated_vars: dict[str, set[str]],
    hardcoded_vars: set[str],
    scope: str,
) -> bool:
    """Return True if a Call node is a safe path expression."""
    name = _func_call_name(node)
    if name in VALIDATION_FUNCS:
        return True
    if _is_path_constructor(node.func) and node.args:
        if _is_safe_path_expr(node.args[0], validated_vars, hardcoded_vars, scope):
            return True
    return False


def _is_safe_name_expr(
    node: ast.Name,
    validated_vars: dict[str, set[str]],
    hardcoded_vars: set[str],
    scope: str,
) -> bool:
    """Return True if a Name node is a safe path expression."""
    scope_vars = validated_vars.get(scope, set())
    module_vars = validated_vars.get(MODULE_SCOPE, set())
    if node.id in scope_vars or node.id in module_vars or node.id in hardcoded_vars:
        return True
    if node.id in PYTEST_FIXTURE_NAMES:
        return True
    return False


def _is_safe_attr_expr(
    node: ast.Attribute,
    validated_vars: dict[str, set[str]],
    hardcoded_vars: set[str],
    scope: str,
) -> bool:
    """Return True if an Attribute node is a safe path expression."""
    if node.attr in DERIVED_ATTRS:
        if _is_safe_path_expr(node.value, validated_vars, hardcoded_vars, scope):
            return True
    return False


_OPEN_CTOR_NAMES = {"open"}


def _is_open_call(node: ast.Call) -> bool:
    """Return True if *node* is a builtin ``open(...)`` or a
    ``Path(...).open(...)`` / ``some_var.open(...)`` call.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id in _OPEN_CTOR_NAMES:
        return True
    return isinstance(func, ast.Attribute) and func.attr in _OPEN_CTOR_NAMES


_OPEN_DETAIL_TEMPLATE = (
    "open({expr}) — path not passed through "
    "validate_read_path() / validate_write_path_within_root() "
    "/ _resolve_repo_write_path()"
)


def _build_open_finding(
    rel: str, lineno: int, expr_str: str, strict: bool,
) -> str:
    """Format a single open()-validation finding line."""
    level = "ERROR" if strict else "WARNING"
    detail = _OPEN_DETAIL_TEMPLATE.format(expr=expr_str)
    return f"  {level}   {rel}:{lineno} — {detail}"


def _find_open_calls(
    tree: ast.AST,
    validated_vars: dict[str, set[str]],
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

    parent_map: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_open_call(node):
            continue
        if not node.args:
            continue

        path_arg = node.args[0]
        scope = _find_scope(node, parent_map)

        if _is_safe_path_expr(path_arg, validated_vars, hardcoded_vars, scope):
            continue

        finding = _build_open_finding(rel, node.lineno, _unparse(path_arg), strict)
        if strict:
            errors.append(finding)
        else:
            warnings.append(finding)

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