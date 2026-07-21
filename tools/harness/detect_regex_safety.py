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
     as regex_search``) are resolved statically.
  2. Classify the pattern argument:
     STATIC_LITERAL, STATIC_CONCAT, STATIC_FORMATTED, ESCAPED_DYNAMIC,
     DYNAMIC, UNKNOWN.
  3. For static patterns, apply heuristic checks for known dangerous
     structures (nested quantifiers, overlapping alternation, backreference
     after ``.*``, adjacent unbounded repetitions, ``re.DOTALL`` + full-document
     ``.*``).
  4. For dynamic/unknown patterns, emit a REVIEW finding.
  5. Scan shell (.sh) scripts for ``grep -E``, ``grep -P``, ``sed -E``,
     ``rg -e``, ``rg -P``, and ``perl`` regex usage.
  6. Suppression requires an inline justification:
     ``# nosec:regex-safety -- <reason>``
  7. File-level or directory-level suppressions are not allowed.

This is a heuristic detector (no full NFA analysis).  It flags known-bad
structural patterns and dynamic injection risks.  False positives should be
suppressed with an inline comment containing a non-empty justification:
  ``# nosec:regex-safety -- trusted generated token, max_input_bytes=64``

Usage:
    python3 tools/harness/detect_regex_safety.py
    python3 tools/harness/detect_regex_safety.py --strict
    python3 tools/harness/detect_regex_safety.py --format json
    python3 tools/harness/detect_regex_safety.py --path tools/

Exit codes:
    0 — no blocking findings
    1 — strict findings, parse errors, or scan failures
    2 — CLI usage / configuration error
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Python ``re`` module functions that accept a pattern as first argument.
_RE_MODULE_FUNCS = frozenset({
    "compile", "search", "match", "fullmatch",
    "findall", "finditer", "split", "sub", "subn",
})

# Compiled pattern object methods (called on the return value of re.compile).
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

# Shell regex command patterns (simple, anchored, no nested quantifiers).
_SH_GREP_E_RE = re.compile(r"\bgrep\s+-[A-Za-z]*E\b")
_SH_GREP_P_RE = re.compile(r"\bgrep\s+-[A-Za-z]*P\b")
_SH_SED_E_RE = re.compile(r"\bsed\s+-E\b")
_SH_RG_RE = re.compile(r"\brg\s+(?:-e|--regexp|-P)\b")
_SH_PERL_RE = re.compile(r"\bperl\s+(?:-e|-E)\s+")

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

# Pattern truncation limit for output.
_MAX_PATTERN_DISPLAY = 80


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
# AST-based Python regex extraction
# ---------------------------------------------------------------------------

class RegexASTVisitor(ast.NodeVisitor):
    """Walk a Python AST and collect regex API calls.

    Resolves import aliases for the ``re`` module and ``from re import ...``
    forms.  Tracks variables assigned to ``re.compile(...)`` so that
    ``pattern.search(...)`` calls are also identified.
    """

    def __init__(self) -> None:
        self.findings: list[RegexFinding] = []
        self.errors: list[ScanError] = []
        self._re_names: set[str] = {"re"}
        self._from_re_names: dict[str, str] = {}
        self._compiled_vars: dict[str, str] = {}
        # Maps variable name -> pattern string (for re.compile assignment)
        self._var_patterns: dict[str, str] = {}
        # Maps variable name -> compile line number (for suppression check)
        self._compile_lines: dict[str, int] = {}
        self._source_lines: list[str] = []
        self._file_path: str = ""
        self._tree: ast.AST | None = None

    def set_source(self, tree: ast.AST, source_lines: list[str],
                    file_path: str) -> None:
        """Set the AST tree and source for this visitor."""
        self._tree = tree
        self._source_lines = source_lines
        self._file_path = file_path

    # -- Import resolution --------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        """Track ``import re`` and ``import re as alias``."""
        for alias in node.names:
            if alias.name == "re":
                bound = alias.asname or "re"
                self._re_names.add(bound)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Track ``from re import compile`` and ``from re import search as s``."""
        if node.module == "re":
            for alias in node.names:
                real_name = alias.name
                bound = alias.asname or alias.name
                self._from_re_names[bound] = real_name
                if real_name == "compile":
                    # ``from re import compile`` — track as a compile alias
                    self._from_re_names[bound] = "compile"
        self.generic_visit(node)

    # -- Assignment tracking -----------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track ``pattern = re.compile(...)`` assignments."""
        if isinstance(node.value, ast.Call):
            api_name = self._identify_re_call(node.value)
            if api_name == "compile":
                pattern_str = self._extract_pattern_arg(node.value)
                if pattern_str is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self._compiled_vars[target.id] = api_name
                            self._var_patterns[target.id] = pattern_str
                            self._compile_lines[target.id] = node.value.lineno
        self.generic_visit(node)

    # -- Call detection -----------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        """Identify re.* and compiled-pattern.* calls."""
        api_name, pattern_arg = self._match_regex_call(node)
        if api_name is not None:
            self._record_finding(node, api_name, pattern_arg)
        self.generic_visit(node)

    def _match_regex_call(self, node: ast.Call) -> tuple[str | None, ast.AST | None]:
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
        if var_name in self._re_names:
            return self._match_re_module_call(func, node)
        if var_name in self._compiled_vars:
            return self._match_compiled_call(func, var_name)
        return None, None

    def _match_re_module_call(
        self, func: ast.Attribute, node: ast.Call,
    ) -> tuple[str | None, ast.AST | None]:
        api = func.attr
        if api in _RE_MODULE_FUNCS:
            return api, node.args[0] if node.args else None
        return None, None

    def _match_compiled_call(
        self, func: ast.Attribute, var_name: str,
    ) -> tuple[str | None, ast.AST | None]:
        api = func.attr
        if api not in _PATTERN_METHODS:
            return None, None
        stored = self._var_patterns.get(var_name)
        if stored is not None:
            return f"compiled.{api}", ast.Constant(value=stored)
        return f"compiled.{api}", None

    def _match_name_call(
        self, func: ast.Name, node: ast.Call,
    ) -> tuple[str | None, ast.AST | None]:
        bound = func.id
        if bound not in self._from_re_names:
            return None, None
        real = self._from_re_names[bound]
        if real in _RE_MODULE_FUNCS:
            return real, node.args[0] if node.args else None
        return None, None

    def _identify_re_call(self, node: ast.Call) -> str | None:
        """Return the API name if ``node`` is a re.* call, else None."""
        api, _ = self._match_regex_call(node)
        return api

    def _extract_pattern_arg(self, node: ast.Call) -> str | None:
        """Extract the pattern string from a re.compile(...) call.

        Returns the resolved pattern string or None if dynamic.
        """
        return self._resolve_pattern(node.args[0]) if node.args else None

    def _resolve_pattern(self, arg: ast.AST) -> str | None:
        """Resolve an AST node to a pattern string if statically determinable.

        Returns None for dynamic/unknown patterns.
        """
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            return self._resolve_joined_str(arg)
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            return self._resolve_binop_pattern(arg)
        if isinstance(arg, ast.Call) and self._is_re_escape_call(arg):
            return None
        return None

    def _resolve_binop_pattern(self, arg: ast.BinOp) -> str | None:
        left = self._resolve_pattern(arg.left)
        right = self._resolve_pattern(arg.right)
        return left + right if left is not None and right is not None else None

    def _resolve_joined_str(self, node: ast.JoinedStr) -> str | None:
        """Resolve an f-string to a static string if it has no dynamic parts.

        Returns None if the f-string contains dynamic expressions.
        """
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue):
                # Dynamic expression inside f-string
                return None
            else:
                return None
        return "".join(parts)

    def _classify_pattern_source(
        self, arg: ast.AST | None,
    ) -> tuple[PatternSource, str | None]:
        """Classify the pattern argument and return (source, pattern_str)."""
        if arg is None:
            return PatternSource.UNKNOWN, None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return PatternSource.STATIC_LITERAL, arg.value
        if isinstance(arg, ast.JoinedStr):
            return self._classify_joined_str(arg)
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
            return self._classify_binop_pattern(arg)
        if isinstance(arg, ast.Call) and self._is_re_escape_call(arg):
            return PatternSource.ESCAPED_DYNAMIC, None
        if isinstance(arg, ast.Name):
            return self._classify_name_pattern(arg)
        return PatternSource.UNKNOWN, None

    def _classify_joined_str(
        self, arg: ast.JoinedStr,
    ) -> tuple[PatternSource, str | None]:
        result = self._resolve_joined_str(arg)
        if result is not None:
            return PatternSource.STATIC_FORMATTED, result
        return PatternSource.DYNAMIC, None

    def _classify_binop_pattern(
        self, arg: ast.BinOp,
    ) -> tuple[PatternSource, str | None]:
        left = self._resolve_pattern(arg.left)
        right = self._resolve_pattern(arg.right)
        if left is not None and right is not None:
            return PatternSource.STATIC_CONCAT, left + right
        if self._is_re_escape_call(arg.left) or \
                self._is_re_escape_call(arg.right):
            return PatternSource.ESCAPED_DYNAMIC, None
        return PatternSource.DYNAMIC, None

    def _classify_name_pattern(
        self, arg: ast.Name,
    ) -> tuple[PatternSource, str | None]:
        if arg.id in self._var_patterns:
            return PatternSource.STATIC_LITERAL, self._var_patterns[arg.id]
        return PatternSource.UNKNOWN, None

    def _is_re_escape_call(self, node: ast.AST) -> bool:
        """Check if ``node`` is a ``re.escape(...)`` call."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id in self._re_names and func.attr == "escape"
        if isinstance(func, ast.Name):
            return func.id in self._from_re_names and \
                self._from_re_names[func.id] == "escape"
        return False

    def _record_finding(
        self, node: ast.Call, api: str, pattern_arg: ast.AST | None,
    ) -> None:
        """Record a finding for a regex API call."""
        line = node.lineno
        func_name = self._enclosing_function_name(node)

        # For compiled-pattern method calls, also check suppression at
        # the re.compile() line where the nosec comment likely lives.
        compile_line = None
        if api.startswith("compiled."):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                var_name = func.value.id
                compile_line = self._compile_lines.get(var_name)

        pattern_source, pattern_str = self._classify_pattern_source(pattern_arg)

        # Check for re.DOTALL flag
        has_dotall = self._check_dotall(node)
        # Check for other flags
        flags_desc = self._describe_flags(node)

        # Determine input scope
        input_scope = self._infer_input_scope(api, node)

        if pattern_str is not None:
            # Static pattern — run ReDoS heuristics
            reason, remediation, severity = _analyze_static_pattern(
                pattern_str,
            )
            if reason is not None:
                self.findings.append(RegexFinding(
                    severity=severity,
                    engine=Engine.PYTHON_RE,
                    file_path=self._file_path,
                    line=line,
                    function=func_name,
                    api=api,
                    pattern=pattern_str,
                    pattern_source=pattern_source,
                    input_scope=input_scope,
                    reason=reason,
                    remediation=remediation,
                    compile_line=compile_line,
                ))
            elif has_dotall and ".*" in pattern_str:
                # DOTALL + .* over full document — REVIEW
                self.findings.append(RegexFinding(
                    severity=Severity.REVIEW,
                    engine=Engine.PYTHON_RE,
                    file_path=self._file_path,
                    line=line,
                    function=func_name,
                    api=api,
                    pattern=pattern_str,
                    pattern_source=pattern_source,
                    input_scope=input_scope,
                    reason=(
                        "re.DOTALL with .* pattern — if applied to unbounded "
                        "input, .* can cause excessive backtracking"
                    ),
                    remediation=(
                        "Anchor the pattern or use a deterministic line-by-line "
                        "scanner instead of DOTALL + .*"
                    ),
                    compile_line=compile_line,
                ))
        elif pattern_source == PatternSource.DYNAMIC:
            self.findings.append(
                RegexFinding(
                    severity=Severity.REVIEW,
                    engine=Engine.PYTHON_RE,
                    file_path=self._file_path,
                    line=line,
                    function=func_name,
                    api=api,
                    pattern="<dynamic>",
                    pattern_source=PatternSource.DYNAMIC,
                    input_scope=input_scope,
                    reason=f"Dynamic pattern source — pattern is constructed from runtime values{f' with {flags_desc}' if flags_desc else ''}. If input is untrusted, this is a regex injection risk.",
                    remediation=(
                        "Use re.escape() for dynamic components, or validate "
                        "input against an allowlist before using as a pattern."
                    ),
                    compile_line=compile_line,
                )
            )
        elif pattern_source == PatternSource.ESCAPED_DYNAMIC:
            # re.escape() wrapping is safe — no finding
            pass
        elif pattern_source == PatternSource.UNKNOWN:
            self.findings.append(RegexFinding(
                severity=Severity.INFO,
                engine=Engine.PYTHON_RE,
                file_path=self._file_path,
                line=line,
                function=func_name,
                api=api,
                pattern="<unknown>",
                pattern_source=PatternSource.UNKNOWN,
                input_scope=input_scope,
                reason=(
                    "Pattern source could not be statically resolved — "
                    "manual review recommended"
                ),
                remediation=(
                    "Ensure the pattern is a static literal or wrapped with "
                    "re.escape() if it incorporates dynamic values."
                ),
                compile_line=compile_line,
            ))

    def _check_dotall(self, node: ast.Call) -> bool:
        """Check if re.DOTALL is passed as a flag argument."""
        for arg in node.args[1:]:
            if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and (arg.value.id in self._re_names and arg.attr == "DOTALL"):
                return True
            if isinstance(arg, ast.BinOp) and self._check_dotall_in_bitor(arg):
                return True
        return False

    def _check_dotall_in_bitor(self, node: ast.BinOp) -> bool:
        """Check if re.DOTALL appears in a bitwise-or flag expression."""
        for side in (node.left, node.right):
            if isinstance(side, ast.Attribute) and isinstance(side.value, ast.Name) and (side.value.id in self._re_names and side.attr == "DOTALL"):
                return True
            if isinstance(side, ast.BinOp) and self._check_dotall_in_bitor(side):
                return True
        return False

    def _describe_flags(self, node: ast.Call) -> str:
        """Describe flag arguments for diagnostic output."""
        flags: list[str] = []
        flags.extend(
            f"re.{arg.attr}"
            for arg in node.args[1:]
            if isinstance(arg, ast.Attribute)
            and (
                isinstance(arg.value, ast.Name) and arg.value.id in self._re_names
            )
        )
        return ", ".join(flags) if flags else ""

    def _infer_input_scope(self, api: str, node: ast.Call) -> str:
        """Infer the likely input scope from the API and call context."""
        if api == "compile":
            return "pattern definition"
        if len(node.args) < 2:
            return "unknown"
        return self._infer_second_arg_scope(node.args[1])

    def _infer_second_arg_scope(self, second: ast.AST) -> str:
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            return "static string"
        if isinstance(second, ast.Call):
            return self._infer_call_scope(second)
        if isinstance(second, ast.Subscript):
            return "indexed value (possibly user-controlled)"
        if isinstance(second, ast.Name):
            return "variable (unknown origin)"
        return "unknown"

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
        """Find the name of the enclosing function for a given AST node.

        This is a best-effort search using line numbers.
        """
        if self._tree is None:
            return "<module>"
        target_line = getattr(node, "lineno", 0)
        best_name = "<module>"
        best_line = 0
        for n in ast.walk(self._tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (n.lineno <= target_line and n.lineno > best_line):
                end_line = getattr(n, "end_lineno", n.lineno + 1000)
                if target_line <= end_line:
                    best_name = n.name
                    best_line = n.lineno
        return best_name


# ---------------------------------------------------------------------------
# ReDoS heuristic analysis (operates on resolved pattern strings)
# ---------------------------------------------------------------------------

def _group_has_literal_anchor(group_content: str) -> bool:
    """Check if a regex group starts with a required non-quantified literal.

    A literal anchor at the start of a group prevents overlap between
    iterations of an outer quantifier, making the pattern safe.
    """
    inner = group_content
    inner = inner.removeprefix("?:")
    if not inner:
        return False
    # Literal char not followed by a quantifier
    if inner[0] not in (".", "[", "\\", "(", "?", "*", "+", "|", "^", "$"):
        return len(inner) < 2 or inner[1] not in ("+", "*", "?", "{")
    # Literal escape (e.g. \_, \-) not followed by a quantifier
    if inner.startswith("\\") and len(inner) > 1 and inner[1] not in "wWdDsS.bB":
        return len(inner) < 3 or inner[2] not in ("+", "*", "?", "{")
    return False


def _check_nested_quantifier(pattern: str) -> str | None:
    """Check for nested quantifiers like (a+)+, (a*)+, (.+)+, (.*)+.

    Only flags when the outer quantifier is + or * (unbounded) AND the group
    content does not start with a required literal anchor.

    Safe patterns (literal separator prevents overlap):
      (-[a-z]+)*  — the '-' is required at each iteration start
      (_\\d+)*    — the '_' is required at each iteration start

    Dangerous patterns:
      (a+)+       — inner and outer both consume 'a'
      (.*)+       — unbounded overlap
      ([^x]*)+    — inner can match empty, outer repeats
    """
    # Match group containing a quantifier followed by an outer quantifier.
    # Use [^)+*]* to match non-special chars, avoiding backtracking.
    nested_re = re.compile(r"\((\?:)?([^)+*]*[+*])[^)]*\)([+*])")
    for m in nested_re.finditer(pattern):
        full_group_content = pattern[m.start() + 1 : m.end() - 2]
        if _group_has_literal_anchor(full_group_content):
            continue
        return (
            f"nested quantifier '{m.group(0)}' — "
            "inner repetition inside outer repetition causes exponential backtracking"
        )
    return None


def _check_adjacent_overlapping_quantifiers(pattern: str) -> str | None:
    """Check for adjacent unbounded repetitions with overlapping classes.

    Only flags when two ``.*`` or ``.+`` quantifiers are directly adjacent
    with NO required literal separator between them.  Character-class
    quantifiers (``\\s*``, ``\\w+``, ``[a-z]*``) are only flagged when
    adjacent to an identical or ``.``-based quantifier.

    Safe patterns:
      \\s*:\\s*  — ':' is required between them
      \\s+.*    — \\s+ consumes only whitespace, .* consumes rest (no overlap)

    Dangerous patterns:
      .*.*      — both consume any char
      (.*)(.*)  — same via group boundaries
      \\s*\\s*  — identical class, both consume whitespace
    """
    # Only flag .* or .+ adjacent to .* or .+ (true overlap on any char)
    # Also flag identical character-class quantifiers directly adjacent
    dot_star_re = re.compile(r"(\.\*|\.\+)")
    dot_matches = list(dot_star_re.finditer(pattern))
    if len(dot_matches) >= 2:
        for i in range(len(dot_matches) - 1):
            # Check if two .* or .+ are directly adjacent (no separator)
            end1 = dot_matches[i].end()
            start2 = dot_matches[i + 1].start()
            between = pattern[end1:start2]
            # Strip group boundaries and non-quantifier chars
            between_clean = between.replace(")", "").replace("(?:", "").replace("(", "")
            if between_clean == "":
                g1 = dot_matches[i].group(0)
                g2 = dot_matches[i + 1].group(0)
                return (
                    f"adjacent overlapping quantifiers '{g1}...{g2}' — "
                    "both consume any character, causes quadratic backtracking"
                )
    return None


def _check_backreference_after_dotstar(pattern: str) -> str | None:
    r"""Check for backreference (\1, \2...) after .* or .*?.

    Pattern like: (.*?)\n\s*\1 — causes O(n^2) when backreference fails.
    """
    backref_re = re.compile(r"\.\*[?]?.*\\[1-9]")
    if backref_re.search(pattern):
        return "backreference after .* or .*? — causes quadratic backtracking when backreference fails to match"
    return None


def _check_star_in_repetition(pattern: str) -> str | None:
    """Check for quantifier applied to a group containing .* or .+.

    Pattern like: (.*|foo)+ or (?:.*,)+
    """
    group_re = re.compile(r"\(([^)]+)\)[+*]")
    for m in group_re.finditer(pattern):
        group_body = m.group(1)
        if ".*" in group_body or ".+" in group_body:
            return "unbounded repetition inside repeated group — causes exponential backtracking"
    return None


def _pair_overlaps(alt_a: str, alt_b: str) -> bool:
    """Check if two alternation branches could overlap."""
    if not alt_a or not alt_b:
        return False
    if alt_a[0] == alt_b[0]:
        return True
    return bool(re.match(r"\\[wdWD]", alt_a) and re.match(r"\\[wdWD]", alt_b))


def _branches_overlap(alternatives: list[str]) -> bool:
    """Check if any two alternation branches share prefix or character class."""
    from itertools import combinations
    return any(_pair_overlaps(a, b) for a, b in combinations(alternatives, 2))


def _check_overlapping_alternation(pattern: str) -> str | None:
    r"""Check for alternation where branches overlap and are quantified.

    Pattern like: (a|ab)+ or (\w+|\d+)+
    """
    alt_quant_re = re.compile(r"\(([^)|]+(?:\|[^)|]+)+)\)[+*]")
    m = alt_quant_re.search(pattern)
    if not m:
        return None
    alternatives = m[1].split("|")
    if len(alternatives) < 2:
        return None
    if _branches_overlap(alternatives):
        return f"overlapping alternation '{m[0]}' with quantifier — branches overlap"
    return None


def _check_repeated_nullable_group(pattern: str) -> str | None:
    """Check for a group that can match empty string, repeated unbounded.

    Dangerous patterns (group content can match empty):
      (a?)+  — a? can match empty, outer repeats
      (a*)+  — a* can match empty, outer repeats
      (|b)+  — empty alternation branch
      ()+    — empty group

    Safe patterns (group content requires at least one char):
      (?:_\\d+)*   — '_' is required, \\d+ requires 1+ digits
      (?:static\\s+|extern\\s+)* — each branch starts with a required literal
      (x+)*        — x+ requires at least 1 char
      (a|b)*       — each branch requires 1 char
    """
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
        return f"repeated nullable group '{m[0]}' — empty alternation branch allows zero-length match"
    return _check_nullable_single_atom(m, content) if "|" not in content else None


def _check_nullable_single_atom(m: re.Match, content: str) -> str | None:
    if len(content) < 2 or content[-1] not in ("?", "*"):
        return None
    atom = content[:-1]
    if len(atom) == 1 or \
            (atom.startswith("\\") and len(atom) == 2) or \
            (atom.startswith("[") and atom.endswith("]")):
        return f"repeated nullable group '{m[0]}' — group can match empty string, outer quantifier causes quadratic backtracking"
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


# ---------------------------------------------------------------------------
# Shell regex extraction
# ---------------------------------------------------------------------------

def _extract_shell_regexes(
    content: str,
) -> list[tuple[str, int, Engine, str]]:
    """Extract regex patterns from shell scripts.

    Returns list of (pattern, line, engine, command) tuples.
    """
    results: list[tuple[str, int, Engine, str]] = []
    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        _try_extract_shell_line(line, i, results)

    return results


def _try_extract_shell_line(
    line: str, line_num: int,
    results: list[tuple[str, int, Engine, str]],
) -> None:
    _try_append_shell_pattern(
        line, line_num, _SH_GREP_E_RE, Engine.SHELL_ERE, "grep -E", results,
    )
    _try_append_shell_pattern(
        line, line_num, _SH_GREP_P_RE, Engine.SHELL_PCRE, "grep -P", results,
    )
    _try_append_shell_pattern(
        line, line_num, _SH_SED_E_RE, Engine.SHELL_ERE, "sed -E", results,
    )
    if _SH_RG_RE.search(line):
        engine = Engine.SHELL_PCRE if "-P" in line else Engine.SHELL_ERE
        if pattern := _extract_shell_pattern_arg(line):
            results.append((pattern, line_num, engine, "rg"))
    if _SH_PERL_RE.search(line):
        if pattern := _extract_shell_pattern_arg(line):
            results.append((pattern, line_num, Engine.SHELL_PCRE, "perl"))


def _try_append_shell_pattern(
    line: str, line_num: int,
    cmd_re: re.Pattern, engine: Engine, cmd_name: str,
    results: list[tuple[str, int, Engine, str]],
) -> None:
    if not cmd_re.search(line):
        return
    pattern = _extract_shell_pattern_arg(line)
    if pattern is not None:
        results.append((pattern, line_num, engine, cmd_name))


def _extract_shell_pattern_arg(line: str) -> str | None:
    """Extract the first quoted pattern argument from a shell command line.

    Uses simple string scanning — no regex on the pattern itself.
    """
    for i in range(len(line)):
        ch = line[i]
        if ch == "'":
            result = _extract_single_quoted(line, i)
            if result is not None:
                return result
        elif ch == '"':
            result = _extract_double_quoted(line, i)
            if result is not None:
                return result
    return None


def _extract_single_quoted(line: str, start: int) -> str | None:
    end = line.find("'", start + 1)
    return line[start + 1:end] if end > 0 else None


def _extract_double_quoted(line: str, start: int) -> str | None:
    end = line.find('"', start + 1)
    return line[start + 1:end] if end > 0 else None


def _analyze_shell_pattern(
    pattern: str, engine: Engine,
) -> tuple[str | None, str, Severity]:
    """Analyze a shell regex pattern for ReDoS risk.

    POSIX ERE (grep -E) has no backtracking — only flag PCRE patterns.
    """
    if engine == Engine.SHELL_ERE:
        # POSIX ERE is DFA-based — no backtracking risk
        # But check for injection if pattern comes from a variable
        return None, "", Severity.INFO

    # PCRE (grep -P, rg -P, perl) — backtracking engine
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
    """Check for a valid suppression comment on the same or preceding line.

    Returns (suppressed, justification).
    """
    for offset in (0, -1):
        idx = line_num - 1 + offset
        if 0 <= idx < len(lines):
            line = lines[idx]
            if m := _SUPPRESSION_RE.search(line):
                justification = m.group(1).strip()
                return (True, justification) if justification else (True, None)
            # Check for bare suppression without --
            if _BARE_SUPPRESSION_RE.search(line):
                return True, None
    return False, None


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
    shell_regexes: list[tuple[str, int, Engine, str]],
    source_lines: list[str], rel_path: str,
) -> tuple[list[RegexFinding], list[ScanError]]:
    findings: list[RegexFinding] = []
    errors: list[ScanError] = []
    for pattern, line_num, engine, command in shell_regexes:
        suppressed, justification = _check_suppression(source_lines, line_num)
        if suppressed:
            if justification is None:
                errors.append(ScanError(
                    file_path=rel_path, line=line_num,
                    message="Invalid suppression without justification",
                ))
            continue
        reason, remediation, severity = _analyze_shell_pattern(pattern, engine)
        if reason is not None:
            findings.append(RegexFinding(
                severity=severity, engine=engine,
                file_path=rel_path, line=line_num,
                function="<shell>", api=command,
                pattern=pattern,
                pattern_source=PatternSource.STATIC_LITERAL,
                input_scope="shell argument",
                reason=reason, remediation=remediation,
            ))
        elif engine == Engine.SHELL_PCRE and "$" in pattern:
            findings.append(RegexFinding(
                severity=Severity.REVIEW, engine=engine,
                file_path=rel_path, line=line_num,
                function="<shell>", api=command,
                pattern=pattern,
                pattern_source=PatternSource.DYNAMIC,
                input_scope="shell variable",
                reason=(
                    "Shell pattern contains variable expansion ($) — "
                    "if variable is untrusted, this is regex injection"
                ),
                remediation=(
                    "Validate or sanitize variable content before using "
                    "as a regex pattern."
                ),
            ))
    return findings, errors


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
    """Check if a path should be excluded from scanning."""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        # Path is outside repo root — don't exclude (for --path CLI usage)
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
    """Collect all .py and .sh files from scan directories."""
    files: list[Path] = []
    for scan_dir in scan_dirs:
        files.extend(_collect_dir_files(scan_dir, repo_root))
    return _deduplicate_files(files)


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def _resolve_scan_dirs(args: argparse.Namespace) -> tuple[list[Path], int | None]:
    if args.path:
        scan_path = Path(args.path).resolve()
        if not scan_path.exists():
            print(f"ERROR: path does not exist: {scan_path}", file=sys.stderr)
            return [], 1
        return [scan_path], None
    if args.directory:
        scan_path = Path(args.directory).resolve()
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
            "summary": {
                "total_findings": len(all_findings),
                "errors": sum(
                    f.severity == Severity.ERROR for f in all_findings
                ),
                "reviews": sum(
                    f.severity == Severity.REVIEW for f in all_findings
                ),
                "infos": sum(
                    f.severity == Severity.INFO for f in all_findings
                ),
                "parse_errors": len(all_errors),
            },
        }
        print(json.dumps(output, indent=2))
    else:
        for e in all_errors:
            print(e.to_text(), file=sys.stderr)
        for f in all_findings:
            print(f.to_text(), file=sys.stderr)


def _compute_exit_code(
    all_findings: list[RegexFinding],
    all_errors: list[ScanError],
    args: argparse.Namespace,
) -> int:
    error_count = sum(f.severity == Severity.ERROR for f in all_findings)
    review_count = sum(f.severity == Severity.REVIEW for f in all_findings)
    info_count = sum(f.severity == Severity.INFO for f in all_findings)
    parse_error_count = len(all_errors)

    summary = (
        f"Summary: {error_count} ERROR, {review_count} REVIEW, "
        f"{info_count} INFO, {parse_error_count} parse error(s)"
    )
    print(summary, file=sys.stderr)

    if args.strict:
        if error_count > 0 or parse_error_count > 0:
            print(
                f"FAIL: {error_count} blocking finding(s), "
                f"{parse_error_count} parse error(s)",
                file=sys.stderr,
            )
            return 1
        if review_count > 0:
            print(
                f"WARN: {review_count} REVIEW finding(s) require attention "
                "(non-blocking in strict mode)",
                file=sys.stderr,
            )
    elif error_count > 0:
        print(
            f"WARN: {error_count} blocking finding(s) (advisory)",
            file=sys.stderr,
        )

    if error_count == 0 and parse_error_count == 0:
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
        help="Exit 1 on findings (default: advisory exit 0)",
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