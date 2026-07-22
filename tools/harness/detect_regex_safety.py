#!/usr/bin/env python3
"""detect_regex_safety.py — Detect regex patterns prone to catastrophic
backtracking (ReDoS) and dynamic regex injection.

Rule 10 (parser-regex): Avoid regex patterns with overlapping quantifiers or
backtracking hotspots.  Treat static-analysis ReDoS findings as design issues.

Detection strategy:
  1. Parse Python (.py) source files using the standard library ``ast`` module
     to identify calls to ``re.compile``, ``re.search``, ``re.match``,
     ``re.fullmatch``, ``re.findall``, ``re.finditer``, ``re.split``,
     ``re.sub``, ``re.subn``, and equivalent compiled-pattern methods.
     Import aliases (``import re as regex_module``, ``from re import search
     as regex_search``) are resolved statically.  Both positional and keyword
     argument forms are supported:
       re.search(pattern=r"...", string=...)
       re.sub(pattern=r"...", repl="...", string=...)
  2. Track a conservative scope-aware static string constant propagator so
     that ``PATTERN = r"(...)"`` followed by ``re.search(PATTERN, data)``
     resolves to the literal.  Reassignment with a dynamic value invalidates
     the binding.
  3. Classify the pattern argument into segments and pick a source:
     STATIC_LITERAL, STATIC_CONCAT, STATIC_FORMATTED, ESCAPED_DYNAMIC,
     DYNAMIC, UNKNOWN.  ``re.escape()`` only escapes its own operand; a
     concatenation like ``re.escape(x) + r"(a+)+$"`` is still analyzed for
     the static tail.
  4. For static patterns, apply heuristic checks for known dangerous
     structures (nested quantifiers with non-separator content, overlapping
     alternation, backreference after ``.*``, adjacent unbounded repetitions,
     ``re.DOTALL`` + full-document ``.*``).
  5. For dynamic/unknown patterns, emit a REVIEW finding (UNKNOWN is treated
     as REVIEW, never silently downgraded to INFO).
  6. Scan shell (.sh) scripts for ``grep -E``, ``grep -P``, ``sed -E``,
     ``rg -e``, ``rg -P``, and ``perl`` regex usage.  Pattern extraction is
     command-aware: it locates the regex command (past pipes and env
     assignments), skips options, supports ``-e``/``--regexp``/``-P`` and
     ``--``, and does not pick up patterns from preceding commands in a
     pipeline.
  7. Suppression requires an inline justification:
      ``# nosec:regex-safety -- <reason>``
  8. File-level or directory-level suppressions are not allowed.

CLI contract:
  --strict            Exit 1 on ERROR findings, parse errors, or scan/read
                      errors.  REVIEW findings are non-blocking.
  --fail-on-review    Implies --strict and additionally exits 1 on REVIEW
                      findings.  Use this gate when the repository should not
                      ship any unreviewed dynamic/unknown regex.
  Default (neither)   Advisory: exit 0 regardless of findings.

This is a heuristic detector (no full NFA analysis).  It flags known-bad
structural patterns and dynamic injection risks.  False positives should be
suppressed with an inline comment containing a non-empty justification:
  ``# nosec:regex-safety -- trusted generated token, max_input_bytes=64``

Usage:
    python3 tools/harness/detect_regex_safety.py
    python3 tools/harness/detect_regex_safety.py --strict
    python3 tools/harness/detect_regex_safety.py --strict --fail-on-review
    python3 tools/harness/detect_regex_safety.py --format json
    python3 tools/harness/detect_regex_safety.py --path tools/

Exit codes:
    0 — no blocking findings
    1 — strict/fail-on-review findings, parse errors, or scan failures
    2 — CLI usage / configuration error
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Python ``re`` module functions and the keyword that carries the pattern.
# For each function, the positional index of the pattern argument.
_RE_MODULE_FUNCS: dict[str, int] = {
    "compile": 0,
    "search": 0,
    "match": 0,
    "fullmatch": 0,
    "findall": 0,
    "finditer": 0,
    "split": 0,
    "sub": 0,
    "subn": 0,
}


# ---------------------------------------------------------------------------
# Per-API signature definitions (positional index for each named argument)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ReSignature:
    """Positional argument indices for a specific re module function."""
    pattern_index: int
    string_index: int | None
    flags_index: int | None


# Canonical Python re module API signatures:
#   re.compile(pattern, flags=0)
#   re.search(pattern, string, flags=0)
#   re.match(pattern, string, flags=0)
#   re.fullmatch(pattern, string, flags=0)
#   re.findall(pattern, string, flags=0)
#   re.finditer(pattern, string, flags=0)
#   re.split(pattern, string, maxsplit=0, flags=0)
#   re.sub(pattern, repl, string, count=0, flags=0)
#   re.subn(pattern, repl, string, count=0, flags=0)
_RE_SIGNATURES: dict[str, _ReSignature] = {
    "compile":   _ReSignature(pattern_index=0, string_index=None, flags_index=1),
    "search":    _ReSignature(pattern_index=0, string_index=1, flags_index=2),
    "match":     _ReSignature(pattern_index=0, string_index=1, flags_index=2),
    "fullmatch": _ReSignature(pattern_index=0, string_index=1, flags_index=2),
    "findall":   _ReSignature(pattern_index=0, string_index=1, flags_index=2),
    "finditer":  _ReSignature(pattern_index=0, string_index=1, flags_index=2),
    "split":     _ReSignature(pattern_index=0, string_index=1, flags_index=3),
    "sub":       _ReSignature(pattern_index=0, string_index=2, flags_index=4),
    "subn":      _ReSignature(pattern_index=0, string_index=2, flags_index=4),
}

# Compiled pattern object methods.
_PATTERN_METHODS = frozenset({
    "search", "match", "fullmatch",
    "findall", "finditer", "split", "sub", "subn",
})

# Default scan directories (relative to repo root).
_DEFAULT_SCAN_DIRS = (
    "tools", "packaging", "tests", "skills",
)

# File globs to scan.
_SCAN_GLOBS = ("*.py", "*.sh")

# Directory names to exclude from traversal.
_EXCLUDE_DIRS = frozenset({
    ".git", "target", "node_modules", ".venv", "__pycache__",
    "build", "dist", "coverage", "tmp", "temp", ".test-tmp",
})

# Pattern truncation limit for output.
_MAX_PATTERN_DISPLAY = 80

# Placeholder function name used for shell-extracted regex findings.
_SHELL_FUNCTION_NAME = "<shell>"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(Enum):
    """Finding severity levels."""
    ERROR = "ERROR"
    REVIEW = "REVIEW"
    INFO = "INFO"


class PatternSource(Enum):
    """How the pattern was classified."""
    STATIC_LITERAL = "STATIC_LITERAL"
    STATIC_CONCAT = "STATIC_CONCAT"
    STATIC_FORMATTED = "STATIC_FORMATTED"
    ESCAPED_DYNAMIC = "ESCAPED_DYNAMIC"
    DYNAMIC = "DYNAMIC"
    UNKNOWN = "UNKNOWN"


class Engine(Enum):
    """Regex engine identified."""
    PYTHON_RE = "python-re"
    SHELL_ERE = "shell-ere"
    SHELL_PCRE = "shell-pcre"


# Suppression: must include ``-- <justification>`` with non-empty text.
_SUPPRESSION_RE = re.compile(
    r"#\s*nosec:regex-safety\s*--\s*(.+)",
    re.IGNORECASE,
)
# Bare suppression without ``--`` — detected as invalid.
_BARE_SUPPRESSION_RE = re.compile(
    r"#\s*nosec:regex-safety(?!\s*--)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RegexFinding:
    """A single regex safety finding."""
    severity: Severity
    engine: Engine
    file_path: str
    line: int
    function: str
    api: str
    pattern: str
    pattern_source: PatternSource
    input_scope: str
    reason: str
    remediation: str
    compile_line: int | None = None

    def to_text(self) -> str:
        """Render finding as human-readable text."""
        short_pat = self.pattern
        if len(short_pat) > _MAX_PATTERN_DISPLAY:
            short_pat = f"{short_pat[:_MAX_PATTERN_DISPLAY]}..."
        return (
            f"{self.severity.value}: [{self.engine.value}] "
            f"{self.file_path}:{self.line} "
            f"in {self.function}() via {self.api}()\n"
            f"  pattern: {short_pat}\n"
            f"  source:  {self.pattern_source.value}\n"
            f"  input:   {self.input_scope}\n"
            f"  reason:  {self.reason}\n"
            f"  fix:     {self.remediation}"
        )

    def to_dict(self) -> dict:
        """Render finding as a JSON-serializable dict."""
        return {
            "severity": self.severity.value,
            "engine": self.engine.value,
            "path": self.file_path,
            "line": self.line,
            "function": self.function,
            "api": self.api,
            "pattern": self.pattern[:_MAX_PATTERN_DISPLAY],
            "pattern_source": self.pattern_source.value,
            "input_scope": self.input_scope,
            "reason": self.reason,
            "remediation": self.remediation,
        }


@dataclass
class ScanError:
    """A non-silent error encountered during scanning."""
    file_path: str
    line: int
    message: str

    def to_text(self) -> str:
        return f"PARSE_ERROR: {self.file_path}:{self.line}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "severity": "PARSE_ERROR",
            "path": self.file_path,
            "line": self.line,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Unified lexical binding model (scope-aware, conservative)
# ---------------------------------------------------------------------------

class _BindingKind(Enum):
    STATIC_STRING = "STATIC_STRING"
    DYNAMIC_VALUE = "DYNAMIC_VALUE"
    RE_MODULE_ALIAS = "RE_MODULE_ALIAS"
    RE_FUNCTION_ALIAS = "RE_FUNCTION_ALIAS"
    COMPILED_STATIC_PATTERN = "COMPILED_STATIC_PATTERN"
    COMPILED_DYNAMIC_PATTERN = "COMPILED_DYNAMIC_PATTERN"


@dataclass
class _Binding:
    kind: _BindingKind
    static_value: str | None = None
    real_re_api: str | None = None
    compiled_pattern: str | None = None
    compile_line: int | None = None


class _Sentinel:
    pass


_UNRESOLVED = _Sentinel()


@dataclass
class _Scope:
    bindings: dict[str, _Binding] = field(default_factory=dict)

    def lookup(self, name: str) -> _Binding | _Sentinel:
        return self.bindings.get(name, _UNRESOLVED)

    def assign(self, name: str, binding: _Binding) -> None:
        self.bindings[name] = binding

    def delete(self, name: str) -> None:
        self.bindings.pop(name, None)


def _new_scope_stack() -> list[_Scope]:
    return [_Scope()]


def _scope_lookup(stack: list[_Scope], name: str) -> _Binding | _Sentinel:
    for scope in reversed(stack):
        result = scope.lookup(name)
        if not isinstance(result, _Sentinel):
            return result
    return _UNRESOLVED


def _scope_assign(stack: list[_Scope], name: str, binding: _Binding) -> None:
    stack[-1].assign(name, binding)


def _scope_delete(stack: list[_Scope], name: str) -> None:
    for scope in reversed(stack):
        if name in scope.bindings:
            scope.delete(name)
            return


# ---------------------------------------------------------------------------
# AST-based Python regex extraction
# ---------------------------------------------------------------------------

class RegexASTVisitor(ast.NodeVisitor):
    """Walk a Python AST and collect regex API calls.

    Uses a unified lexical binding model where each scope maintains its own
    binding table.  Bindings carry kind, static value, compiled-pattern
    metadata, and re-alias information.  Name resolution follows Python's
    LEGB rules (inner-to-outer scope lookup).

    Key invariants:
      - Function/class/lambda parameters shadow outer bindings.
      - Reassignment of an ``re`` alias invalidates the alias.
      - Unknown RHS on assignment makes the target DYNAMIC_VALUE, never
        preserves the old static binding.
      - Compiled pattern reassignment to dynamic produces REVIEW on
        subsequent method calls.
    """

    def __init__(self) -> None:
        self.findings: list[RegexFinding] = []
        self.errors: list[ScanError] = []
        self._source_lines: list[str] = []
        self._file_path: str = ""
        self._tree: ast.AST | None = None
        self._scope_stack: list[_Scope] = _new_scope_stack()

    def set_source(self, tree: ast.AST, source_lines: list[str],
                    file_path: str) -> None:
        self._tree = tree
        self._source_lines = source_lines
        self._file_path = file_path

    # -- Scope management --------------------------------------------------

    def _enter_scope(self) -> None:
        self._scope_stack.append(_Scope())

    def _leave_scope(self) -> None:
        self._scope_stack.pop()

    # -- Import resolution --------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "re":
                bound = alias.asname or "re"
                _scope_assign(self._scope_stack, bound, _Binding(
                    kind=_BindingKind.RE_MODULE_ALIAS,
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "re":
            for alias in node.names:
                real_name = alias.name
                bound = alias.asname or alias.name
                _scope_assign(self._scope_stack, bound, _Binding(
                    kind=_BindingKind.RE_FUNCTION_ALIAS,
                    real_re_api=real_name,
                ))
        self.generic_visit(node)

    # -- Function / class scope tracking ----------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped_node_with_params(
            node, node.args, [d for d in node.decorator_list],
        )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped_node_with_params(
            node, node.args, [d for d in node.decorator_list],
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped_node(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_scoped_node_with_params(node, node.args, [])

    def _visit_scoped_node(self, node: ast.AST) -> None:
        self._enter_scope()
        self.generic_visit(node)
        self._leave_scope()

    def _visit_scoped_node_with_params(
        self, node: ast.AST, args: ast.arguments,
        decorators: list[ast.AST],
    ) -> None:
        self._enter_scope()
        self._bind_function_params(args)
        for child in decorators:
            self.visit(child)
        for child in ast.iter_child_nodes(node):
            if child not in decorators and child is not args:
                self.visit(child)
        self._leave_scope()

    def _bind_function_params(self, args: ast.arguments) -> None:
        for arg in args.args:
            _scope_assign(self._scope_stack, arg.arg, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        for arg in args.posonlyargs:
            _scope_assign(self._scope_stack, arg.arg, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        for arg in args.kwonlyargs:
            _scope_assign(self._scope_stack, arg.arg, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        if args.vararg:
            _scope_assign(self._scope_stack, args.vararg.arg, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        if args.kwarg:
            _scope_assign(self._scope_stack, args.kwarg.arg, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))

    # -- For / with / except / comprehension target shadow ----------------

    def visit_For(self, node: ast.For) -> None:
        self._bind_target_dynamic(node.target)
        self.visit(node.iter)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._bind_target_dynamic(node.target)
        self.visit(node.iter)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self._bind_target_dynamic(item.optional_vars)
        for child in node.body:
            self.visit(child)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self._bind_target_dynamic(item.optional_vars)
        for child in node.body:
            self.visit(child)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            _scope_assign(self._scope_stack, node.name, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        if node.type:
            self.visit(node.type)
        for child in node.body:
            self.visit(child)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, node.generators, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, node.generators, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, node.generators, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, node.generators, None)

    def _visit_comprehension(
        self, node: ast.AST,
        generators: list[ast.comprehension],
        elt: ast.AST | None,
    ) -> None:
        self._enter_scope()
        for gen in generators:
            self.visit(gen.iter)
            self._bind_target_dynamic(gen.target)
            for if_ in gen.ifs:
                self.visit(if_)
        if elt is not None:
            self.visit(elt)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        self._leave_scope()

    def _bind_target_dynamic(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            _scope_assign(self._scope_stack, target.id, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target_dynamic(elt)
        elif isinstance(target, ast.Starred):
            self._bind_target_dynamic(target.value)

    # -- Assignment tracking -----------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        binding = self._resolve_assignment_binding(node.value)
        for target in node.targets:
            self._assign_target(target, binding, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            self.generic_visit(node)
            return
        binding = self._resolve_assignment_binding(node.value)
        self._assign_target(node.target, binding, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            _scope_assign(self._scope_stack, node.target.id, _Binding(
                kind=_BindingKind.DYNAMIC_VALUE,
            ))
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                _scope_delete(self._scope_stack, target.id)
        self.generic_visit(node)

    def _assign_target(
        self, target: ast.AST, binding: _Binding, value: ast.AST,
    ) -> None:
        if isinstance(target, ast.Name):
            name = target.id
            if binding.kind == _BindingKind.COMPILED_STATIC_PATTERN:
                _scope_assign(self._scope_stack, name, binding)
            elif binding.kind == _BindingKind.COMPILED_DYNAMIC_PATTERN:
                _scope_assign(self._scope_stack, name, binding)
            else:
                _scope_assign(self._scope_stack, name, binding)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._assign_target(elt, _Binding(
                    kind=_BindingKind.DYNAMIC_VALUE,
                ), value)

    def _resolve_assignment_binding(self, value: ast.AST) -> _Binding:
        if isinstance(value, ast.Call):
            api_name = self._identify_re_call(value)
            if api_name == "compile":
                pattern_arg = self._get_call_argument(value, 0, "pattern")
                pattern_str = self._resolve_pattern(pattern_arg)
                if pattern_str is not None:
                    return _Binding(
                        kind=_BindingKind.COMPILED_STATIC_PATTERN,
                        compiled_pattern=pattern_str,
                        compile_line=value.lineno,
                    )
                return _Binding(
                    kind=_BindingKind.COMPILED_DYNAMIC_PATTERN,
                    compile_line=value.lineno,
                )
            if api_name is not None:
                return _Binding(kind=_BindingKind.DYNAMIC_VALUE)
        static = self._resolve_static_string(value)
        if static is not None:
            return _Binding(
                kind=_BindingKind.STATIC_STRING,
                static_value=static,
            )
        if isinstance(value, ast.Name):
            looked = _scope_lookup(self._scope_stack, value.id)
            if isinstance(looked, _Sentinel):
                return _Binding(kind=_BindingKind.DYNAMIC_VALUE)
            if looked.kind in (
                _BindingKind.RE_MODULE_ALIAS,
                _BindingKind.RE_FUNCTION_ALIAS,
            ):
                return _Binding(
                    kind=looked.kind,
                    real_re_api=looked.real_re_api,
                )
            return _Binding(kind=_BindingKind.DYNAMIC_VALUE)
        return _Binding(kind=_BindingKind.DYNAMIC_VALUE)

    def _resolve_static_string(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return self._resolve_joined_str(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._resolve_static_string(node.left)
            right = self._resolve_static_string(node.right)
            if left is not None and right is not None:
                return left + right
            return None
        if isinstance(node, ast.Name):
            looked = _scope_lookup(self._scope_stack, node.id)
            if isinstance(looked, _Sentinel):
                return None
            if looked.kind == _BindingKind.STATIC_STRING and looked.static_value is not None:
                return looked.static_value
            return None
        return None

    # -- Call detection -----------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        """Identify re.* and compiled-pattern.* calls."""
        # Detect duplicate positional+keyword for the same argument — treat
        # conservatively as an unknown call (don't trust either side).
        api_name, pattern_arg = self._match_regex_call(node)
        if api_name is not None:
            self._record_finding(node, api_name, pattern_arg)
        self.generic_visit(node)

    def _match_regex_call(
        self, node: ast.Call,
    ) -> tuple[str | None, ast.AST | None]:
        """Check if ``node`` is a regex API call.

        Returns (api_name, pattern_arg_node) or (None, None).
        """
        func = node.func
        if isinstance(func, ast.Attribute):
            return self._match_attribute_call(func, node)
        if isinstance(func, ast.Name):
            return self._match_name_call(func, node)
        return None, None

    def _match_attribute_call(
        self, func: ast.Attribute, node: ast.Call,
    ) -> tuple[str | None, ast.AST | None]:
        if not isinstance(func.value, ast.Name):
            return None, None
        var_name = func.value.id
        looked = _scope_lookup(self._scope_stack, var_name)
        if isinstance(looked, _Sentinel):
            return None, None
        if looked.kind == _BindingKind.RE_MODULE_ALIAS:
            return self._match_re_module_call(func, node)
        if looked.kind in (
            _BindingKind.COMPILED_STATIC_PATTERN,
            _BindingKind.COMPILED_DYNAMIC_PATTERN,
        ):
            return self._match_compiled_call(func, var_name, looked)
        return None, None

    def _match_re_module_call(
        self, func: ast.Attribute, node: ast.Call,
    ) -> tuple[str | None, ast.AST | None]:
        api = func.attr
        if api not in _RE_MODULE_FUNCS:
            return None, None
        pattern_arg = self._get_call_argument(node, _RE_MODULE_FUNCS[api], "pattern")
        return (api, None) if pattern_arg is _DUP_ARG else (api, pattern_arg)

    def _match_compiled_call(
        self, func: ast.Attribute, var_name: str, binding: _Binding,
    ) -> tuple[str | None, ast.AST | None]:
        api = func.attr
        if api not in _PATTERN_METHODS:
            return None, None
        if binding.kind == _BindingKind.COMPILED_STATIC_PATTERN:
            stored = binding.compiled_pattern
            if stored is not None:
                return f"compiled.{api}", ast.Constant(value=stored)
            return f"compiled.{api}", None
        return f"compiled.{api}", None

    def _match_name_call(
        self, func: ast.Name, node: ast.Call,
    ) -> tuple[str | None, ast.AST | None]:
        bound = func.id
        looked = _scope_lookup(self._scope_stack, bound)
        if isinstance(looked, _Sentinel):
            return None, None
        if looked.kind != _BindingKind.RE_FUNCTION_ALIAS:
            return None, None
        real = looked.real_re_api
        if real and real in _RE_MODULE_FUNCS:
            pattern_arg = self._get_call_argument(node, _RE_MODULE_FUNCS[real], "pattern")
            return (real, None) if pattern_arg is _DUP_ARG else (real, pattern_arg)
        return None, None

    def _identify_re_call(self, node: ast.Call) -> str | None:
        """Return the API name if ``node`` is a re.* call, else None."""
        api, _ = self._match_regex_call(node)
        return api

    # -- Argument extraction (positional + keyword) -----------------------

    def _get_call_argument(
        self, node: ast.Call, positional_index: int, keyword_name: str,
    ) -> ast.AST | None | _DupArg:
        """Return the AST node for a named argument.

        Resolves both positional and keyword forms.  Returns:
          - the AST node if found exactly once
          - None if the argument is absent
          - _DUP_ARG if the same name is supplied both positionally and as a
            keyword (treated conservatively as untrustworthy)
        """
        keyword: ast.AST | None = next(
            (kw.value for kw in node.keywords if kw.arg == keyword_name), None
        )
        positional = (
            node.args[positional_index]
            if positional_index < len(node.args)
            else None
        )
        if positional is not None and keyword is not None:
            return _DUP_ARG
        return keyword if keyword is not None else positional

    # -- Pattern resolution ------------------------------------------------

    def _resolve_pattern(self, arg: ast.AST | None) -> str | None:
        if arg is None:
            return None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            return self._resolve_joined_str(arg)
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            return self._resolve_binop_pattern(arg)
        if isinstance(arg, ast.Name):
            looked = _scope_lookup(self._scope_stack, arg.id)
            if isinstance(looked, _Sentinel):
                return None
            if looked.kind == _BindingKind.STATIC_STRING and looked.static_value is not None:
                return looked.static_value
            if looked.kind == _BindingKind.COMPILED_STATIC_PATTERN and looked.compiled_pattern is not None:
                return looked.compiled_pattern
            return None
        if isinstance(arg, ast.Call) and self._is_re_escape_call(arg):
            return None
        return None

    def _resolve_binop_pattern(self, arg: ast.BinOp) -> str | None:
        left = self._resolve_pattern(arg.left)
        right = self._resolve_pattern(arg.right)
        return None if left is None or right is None else left + right

    def _resolve_joined_str(self, node: ast.JoinedStr) -> str | None:
        """Resolve an f-string to a static string if it has no dynamic parts."""
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue):
                return None
            else:
                return None
        return "".join(parts)

    # -- Segment-based classification (re.escape composition) -------------

    def _classify_pattern_source(
        self, arg: ast.AST | None,
    ) -> tuple[PatternSource, str | None, list[_Segment]]:
        """Classify the pattern argument and return (source, pattern_str, segments).

        segments is the ordered list of classified sub-expressions.  Each
        segment is one of:
          - STATIC(value)
          - ESCAPED_DYNAMIC
          - DYNAMIC
          - UNKNOWN
        """
        if arg is None:
            return PatternSource.UNKNOWN, None, [_Segment.unknown()]
        segments = self._collect_segments(arg)
        kinds = {s.kind for s in segments}
        static_parts = [s.value for s in segments if s.kind == _SegKind.STATIC]
        escaped = _SegKind.ESCAPED_DYNAMIC in kinds
        dynamic = _SegKind.DYNAMIC in kinds
        unknown = _SegKind.UNKNOWN in kinds

        if not escaped and not dynamic and not unknown:
            joined = "".join(static_parts)
            source = (PatternSource.STATIC_CONCAT if len(segments) > 1
                      else PatternSource.STATIC_LITERAL)
            return source, joined, segments

        if dynamic:
            return PatternSource.DYNAMIC, None, segments
        if escaped and unknown:
            return PatternSource.DYNAMIC, None, segments
        if escaped:
            return PatternSource.ESCAPED_DYNAMIC, None, segments
        return PatternSource.UNKNOWN, None, segments

    def _collect_segments(self, node: ast.AST) -> list[_Segment]:
        """Decompose an expression into ordered static/dynamic segments."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [_Segment.static(node.value)]
        if isinstance(node, ast.JoinedStr):
            return self._collect_joined_str_segments(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left_segs = self._collect_segments(node.left)
            right_segs = self._collect_segments(node.right)
            return left_segs + right_segs
        if isinstance(node, ast.Name):
            looked = _scope_lookup(self._scope_stack, node.id)
            if isinstance(looked, _Sentinel):
                return [_Segment.unknown()]
            if looked.kind == _BindingKind.STATIC_STRING and looked.static_value is not None:
                return [_Segment.static(looked.static_value)]
            if looked.kind in (
                _BindingKind.COMPILED_STATIC_PATTERN,
            ) and looked.compiled_pattern is not None:
                return [_Segment.static(looked.compiled_pattern)]
            if looked.kind == _BindingKind.DYNAMIC_VALUE:
                return [_Segment.dynamic()]
            return [_Segment.unknown()]
        if isinstance(node, ast.Call):
            if self._is_re_escape_call(node):
                return [_Segment.escaped_dynamic()]
            # Other calls are dynamic.
            return [_Segment.dynamic()]
        # Anything else: unknown.
        return [_Segment.unknown()]

    def _collect_joined_str_segments(self, node: ast.JoinedStr) -> list[_Segment]:
        """Decompose an f-string into segments.

        Static string parts become STATIC segments.  FormattedValue parts
        that wrap a ``re.escape()`` call become ESCAPED_DYNAMIC; other
        FormattedValue parts become DYNAMIC.
        """
        segments: list[_Segment] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                segments.append(_Segment.static(val.value))
            elif isinstance(val, ast.FormattedValue):
                if self._is_re_escape_call(val.value):
                    segments.append(_Segment.escaped_dynamic())
                else:
                    segments.append(_Segment.dynamic())
            else:
                segments.append(_Segment.dynamic())
        return segments

    def _classify_name_pattern(
        self, arg: ast.Name,
    ) -> tuple[PatternSource, str | None]:
        looked = _scope_lookup(self._scope_stack, arg.id)
        if isinstance(looked, _Sentinel):
            return PatternSource.UNKNOWN, None
        if looked.kind == _BindingKind.COMPILED_STATIC_PATTERN and looked.compiled_pattern is not None:
            return PatternSource.STATIC_LITERAL, looked.compiled_pattern
        if looked.kind == _BindingKind.STATIC_STRING and looked.static_value is not None:
            return PatternSource.STATIC_LITERAL, looked.static_value
        if looked.kind in (
            _BindingKind.DYNAMIC_VALUE,
            _BindingKind.COMPILED_DYNAMIC_PATTERN,
        ):
            return PatternSource.DYNAMIC, None
        return PatternSource.UNKNOWN, None

    def _is_re_escape_call(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            looked = _scope_lookup(self._scope_stack, func.value.id)
            if isinstance(looked, _Sentinel):
                return False
            return looked.kind == _BindingKind.RE_MODULE_ALIAS and func.attr == "escape"
        if isinstance(func, ast.Name):
            looked = _scope_lookup(self._scope_stack, func.id)
            if isinstance(looked, _Sentinel):
                return False
            return looked.kind == _BindingKind.RE_FUNCTION_ALIAS and looked.real_re_api == "escape"
        return False

    # -- Finding emission ---------------------------------------------------

    def _record_finding(
        self, node: ast.Call, api: str, pattern_arg: ast.AST | None,
    ) -> None:
        compile_line = self._lookup_compile_line(node, api)
        pattern_source, pattern_str, segments = self._classify_pattern_source(
            pattern_arg,
        )
        ctx = _FindingCtx(
            line=node.lineno,
            func_name=self._enclosing_function_name(node),
            api=api,
            pattern_source=pattern_source,
            input_scope=self._infer_input_scope(api, node),
            compile_line=compile_line,
            flags_desc=self._describe_flags(node, api),
        )
        has_dotall = self._check_dotall(node, api)

        if api.startswith("compiled."):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                looked = _scope_lookup(self._scope_stack, func.value.id)
                if not isinstance(looked, _Sentinel) and looked.kind == _BindingKind.COMPILED_DYNAMIC_PATTERN:
                    self._emit_dynamic_compiled_review(ctx)
                    return

        if all(s.kind == _SegKind.STATIC for s in segments) and pattern_str is not None:
            self._emit_static_finding(ctx, pattern_str, has_dotall)
            return

        static_danger = self._find_dangerous_static_segment(segments)
        if static_danger is not None:
            self._emit_error(ctx, static_danger[1], static_danger[0])
            return

        if pattern_str is not None:
            if has_dotall and ".*" in pattern_str:
                self._emit_dotall_review(ctx, pattern_str)
            return

        self._emit_composition_finding(ctx, segments, pattern_source)

    def _emit_static_finding(
        self, ctx: _FindingCtx, pattern_str: str, has_dotall: bool,
    ) -> None:
        """Emit finding for a fully-static pattern after full ReDoS analysis."""
        reason, _, severity = _analyze_static_pattern(pattern_str)
        if reason is not None and severity == Severity.ERROR:
            self._emit_error(ctx, pattern_str, reason)
            return
        if has_dotall and ".*" in pattern_str:
            self._emit_dotall_review(ctx, pattern_str)

    def _emit_composition_finding(
        self, ctx: _FindingCtx, segments: list[_Segment],
        pattern_source: PatternSource,
    ) -> None:
        """Emit finding for escaped/dynamic/unknown compositions."""
        has_escaped = any(s.kind == _SegKind.ESCAPED_DYNAMIC for s in segments)
        has_unescaped_dynamic = any(
            s.kind in (_SegKind.DYNAMIC, _SegKind.UNKNOWN) for s in segments
        )

        if has_escaped and not has_unescaped_dynamic:
            has_operators = any(
                s.kind == _SegKind.STATIC and s.value is not None
                and _contains_regex_operators(s.value)
                for s in segments
            )
            if has_operators:
                self._emit_escaped_composition_review(ctx)
            return

        if has_unescaped_dynamic:
            self._emit_dynamic_review(ctx)
        elif pattern_source == PatternSource.UNKNOWN:
            self._emit_unknown_review(ctx)

    def _lookup_compile_line(self, node: ast.Call, api: str) -> int | None:
        if not api.startswith("compiled."):
            return None
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            looked = _scope_lookup(self._scope_stack, func.value.id)
            if isinstance(looked, _Sentinel):
                return None
            if looked.kind in (
                _BindingKind.COMPILED_STATIC_PATTERN,
                _BindingKind.COMPILED_DYNAMIC_PATTERN,
            ):
                return looked.compile_line
        return None

    def _find_dangerous_static_segment(
        self, segments: list[_Segment],
    ) -> tuple[str, str] | None:
        for seg in segments:
            if seg.kind != _SegKind.STATIC:
                continue
            reason, _, severity = _analyze_static_pattern(seg.value)
            if reason is not None and severity == Severity.ERROR:
                return reason, seg.value
        return None

    def _emit_error(
        self, ctx: "_FindingCtx", pattern: str, reason: str,
    ) -> None:
        self.findings.append(RegexFinding(
            severity=Severity.ERROR,
            engine=Engine.PYTHON_RE,
            file_path=self._file_path,
            line=ctx.line, function=ctx.func_name, api=ctx.api,
            pattern=pattern, pattern_source=ctx.pattern_source,
            input_scope=ctx.input_scope, reason=reason,
            remediation=(
                "Redesign the pattern to avoid overlapping quantifiers or "
                "use a deterministic parser (splitlines, startswith, partition)."
            ),
            compile_line=ctx.compile_line,
        ))

    def _emit_dynamic_compiled_review(self, ctx: _FindingCtx) -> None:
        self.findings.append(RegexFinding(
            severity=Severity.REVIEW,
            engine=Engine.PYTHON_RE,
            file_path=self._file_path,
            line=ctx.line, function=ctx.func_name, api=ctx.api,
            pattern="<dynamic>", pattern_source=PatternSource.DYNAMIC,
            input_scope=ctx.input_scope,
            reason=(
                "Compiled pattern source became dynamic after reassignment — "
                "the pattern used in this call is no longer the statically "
                "known safe value"
            ),
            remediation=(
                "Avoid reassigning compiled pattern variables to dynamic "
                "values, or validate the new pattern before use."
            ),
            compile_line=ctx.compile_line,
        ))

    def _emit_dotall_review(
        self, ctx: _FindingCtx, pattern: str,
    ) -> None:
        self.findings.append(RegexFinding(
            severity=Severity.REVIEW,
            engine=Engine.PYTHON_RE,
            file_path=self._file_path,
            line=ctx.line, function=ctx.func_name, api=ctx.api,
            pattern=pattern, pattern_source=ctx.pattern_source,
            input_scope=ctx.input_scope,
            reason=(
                "re.DOTALL with .* pattern — if applied to unbounded input, "
                ".* can cause excessive backtracking"
            ),
            remediation=(
                "Anchor the pattern or use a deterministic line-by-line "
                "scanner instead of DOTALL + .*"
            ),
            compile_line=ctx.compile_line,
        ))

    def _emit_dynamic_review(self, ctx: _FindingCtx) -> None:
        flag_suffix = f" with {ctx.flags_desc}" if ctx.flags_desc else ""
        self.findings.append(RegexFinding(
            severity=Severity.REVIEW,
            engine=Engine.PYTHON_RE,
            file_path=self._file_path,
            line=ctx.line, function=ctx.func_name, api=ctx.api,
            pattern="<dynamic>", pattern_source=PatternSource.DYNAMIC,
            input_scope=ctx.input_scope,
            reason=(
                f"Dynamic pattern source — pattern is constructed from "
                f"runtime values{flag_suffix}. If input is untrusted, this "
                "is a regex injection risk."
            ),
            remediation=(
                "Use re.escape() for dynamic components, or validate input "
                "against an allowlist before using as a pattern."
            ),
            compile_line=ctx.compile_line,
        ))

    def _emit_unknown_review(self, ctx: _FindingCtx) -> None:
        self.findings.append(RegexFinding(
            severity=Severity.REVIEW,
            engine=Engine.PYTHON_RE,
            file_path=self._file_path,
            line=ctx.line, function=ctx.func_name, api=ctx.api,
            pattern="<unknown>", pattern_source=PatternSource.UNKNOWN,
            input_scope=ctx.input_scope,
            reason=(
                "Pattern source could not be statically resolved — manual "
                "review required"
            ),
            remediation=(
                "Ensure the pattern is a static literal or wrapped with "
                "re.escape() if it incorporates dynamic values."
            ),
            compile_line=ctx.compile_line,
        ))

    def _emit_escaped_composition_review(self, ctx: _FindingCtx) -> None:
        self.findings.append(RegexFinding(
            severity=Severity.REVIEW,
            engine=Engine.PYTHON_RE,
            file_path=self._file_path,
            line=ctx.line, function=ctx.func_name, api=ctx.api,
            pattern="<escaped+operators>",
            pattern_source=PatternSource.ESCAPED_DYNAMIC,
            input_scope=ctx.input_scope,
            reason=(
                "re.escape() composition with regex operators — escape only "
                "prevents injection of the dynamic value itself; surrounding "
                "operators may form a dangerous pattern depending on the "
                "escaped content (e.g. re.escape('a') + '+)+$' → '(a+)+$')"
            ),
            remediation=(
                "Verify that the combined pattern cannot produce nested "
                "quantifiers or overlapping repetitions for any possible "
                "escaped value."
            ),
            compile_line=ctx.compile_line,
        ))

    def _check_dotall(self, node: ast.Call, api: str) -> bool:
        """Check if re.DOTALL is passed as a flag argument."""
        sig = _RE_SIGNATURES.get(api)
        if sig is not None and sig.flags_index is not None:
            flags_arg = self._get_call_argument(
                node, sig.flags_index, "flags",
            )
        else:
            flags_arg = self._get_call_argument(node, 1, "flags")
        if flags_arg is None or flags_arg is _DUP_ARG:
            return False
        return self._contains_dotall(flags_arg)

    def _contains_dotall(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            looked = _scope_lookup(self._scope_stack, node.value.id)
            if isinstance(looked, _Sentinel):
                return False
            return looked.kind == _BindingKind.RE_MODULE_ALIAS and node.attr == "DOTALL"
        if isinstance(node, ast.BinOp):
            return (self._contains_dotall(node.left)
                    or self._contains_dotall(node.right))
        return False

    def _describe_flags(self, node: ast.Call, api: str) -> str:
        """Describe flag arguments for diagnostic output."""
        sig = _RE_SIGNATURES.get(api)
        if sig is not None and sig.flags_index is not None:
            flags_arg = self._get_call_argument(
                node, sig.flags_index, "flags",
            )
        else:
            flags_arg = self._get_call_argument(node, 1, "flags")
        if flags_arg is None or flags_arg is _DUP_ARG:
            return ""
        candidates: list[ast.AST] = []
        if isinstance(flags_arg, ast.BinOp):
            candidates = self._flatten_bitor(flags_arg)
        else:
            candidates = [flags_arg]
        flags = [
            f"re.{side.attr}" for side in candidates
            if isinstance(side, ast.Attribute)
            and isinstance(side.value, ast.Name)
        ]
        valid_flags = []
        for side, flag_str in zip(candidates, flags):
            if not isinstance(side, ast.Attribute) or not isinstance(side.value, ast.Name):
                continue
            looked = _scope_lookup(self._scope_stack, side.value.id)
            if isinstance(looked, _Sentinel):
                continue
            if looked.kind == _BindingKind.RE_MODULE_ALIAS:
                valid_flags.append(flag_str)
        return ", ".join(valid_flags) if valid_flags else ""

    def _flatten_bitor(self, node: ast.AST) -> list[ast.AST]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._flatten_bitor(node.left) + self._flatten_bitor(node.right)
        return [node]

    def _infer_input_scope(self, api: str, node: ast.Call) -> str:
        """Infer the likely input scope from the API and call context."""
        if api == "compile":
            return "pattern definition"
        # Look up the correct string index for this API
        sig = _RE_SIGNATURES.get(api)
        if sig is not None and sig.string_index is not None:
            string_arg = self._get_call_argument(
                node, sig.string_index, "string",
            )
        elif api.startswith("compiled."):
            string_arg = self._get_call_argument(
                node, _COMPILED_STRING_POSITIONAL, "string",
            )
        else:
            string_arg = self._get_call_argument(node, 1, "string")
        if string_arg is None or string_arg is _DUP_ARG:
            return "unknown"
        return self._infer_arg_scope(string_arg)

    def _infer_arg_scope(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "static string"
        if isinstance(node, ast.Call):
            return self._infer_call_scope(node)
        if isinstance(node, ast.Subscript):
            return "indexed value (possibly user-controlled)"
        return "variable (unknown origin)" if isinstance(node, ast.Name) else "unknown"

    def _infer_call_scope(self, call: ast.Call) -> str:
        func = call.func
        if not isinstance(func, ast.Attribute):
            return "unknown"
        if func.attr in ("read", "read_text", "read_bytes"):
            return "file content"
        if func.attr == "getenv":
            return "environment variable"
        if func.attr == "get":
            return "dict value (possibly user-controlled)"
        return "unknown"

    def _enclosing_function_name(self, node: ast.AST) -> str:
        """Find the name of the enclosing function for a given AST node."""
        if self._tree is None:
            return "<module>"
        target_line = getattr(node, "lineno", 0)
        best_name = "<module>"
        best_line = 0
        for n in ast.walk(self._tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                n.lineno <= target_line and n.lineno > best_line
            ):
                end_line = getattr(n, "end_lineno", n.lineno + 1000)
                if target_line <= end_line:
                    best_name = n.name
                    best_line = n.lineno
        return best_name


# Positional indices for ``string`` and ``flags`` are now per-API via
# _RE_SIGNATURES.  The following are kept as fallbacks for compiled-pattern
# method calls where we don't have a signature (the pattern is already known).
# Note: compiled-pattern methods don't accept flags positionally, so only the
# string index is needed here.
_COMPILED_STRING_POSITIONAL = 0  # compiled.search(string, ...)


class _DupArg:
    """Marker for a positional+keyword duplicate argument."""


@dataclass
class _FindingCtx:
    """Context shared by finding-emission helpers."""
    line: int
    func_name: str
    api: str
    pattern_source: PatternSource
    input_scope: str
    compile_line: int | None
    flags_desc: str = ""


_DUP_ARG = _DupArg()


@dataclass(frozen=True)
class _Segment:
    """A single classified segment of a pattern expression."""
    kind: "_SegKind"
    value: str | None = None

    @classmethod
    def static(cls, value: str) -> "_Segment":
        return cls(_SegKind.STATIC, value)

    @classmethod
    def escaped_dynamic(cls) -> "_Segment":
        return cls(_SegKind.ESCAPED_DYNAMIC)

    @classmethod
    def dynamic(cls) -> "_Segment":
        return cls(_SegKind.DYNAMIC)

    @classmethod
    def unknown(cls) -> "_Segment":
        return cls(_SegKind.UNKNOWN)


class _SegKind(Enum):
    STATIC = "STATIC"
    ESCAPED_DYNAMIC = "ESCAPED_DYNAMIC"
    DYNAMIC = "DYNAMIC"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# ReDoS heuristic analysis (operates on resolved pattern strings)
# ---------------------------------------------------------------------------

# Tokenize a regex into atoms for analysis.  We do not need a full parser —
# only enough to recognize quantifiers and the atom they apply to.  We use a
# small character scanner that walks the pattern left-to-right, tracking
# group nesting depth, and records (atom_repr, quantifier, depth) tuples.


def _tokenize_regex(pattern: str) -> list["_Token"]:
    """Convert a regex string into a flat token sequence for heuristic analysis.

    The tokenizer is intentionally shallow: it tracks group nesting and
    emits atoms/quantifiers/anchors/alternations.  Consecutive literal
    atoms are merged so multi-character literal separators (e.g. ``static``)
    are analyzed as one unit instead of many single-character atoms.
    """
    raw_tokens: list[_Token] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        handler = _CHAR_HANDLERS.get(ch)
        if handler is not None:
            token, i = handler(pattern, i, n)
            raw_tokens.append(token)
            continue
        # Default literal atom.
        raw_tokens.append(_Token(_TKind.ATOM, ch, i))
        i += 1
    return _merge_literal_atoms(raw_tokens)


def _handle_escape(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    if i + 1 < n:
        return _Token(_TKind.ATOM, pattern[i:i + 2], i), i + 2
    return _Token(_TKind.ATOM, pattern[i], i), i + 1


def _handle_class(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    j = i + 1
    if j < n and pattern[j] == "^":
        j += 1
    if j < n and pattern[j] == "]":
        j += 1
    while j < n and pattern[j] != "]":
        j += 2 if pattern[j] == "\\" and j + 1 < n else 1
    if j < n:
        j += 1
    return _Token(_TKind.ATOM, pattern[i:j], i), j


def _handle_group_open(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    if i + 1 < n and pattern[i + 1] == "?":
        return _handle_special_group(pattern, i, n)
    return _Token(_TKind.GROUP_OPEN, pattern[i], i), i + 1


def _handle_special_group(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    k = i + 2
    if k < n and pattern[k] == "#":
        end = pattern.find(")", k)
        new_i = n if end == -1 else end + 1
        return _Token(_TKind.GROUP_OPEN, "(", new_i), new_i
    while k < n and pattern[k] in ":=!<>(?P":
        k += 1
    return _Token(_TKind.GROUP_OPEN, pattern[i], i), k


def _handle_close(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    return _Token(_TKind.GROUP_CLOSE, pattern[i], i), i + 1


def _handle_quant_char(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    return _Token(_TKind.QUANT, pattern[i], i), i + 1


def _handle_brace(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    j = i + 1
    while j < n and pattern[j] in "0123456789,":
        j += 1
    if j < n and pattern[j] == "}":
        return _Token(_TKind.QUANT, pattern[i:j + 1], i), j + 1
    return _Token(_TKind.ATOM, pattern[i], i), i + 1


def _handle_alt(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    return _Token(_TKind.ALT, pattern[i], i), i + 1


def _handle_anchor(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    return _Token(_TKind.ANCHOR, pattern[i], i), i + 1


def _handle_dot(pattern: str, i: int, n: int) -> tuple[_Token, int]:
    return _Token(_TKind.ATOM, pattern[i], i), i + 1


_CHAR_HANDLERS = {
    "\\": _handle_escape,
    "[": _handle_class,
    "(": _handle_group_open,
    ")": _handle_close,
    "+": _handle_quant_char,
    "*": _handle_quant_char,
    "?": _handle_quant_char,
    "{": _handle_brace,
    "|": _handle_alt,
    "^": _handle_anchor,
    "$": _handle_anchor,
    ".": _handle_dot,
}


def _merge_literal_atoms(tokens: list[_Token]) -> list[_Token]:
    """Merge adjacent literal atoms so separators stay intact.

    Only non-metacharacter literals are merged.  Regex metacharacters and
    escape sequences remain standalone tokens so the later quantifier/group
    analysis can see the real structure.

    Important: a literal that is immediately followed by a quantifier (+, *, ?, {m,n})
    must NOT be merged with preceding literals, because the quantifier applies
    to that specific atom only.  For example, in "-ab+", the '+' applies to 'b'
    only, not to the entire "-ab".
    """
    _META = set("\\.[]()+*?{}|^$")
    merged: list[_Token] = []
    run_chars: list[str] = []
    run_pos = 0
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        # Check if this token is a mergeable literal AND the next token is NOT a quantifier.
        # We must not merge the last literal before a quantifier because the quantifier
        # binds to that specific atom.
        next_is_quant = (
            i + 1 < n
            and tokens[i + 1].kind == _TKind.QUANT
        )
        if _is_mergeable_literal(tok, _META) and not next_is_quant:
            if not run_chars:
                run_chars = [tok.text]
                run_pos = tok.pos
            else:
                run_chars.append(tok.text)
            i += 1
            continue
        _flush_run(merged, run_chars, run_pos)
        run_chars = []
        merged.append(tok)
        i += 1
    _flush_run(merged, run_chars, run_pos)
    return merged


def _is_mergeable_literal(tok: _Token, meta: set[str]) -> bool:
    return (tok.kind == _TKind.ATOM and len(tok.text) == 1
            and tok.text not in meta)


def _flush_run(
    merged: list[_Token], run_chars: list[str], run_pos: int,
) -> None:
    if not run_chars:
        return
    if len(run_chars) > 1:
        merged.append(_Token(_TKind.ATOM, "".join(run_chars), run_pos))
    else:
        merged.append(_Token(_TKind.ATOM, run_chars[0], run_pos))


class _TKind(Enum):
    ATOM = "ATOM"
    QUANT = "QUANT"
    GROUP_OPEN = "GROUP_OPEN"
    GROUP_CLOSE = "GROUP_CLOSE"
    ALT = "ALT"
    ANCHOR = "ANCHOR"


@dataclass(frozen=True)
class _Token:
    kind: _TKind
    text: str
    pos: int


def _atom_matches_char_class(atom: str, ch: str) -> bool:
    """Conservative check: does the atom definitely match character ``ch``?

    Returns False when uncertain.  Used to detect whether an outer-group
    iteration can consume the same character that an inner unbounded atom
    can.
    """
    if atom == ".":
        return True
    if atom.startswith("\\"):
        return _escape_matches_char(atom, ch)
    if atom.startswith("["):
        return _bracket_matches_char(atom, ch)
    return atom == ch if len(atom) == 1 else False


def _escape_matches_char(atom: str, ch: str) -> bool:
    if atom == r"\d":
        return ch in "0123456789"
    if atom == r"\w":
        return ch.isalnum() or ch == "_"
    return ch in " \t\n\r\f\v" if atom == r"\s" else False


def _bracket_matches_char(atom: str, ch: str) -> bool:
    # Negated class — can't prove it doesn't match ch.
    if "^" in atom[1:3]:
        return False
    body = atom[1:-1] if atom.endswith("]") else atom[1:]
    if _bracket_range_matches(body, ch):
        return True
    return _bracket_literal_tail_matches(body, ch)


def _bracket_range_matches(body: str, ch: str) -> bool:
    k = 0
    while k < len(body) - 2:
        if body[k + 1] == "-" and k + 2 < len(body):
            lo, hi = body[k], body[k + 2]
            if lo <= ch <= hi:
                return True
            k += 3
        elif body[k] == ch:
            return True
        else:
            k += 1
    return False


def _bracket_literal_tail_matches(body: str, ch: str) -> bool:
    # Scan the body for a literal ch that is not part of a range.  We re-walk
    # to find the starting index after range expansion.
    k = 0
    while k < len(body) - 2:
        k += 3 if body[k + 1] == "-" and k + 2 < len(body) else 1
    while k < len(body):
        if body[k] == ch:
            return True
        k += 1
    return False


def _quantifier_is_unbounded(q: str) -> bool:
    """True if a quantifier allows an unbounded number of repetitions."""
    if q in {"+", "*"}:
        return True
    if q.startswith("{"):
        # {n,m} — unbounded only if m is empty (i.e. {n,}).
        body = q[1:-1]
        if "," in body:
            _, upper = body.split(",", 1)
            return upper.strip() == ""
        return False  # {n} or {n,m} with finite upper bound — bounded.
    return False


def _find_groups(tokens: list[_Token]) -> list[tuple[int, int, str]]:
    """Return (open_idx, close_idx, content_repr) for each (...) group.

    The content_repr is the substring of the pattern between the open and
    close token positions (exclusive of the parens themselves), with
    non-capturing/lookahead markers stripped.
    """
    groups: list[tuple[int, int, str]] = []
    stack: list[int] = []
    for idx, tok in enumerate(tokens):
        if tok.kind == _TKind.GROUP_OPEN:
            stack.append(idx)
        elif tok.kind == _TKind.GROUP_CLOSE and stack:
            open_idx = stack.pop()
            # The actual content offset in the source pattern.
            tokens[open_idx].pos
            close_pos = tok.pos
            content = _pattern_between(pattern_pos_close=close_pos,
                                       tokens=tokens,
                                       open_idx=open_idx)
            groups.append((open_idx, idx, content))
    return groups


def _pattern_between(
    pattern_pos_close: int,
    tokens: list[_Token], open_idx: int,
) -> str:
    """Reconstruct the group's content text from tokens."""
    parts: list[str] = []
    for tok in tokens[open_idx + 1:]:
        if tok.pos >= pattern_pos_close:
            break
        parts.append(tok.text)
    return "".join(parts)


def _check_nested_quantifier(pattern: str) -> str | None:
    """Detect nested quantifier patterns that commonly cause ReDoS.

    Heuristic: a capturing/non-capturing group containing an unbounded
    quantifier is itself repeated unboundedly, unless every alternation
    branch starts with a strong literal separator that the inner atom can't
    consume.  Returns a human-readable reason string on match, else ``None``.
    """
    """Check for nested quantifiers like (a+)+, (a*)+, (.+)+, (.*)+.

    A group with an unbounded inner quantifier that is itself repeated
    unbounded is dangerous UNLESS each outer iteration is provably started by
    a fixed separator that the inner quantifier cannot consume.

    Safe:   (?:-\\d+)*         — '-' is a required separator; \\d+ cannot
                                consume '-'.
             (?:_[a-z]+)*       — '_' separator; [a-z] cannot consume '_'.
             (?:static\\s+|...)* — each branch starts with a literal word
                                that \\s+ cannot consume.

    Danger: (aa+)+             — the inner 'a+' can consume the leading 'a',
                                so iterations overlap.
             (a+)+              — same.
             (?:aa+)+           — same with non-capturing marker.
    """
    tokens = _tokenize_regex(pattern)
    groups = _find_groups(tokens)
    for open_idx, close_idx, _content in groups:
        outer_quant = _outer_quantifier(tokens, close_idx)
        if outer_quant is None or not _quantifier_is_unbounded(outer_quant.text):
            continue
        result = _analyze_group_for_nested(
            tokens, open_idx, close_idx, outer_quant.text,
        )
        if result is not None:
            return result
    return None


def _outer_quantifier(
    tokens: list[_Token], close_idx: int,
) -> _Token | None:
    """Return the token immediately after a group close, if it's a quant."""
    if close_idx + 1 >= len(tokens):
        return None
    tok = tokens[close_idx + 1]
    return tok if tok.kind == _TKind.QUANT else None


def _analyze_group_for_nested(
    tokens: list[_Token], open_idx: int, close_idx: int,
    outer_quant: str,
) -> str | None:
    """Inspect a group's body for an unbounded inner quantifier.

    A group is flagged as a nested-quantifier ERROR unless we can prove that
    every outer iteration starts with a fixed literal separator that the
    inner unbounded atom cannot consume.  The proof is branch-local: split
    the body on top-level ``|`` alternations and require each branch to be
    safe.
    """
    body_tokens = tokens[open_idx + 1:close_idx]
    if not body_tokens:
        return None
    branches = _split_branches(body_tokens)
    for branch in branches:
        result = _analyze_branch_for_nested(branch, outer_quant)
        if result is not None:
            return result
    return None


def _split_branches(body_tokens: list[_Token]) -> list[list[_Token]]:
    """Split a group's body tokens on top-level ``|`` tokens."""
    branches: list[list[_Token]] = [[]]
    depth = 0
    for tok in body_tokens:
        if tok.kind == _TKind.GROUP_OPEN:
            depth += 1
            branches[-1].append(tok)
        elif tok.kind == _TKind.GROUP_CLOSE:
            depth = max(0, depth - 1)
            branches[-1].append(tok)
        elif tok.kind == _TKind.ALT and depth == 0:
            branches.append([])
        else:
            branches[-1].append(tok)
    return [b for b in branches if b]


def _analyze_branch_for_nested(
    branch: list[_Token], outer_quant: str,
) -> str | None:
    """Analyze one alternation branch for a nested-quantifier risk.

    A branch is exempt (safe) only when ALL of the following hold:
      - it starts with a literal separator atom that is either a multi-char
        literal (e.g. ``static``) or a single non-alphanumeric literal char
        (e.g. ``-``, ``_``, ``.``); single alphanumeric separators (``a``,
        ``b``) are NOT strong enough to exempt because they overlap with
        common text content,
      - the literal separator's character set is disjoint from every
        unbounded inner atom in the branch, AND
      - the branch has no leading optional quantifier on the separator.
    Otherwise, if any unbounded inner quantifier exists, the branch is
    dangerous.
    """
    leading_idx = 0
    while leading_idx < len(branch) and branch[leading_idx].kind == _TKind.ANCHOR:
        leading_idx += 1
    if leading_idx >= len(branch):
        return None
    leading = branch[leading_idx]
    if leading.kind == _TKind.QUANT:
        return None
    if (leading.kind != _TKind.ATOM
            or not _atom_is_literal(leading.text)
            or not _is_strong_separator(leading.text)):
        return _find_unbounded_in_branch(branch, outer_quant)
    return _check_strong_separator_branch(branch, leading.text, outer_quant)


def _check_strong_separator_branch(
    branch: list[_Token], leading_text: str, outer_quant: str,
) -> str | None:
    for i, tok in enumerate(branch):
        if tok.kind != _TKind.ATOM or i + 1 >= len(branch):
            continue
        nxt = branch[i + 1]
        if nxt.kind != _TKind.QUANT or not _quantifier_is_unbounded(nxt.text):
            continue
        if len(leading_text) >= 2:
            if _atom_matches_char_class(tok.text, leading_text[0]):
                return (
                    f"nested quantifier — inner '{tok.text}{nxt.text}' inside "
                    f"outer '{outer_quant}' can consume the first character of "
                    "the leading multi-char separator, causing exponential "
                    "backtracking"
                )
            continue
        if _literal_overlaps_atom(leading_text, tok.text):
            return (
                f"nested quantifier — inner '{tok.text}{nxt.text}' inside "
                f"outer '{outer_quant}' can consume characters from the "
                "leading literal separator, causing exponential backtracking"
            )
    return None


def _is_strong_separator(text: str) -> bool:
    """A separator is 'strong' when it is unlikely to be consumed by an
    inner unbounded atom over text content.

    Multi-char literals (``static``, ``extern``) are always strong because
    each outer iteration requires the full word.  Single-char separators are
    strong only when they are non-alphanumeric (``-``, ``_``, ``.``, ``/``,
    ``:`` etc.) — a single alphanumeric char like ``a`` is too easy to
    overlap with adjacent text and is treated as a weak separator.
    """
    if len(text) >= 2:
        return True
    return not text.isalnum() if len(text) == 1 else False


def _find_unbounded_in_branch(
    branch: list[_Token], outer_quant: str,
) -> str | None:
    """Flag any unbounded inner quantifier when no literal separator proof."""
    for i, tok in enumerate(branch):
        if tok.kind != _TKind.ATOM:
            continue
        if i + 1 >= len(branch):
            continue
        nxt = branch[i + 1]
        if nxt.kind == _TKind.QUANT and _quantifier_is_unbounded(nxt.text):
            return (
                f"nested quantifier — inner '{tok.text}{nxt.text}' inside "
                f"outer '{outer_quant}' has unbounded inner repetition; "
                "redesign to use a required literal separator"
            )
    return None


def _atom_is_literal(text: str) -> bool:
    """True if the atom is a plain literal (no regex metachar semantics).

    Single-character escape sequences that escape a metacharacter (e.g.
    ``\\.``, ``\\-``, ``\\_``) are treated as literals because they match
    exactly one fixed character.  Character-class escapes (``\\d``, ``\\w``,
    ``\\s``) and bracket expressions (``[...]``) are NOT literals.
    """
    if not text:
        return False
    if text == ".":
        return False
    if text.startswith("["):
        return False
    if text.startswith("\\"):
        # Two-char escape: literal only if the escaped char is not a known
        # character-class escape (d, w, s, D, W, S, b, B).
        return len(text) == 2 and text[1] not in "dwsDWSbB"
    # Plain literal char(s) — none may be a metacharacter.
    return all(ch not in set("\\.[]()+*?{}|^$") for ch in text)


def _literal_overlaps_atom(literal: str, atom: str) -> bool:
    """True if the atom can match at least one character of the literal."""
    if atom == ".":
        return True
    if atom.startswith("["):
        return any(_atom_matches_char_class(atom, ch) for ch in literal)
    if atom.startswith("\\"):
        return any(_atom_matches_char_class(atom, ch) for ch in literal)
    return atom in literal if len(atom) == 1 else any(ch in literal for ch in atom)


def _check_adjacent_overlapping_quantifiers(pattern: str) -> str | None:
    """Check for adjacent unbounded repetitions with overlapping classes."""
    # Only flag .* or .+ adjacent to .* or .+ (true overlap on any char).
    dot_star_re = re.compile(r"(\.\*|\.\+)")
    dot_matches = list(dot_star_re.finditer(pattern))
    if len(dot_matches) >= 2:
        for i in range(len(dot_matches) - 1):
            end1 = dot_matches[i].end()
            start2 = dot_matches[i + 1].start()
            between = pattern[end1:start2]
            between_clean = (between.replace(")", "")
                                     .replace("(?:", "")
                                     .replace("(", ""))
            if between_clean == "":
                g1 = dot_matches[i].group(0)
                g2 = dot_matches[i + 1].group(0)
                return (
                    f"adjacent overlapping quantifiers '{g1}...{g2}' — "
                    "both consume any character, causes quadratic backtracking"
                )
    return None


def _check_backreference_after_dotstar(pattern: str) -> str | None:
    r"""Check for backreference (\1, \2...) after .* or .*?."""
    backref_re = re.compile(r"\.\*[?]?.*\\[1-9]")
    if backref_re.search(pattern):
        return ("backreference after .* or .*? — causes quadratic backtracking "
                "when backreference fails to match")
    return None


def _check_star_in_repetition(pattern: str) -> str | None:
    """Check for quantifier applied to a group containing .* or .+."""
    group_re = re.compile(r"\(([^)]+)\)[+*]")
    for m in group_re.finditer(pattern):
        group_body = m.group(1)
        if ".*" in group_body or ".+" in group_body:
            return ("unbounded repetition inside repeated group — causes "
                    "exponential backtracking")
    return None


def _pair_overlaps(alt_a: str, alt_b: str) -> bool:
    if not alt_a or not alt_b:
        return False
    if alt_a[0] == alt_b[0]:
        return True
    return bool(re.match(r"\\[wdWD]", alt_a) and re.match(r"\\[wdWD]", alt_b))


def _branches_overlap(alternatives: list[str]) -> bool:
    from itertools import combinations
    return any(_pair_overlaps(a, b) for a, b in combinations(alternatives, 2))


def _check_overlapping_alternation(pattern: str) -> str | None:
    r"""Check for alternation where branches overlap and are quantified.

    Pattern like: (a|ab)+ or (\\w+|\\d+)+
    """
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i] == "(":
            close = _find_group_close(pattern, i)
            if close == -1:
                i += 1
                continue
            result = _check_alt_at_group(pattern, i, close)
            if result is not None:
                return result
            i = close + 1
        else:
            i += 1
    return None


def _check_alt_at_group(
    pattern: str, open_pos: int, close_pos: int,
) -> str | None:
    body = pattern[open_pos + 1:close_pos]
    quant = _read_outer_quantifier(pattern, close_pos + 1)
    if not quant or not _quantifier_is_unbounded(quant):
        return None
    inner = _strip_non_capturing_marker(body)
    if inner is None:
        return None
    alts = _split_top_alt(inner)
    if len(alts) < 2:
        return None
    if _branches_overlap(alts):
        return (
            f"overlapping alternation '({body}){quant}' with quantifier — "
            "branches overlap"
        )
    return None


def _read_outer_quantifier(pattern: str, j: int) -> str:
    n = len(pattern)
    if j < n and pattern[j] in "+*?":
        return pattern[j]
    if j < n and pattern[j] == "{":
        k = j + 1
        while k < n and pattern[k] in "0123456789,":
            k += 1
        if k < n and pattern[k] == "}":
            return pattern[j:k + 1]
    return ""


def _strip_non_capturing_marker(body: str) -> str | None:
    if not body.startswith("?"):
        return body
    return body[2:] if body.startswith("?:") else None


def _find_group_close(pattern: str, open_pos: int) -> int:
    """Return the index of the matching ')' for the '(' at open_pos, or -1."""
    depth = 0
    i = open_pos
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "[":
            i = _skip_class(pattern, i, n)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _skip_class(pattern: str, i: int, n: int) -> int:
    """Return the index after a ``[...]`` character class starting at i."""
    j = i + 1
    if j < n and pattern[j] == "^":
        j += 1
    if j < n and pattern[j] == "]":
        j += 1
    while j < n and pattern[j] != "]":
        j += 2 if pattern[j] == "\\" and j + 1 < n else 1
    return j + 1 if j < n else j


def _split_top_alt(body: str) -> list[str]:
    """Split a group body on top-level ``|`` alternations."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n:
            current.append(body[i:i + 2])
            i += 2
            continue
        if ch == "[":
            end = _skip_class(body, i, n)
            current.append(body[i:end])
            i = end
            continue
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "|" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append("".join(current))
    return parts


def _check_repeated_nullable_group(pattern: str) -> str | None:
    """Check for a group that can match empty string, repeated unbounded."""
    group_re = re.compile(r"\((\?:)?([^()]*)\)[+*]")
    for m in group_re.finditer(pattern):
        result = _check_nullable_group_content(m)
        if result is not None:
            return result
    return None


def _check_nullable_group_content(m: re.Match) -> str | None:
    content = m[2]
    if not content:
        return f"repeated nullable group '{m[0]}' — empty group repeated causes infinite loop"
    if content.startswith("|") or content.endswith("|") or "||" in content:
        return (f"repeated nullable group '{m[0]}' — empty alternation branch "
                "allows zero-length match")
    return _check_nullable_single_atom(m, content) if "|" not in content else None


def _check_nullable_single_atom(m: re.Match, content: str) -> str | None:
    if len(content) < 2 or content[-1] not in ("?", "*"):
        return None
    atom = content[:-1]
    if len(atom) == 1 or \
            (atom.startswith("\\") and len(atom) == 2) or \
            (atom.startswith("[") and atom.endswith("]")):
        return (f"repeated nullable group '{m[0]}' — group can match empty "
                "string, outer quantifier causes quadratic backtracking")
    return None


# All checks in priority order
_CHECKS = [
    _check_nested_quantifier,
    _check_star_in_repetition,
    _check_adjacent_overlapping_quantifiers,
    _check_backreference_after_dotstar,
    _check_overlapping_alternation,
    _check_repeated_nullable_group,
]


def _analyze_static_pattern(
    pattern: str,
) -> tuple[str | None, str, Severity]:
    """Run all ReDoS checks on a static pattern string.

    Returns (reason, remediation, severity) or (None, "", Severity.INFO).
    """
    for check in _CHECKS:
        if result := check(pattern):
            return (
                result,
                "Redesign the pattern to avoid overlapping quantifiers or "
                "use a deterministic parser (splitlines, startswith, partition).",
                Severity.ERROR,
            )
    return None, "", Severity.INFO


def _contains_regex_operators(text: str) -> bool:
    """Check if a static segment contains regex operators (quantifiers, groups, etc.).

    Used to determine whether an escaped-dynamic composition with adjacent
    static segments might form dangerous patterns depending on the escaped value.
    """
    _OPERATOR_CHARS = set("+*?|()[]{}^$.")
    return any(ch in _OPERATOR_CHARS for ch in text)


# ---------------------------------------------------------------------------
# Shell regex extraction
# ---------------------------------------------------------------------------

# Commands that introduce PCRE regex usage.  Each value is the canonical
# command name we look for; the actual flag presence is checked separately.
_PCRE_COMMANDS = {
    "grep": ({"-P", "--perl-regexp"}, {"-e", "--regexp"}),
    "rg": ({"-P", "--pcre2"}, {"-e", "--regexp"}),
    "perl": (set(), {"-e", "-E"}),
}

_PATTERN_FILE_OPTIONS: dict[str, set[str]] = {
    "grep": {"-f", "--file"},
    "rg": {"-f", "--file"},
}

_REQUIRED_VALUE_OPTIONS: dict[str, set[str]] = {
    "grep": {"-m", "-A", "-B", "-C", "--max-count",
             "--after-context", "--before-context", "--context",
             "--label"},
    "rg": {"-g", "--glob", "-t", "--type", "-T", "--type-not",
            "-m", "--max-count", "-A", "--after-context",
            "-B", "--before-context", "-C", "--context",
            "-j", "--threads", "--max-filesize", "--max-depth",
            "--replace", "-r"},
    "perl": set(),
}

_OPTIONAL_VALUE_OPTIONS: dict[str, set[str]] = {
    "grep": {"--color", "--colour"},
    "rg": set(),
    "perl": set(),
}


@dataclass
class _ShellParseResult:
    is_pcre: bool = False
    inline_patterns: list[str] = field(default_factory=list)
    pattern_file: str | None = None
    has_unknown_option: bool = False
    parse_confidence: str = "high"


def _split_shell_pipeline(line: str) -> list[list[str]]:
    """Tokenize a shell command line into per-segment token lists.

    Splits on ``|``, ``&&``, ``||``, ``;``, and ``&``.  Returns a list of
    token lists, each representing one command segment.  Uses ``shlex`` for
    quoting-aware tokenization with punctuation_chars to handle no-space
    operators like ``cmd|grep`` and ``cmd&&grep``.
    """
    # Handle backslash continuation: join logical lines first
    logical_line = line.replace("\\\n", " ")

    try:
        lexer = shlex.shlex(logical_line, posix=True, punctuation_chars="|&;")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes — fall back to whitespace split.
        tokens = logical_line.split()
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in ("|", "&&", "||", ";", "&", ";;"):
            segments.append([])
        else:
            segments[-1].append(tok)
    return [s for s in segments if s]


def _strip_env_assignments(tokens: list[str]) -> list[str]:
    """Strip leading ``VAR=value`` assignments from a command's token list."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if "=" in tok and not tok.startswith("-") and _looks_like_env_assignment(tok):
            i += 1
        else:
            break
    return tokens[i:]


def _looks_like_env_assignment(tok: str) -> bool:
    """True if token looks like ``NAME=value`` (NAME is a valid identifier)."""
    name, _, _ = tok.partition("=")
    if not name:
        return False
    return name.isidentifier() or all(
        ch.isalnum() or ch in "_" for ch in name
    )


def _parse_shell_command(
    tokens: list[str], cmd_base: str,
) -> _ShellParseResult:
    """Parse shell command tokens and extract regex-related information.

    Scans all tokens before deciding, collecting inline patterns,
    pattern-file references, PCRE flag presence, and parse confidence.
    """
    result = _ShellParseResult()
    pcre_flags, pattern_opts = _PCRE_COMMANDS[cmd_base]
    file_opts = _PATTERN_FILE_OPTIONS.get(cmd_base, set())
    req_val_opts = _REQUIRED_VALUE_OPTIONS.get(cmd_base, set())
    opt_val_opts = _OPTIONAL_VALUE_OPTIONS.get(cmd_base, set())
    all_known = pcre_flags | pattern_opts | file_opts | req_val_opts | opt_val_opts

    saw_double_dash = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if saw_double_dash:
            if not result.inline_patterns:
                result.inline_patterns.append(tok)
            i += 1
            continue
        if tok == "--":
            saw_double_dash = True
            i += 1
            continue
        if tok.startswith("--"):
            i = _parse_long_option(
                tok, tokens, i, result, pcre_flags, pattern_opts,
                file_opts, req_val_opts, opt_val_opts, all_known,
            )
            continue
        if tok.startswith("-") and len(tok) > 1:
            i = _parse_short_option(
                tok, tokens, i, result, pcre_flags, pattern_opts,
                file_opts, req_val_opts, all_known,
            )
            continue
        if not result.inline_patterns:
            result.inline_patterns.append(tok)
        i += 1

    return result


def _parse_long_option(
    tok: str, tokens: list[str], i: int,
    result: _ShellParseResult,
    pcre_flags: set[str], pattern_opts: set[str],
    file_opts: set[str], req_val_opts: set[str],
    opt_val_opts: set[str], all_known: set[str],
) -> int:
    """Parse a long option token, mutating result in place. Returns new index."""
    if "=" in tok:
        opt, _, val = tok.partition("=")
        if opt in pcre_flags:
            result.is_pcre = True
            return i + 1
        if opt in pattern_opts:
            result.is_pcre = True
            result.inline_patterns.append(val)
            return i + 1
        if opt in file_opts:
            result.pattern_file = val
            return i + 1
        if opt not in all_known:
            result.has_unknown_option = True
            result.parse_confidence = "low"
        return i + 1
    if tok in pcre_flags:
        result.is_pcre = True
        return i + 1
    if tok in pattern_opts:
        result.is_pcre = True
        val = tokens[i + 1] if i + 1 < len(tokens) else None
        if val is not None:
            result.inline_patterns.append(val)
            return i + 2
        return i + 1
    if tok in file_opts:
        val = tokens[i + 1] if i + 1 < len(tokens) else None
        if val is not None:
            result.pattern_file = val
            return i + 2
        return i + 1
    if tok in req_val_opts:
        if i + 1 < len(tokens):
            return i + 2
        return i + 1
    if tok in opt_val_opts:
        return i + 1
    result.has_unknown_option = True
    result.parse_confidence = "low"
    return i + 1


def _parse_short_option(
    tok: str, tokens: list[str], i: int,
    result: _ShellParseResult,
    pcre_flags: set[str], pattern_opts: set[str],
    file_opts: set[str], req_val_opts: set[str],
    all_known: set[str],
) -> int:
    """Parse a short option cluster, mutating result in place. Returns new index."""
    cluster = tok[1:]
    idx = 0
    while idx < len(cluster):
        ch = cluster[idx]
        flag = f"-{ch}"
        if flag in pcre_flags:
            result.is_pcre = True
            idx += 1
            continue
        if flag in pattern_opts:
            result.is_pcre = True
            rest = cluster[idx + 1:]
            if rest:
                result.inline_patterns.append(rest)
                return i + 1
            val = tokens[i + 1] if i + 1 < len(tokens) else None
            if val is not None:
                result.inline_patterns.append(val)
                return i + 2
            return i + 1
        if flag in file_opts:
            rest = cluster[idx + 1:]
            if rest:
                result.pattern_file = rest
                return i + 1
            val = tokens[i + 1] if i + 1 < len(tokens) else None
            if val is not None:
                result.pattern_file = val
                return i + 2
            return i + 1
        if flag in req_val_opts:
            rest = cluster[idx + 1:]
            if rest:
                return i + 1
            if i + 1 < len(tokens):
                return i + 2
            return i + 1
        if flag not in all_known:
            result.has_unknown_option = True
            result.parse_confidence = "low"
        idx += 1
    return i + 1


def _has_unbalanced_quotes(line: str) -> bool:
    """Check if a shell line has unbalanced quotes using shlex."""
    logical_line = line.replace("\\\n", " ")
    try:
        list(shlex.shlex(logical_line, posix=True, punctuation_chars="|&;"))
    except ValueError:
        return True
    return False


def _extract_shell_regexes(
    content: str,
) -> list[tuple[str, int, Engine, str, PatternSource]]:
    """Extract regex patterns from shell scripts.

    Returns list of (pattern, line, engine, command, pattern_source) tuples.
    Handles backslash-newline continuation to form logical lines.
    """
    results: list[tuple[str, int, Engine, str, PatternSource]] = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        line_num = i + 1
        while line.rstrip().endswith("\\") and i + 1 < len(lines):
            line = line.rstrip()[:-1] + " " + lines[i + 1].lstrip()
            i += 1
        i += 1
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        if _has_unbalanced_quotes(line):
            results.extend(_extract_segment_regexes_broken(line_num, line))
            continue
        segments = _split_shell_pipeline(line)
        for seg_tokens in segments:
            results.extend(_extract_segment_regexes(seg_tokens, line_num))
    return results


def _extract_segment_regexes_broken(
    line_num: int, line: str,
) -> list[tuple[str, int, Engine, str, PatternSource]]:
    """Produce an unparsed result for a line with unbalanced quotes."""
    for cmd in _PCRE_COMMANDS:
        if cmd in line:
            return [("", line_num, Engine.SHELL_PCRE, cmd,
                     PatternSource.UNKNOWN)]
    return []


def _extract_segment_regexes(
    seg_tokens: list[str], line_num: int,
) -> list[tuple[str, int, Engine, str, PatternSource]]:
    results: list[tuple[str, int, Engine, str, PatternSource]] = []
    seg_tokens = _strip_env_assignments(seg_tokens)
    if not seg_tokens:
        return results
    cmd = seg_tokens[0]
    cmd_base = cmd.rsplit("/", 1)[-1]
    if cmd_base not in _PCRE_COMMANDS:
        return results
    parse = _parse_shell_command(seg_tokens[1:], cmd_base)
    is_pcre_or_perl = parse.is_pcre or cmd_base == "perl"
    psrc = PatternSource.UNKNOWN if parse.parse_confidence == "low" else PatternSource.STATIC_LITERAL
    for pat in parse.inline_patterns:
        results.append((pat, line_num, Engine.SHELL_PCRE, cmd_base, psrc))
    if parse.pattern_file is not None:
        file_psrc = PatternSource.STATIC_LITERAL
        file_path = Path(parse.pattern_file)
        if file_path.is_file():
            try:
                for file_line in file_path.read_text(encoding="utf-8").splitlines():
                    if file_line:
                        results.append((file_line, line_num, Engine.SHELL_PCRE,
                                         cmd_base, file_psrc))
            except OSError:
                file_psrc = PatternSource.UNKNOWN
                results.append(("", line_num, Engine.SHELL_PCRE, cmd_base, file_psrc))
        else:
            file_psrc = PatternSource.UNKNOWN
            results.append(("", line_num, Engine.SHELL_PCRE, cmd_base, file_psrc))
    if not parse.inline_patterns and parse.pattern_file is None and is_pcre_or_perl:
        results.append(("", line_num, Engine.SHELL_PCRE, cmd_base,
                         PatternSource.UNKNOWN))
    return results


def _analyze_shell_pattern(
    pattern: str, engine: Engine,
) -> tuple[str | None, str, Severity]:
    """Analyze a shell regex pattern for ReDoS risk."""
    if engine == Engine.SHELL_ERE:
        return None, "", Severity.INFO
    for check in _CHECKS:
        if result := check(pattern):
            return (
                result,
                "Avoid backtracking-prone patterns in PCRE contexts. "
                "Use a simpler pattern or a deterministic parser.",
                Severity.ERROR,
            )
    return None, "", Severity.INFO


# ---------------------------------------------------------------------------
# Suppression checking
# ---------------------------------------------------------------------------

def _check_suppression(
    lines: list[str], line_num: int,
) -> tuple[bool, str | None]:
    """Check for a valid suppression comment.

    Looks on the same line, the preceding line, and then walks upward through
    consecutive comment/blank lines (up to 30 lines) so that multi-line
    ``re.compile(...)`` calls can be suppressed by a comment block placed
    immediately above the statement.
    """
    # Same line or immediately preceding line.
    for offset in (0, -1):
        idx = line_num - 1 + offset
        if 0 <= idx < len(lines):
            result = _match_suppression_in_line(lines[idx])
            if result is not None:
                return result
    # Walk upward through consecutive comment/blank lines.
    idx = line_num - 2
    steps = 0
    while idx >= 0 and steps < 30:
        stripped = lines[idx].strip()
        if stripped and not stripped.startswith("#"):
            break
        result = _match_suppression_in_line(lines[idx])
        if result is not None:
            return result
        idx -= 1
        steps += 1
    return False, None


def _match_suppression_in_line(line: str) -> tuple[bool, str | None] | None:
    """Return a suppression match for a line, or None if not a suppression."""
    if m := _SUPPRESSION_RE.search(line):
        justification = m.group(1).strip()
        return (True, justification) if justification else (True, None)
    return (True, None) if _BARE_SUPPRESSION_RE.search(line) else None


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def _read_file_content(
    file_path: Path, rel_path: str, errors: list[ScanError],
) -> str | None:
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            errors.append(ScanError(
                file_path=rel_path, line=0,
                message="File is not valid UTF-8; decoded with replacement",
            ))
            return content
        except OSError as e:
            errors.append(ScanError(
                file_path=rel_path, line=0,
                message=f"Cannot read file: {e}",
            ))
            return None
    except OSError as e:
        errors.append(ScanError(
            file_path=rel_path, line=0,
            message=f"Cannot read file: {e}",
        ))
        return None


def _scan_python_file(
    file_path: Path, repo_root: Path,
) -> tuple[list[RegexFinding], list[ScanError]]:
    """Scan a Python file using AST-based extraction."""
    try:
        rel_path = str(file_path.relative_to(repo_root))
    except ValueError:
        rel_path = str(file_path)
    findings: list[RegexFinding] = []
    errors: list[ScanError] = []

    content = _read_file_content(file_path, rel_path, errors)
    if content is None:
        return findings, errors

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError as e:
        errors.append(ScanError(
            file_path=rel_path, line=e.lineno or 0,
            message=f"Python syntax error: {e.msg}",
        ))
        return findings, errors

    source_lines = content.split("\n")
    visitor = RegexASTVisitor()
    visitor.set_source(tree, source_lines, rel_path)
    visitor.visit(tree)

    for f in visitor.findings:
        suppressed, justification = _check_suppression(source_lines, f.line)
        if not suppressed and f.compile_line is not None:
            suppressed, justification = _check_suppression(
                source_lines, f.compile_line,
            )
        if suppressed:
            if justification is None:
                errors.append(ScanError(
                    file_path=rel_path, line=f.line,
                    message=(
                        "Invalid suppression: # nosec:regex-safety without "
                        "justification. Required format: "
                        "# nosec:regex-safety -- <reason>"
                    ),
                ))
            continue
        findings.append(f)

    errors.extend(visitor.errors)
    return findings, errors


def _process_shell_regexes(
    shell_regexes: list[tuple[str, int, Engine, str, PatternSource]],
    source_lines: list[str], rel_path: str,
) -> tuple[list[RegexFinding], list[ScanError]]:
    findings: list[RegexFinding] = []
    errors: list[ScanError] = []
    for pattern, line_num, engine, command, psrc in shell_regexes:
        suppressed, justification = _check_suppression(source_lines, line_num)
        if suppressed:
            if justification is None:
                errors.append(ScanError(
                    file_path=rel_path, line=line_num,
                    message="Invalid suppression without justification",
                ))
            continue
        finding = _build_shell_finding(pattern, line_num, engine, command,
                                       rel_path, psrc)
        if finding is not None:
            findings.append(finding)
    return findings, errors


def _build_shell_finding(
    pattern: str, line_num: int, engine: Engine, command: str, rel_path: str,
    pattern_source: PatternSource = PatternSource.STATIC_LITERAL,
) -> RegexFinding | None:
    reason, remediation, severity = _analyze_shell_pattern(pattern, engine)
    if reason is not None:
        return RegexFinding(
            severity=severity, engine=engine, file_path=rel_path,
            line=line_num, function=_SHELL_FUNCTION_NAME, api=command, pattern=pattern,
            pattern_source=pattern_source,
            input_scope="shell argument",
            reason=reason, remediation=remediation,
        )
    if engine == Engine.SHELL_PCRE and "$" in pattern:
        return RegexFinding(
            severity=Severity.REVIEW, engine=engine, file_path=rel_path,
            line=line_num, function=_SHELL_FUNCTION_NAME, api=command, pattern=pattern,
            pattern_source=PatternSource.DYNAMIC, input_scope="shell variable",
            reason=(
                "Shell pattern contains variable expansion ($) — if "
                "variable is untrusted, this is regex injection"
            ),
            remediation=(
                "Validate or sanitize variable content before using as a "
                "regex pattern."
            ),
        )
    if engine == Engine.SHELL_PCRE and not pattern:
        return RegexFinding(
            severity=Severity.REVIEW, engine=engine, file_path=rel_path,
            line=line_num, function=_SHELL_FUNCTION_NAME, api=command,
            pattern="<unparsed>", pattern_source=PatternSource.UNKNOWN,
            input_scope="shell argument",
            reason=(
                "PCRE flag present but pattern argument could not be "
                "reliably extracted — manual review required"
            ),
            remediation=(
                "Restructure the command so the pattern is a single quoted "
                "argument following the regex flag."
            ),
        )
    if pattern_source == PatternSource.UNKNOWN and pattern:
        return RegexFinding(
            severity=Severity.REVIEW, engine=engine, file_path=rel_path,
            line=line_num, function=_SHELL_FUNCTION_NAME, api=command,
            pattern=pattern, pattern_source=PatternSource.UNKNOWN,
            input_scope="shell argument",
            reason=(
                "PCRE pattern extracted with low confidence due to "
                "unrecognized options — manual review recommended"
            ),
            remediation=(
                "Restructure the command so the pattern is a single quoted "
                "argument following the regex flag, or simplify the options."
            ),
        )
    return None


def _scan_shell_file(
    file_path: Path, repo_root: Path,
) -> tuple[list[RegexFinding], list[ScanError]]:
    """Scan a shell script for regex usage."""
    try:
        rel_path = str(file_path.relative_to(repo_root))
    except ValueError:
        rel_path = str(file_path)
    findings: list[RegexFinding] = []
    errors: list[ScanError] = []

    content = _read_file_content(file_path, rel_path, errors)
    if content is None:
        return findings, errors

    source_lines = content.split("\n")
    shell_regexes = _extract_shell_regexes(content)
    f, e = _process_shell_regexes(shell_regexes, source_lines, rel_path)
    findings.extend(f)
    errors.extend(e)
    return findings, errors


def _should_exclude(path: Path, repo_root: Path) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return False
    parts = rel.parts
    return any(part in _EXCLUDE_DIRS for part in parts)


def _collect_dir_files(
    scan_dir: Path, repo_root: Path,
) -> list[Path]:
    files: list[Path] = []
    if scan_dir.is_file():
        if scan_dir.suffix in (".py", ".sh"):
            files.append(scan_dir)
        return files
    if not scan_dir.is_dir():
        return files
    for glob in _SCAN_GLOBS:
        files.extend(
            f
            for f in sorted(scan_dir.rglob(glob))
            if not _should_exclude(f, repo_root)
        )
    return files


def _deduplicate_files(files: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(f)
    return unique


def _collect_files(
    scan_dirs: list[Path], repo_root: Path,
) -> list[Path]:
    files: list[Path] = []
    for scan_dir in scan_dirs:
        files.extend(_collect_dir_files(scan_dir, repo_root))
    return _deduplicate_files(files)


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def _resolve_scan_dirs(args: argparse.Namespace) -> tuple[list[Path], int | None]:
    if args.path:
        try:
            scan_path = validate_read_path(args.path, purpose="regex scan path")
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return [], 1
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return [], 2
        return [scan_path], None
    if args.directory:
        try:
            scan_path = validate_read_path(args.directory, purpose="regex scan directory")
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return [], 1
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return [], 2
        if not scan_path.is_dir():
            print(f"ERROR: not a directory: {scan_path}", file=sys.stderr)
            return [], 2
        return [scan_path], None
    return [REPO_ROOT / d for d in _DEFAULT_SCAN_DIRS], None


def _validate_scan_dirs(
    scan_dirs: list[Path], args: argparse.Namespace,
) -> tuple[list[Path], int | None]:
    valid_dirs: list[Path] = []
    for d in scan_dirs:
        if d.is_dir() or d.is_file():
            valid_dirs.append(d)
        elif args.path or args.directory:
            print(
                f"ERROR: scan path does not exist: {d}",
                file=sys.stderr,
            )
            return [], 1
    return valid_dirs, None


def _scan_files(
    files: list[Path],
) -> tuple[list[RegexFinding], list[ScanError]]:
    all_findings: list[RegexFinding] = []
    all_errors: list[ScanError] = []
    for f in files:
        if f.suffix == ".py":
            findings, errors = _scan_python_file(f, REPO_ROOT)
        elif f.suffix == ".sh":
            findings, errors = _scan_shell_file(f, REPO_ROOT)
        else:
            continue
        all_findings.extend(findings)
        all_errors.extend(errors)
    return all_findings, all_errors


def _output_results(
    all_findings: list[RegexFinding],
    all_errors: list[ScanError],
    args: argparse.Namespace,
) -> None:
    if args.format == "json":
        output = {
            "findings": [f.to_dict() for f in all_findings],
            "errors": [e.to_dict() for e in all_errors],
            "summary": _build_summary(all_findings, all_errors),
        }
        print(json.dumps(output, indent=2))
    else:
        for e in all_errors:
            print(e.to_text(), file=sys.stderr)
        for f in all_findings:
            print(f.to_text(), file=sys.stderr)


def _build_summary(
    all_findings: list[RegexFinding], all_errors: list[ScanError],
) -> dict:
    return {
        "total_findings": len(all_findings),
        "errors": sum(f.severity == Severity.ERROR for f in all_findings),
        "reviews": sum(f.severity == Severity.REVIEW for f in all_findings),
        "infos": sum(f.severity == Severity.INFO for f in all_findings),
        "parse_errors": len(all_errors),
    }


def _compute_exit_code(
    all_findings: list[RegexFinding],
    all_errors: list[ScanError],
    args: argparse.Namespace,
) -> int:
    error_count = sum(f.severity == Severity.ERROR for f in all_findings)
    review_count = sum(f.severity == Severity.REVIEW for f in all_findings)
    info_count = sum(f.severity == Severity.INFO for f in all_findings)
    parse_error_count = len(all_errors)

    strict_mode = args.strict or args.fail_on_review

    summary = (
        f"Summary: {error_count} ERROR, {review_count} REVIEW, "
        f"{info_count} INFO, {parse_error_count} parse error(s)"
    )
    print(summary, file=sys.stderr)

    if strict_mode:
        blocking = error_count + parse_error_count
        if blocking > 0:
            print(
                f"FAIL: {error_count} blocking finding(s), "
                f"{parse_error_count} parse error(s)",
                file=sys.stderr,
            )
            return 1
        if args.fail_on_review and review_count > 0:
            print(
                f"FAIL: {review_count} REVIEW finding(s) require attention "
                "(--fail-on-review treats REVIEW as blocking)",
                file=sys.stderr,
            )
            return 1
        return _report_review_and_pass(
            review_count,
            ' REVIEW finding(s) require attention (non-blocking in --strict mode)',
        )
    if error_count > 0:
        print(
            f"WARN: {error_count} blocking finding(s) (advisory)",
            file=sys.stderr,
        )
    return _report_review_and_pass(
        review_count, ' REVIEW finding(s) (advisory)'
    )


def _report_review_and_pass(review_count: int, suffix: str) -> int:
    """Print REVIEW summary (if any) and return exit code 0 (pass)."""
    if review_count > 0:
        print(f"WARN: {review_count}{suffix}", file=sys.stderr)
    print("OK: regex safety check passed", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detect unsafe regex patterns prone to catastrophic backtracking "
            "(Rule 10) and dynamic regex injection."
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help=(
            "Directory to scan (default: tools/, packaging/, tests/, skills/ "
            "relative to repo root)"
        ),
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Specific path to scan (overrides default scope)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 on ERROR findings, parse errors, or scan/read errors. "
            "REVIEW findings are non-blocking."
        ),
    )
    parser.add_argument(
        "--fail-on-review",
        action="store_true",
        help=(
            "Implies --strict and additionally exits 1 on REVIEW findings. "
            "Use when the repository should ship no unreviewed "
            "dynamic/unknown regex."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    scan_dirs, exit_code = _resolve_scan_dirs(args)
    if exit_code is not None:
        return exit_code

    valid_dirs, exit_code = _validate_scan_dirs(scan_dirs, args)
    if exit_code is not None:
        return exit_code

    files = _collect_files(valid_dirs, REPO_ROOT)
    if not files:
        print("OK: no files to scan", file=sys.stderr)
        return 0

    all_findings, all_errors = _scan_files(files)
    _output_results(all_findings, all_errors, args)
    return _compute_exit_code(all_findings, all_errors, args)


if __name__ == "__main__":
    sys.exit(main())