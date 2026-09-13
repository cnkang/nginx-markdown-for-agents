#!/usr/bin/env python3
"""Detect duplicate top-level definitions in the repository's Python tooling.

A second definition of the same name silently replaces the first, so an edit
that lands in the earlier copy has no effect and the later one keeps running.
That is hard to see by reading a diff, so it is checked mechanically.

Exit 0 when every file defines each top-level name once, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("tools", "packaging")


BINDING_TARGET_TYPES = (ast.Name, ast.Tuple, ast.List, ast.Starred)


def _is_binding_target(target: ast.AST) -> bool:
    """True for a target that binds a name rather than assigning to an attribute."""
    return isinstance(target, BINDING_TARGET_TYPES)


BINDING_TARGET_GETTERS = {
    ast.Assign: lambda node: list(node.targets),
    ast.AnnAssign: lambda node: [node.target],
    ast.For: lambda node: [node.target],
    ast.AsyncFor: lambda node: [node.target],
    ast.With: lambda node: [
        item.optional_vars for item in node.items if item.optional_vars is not None
    ],
}


def _imported_names(node: ast.AST) -> list[str]:
    """Return the names a top-level import binds."""
    if isinstance(node, ast.Import):
        # `import a.b` binds `a` at runtime, but two different submodules of the
        # same package are both legitimate, so the module path is the identity
        # that matters here.
        return [alias.asname or alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [alias.asname or alias.name for alias in node.names if alias.name != "*"]
    return []


def _bound_targets(node: ast.AST) -> list[ast.AST]:
    """Return the binding targets a top-level statement introduces."""
    getter = BINDING_TARGET_GETTERS.get(type(node))
    if getter is None:
        return []
    return [target for target in getter(node) if _is_binding_target(target)]


def _bound_names(node: ast.AST) -> list[str]:
    """Return the top-level names a binding statement introduces."""
    return [
        child.id
        for target in _bound_targets(node)
        for child in ast.walk(target)
        if isinstance(child, ast.Name)
    ]


def duplicate_names(path: Path) -> list[str]:
    """Return the top-level names this file defines more than once."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        else:
            names.extend(_imported_names(node))
            names.extend(_bound_names(node))

    return sorted(name for name, count in Counter(names).items() if count > 1)


def collect_errors(root: Path = REPO_ROOT) -> list[str]:
    """Return one message per file that defines a top-level name twice."""
    errors: list[str] = []
    for scan_root in SCAN_ROOTS:
        for path in sorted((root / scan_root).rglob("*.py")):
            if ".venv" in path.parts or "build" in path.parts:
                continue
            try:
                names = duplicate_names(path)
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                errors.append(f"{path.relative_to(root)}: cannot parse ({exc})")
                continue
            for name in names:
                errors.append(
                    f"{path.relative_to(root)}: top-level name {name!r} is defined "
                    "more than once; the earlier definition is dead code"
                )
    return errors


def main() -> int:
    """Report duplicate definitions and exit non-zero when any are found."""
    errors = collect_errors()
    if errors:
        print("FAIL: duplicate top-level definitions found")
        for error in errors:
            print(f"  {error}")
        return 1
    print("OK: no duplicate top-level definitions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
