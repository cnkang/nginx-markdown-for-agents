#!/usr/bin/env python3
"""Detect unused and repeated Python imports (pyflakes unused-import /
repeated-import, as CodeQL reports them).

Two rules:

1. An imported module name that is never referenced anywhere in the file is
   dead weight and misleads readers about what the module depends on.
2. A name imported again inside a function shadows the module-level import
   there. The outer binding is dead only if nothing *outside* that function
   still resolves to it -- a shadow in one function does not kill an import
   another function uses. Reporting it as dead regardless produces false
   positives, so uses are tracked per scope.

Both rules were found on `main` by CodeQL after the local gates passed, so
neither had local coverage.

Usage:
    python3 tools/harness/detect_unused_imports.py [--root DIR]

Exits 0 when clean, 1 when any finding is reported.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path  # noqa: E402

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


def _function_scopes(tree: ast.AST) -> list[ast.AST]:
    """Every function and lambda in the tree.

    ``ast.walk`` does not visit the module first, so scopes are collected
    explicitly: a scope's members are the nodes no *other* function contains.
    """
    functions: list[ast.AST] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]
    return functions


def _owning_scope(functions: list[ast.AST], target: ast.AST) -> ast.AST | None:
    """The innermost function containing ``target``, or None for module scope.

    Innermost matters: an import inside a nested function belongs to that
    function, not its enclosing one.
    """
    best: ast.AST | None = None
    best_size = -1
    for fn in functions:
        if fn is target:
            continue
        if not any(child is target for child in ast.walk(fn)):
            continue
        size = sum(1 for _ in ast.walk(fn))
        if best is None or size < best_size:
            best, best_size = fn, size
    return best


class _ScopeScanner(ast.NodeVisitor):
    """Record the names loaded in each function scope, plus the module scope.

    Decorators, default values and annotations are evaluated in the enclosing
    scope, so a name used there belongs to the parent, not the function.
    """

    def __init__(self, functions: list[ast.AST]) -> None:
        self.functions = functions
        self.loads: dict[int, set[str]] = {id(fn): set() for fn in functions}
        self.module_loads: set[str] = set()
        self.annotation_names: set[str] = set()
        self._stack: list[ast.AST | None] = [None]

    def _sink(self) -> set[str]:
        owner = self._stack[-1]
        return self.module_loads if owner is None else self.loads[id(owner)]

    def _enter(self, node: ast.AST) -> None:
        self._stack.append(node)
        self.loads.setdefault(id(node), set())

    def _leave(self) -> None:
        self._stack.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # `x: Path = ...`, `items: List[str] = []` -- the annotation is a type
        # reference in both its plain and subscripted forms.
        self._record_annotation(node.annotation)
        if node.annotation is not None:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        if node.target is not None:
            self.visit(node.target)

    def _record_annotation(self, node: ast.AST | None) -> None:
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self.annotation_names |= _annotation_tokens(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Decorators, defaults, parameters and the return annotation are all
        # evaluated in the enclosing scope, so a type named only there keeps
        # its module-level import alive.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for arg in self._all_args(node.args):
            self.visit(arg)
        for default in self._defaults(node.args):
            self.visit(default)
        self._record_annotation(node.returns)
        if node.returns is not None:
            self.visit(node.returns)
        for arg in self._all_args(node.args):
            self._record_annotation(getattr(arg, "annotation", None))
        self._enter(node)
        for stmt in node.body:
            self.visit(stmt)
        self._leave()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for arg in self._all_args(node.args):
            self.visit(arg)
        for default in self._defaults(node.args):
            self.visit(default)
        self._enter(node)
        self.visit(node.body)
        self._leave()

    @staticmethod
    def _all_args(args: ast.arguments) -> list[ast.AST]:
        """Every parameter node, so an annotation-only name still counts."""
        found: list[ast.AST] = [
            *args.posonlyargs,
            *args.args,
            *args.kwonlyargs,
        ]
        if args.vararg is not None:
            found.append(args.vararg)
        if args.kwarg is not None:
            found.append(args.kwarg)
        return found

    @staticmethod
    def _defaults(args: ast.arguments) -> list[ast.expr]:
        found: list[ast.expr] = list(args.defaults)
        found += [d for d in args.kw_defaults if d is not None]
        return found

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._sink().add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # ``os.path`` binds ``os``; walk to the root of the chain.
        target: ast.expr = node
        while isinstance(target, ast.Attribute):
            target = target.value
        if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Load):
            self._sink().add(target.id)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # A bare string constant is data. String *annotations* are recorded by
        # _record_annotation at the point the annotation is visited, so
        # `msg = "sys"` cannot keep a dead import alive.
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


def _partition_imports(
    tree: ast.AST, functions: list[ast.AST]
) -> tuple[set[str], dict[str, dict[int, int]]]:
    """Split imports into module-level names and per-function re-imports.

    Returns ``(module_level, nested)`` where ``nested`` maps a name to the
    function scopes that import it, keyed by scope id, with the line of the
    first such import.
    """
    module_level: set[str] = set()
    nested: dict[str, dict[int, int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        names = _bound_names(node)
        if not names:
            continue
        line = getattr(node, "lineno", 0)
        scope = _owning_scope(functions, node)
        for name in names:
            if scope is None:
                module_level.add(name)
            else:
                nested.setdefault(name, {}).setdefault(id(scope), line)
    return module_level, nested


def _all_loads(scanner: _ScopeScanner, functions: list[ast.AST]) -> set[str]:
    """Every name loaded in any scope, plus names used in string annotations."""
    loads = set(scanner.module_loads)
    for fn in functions:
        loads |= scanner.loads.get(id(fn), set())
    return loads


def _outer_binding_survives(
    name: str,
    scopes: dict[int, int],
    scanner: _ScopeScanner,
    functions: list[ast.AST],
) -> bool:
    """True when something outside the shadowing functions still uses ``name``.

    A function-local re-import only kills the module-level binding when no
    other scope resolves to it.
    """
    if name in scanner.module_loads or name in scanner.annotation_names:
        return True
    return any(
        id(fn) not in scopes and scanner.loads.get(id(fn), set()) & {name}
        for fn in functions
    )


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

        functions = _function_scopes(tree)
        scanner = _ScopeScanner(functions)
        scanner.visit(tree)
        module_imports, nested = _partition_imports(tree, functions)
        loads = _all_loads(scanner, functions)

        # Rule 1: a module-level import nothing loads anywhere.
        for name in sorted(module_imports):
            if name not in loads and name not in scanner.annotation_names:
                errors.append(f"{relative}: unused import {name!r}")

        # Rule 2: a function-local re-import.
        for name in sorted(set(nested) & module_imports):
            scopes = nested[name]
            if _outer_binding_survives(name, scopes, scanner, functions):
                continue
            first_line = min(scopes.values())
            errors.append(
                f"{relative}: {name!r} is imported again inside a function "
                f"(line {first_line}), shadowing the module-level import; "
                "nothing outside that function uses the outer binding"
            )
    return errors


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=None,
        help="directory to scan (defaults to the repository root)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = (
        validate_read_path(args.root, purpose="unused-import scan root")
        if args.root
        else REPO_ROOT
    )
    errors = collect_errors(root)
    if errors:
        print(f"FAIL: {len(errors)} import finding(s)")
        for error in errors:
            print(f"  {error}")
        return 1
    print("OK: no unused or repeated Python imports")
    return 0


if __name__ == "__main__":
    sys.exit(main())