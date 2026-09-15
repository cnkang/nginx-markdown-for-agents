#!/usr/bin/env python3
"""Detect duplicate top-level definitions in the repository's Python tooling.

A second definition of the same function or class silently replaces the first, so
an edit that lands in the earlier copy has no effect and the later one keeps
running.  That is hard to see by reading a diff, so it is checked mechanically.

Only repeated `def` and `class` statements are reported.  Rebinding a name, or
importing a module twice, is ordinary Python that a reader can see, and flagging
it would trade a real signal for noise.

Exit 0 when every file defines each top-level function and class once.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("tools", "packaging")


def duplicate_names(path: Path) -> list[str]:
    """Return the top-level names this file defines more than once."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)


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
