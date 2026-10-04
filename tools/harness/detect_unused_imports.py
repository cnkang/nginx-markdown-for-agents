#!/usr/bin/env python3
"""Detect unused and repeated Python imports (pyflakes unused-import /
repeated-import, as CodeQL reports them).

Two rules:

1. An imported module name that is never referenced anywhere in the file is
   dead weight and misleads readers about what the module depends on.
2. A name imported again inside a function shadows the module-level import.
   The inner binding wins, so the outer one is dead -- and if a later edit
   removes the inner import, the function silently starts using whatever the
   outer one happens to be.

Both were found on `main` by CodeQL after the local gates passed, so neither
has local coverage.

Usage:
    python3 tools/harness/detect_unused_imports.py [--strict]

Exits 0 when clean, 1 when any finding is reported.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if ".git" not in path.parts
        and "build" not in path.parts
        and "target" not in path.parts
        and "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and "node_modules" not in path.parts
    )


def _bound_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Return the local names an import statement binds.

    ``from __future__ import ...`` binds nothing at runtime -- those are
    compiler directives -- so it never contributes a name to check.
    """
    if isinstance(node, ast.Import):
        return [alias.asname or alias.name.split(".")[0] for alias in node.names]
    if node.module == "__future__":
        return []
    return [alias.asname or alias.name for alias in node.names if alias.name != "*"]


class _UsageScanner(ast.NodeVisitor):
    """Collect every identifier load in the module, with its binding depth.

    A load inside a function is recorded separately so a module-level import
    shadowed by a function-local one is still reported: the outer binding can
    never be reached from that function.
    """

    def __init__(self) -> None:
        self.module_loads: set[str] = set()
        self.nested_loads: set[str] = set()
        self.string_annotations: set[str] = set()
        self._depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            if self._depth:
                self.nested_loads.add(node.id)
            else:
                self.module_loads.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # ``os.path`` binds ``os``; walk to the root of the chain.
        target: ast.expr = node
        while isinstance(target, ast.Attribute):
            target = target.value
        if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Load):
            if self._depth:
                self.nested_loads.add(target.id)
            else:
                self.module_loads.add(target.id)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Names used only inside string annotations still count as usage.
        if isinstance(node.value, str):
            for token in _annotation_tokens(node.value):
                self.string_annotations.add(token)
        self.generic_visit(node)


def _annotation_tokens(text: str) -> set[str]:
    """Return identifier-shaped tokens in a string annotation."""
    tokens: set[str] = set()
    current = ""
    for char in text:
        if char.isalnum() or char == "_":
            current += char
        else:
            if current:
                tokens.add(current)
            current = ""
    if current:
        tokens.add(current)
    return tokens


def _all_uses(scanner: _UsageScanner) -> set[str]:
    return scanner.module_loads | scanner.nested_loads | scanner.string_annotations


def collect_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in _iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            # A file that will not parse is another gate's problem; skipping it
            # here keeps this detector from masking that failure.
            continue
        relative = path.relative_to(root)

        scanner = _UsageScanner()
        scanner.visit(tree)
        uses = _all_uses(scanner)

        module_imports: dict[str, int] = {}
        nested_imports: dict[str, int] = {}

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = _bound_names(node)
            if not names:
                continue
            line = getattr(node, "lineno", 0)
            if _is_inside_function(tree, node):
                for name in names:
                    # Report the first shadowing import, not an arbitrary one:
                    # ast.walk visits in an order that does not start here.
                    nested_imports.setdefault(name, line)
            else:
                for name in names:
                    module_imports[name] = module_imports.get(name, 0) + 1

        for name in sorted(module_imports):
            if name not in uses:
                errors.append(
                    f"{relative}: unused import {name!r}"
                )

        for name in sorted(set(nested_imports) & set(module_imports)):
            errors.append(
                f"{relative}: {name!r} is imported again inside a function "
                f"(line {nested_imports[name]}), shadowing the module-level "
                "import; the outer binding is dead"
            )
    return errors


def _is_inside_function(tree: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if child is target:
                return True
    return False


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat findings as errors (the default; kept for symmetry)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    errors = collect_errors(REPO_ROOT)
    if errors:
        print(f"FAIL: {len(errors)} import finding(s)")
        for error in errors:
            print(f"  {error}")
        return 1
    print("OK: no unused or repeated Python imports")
    return 0


if __name__ == "__main__":
    sys.exit(main())