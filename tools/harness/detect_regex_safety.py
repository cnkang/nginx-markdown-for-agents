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

# Prefix for compiled pattern method APIs (e.g., "compiled.search", "compiled.match").
_COMPILED_API_PREFIX = "compiled."

# Input scope description for shell arguments.
_SHELL_ARG_INPUT_SCOPE = "shell argument"


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
    # A tombstone written by ``del`` in the lexical scope where the delete
    # occurs.  Lookup stops at a DELETED binding instead of continuing to an
    # enclosing scope, mirroring Python's lexical delete semantics.
    DELETED = "DELETED"


@dataclass
class _Binding:
    kind: _BindingKind
    static_value: str | None = None
    real_re_api: str | None = None
    compiled_pattern: str | None = None
    compile_line: int | None = None


def _static_binding_value(binding: _Binding | _Sentinel) -> str | None:
    """Return a statically-known string represented by one lexical binding."""
    if isinstance(binding, _Sentinel):
        return None
    if binding.kind == _BindingKind.STATIC_STRING:
        return binding.static_value
    if binding.kind == _BindingKind.COMPILED_STATIC_PATTERN:
        return binding.compiled_pattern
    return None


def _segment_for_binding(binding: _Binding | _Sentinel) -> _Segment:
    """Convert a lexical binding to its conservative pattern segment."""
    static_value = _static_binding_value(binding)
    if static_value is not None:
        return _Segment.static(static_value)
    if isinstance(binding, _Binding) and binding.kind in (
        _BindingKind.DYNAMIC_VALUE,
        _BindingKind.COMPILED_DYNAMIC_PATTERN,
        _BindingKind.DELETED,
    ):
        return _Segment.dynamic()
    return _Segment.unknown()


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
    # Walk inner-to-outer.  A DELETED tombstone in an inner scope shadows
    # the same name in outer scopes, mirroring Python's lexical ``del``:
    # after ``del p`` in a function, ``p`` no longer refers to the module
    # binding while inside that function.
    for scope in reversed(stack):
        result = scope.lookup(name)
        if not isinstance(result, _Sentinel):
            return result
    return _UNRESOLVED


def _scope_assign(stack: list[_Scope], name: str, binding: _Binding) -> None:
    stack[-1].assign(name, binding)


def _scope_delete(
    stack: list[_Scope], name: str,
    global_names: set[str] | None = None, nonlocal_names: set[str] | None = None,
) -> None:
    """Apply a lexical ``del name``.

    Without ``global``/``nonlocal`` declarations (not yet modeled here), a
    ``del name`` only affects the current lexical scope: if the name is
    bound there it is removed, otherwise a DELETED tombstone is written so
    subsequent lookups in this scope do not fall through to an enclosing
    binding.  ``global``/``nonlocal`` declarations are recorded for a
    conservative REVIEW (see visit_Global/visit_Nonlocal); when present,
    the delete is applied against the module scope or the nearest enclosing
    binding respectively to avoid mis-modeling cross-scope deletes.
    """
    if global_names is not None and name in global_names:
        if len(stack) > 1 and name in stack[0].bindings:
            stack[0].delete(name)
        else:
            stack[-1].assign(name, _Binding(kind=_BindingKind.DELETED))
        return
    if nonlocal_names is not None and name in nonlocal_names:
        for scope in reversed(stack[1:]):
            if name in scope.bindings:
                scope.delete(name)
                return
        stack[-1].assign(name, _Binding(kind=_BindingKind.DELETED))
        return
    if name in stack[-1].bindings:
        stack[-1].delete(name)
    else:
        stack[-1].assign(name, _Binding(kind=_BindingKind.DELETED))


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
        # Per-scope ``global``/``nonlocal`` declaration sets (indexed by
        # position in the scope stack).  ``global``/``nonlocal`` are only
        # partially modeled: a ``del`` honoring them is applied against the
        # module/enclosing scope, and any use of these statements is recorded
        # so callers can emit a conservative REVIEW when relevant.
        self._global_decls: list[set[str]] = [set()]
        self._nonlocal_decls: list[set[str]] = [set()]

    def set_source(self, tree: ast.AST, source_lines: list[str],
                    file_path: str) -> None:
        self._tree = tree
        self._source_lines = source_lines
        self._file_path = file_path

    # -- Scope management --------------------------------------------------

    def _enter_scope(self) -> None:
        self._scope_stack.append(_Scope())
        self._global_decls.append(set())
        self._nonlocal_decls.append(set())

    def _leave_scope(self) -> None:
        self._scope_stack.pop()
        self._global_decls.pop()
        self._nonlocal_decls.pop()

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
        self._visit_function_def(node, list(node.decorator_list))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_def(node, list(node.decorator_list))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped_node(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_lambda(node)

    def _visit_scoped_node(self, node: ast.AST) -> None:
        self._enter_scope()
        self.generic_visit(node)
        self._leave_scope()

    def _visit_function_def(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
        decorators: list[ast.AST],
    ) -> None:
        """Visit a function definition honoring Python's evaluation order.

        Defaults, decorators, return annotation, and parameter annotations
        are evaluated in the *enclosing* scope BEFORE the function name is
        bound there and BEFORE the function body scope is entered.  Only
        then are parameters bound (as DYNAMIC_VALUE) inside the new scope
        and the body analyzed.
        """
        # 1. Decorators, evaluated in the enclosing scope.
        for dec in decorators:
            self.visit(dec)
        # 2. Default value expressions (positional + keyword-only),
        #    evaluated in the enclosing scope.
        self._visit_function_defaults(node.args)
        # 3. Return annotation, evaluated in the enclosing scope.
        if getattr(node, "returns", None) is not None:
            self.visit(node.returns)
        # 4. Parameter annotations, evaluated in the enclosing scope.
        self._visit_param_annotations(node.args)
        # 5. Bind the function name in the enclosing scope as DYNAMIC_VALUE
        #    (the function object is opaque to regex analysis).
        _scope_assign(self._scope_stack, node.name, _Binding(
            kind=_BindingKind.DYNAMIC_VALUE,
        ))
        # 6. Enter the body scope, bind parameters, then analyze the body.
        self._enter_scope()
        self._bind_function_params(node.args)
        for child in node.body:
            self.visit(child)
        self._leave_scope()

    def _visit_function_defaults(self, args: ast.arguments) -> None:
        for default in args.defaults:
            self.visit(default)
        for default in args.kw_defaults:
            if default is not None:
                self.visit(default)

    def _visit_param_annotations(self, args: ast.arguments) -> None:
        for arg in (list(args.args) + list(args.posonlyargs)
                    + list(args.kwonlyargs)):
            if arg.annotation is not None:
                self.visit(arg.annotation)
        if args.vararg is not None and args.vararg.annotation is not None:
            self.visit(args.vararg.annotation)
        if args.kwarg is not None and args.kwarg.annotation is not None:
            self.visit(args.kwarg.annotation)

    def _visit_lambda(self, node: ast.Lambda) -> None:
        """Visit a lambda honoring Python's evaluation order.

        Default expressions are evaluated in the enclosing scope BEFORE the
        lambda scope is entered.  Annotations (if any) are also evaluated in
        the enclosing scope.
        """
        self._visit_function_defaults(node.args)
        self._visit_param_annotations(node.args)
        self._enter_scope()
        self._bind_function_params(node.args)
        self.visit(node.body)
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
        self._visit_for_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for_loop(node)

    def _visit_for_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self._bind_target_dynamic(node.target)
        self.visit(node.iter)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)

    def visit_With(self, node: ast.With) -> None:
        self._visit_with_node(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with_node(node)

    def _visit_with_node(self, node: ast.With | ast.AsyncWith) -> None:
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
            name = node.target.id
            binding = self._reconcile_compiled_reassignment(
                name, _Binding(kind=_BindingKind.DYNAMIC_VALUE), node.value,
            )
            _scope_assign(self._scope_stack, name, binding)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                _scope_delete(
                    self._scope_stack, target.id,
                    global_names=self._global_decls[-1],
                    nonlocal_names=self._nonlocal_decls[-1],
                )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._global_decls[-1].update(node.names)
        # global/nonlocal are not fully modeled; record nothing further here.
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._nonlocal_decls[-1].update(node.names)
        self.generic_visit(node)

    def _assign_target(
        self, target: ast.AST, binding: _Binding, value: ast.AST,
    ) -> None:
        if isinstance(target, ast.Name):
            name = target.id
            binding = self._reconcile_compiled_reassignment(name, binding, value)
            _scope_assign(self._scope_stack, name, binding)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._assign_target(elt, _Binding(
                    kind=_BindingKind.DYNAMIC_VALUE,
                ), value)

    def _reconcile_compiled_reassignment(
        self, name: str, binding: _Binding, value: ast.AST,
    ) -> _Binding:
        """Preserve compiled-regex semantics across reassignment.

        If ``name`` was previously bound to a compiled pattern
        (COMPILED_STATIC_PATTERN or COMPILED_DYNAMIC_PATTERN) and the new
        binding is a non-compiled dynamic value, promote it to
        COMPILED_DYNAMIC_PATTERN so subsequent ``name.search()``/etc. still
        emit a REVIEW instead of being silently ignored as a plain
        DYNAMIC_VALUE.  The original ``compile_line`` is carried over so
        the finding can point back at the ``re.compile(...)`` site.

        Augmented assignment (``p += ...``) is treated as a dynamic
        reassignment of the same target and goes through the same path.
        """
        if binding.kind in (
            _BindingKind.COMPILED_STATIC_PATTERN,
            _BindingKind.COMPILED_DYNAMIC_PATTERN,
        ):
            return binding
        old = _scope_lookup(self._scope_stack, name)
        if isinstance(old, _Sentinel):
            return binding
        if old.kind in (
            _BindingKind.COMPILED_STATIC_PATTERN,
            _BindingKind.COMPILED_DYNAMIC_PATTERN,
        ) and binding.kind == _BindingKind.DYNAMIC_VALUE:
            return _Binding(
                kind=_BindingKind.COMPILED_DYNAMIC_PATTERN,
                compile_line=old.compile_line,
            )
        return binding

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
            return left + right if left is not None and right is not None else None
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
            return self._match_compiled_call(func, looked)
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
        self, func: ast.Attribute, binding: _Binding,
    ) -> tuple[str | None, ast.AST | None]:
        api = func.attr
        if api not in _PATTERN_METHODS:
            return None, None
        if binding.kind == _BindingKind.COMPILED_STATIC_PATTERN:
            stored = binding.compiled_pattern
            if stored is not None:
                return f"{_COMPILED_API_PREFIX}{api}", ast.Constant(value=stored)
            return f"{_COMPILED_API_PREFIX}{api}", None
        return f"{_COMPILED_API_PREFIX}{api}", None

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
            return _static_binding_value(_scope_lookup(self._scope_stack, arg.id))
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
            return [_segment_for_binding(_scope_lookup(self._scope_stack, node.id))]
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
            return self._is_re_module_attr(func, "escape")
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

        if api.startswith(_COMPILED_API_PREFIX):
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
        if not api.startswith(_COMPILED_API_PREFIX):
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
            return self._is_re_module_attr(node, "DOTALL")
        if isinstance(node, ast.BinOp):
            return (self._contains_dotall(node.left)
                    or self._contains_dotall(node.right))
        return False

    def _is_re_module_attr(self, node: ast.Attribute, attr_name: str) -> bool:
        """Check if ``node`` accesses ``attr_name`` on an ``re`` module alias."""
        looked = _scope_lookup(self._scope_stack, node.value.id)
        if isinstance(looked, _Sentinel):
            return False
        return looked.kind == _BindingKind.RE_MODULE_ALIAS and node.attr == attr_name

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
        candidates = self._flatten_bitor(flags_arg)
        return ", ".join(filter(None, (
            self._describe_re_flag(candidate) for candidate in candidates
        )))

    def _describe_re_flag(self, candidate: ast.AST) -> str | None:
        """Return a canonical flag name only for a live ``re`` module alias."""
        if not isinstance(candidate, ast.Attribute):
            return None
        if not isinstance(candidate.value, ast.Name):
            return None
        binding = _scope_lookup(self._scope_stack, candidate.value.id)
        if isinstance(binding, _Sentinel):
            return None
        if binding.kind != _BindingKind.RE_MODULE_ALIAS:
            return None
        return f"re.{candidate.attr}"

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
        elif api.startswith(_COMPILED_API_PREFIX):
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
        reason = _strong_separator_overlap_reason(tok, nxt, leading_text, outer_quant)
        if reason is not None:
            return reason
    return None


def _strong_separator_overlap_reason(
    tok: _Token, nxt: _Token, leading_text: str, outer_quant: str,
) -> str | None:
    """Return a nested-quantifier reason when the inner atom overlaps the separator."""
    if len(leading_text) >= 2:
        if _atom_matches_char_class(tok.text, leading_text[0]):
            return (
                f"nested quantifier — inner '{tok.text}{nxt.text}' inside "
                f"outer '{outer_quant}' can consume the first character of "
                "the leading multi-char separator, causing exponential "
                "backtracking"
            )
        return None
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

# Supported shell regex commands.  For each command we classify its options
# into:
#   pcre_flags       — options that switch the command to a PCRE engine
#                      (grep -P, rg --pcre2).  perl is always PCRE.
#   pattern_options  — options that carry a regex pattern (-e, --regexp).
#   pattern_file_options — options that take a pattern file (-f, --file).
#   required_value   — options that consume the next token as a value.
#   optional_value   — options that may take a value via '=' but never
#                      consume the next token.
#   boolean_flags    — options that take no value (do not consume a token).
#   default_engine   — the engine used when no PCRE flag is present:
#                      grep/rg default engines are NOT PCRE (BRE/ERE for
#                      grep, Rust regex NFA for rg) and must not trigger
#                      PCRE ReDoS analysis.

_PCRE_FLAGS: dict[str, set[str]] = {
    "grep": {"-P", "--perl-regexp"},
    "rg": {"-P", "--pcre2"},
    "perl": set(),  # perl is always PCRE
}

_PATTERN_OPTIONS: dict[str, set[str]] = {
    "grep": {"-e", "--regexp"},
    "rg": {"-e", "--regexp"},
    "perl": {"-e", "-E"},
}

_PATTERN_FILE_OPTIONS: dict[str, set[str]] = {
    "grep": {"-f", "--file"},
    "rg": {"-f", "--file"},
}

_REQUIRED_VALUE_OPTIONS: dict[str, set[str]] = {
    "grep": {"-m", "-A", "-B", "-C", "--max-count",
             "--after-context", "--before-context", "--context",
             "--label", "-d", "--directories", "-D", "--devices",
             "--binary-files", "--exclude", "--exclude-from",
             "--exclude-dir", "--include"},
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

_BOOLEAN_FLAGS: dict[str, set[str]] = {
    "grep": {"-n", "--line-number", "-q", "--quiet", "--silent", "-i",
             "--ignore-case", "-v", "--invert-match", "-w", "--word-regexp",
             "-x", "--line-regexp", "-s", "--no-messages", "-h",
             "--no-filename", "-H", "--with-filename", "-o", "--only-matching",
             "-a", "--text", "-I", "-r", "--recursive", "-R",
             "--dereference-recursive", "-l", "--files-with-matches", "-L",
             "--files-without-match", "-z", "--null-data", "-U", "--binary"},
    "rg": {"-n", "--line-number", "-i", "--ignore-case", "-v",
           "--invert-match", "-w", "--word-regexp", "-x", "--line-regexp",
           "-q", "--quiet", "-s", "--case-sensitive", "-S", "--smart-case",
           "-u", "--unrestricted", "-H", "--hidden", "-l",
           "--files-with-matches", "--no-heading", "--json", "--multiline",
           "--multiline-dotall"},
    "perl": set(),
}

# perl is inherently PCRE; grep/rg default engines are NOT PCRE.
_SHELL_DEFAULT_ENGINE: dict[str, Engine | None] = {
    "grep": None,  # BRE/ERE — not PCRE
    "rg": None,    # Rust regex NFA — not PCRE
    "perl": Engine.SHELL_PCRE,
}

# Commands whose regex usage we inspect.  (keyed by base command name.)
_PCRE_COMMANDS = set(_PCRE_FLAGS)


@dataclass
class _ShellParseResult:
    engine: Engine | None = None
    inline_patterns: list[str] = field(default_factory=list)
    pattern_files: list[str] = field(default_factory=list)
    has_unknown_option: bool = False
    parse_confidence: str = "high"


@dataclass(frozen=True)
class _ShellOptionSets:
    """Classified option sets for one supported shell regex command."""
    pcre_flags: set[str]
    pattern: set[str]
    pattern_file: set[str]
    required_value: set[str]
    optional_value: set[str]
    boolean_flags: set[str]

    @property
    def known(self) -> set[str]:
        """Return every option whose argument contract is known."""
        return (
            self.pcre_flags | self.pattern | self.pattern_file
            | self.required_value | self.optional_value | self.boolean_flags
        )


def _shell_option_sets(cmd_base: str) -> _ShellOptionSets:
    """Build one complete option classification for ``cmd_base``."""
    return _ShellOptionSets(
        pcre_flags=_PCRE_FLAGS[cmd_base],
        pattern=_PATTERN_OPTIONS.get(cmd_base, set()),
        pattern_file=_PATTERN_FILE_OPTIONS.get(cmd_base, set()),
        required_value=_REQUIRED_VALUE_OPTIONS.get(cmd_base, set()),
        optional_value=_OPTIONAL_VALUE_OPTIONS.get(cmd_base, set()),
        boolean_flags=_BOOLEAN_FLAGS.get(cmd_base, set()),
    )


def _shell_option_sets(cmd_base: str) -> _ShellOptionSets:
    """Build one complete option classification for ``cmd_base``."""
    return _ShellOptionSets(
        pcre_flags=_PCRE_FLAGS[cmd_base],
        pattern=_PATTERN_OPTIONS.get(cmd_base, set()),
        pattern_file=_PATTERN_FILE_OPTIONS.get(cmd_base, set()),
        required_value=_REQUIRED_VALUE_OPTIONS.get(cmd_base, set()),
        optional_value=_OPTIONAL_VALUE_OPTIONS.get(cmd_base, set()),
        boolean_flags=_BOOLEAN_FLAGS.get(cmd_base, set()),
    )


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
    pattern-file references, engine selection, and parse confidence.
    When an unknown option is encountered, every subsequent non-option
    token that is not a known option value is also collected as a
    pattern candidate so a dangerous pattern cannot be hidden behind an
    unrecognized option.
    """
    result = _ShellParseResult()
    options = _shell_option_sets(cmd_base)
    # Apply the command's default engine (perl is inherently PCRE).
    result.engine = _SHELL_DEFAULT_ENGINE.get(cmd_base)

    saw_double_dash = False
    first_inline_taken = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if saw_double_dash:
            _collect_inline_candidate(result, tok)
            i += 1
            continue
        if tok == "--":
            saw_double_dash = True
            i += 1
            continue
        if tok.startswith("--"):
            i = _parse_long_option(tok, tokens, i, result, options)
            continue
        if tok.startswith("-") and len(tok) > 1:
            i = _parse_short_option(tok, tokens, i, result, options)
            continue
        # Non-option token — the first is the deterministic inline pattern;
        # after an unknown option, additional non-option tokens are also
        # collected so a hidden dangerous pattern is still analyzed.
        if not first_inline_taken or result.has_unknown_option:
            _collect_inline_candidate(result, tok)
            first_inline_taken = True
        i += 1

    return result


def _collect_inline_candidate(result: _ShellParseResult, tok: str) -> None:
    """Append a non-option token as an inline pattern candidate (dedup)."""
    if tok not in result.inline_patterns:
        result.inline_patterns.append(tok)


def _parse_long_option(
    tok: str, tokens: list[str], i: int,
    result: _ShellParseResult, options: _ShellOptionSets,
) -> int:
    """Parse a long option token, mutating result in place. Returns new index."""
    option, separator, value = tok.partition("=")
    value = value if separator else None
    return _consume_shell_option(option, value, tokens, i, result, options)


def _parse_short_option(
    tok: str, tokens: list[str], i: int,
    result: _ShellParseResult, options: _ShellOptionSets,
) -> int:
    """Parse a short option cluster, mutating result in place. Returns new index."""
    cluster = tok[1:]
    for idx in range(len(cluster)):
        flag = f"-{cluster[idx]}"
        rest = cluster[idx + 1:] or None
        kind = _shell_option_kind(flag, options)
        if kind in ("pattern", "file", "required"):
            return _consume_shell_option(flag, rest, tokens, i, result, options)
        _consume_shell_option(flag, None, tokens, i, result, options)
    return i + 1


def _consume_shell_option(
    option: str, attached_value: str | None, tokens: list[str], index: int,
    result: _ShellParseResult, options: _ShellOptionSets,
) -> int:
    """Apply one option contract and return the next token index."""
    kind = _shell_option_kind(option, options)
    if kind == "unknown":
        _mark_unknown_shell_option(result)
        return index + 1
    if kind == "pcre":
        result.engine = Engine.SHELL_PCRE
        return index + 1
    if kind in ("optional", "boolean"):
        return index + 1
    # pattern, file, required — these options consume a value.
    value, next_index = _option_value(attached_value, tokens, index)
    if kind == "pattern" and value is not None:
        result.inline_patterns.append(value)
    elif kind == "file" and value is not None:
        result.pattern_files.append(value)
    return next_index


def _shell_option_kind(option: str, options: _ShellOptionSets) -> str:
    """Return the argument contract category for one parsed option."""
    if option in options.pcre_flags:
        return "pcre"
    if option in options.pattern:
        return "pattern"
    if option in options.pattern_file:
        return "file"
    if option in options.required_value:
        return "required"
    if option in options.optional_value:
        return "optional"
    if option in options.boolean_flags:
        return "boolean"
    return "unknown"


def _option_value(
    attached_value: str | None, tokens: list[str], index: int,
) -> tuple[str | None, int]:
    """Return an attached or following required option value and next index."""
    if attached_value is not None:
        return attached_value, index + 1
    if index + 1 < len(tokens):
        return tokens[index + 1], index + 2
    return None, index + 1


def _mark_unknown_shell_option(result: _ShellParseResult) -> None:
    """Downgrade parsing confidence after an unknown option changes layout."""
    result.has_unknown_option = True
    result.parse_confidence = "low"


def _has_unbalanced_quotes(line: str) -> bool:
    """Check if a shell line has unbalanced quotes using shlex."""
    logical_line = line.replace("\\\n", " ")
    try:
        list(shlex.shlex(logical_line, posix=True, punctuation_chars="|&;"))
    except ValueError:
        return True
    return False


def _extract_shell_regexes(
    content: str, shell_file_path: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[list[tuple[str, int, Engine, str, PatternSource]], list[ScanError]]:
    """Extract regex patterns from shell scripts.

    Returns list of (pattern, line, engine, command, pattern_source) tuples
    plus scan errors raised while resolving pattern files.  Handles
    backslash-newline continuation to form logical lines.  Relative
    pattern-file paths are resolved against ``shell_file_path``'s
    directory and validated against ``repo_root`` (or REPO_ROOT) before
    reading.
    """
    results: list[tuple[str, int, Engine, str, PatternSource]] = []
    errors: list[ScanError] = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        line_num = i + 1
        while line.rstrip().endswith("\\") and i + 1 < len(lines):
            line = f"{line.rstrip()[:-1]} {lines[i + 1].lstrip()}"
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
            r, e = _extract_segment_regexes(
                seg_tokens, line_num, shell_file_path, repo_root,
            )
            results.extend(r)
            errors.extend(e)
    return results, errors


def _extract_segment_regexes_broken(
    line_num: int, line: str,
) -> list[tuple[str, int, Engine, str, PatternSource]]:
    """Produce an unparsed result for a line with unbalanced quotes."""
    return next(
        (
            [("", line_num, Engine.SHELL_PCRE, cmd, PatternSource.UNKNOWN)]
            for cmd in _PCRE_COMMANDS
            if cmd in line
        ),
        [],
    )


def _extract_segment_regexes(
    seg_tokens: list[str], line_num: int,
    shell_file_path: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[list[tuple[str, int, Engine, str, PatternSource]], list[ScanError]]:
    results: list[tuple[str, int, Engine, str, PatternSource]] = []
    errors: list[ScanError] = []
    seg_tokens = _strip_env_assignments(seg_tokens)
    if not seg_tokens:
        return results, errors
    cmd = seg_tokens[0]
    cmd_base = cmd.rsplit("/", 1)[-1]
    if cmd_base not in _PCRE_COMMANDS:
        return results, errors
    parse = _parse_shell_command(seg_tokens[1:], cmd_base)
    engine = parse.engine
    # grep/rg default engines are NOT PCRE (BRE/ERE for grep, Rust regex NFA
    # for rg).  Only perl and commands with an explicit PCRE flag produce
    # Engine.SHELL_PCRE.  Non-PCRE inline patterns are not analyzed for
    # catastrophic backtracking (those engines are DFA/NFA-based).
    if engine is None:
        engine = _SHELL_DEFAULT_ENGINE.get(cmd_base) or Engine.SHELL_ERE
    source = _shell_pattern_source(parse)
    results.extend(_inline_shell_patterns(parse, line_num, cmd_base, engine, source))
    file_results, file_errors = _pattern_file_regexes(
        parse.pattern_files, line_num, cmd_base, engine, shell_file_path, repo_root,
    )
    results.extend(file_results)
    errors.extend(file_errors)
    if _needs_unknown_shell_pattern(parse, cmd_base):
        results.append(("", line_num, engine, cmd_base, PatternSource.UNKNOWN))
    return results, errors


def _shell_pattern_source(parse: _ShellParseResult) -> PatternSource:
    """Classify inline shell patterns from parser confidence."""
    if parse.parse_confidence == "low":
        return PatternSource.UNKNOWN
    return PatternSource.STATIC_LITERAL


def _inline_shell_patterns(
    parse: _ShellParseResult, line_num: int, cmd_base: str,
    engine: Engine, source: PatternSource,
) -> list[tuple[str, int, Engine, str, PatternSource]]:
    """Build findings for all inline patterns from one shell command."""
    return [
        (pattern, line_num, engine, cmd_base, source)
        for pattern in parse.inline_patterns
    ]


def _pattern_file_regexes(
    pattern_files: list[str], line_num: int, cmd_base: str,
    engine: Engine, shell_file_path: Path | None, repo_root: Path | None,
) -> tuple[list[tuple[str, int, Engine, str, PatternSource]], list[ScanError]]:
    """Read static pattern-file entries or emit conservative REVIEW results.

    Each pattern-file path is resolved relative to the shell script's
    directory (not the detector's cwd), validated to remain within
    ``repo_root`` (or REPO_ROOT), then read as UTF-8.  Read, encoding, or
    boundary failures produce a conservative UNKNOWN REVIEW candidate
    and a ScanError rather than crashing the detector.
    """
    root = repo_root or REPO_ROOT
    results: list[tuple[str, int, Engine, str, PatternSource]] = []
    errors: list[ScanError] = []
    for token in pattern_files:
        resolved, resolve_err = _resolve_pattern_file(
            token, shell_file_path, line_num, root,
        )
        if resolve_err is not None:
            errors.append(resolve_err)
            results.append(("", line_num, engine, cmd_base, PatternSource.UNKNOWN))
            continue
        file_results, read_errors = _read_pattern_file(
            resolved, line_num, engine, cmd_base,
        )
        results.extend(file_results)
        errors.extend(read_errors)
    return results, errors


def _resolve_pattern_file(
    token: str, shell_file_path: Path | None, line_num: int,
    repo_root: Path,
) -> tuple[Path | None, ScanError | None]:
    """Resolve a pattern-file token against the shell script directory.

    Rejects absolute paths, ``..`` traversal, and paths that escape
    ``repo_root`` after symlink resolution.  Returns the resolved Path or
    a ScanError describing why resolution was refused.
    """
    base_dir = shell_file_path.parent if shell_file_path is not None else repo_root
    if Path(token).is_absolute():
        return None, ScanError(
            file_path=str(shell_file_path or token), line=line_num,
            message=(
                f"pattern file {token!r} is an absolute path; only "
                "repository-relative pattern files are allowed"
            ),
        )
    if ".." in Path(token).parts:
        return None, ScanError(
            file_path=str(shell_file_path or token), line=line_num,
            message=(
                f"pattern file {token!r} contains a '..' traversal "
                "component; refusing to read outside the repository"
            ),
        )
    candidate = (base_dir / token)
    try:
        resolved = candidate.resolve()
    except OSError as e:
        return None, ScanError(
            file_path=str(shell_file_path or token), line=line_num,
            message=f"cannot resolve pattern file {token!r}: {e}",
        )
    if not resolved.is_relative_to(repo_root.resolve()):
        return None, ScanError(
            file_path=str(shell_file_path or token), line=line_num,
            message=(
                f"pattern file {token!r} resolves outside the repository "
                f"root; refusing to read"
            ),
        )
    return resolved, None


def _read_pattern_file(
    resolved: Path, line_num: int, engine: Engine, cmd_base: str,
) -> tuple[list[tuple[str, int, Engine, str, PatternSource]], list[ScanError]]:
    """Read one resolved pattern file and return per-line pattern tuples."""
    results: list[tuple[str, int, Engine, str, PatternSource]] = []
    errors: list[ScanError] = []
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        errors.append(ScanError(
            file_path=str(resolved), line=0,
            message=f"pattern file is not valid UTF-8: byte {e.start}: {e.reason}",
        ))
        results.append(("", line_num, engine, cmd_base, PatternSource.UNKNOWN))
        return results, errors
    except (OSError, FileNotFoundError, PermissionError, IsADirectoryError) as e:
        errors.append(ScanError(
            file_path=str(resolved), line=0,
            message=f"cannot read pattern file: {e}",
        ))
        results.append(("", line_num, engine, cmd_base, PatternSource.UNKNOWN))
        return results, errors
    for raw_line in content.split("\n"):
        # Strip a trailing CR from CRLF line endings; grep/rg treat each
        # line as a pattern.  Empty lines are skipped (grep treats an
        # empty pattern line as matching everything, not as a regex).
        pattern = raw_line.rstrip("\r")
        if not pattern.strip():
            continue
        results.append((pattern, line_num, engine, cmd_base, PatternSource.STATIC_LITERAL))
    return results, errors


def _needs_unknown_shell_pattern(parse: _ShellParseResult, cmd_base: str) -> bool:
    """Return whether a PCRE invocation has no safely recoverable pattern."""
    is_pcre_or_perl = parse.engine == Engine.SHELL_PCRE or cmd_base == "perl"
    return not parse.inline_patterns and not parse.pattern_files and is_pcre_or_perl


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
            input_scope=_SHELL_ARG_INPUT_SCOPE,
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
            input_scope=_SHELL_ARG_INPUT_SCOPE,
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
            input_scope=_SHELL_ARG_INPUT_SCOPE,
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
    shell_regexes, extract_errors = _extract_shell_regexes(
        content, file_path, repo_root,
    )
    errors.extend(extract_errors)
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
