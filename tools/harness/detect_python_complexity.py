#!/usr/bin/env python3
"""Detect Python functions whose cognitive complexity exceeds a threshold.

This is a lightweight, dependency-free local guard for AGENTS.md Rule 17.
It intentionally approximates Sonar's Python cognitive-complexity model:
branches, loops, exception handlers, boolean operator chains, and nested
control flow add cost.  The goal is to catch risky functions before a
SonarCloud PR analysis does, not to reproduce every analyzer edge case.

Usage:
    python3 tools/harness/detect_python_complexity.py
    python3 tools/harness/detect_python_complexity.py --path tools/harness
    python3 tools/harness/detect_python_complexity.py --threshold 15

Exit codes:
    0 - all scanned functions are at or below the threshold
    1 - at least one function exceeds the threshold, or a file cannot be parsed
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from tools.lib.path_validation import validate_read_path
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.lib.path_validation import validate_read_path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLD = 15
DEFAULT_SCAN_PATHS = ("tools/harness",)


@dataclass(frozen=True)
class FunctionComplexity:
    path: Path
    name: str
    line: int
    score: int


@dataclass(frozen=True)
class ScanResult:
    functions: list[FunctionComplexity]
    errors: list[str]


class _FunctionComplexityVisitor(ast.NodeVisitor):
    """Collect cognitive-complexity scores for functions in an AST."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.functions: list[FunctionComplexity] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        calculator = _CognitiveComplexityCalculator()
        score = calculator.score_body(node.body)
        self.functions.append(
            FunctionComplexity(
                path=self.path,
                name=node.name,
                line=node.lineno,
                score=score,
            )
        )

        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(child)
            elif isinstance(child, ast.ClassDef):
                self.visit(child)


class _CognitiveComplexityCalculator:
    """Approximate Sonar-style cognitive complexity for one function."""

    def __init__(self) -> None:
        self.score = 0

    def score_body(self, statements: list[ast.stmt]) -> int:
        self._visit_statements(statements, nesting=0)
        return self.score

    def _visit_statements(self, statements: list[ast.stmt], nesting: int) -> None:
        for statement in statements:
            self._visit_statement(statement, nesting)

    def _add_decision(self, node: ast.AST, nesting: int) -> None:
        self.score += 1 + nesting
        self.score += _boolean_complexity(node)

    def _visit_statement(self, statement: ast.stmt, nesting: int) -> None:
        if isinstance(statement, ast.If):
            self._visit_if(statement, nesting)
            return
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            self._visit_loop(statement, nesting)
            return
        if isinstance(statement, ast.Try):
            self._visit_try(statement, nesting)
            return
        if isinstance(statement, ast.Match):
            self._visit_match(statement, nesting)
            return
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return

        for child in ast.iter_child_nodes(statement):
            self._visit_child(child, nesting)

    def _visit_if(self, statement: ast.If, nesting: int) -> None:
        self._add_decision(statement.test, nesting)
        self._visit_statements(statement.body, nesting + 1)
        self._visit_orelse(statement.orelse, nesting)

    def _visit_orelse(self, statements: list[ast.stmt], nesting: int) -> None:
        if len(statements) == 1 and isinstance(statements[0], ast.If):
            self._visit_if(statements[0], nesting)
            return
        self._visit_statements(statements, nesting + 1)

    def _visit_loop(
        self, statement: ast.For | ast.AsyncFor | ast.While, nesting: int,
    ) -> None:
        test = statement.test if isinstance(statement, ast.While) else statement
        self._add_decision(test, nesting)
        self._visit_statements(statement.body, nesting + 1)
        self._visit_statements(statement.orelse, nesting + 1)

    def _visit_try(self, statement: ast.Try, nesting: int) -> None:
        self._visit_statements(statement.body, nesting)
        for handler in statement.handlers:
            self.score += 1 + nesting
            self._visit_statements(handler.body, nesting + 1)
        self._visit_statements(statement.orelse, nesting)
        self._visit_statements(statement.finalbody, nesting)

    def _visit_match(self, statement: ast.Match, nesting: int) -> None:
        self._add_decision(statement.subject, nesting)
        for case in statement.cases:
            if case.guard is not None:
                self._add_decision(case.guard, nesting + 1)
            self._visit_statements(case.body, nesting + 1)

    def _visit_child(self, child: ast.AST, nesting: int) -> None:
        if isinstance(child, ast.IfExp):
            self._add_decision(child.test, nesting)
            self._visit_child(child.body, nesting + 1)
            self._visit_child(child.orelse, nesting + 1)
            return
        if isinstance(child, ast.comprehension):
            self.score += 1 + nesting
            for condition in child.ifs:
                self._add_decision(condition, nesting + 1)
            return
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return

        for grandchild in ast.iter_child_nodes(child):
            self._visit_child(grandchild, nesting)


def _boolean_complexity(node: ast.AST) -> int:
    """Return extra cost for boolean operator chains in an expression."""
    score = 0
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp):
            score += max(0, len(child.values) - 1)
    return score


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _iter_python_files(paths: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.add(path)
        elif path.is_dir():
            files.update(path.rglob("*.py"))
    return sorted(files)


def scan_file(path: Path) -> ScanResult:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return ScanResult([], [f"{_display_path(path)}: {exc}"])

    visitor = _FunctionComplexityVisitor(path)
    visitor.visit(tree)
    return ScanResult(visitor.functions, [])


def scan_paths(paths: list[Path]) -> ScanResult:
    functions: list[FunctionComplexity] = []
    errors: list[str] = []
    for path in _iter_python_files(paths):
        result = scan_file(path)
        functions.extend(result.functions)
        errors.extend(result.errors)
    return ScanResult(functions, errors)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect Python functions over the Rule 17 complexity limit",
    )
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=None,
        help="File or directory to scan; repeatable (default: tools/harness)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Maximum allowed score (default: {DEFAULT_THRESHOLD})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    raw_paths = args.paths or list(DEFAULT_SCAN_PATHS)
    try:
        paths = [
            Path(validate_read_path(raw_path, purpose="Python complexity scan path"))
            for raw_path in raw_paths
        ]
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: path validation failed: {exc}", file=sys.stderr)
        return 1

    result = scan_paths(paths)
    over_limit = [
        function for function in result.functions
        if function.score > args.threshold
    ]

    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)

    for function in over_limit:
        print(
            "ERROR: "
            f"{_display_path(function.path)}:{function.line} "
            f"{function.name} complexity {function.score} "
            f"exceeds threshold {args.threshold}",
            file=sys.stderr,
        )

    if result.errors or over_limit:
        print(
            f"FAIL: {len(over_limit)} function(s) exceed Python complexity "
            f"threshold {args.threshold}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: "
        f"{len(result.functions)} Python function(s) at or below complexity "
        f"threshold {args.threshold}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
