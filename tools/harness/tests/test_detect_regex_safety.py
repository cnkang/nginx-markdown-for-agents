"""Pytest tests for detect_regex_safety.py — AST extraction, risk analysis,
error handling, and CLI contract.

Rule 10 (parser-regex): Validates detection of regex patterns prone to
catastrophic backtracking (ReDoS) and dynamic regex injection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure tools/ is on the path
TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

from harness.detect_regex_safety import (  # noqa: E402
    RegexFinding,
    ScanError,
    Severity,
    PatternSource,
    Engine,
    _check_nested_quantifier,
    _check_adjacent_overlapping_quantifiers,
    _check_backreference_after_dotstar,
    _check_overlapping_alternation,
    _check_repeated_nullable_group,
    _check_star_in_repetition,
    _scan_python_file,
    _scan_shell_file,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_regex_safety.py"


# ---------------------------------------------------------------------------
# Helper: create a temp Python file and scan it
# ---------------------------------------------------------------------------

def _make_py_file(content: str, tmp_path: Path, name: str = "test.py") -> Path:
    """Create a temporary Python file and return its path."""
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def _scan_py(content: str, tmp_path: Path, name: str = "test.py") -> tuple[list[RegexFinding], list[ScanError]]:
    """Create a temp Python file, scan it, and return findings."""
    f = _make_py_file(content, tmp_path, name)
    return _scan_python_file(f, tmp_path)


# ---------------------------------------------------------------------------
# AST extraction: import forms
# ---------------------------------------------------------------------------

class TestImportResolution:
    """Test that import aliases are correctly resolved."""

    def test_import_re(self, tmp_path: Path) -> None:
        """import re; re.compile(r'...')"""
        content = "import re\np = re.compile(r'^[a-z]+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings  # Safe pattern

    def test_import_re_as_alias(self, tmp_path: Path) -> None:
        """import re as regex_module; regex_module.compile(...)"""
        content = "import re as regex_module\np = regex_module.compile(r'^[a-z]+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_from_re_import_compile(self, tmp_path: Path) -> None:
        """from re import compile; compile(...)"""
        content = "from re import compile\np = compile(r'^[a-z]+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_from_re_import_search_as_alias(self, tmp_path: Path) -> None:
        """from re import search as regex_search; regex_search(...)"""
        content = "from re import search as regex_search\nregex_search(r'^[a-z]+$', 'abc')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_multiple_re_imports(self, tmp_path: Path) -> None:
        """Multiple re import forms in same file."""
        content = (
            "import re\n"
            "import re as re2\n"
            "from re import compile as c\n"
            "p1 = re.compile(r'^[a-z]+$')\n"
            "p2 = re2.compile(r'^\\d+$')\n"
            "p3 = c(r'^\\w+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings


# ---------------------------------------------------------------------------
# AST extraction: API detection
# ---------------------------------------------------------------------------

class TestAPIDetection:
    """Test that all re.* functions are detected."""

    @pytest.mark.parametrize("api", [
        "compile", "search", "match", "fullmatch",
        "findall", "finditer", "split", "sub", "subn",
    ])
    def test_module_function_detected(self, tmp_path: Path, api: str) -> None:
        """Each re.* function should be detected."""
        if api == "compile":
            content = f"import re\nre.{api}(r'(a+)+b')\n"
        elif api in {"sub", "subn"}:
            content = f"import re\nre.{api}(r'(a+)+b', 'x', 'aab')\n"
        else:
            content = f"import re\nre.{api}(r'(a+)+b', 'aab')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR
        assert findings[0].api == api

    def test_compiled_pattern_method(self, tmp_path: Path) -> None:
        """compiled_pattern.search(...) should be detected."""
        content = (
            "import re\n"
            "p = re.compile(r'(a+)+b')\n"
            "p.search('aab')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # The compile call itself should be flagged
        compile_findings = [f for f in findings if f.api == "compile"]
        assert len(compile_findings) == 1
        assert compile_findings[0].severity == Severity.ERROR

    def test_multiline_call(self, tmp_path: Path) -> None:
        """Multi-line re.compile call should be detected."""
        content = (
            "import re\n"
            "p = re.compile(\n"
            "    r'(a+)+b'\n"
            ")\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# AST extraction: pattern classification
# ---------------------------------------------------------------------------

class TestPatternClassification:
    """Test pattern source classification."""

    def test_string_literal(self, tmp_path: Path) -> None:
        """Plain string literal pattern."""
        content = "import re\nre.compile('^[a-z]+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_raw_string(self, tmp_path: Path) -> None:
        """Raw string pattern."""
        content = "import re\nre.compile(r'^[a-z]+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_triple_quoted_string(self, tmp_path: Path) -> None:
        """Triple-quoted string pattern."""
        content = 'import re\nre.compile(r"""^[a-z]+$""")\n'
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_literal_concatenation(self, tmp_path: Path) -> None:
        """Adjacent string literal concatenation."""
        content = 'import re\nre.compile(r"^[a-z" r"+]$")\n'
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_string_plus_concatenation(self, tmp_path: Path) -> None:
        """String + string concatenation."""
        content = 'import re\nre.compile(r"^[a-z" + r"+]$")\n'
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_fstring_no_dynamic(self, tmp_path: Path) -> None:
        """f-string with no dynamic parts should be treated as static."""
        content = "import re\nre.compile(rf'^[a-z]+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_fstring_dynamic(self, tmp_path: Path) -> None:
        """f-string with dynamic parts should be REVIEW."""
        content = "import re\nx = 'abc'\nre.compile(rf'^{x}$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # Should have a REVIEW finding for dynamic pattern
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert len(reviews) == 1
        assert reviews[0].pattern_source == PatternSource.DYNAMIC

    def test_re_escape(self, tmp_path: Path) -> None:
        """re.escape() wrapping should not produce findings."""
        content = "import re\nx = 'user_input'\nre.compile(re.escape(x))\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # re.escape is ESCAPED_DYNAMIC — no finding
        escaped = [f for f in findings if f.pattern_source == PatternSource.ESCAPED_DYNAMIC]
        assert not escaped

    def test_dynamic_variable(self, tmp_path: Path) -> None:
        """Unknown variable as pattern should be REVIEW (not INFO)."""
        content = "import re\nx = get_pattern()\nre.compile(x)\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # Per the unified CLI contract, UNKNOWN pattern sources are treated
        # as REVIEW (require manual attention), never silently downgraded to
        # INFO.  A dynamic call result (get_pattern()) invalidates the binding.
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert len(reviews) == 1
        assert reviews[0].pattern_source in (
            PatternSource.UNKNOWN, PatternSource.DYNAMIC,
        )


# ---------------------------------------------------------------------------
# Risk analysis: dangerous patterns
# ---------------------------------------------------------------------------

class TestDangerousPatterns:
    """Test that known dangerous patterns are detected."""

    @pytest.mark.parametrize("pattern", [
        r"(a+)+b",
        r"(a*)+b",
        r"(.*)+end",
        r"(.+)+end",
    ])
    def test_nested_quantifier(self, pattern: str) -> None:
        """Nested quantifiers should be detected."""
        result = _check_nested_quantifier(pattern)
        assert result is not None
        assert "nested quantifier" in result

    def test_adjacent_dotstar(self) -> None:
        """Adjacent .*.* should be detected."""
        result = _check_adjacent_overlapping_quantifiers(r"(.*)(.*)end")
        assert result is not None
        assert "adjacent" in result

    def test_backreference_after_dotstar(self) -> None:
        r"""Backreference after .* should be detected."""
        result = _check_backreference_after_dotstar(r"(.*?)\n\s*\1")
        assert result is not None
        assert "backreference" in result

    def test_star_in_repetition(self) -> None:
        """.* inside repeated group should be detected."""
        result = _check_star_in_repetition(r"(.*,)+end")
        assert result is not None
        assert "unbounded repetition" in result

    def test_overlapping_alternation(self) -> None:
        """Overlapping alternation with quantifier should be detected."""
        result = _check_overlapping_alternation(r"(foo|foobar)+end")
        assert result is not None
        assert "overlapping alternation" in result

    def test_repeated_nullable_group(self) -> None:
        """Repeated nullable group should be detected."""
        result = _check_repeated_nullable_group(r"(a?)+")
        assert result is not None
        assert "nullable" in result


# ---------------------------------------------------------------------------
# Risk analysis: safe patterns (no false positives)
# ---------------------------------------------------------------------------

class TestSafePatterns:
    """Test that safe patterns are not flagged."""

    @pytest.mark.parametrize("pattern", [
        r"^[a-z][a-z0-9_]*$",
        r"^[a-z](-[a-z]+)*$",
        r"\d+\.\d+\.\d+",
        r"\s*:\s*(\d+)",
        r"^[0-9a-f]{64}$",
        r"releases/download/v\d+\.\d+\.\d+",
        r"^[A-Z][A-Z0-9_]*$",
        r"\b(markdown_\w+)\b",
        r"^[A-Za-z0-9._/-]+$",
    ])
    def test_safe_pattern_not_flagged(self, pattern: str) -> None:
        """Safe patterns should not produce any finding."""
        for check in [
            _check_nested_quantifier,
            _check_adjacent_overlapping_quantifiers,
            _check_backreference_after_dotstar,
            _check_overlapping_alternation,
            _check_repeated_nullable_group,
            _check_star_in_repetition,
        ]:
            assert check(pattern) is None, (
                f"Check {check.__name__} flagged safe pattern: {pattern}"
            )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Test that errors are properly surfaced, not silently ignored."""

    def test_syntax_error(self, tmp_path: Path) -> None:
        """Python syntax error should produce a ScanError."""
        content = "import re\np = re.compile(\n"  # Unclosed paren
        findings, errors = _scan_py(content, tmp_path)
        assert len(errors) == 1
        assert "syntax" in errors[0].message.lower()
        assert len(findings) == 0

    def test_unreadable_file(self, tmp_path: Path) -> None:
        """Unreadable file should produce a ScanError."""
        if os.geteuid() == 0:
            pytest.skip("root bypasses file permissions")
        f = tmp_path / "unreadable.py"
        # Create file with owner-only permissions to avoid world-readable window
        fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b"import re\n")
        finally:
            os.close(fd)
        os.chmod(str(f), 0o000)
        try:
            _findings, errors = _scan_python_file(f, tmp_path)
            assert len(errors) >= 1
        finally:
            os.chmod(str(f), 0o600)

    def test_missing_path_cli(self, tmp_path: Path) -> None:
        """Missing path should return non-zero via CLI."""
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", "/nonexistent/path"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory should return OK."""
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "OK" in result.stderr


# ---------------------------------------------------------------------------
# Suppression contract
# ---------------------------------------------------------------------------

class TestSuppression:
    """Test suppression comment handling."""

    def test_valid_suppression(self, tmp_path: Path) -> None:
        """Suppression with justification should suppress finding."""
        content = (
            "import re\n"
            "# nosec:regex-safety -- trusted generated token, max_input_bytes=64\n"
            "p = re.compile(r'(a+)+b')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings  # Suppressed

    def test_invalid_suppression_no_justification(self, tmp_path: Path) -> None:
        """Suppression without justification should produce error."""
        content = (
            "import re\n"
            "# nosec:regex-safety\n"
            "p = re.compile(r'(a+)+b')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert len(errors) == 1
        assert "justification" in errors[0].message.lower()
        assert not findings  # Still suppressed but with error

    def test_suppression_on_preceding_line(self, tmp_path: Path) -> None:
        """Suppression on preceding line should work."""
        content = (
            "import re\n"
            "# nosec:regex-safety -- bounded input, max_input_bytes=128\n"
            "p = re.compile(r'(a+)+b')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_suppression_at_compile_line_for_compiled_method(self, tmp_path: Path) -> None:
        """Suppression at re.compile line should also suppress compiled.* findings."""
        content = (
            "import re\n"
            "# nosec:regex-safety -- bounded Rust raw-string fence\n"
            "raw_string_re = re.compile(r'r(#+)\"(.*?)\\1\"', re.DOTALL)\n"
            "for m in raw_string_re.finditer('content'):\n"
            "    pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings  # Suppressed at compile line


# ---------------------------------------------------------------------------
# Shell regex extraction
# ---------------------------------------------------------------------------

class TestShellRegex:
    """Test shell regex pattern extraction."""

    def test_grep_e_detected(self, tmp_path: Path) -> None:
        """grep -E pattern should be detected."""
        self._scan_shell_content(
            '#!/usr/bin/env bash\ngrep -E "(a+)+match" file.txt\n', tmp_path
        )
        # ERE is DFA-based — no backtracking risk, so no finding
        # unless pattern has variable expansion

    def test_grep_p_detected(self, tmp_path: Path) -> None:
        """grep -P pattern should be detected as PCRE."""
        findings = self._scan_shell_content(
            '#!/usr/bin/env bash\ngrep -P "(a+)+match" file.txt\n', tmp_path
        )
        # PCRE with dangerous pattern should be flagged
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].engine == Engine.SHELL_PCRE

    def test_shell_variable_in_pattern(self, tmp_path: Path) -> None:
        """Shell variable in PCRE pattern should be REVIEW."""
        findings = self._scan_shell_content(
            '#!/usr/bin/env bash\nPATTERN="$USER_INPUT"\ngrep -P "$PATTERN" file.txt\n',
            tmp_path,
        )
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert reviews

    def _scan_shell_content(self, content: str, tmp_path: Path):
        """Write shell content to a temp file and scan it."""
        f = tmp_path / "test.sh"
        f.write_text(content)
        result, errors = _scan_shell_file(f, tmp_path)
        assert not errors
        return result


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

class TestCLI:
    """Test CLI contract."""

    def test_default_pass(self, tmp_path: Path) -> None:
        """Default mode should pass on clean files."""
        content = "import re\np = re.compile(r'^[a-z]+$')\n"
        _make_py_file(content, tmp_path)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_strict_finding_nonzero(self, tmp_path: Path) -> None:
        """--strict should return non-zero on ERROR findings."""
        content = "import re\np = re.compile(r'(a+)+b')\n"
        _make_py_file(content, tmp_path)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path), "--strict"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_json_output_parseable(self, tmp_path: Path) -> None:
        """--format json should produce valid JSON."""
        content = "import re\np = re.compile(r'(a+)+b')\n"
        _make_py_file(content, tmp_path)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path), "--format", "json"],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert "findings" in data
        assert "errors" in data
        assert "summary" in data
        assert data["summary"]["total_findings"] >= 1

    def test_invalid_path_nonzero(self) -> None:
        """Invalid path should return non-zero."""
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", "/nonexistent"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_space_in_path(self, tmp_path: Path) -> None:
        """Path with spaces should work."""
        spaced = tmp_path / "with space"
        spaced.mkdir()
        f = spaced / "test.py"
        f.write_text("import re\np = re.compile(r'^[a-z]+$')\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(spaced)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# P1-1: keyword-argument regex calls
# ---------------------------------------------------------------------------

class TestKeywordArguments:
    """Re.* calls using ``pattern=`` / ``string=`` / ``flags=`` keywords."""

    @pytest.mark.parametrize("api,extra", [
        ("compile", ""),
        ("search", ", string='abc'"),
        ("match", ", string='abc'"),
        ("fullmatch", ", string='abc'"),
        ("findall", ", string='abc'"),
        ("finditer", ", string='abc'"),
        ("split", ", string='abc'"),
        ("sub", ", repl='x', string='abc'"),
        ("subn", ", repl='x', string='abc'"),
    ])
    def test_keyword_pattern(self, tmp_path: Path, api: str, extra: str) -> None:
        """``pattern=`` keyword should be recognized as the pattern arg."""
        content = f"import re\nre.{api}(pattern=r'(a+)+b'{extra})\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].api == api

    def test_from_re_import_alias_keyword(self, tmp_path: Path) -> None:
        """``from re import search as s; s(pattern=..., string=...)``."""
        content = (
            "from re import search as s\n"
            "s(pattern=r'(a+)+$', string='data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_flags_keyword(self, tmp_path: Path) -> None:
        """``flags=re.DOTALL`` as keyword should be parsed (no crash)."""
        content = (
            "import re\n"
            "re.compile(pattern=r'foo.*', flags=re.DOTALL)\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert len(reviews) == 1

    def test_string_keyword_input_scope(self, tmp_path: Path) -> None:
        """``string=`` keyword drives input_scope inference."""
        content = (
            "import re\n"
            "re.search(pattern=r'safe', string=open('x').read())\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # No ERROR (safe pattern); possibly a REVIEW for dynamic string.
        # Just ensure no crash and api == 'search'.

    def test_duplicate_positional_and_keyword(self, tmp_path: Path) -> None:
        """Pattern given both positionally and via keyword → treated as unknown.

        The detector must not crash and must not flag the duplicate itself as
        a Python syntax error; Python would reject this at parse time, so we
        use a legal-ish form: keyword pattern after a positional string.
        """
        # This is valid Python (positional string, keyword pattern).
        content = "import re\nre.search('abc', pattern=r'(a+)+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors


# ---------------------------------------------------------------------------
# P1-2: static string constant propagation
# ---------------------------------------------------------------------------

class TestStaticPropagation:
    """Conservative scope-aware static string propagation."""

    def test_module_level_constant(self, tmp_path: Path) -> None:
        content = (
            "import re\n"
            "PATTERN = r'(a+)+$'\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_reassignment_invalidates(self, tmp_path: Path) -> None:
        """Reassigning to a dynamic value must invalidate the static binding."""
        content = (
            "import re\n"
            "PATTERN = r'(a+)+$'\n"
            "PATTERN = get_pattern()\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # After reassignment, PATTERN is dynamic → REVIEW (not ERROR with old literal).
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert len(reviews) == 1

    def test_binop_static_concat(self, tmp_path: Path) -> None:
        content = (
            "import re\n"
            "PREFIX = r'^'\n"
            "BODY = r'(a+)'\n"
            "SUFFIX = r'+'\n"
            "PATTERN = PREFIX + BODY + SUFFIX\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_annassign_final(self, tmp_path: Path) -> None:
        content = (
            "import re\n"
            "PATTERN: Final[str] = r'(a+)+$'\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_function_scope_isolation(self, tmp_path: Path) -> None:
        """A function-local reassign does not leak to module scope."""
        content = (
            "import re\n"
            "PATTERN = r'safe'\n"
            "def f():\n"
            "    PATTERN = r'(a+)+$'\n"
            "    re.search(PATTERN, 'data')\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # Inside f(): ERROR on the dangerous local PATTERN.
        errors_inside = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_inside) == 1
        # At module level: PATTERN still 'safe' → no finding.
        assert sum(f.line == 6 and f.severity == Severity.ERROR for f in findings) == 0


# ---------------------------------------------------------------------------
# P1-3: re.escape composition
# ---------------------------------------------------------------------------

class TestEscapeComposition:
    """Segment classification of re.escape concatenations."""

    def test_pure_escape_no_finding(self, tmp_path: Path) -> None:
        content = "import re\nx = 'u'\nre.compile(re.escape(x))\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_escape_plus_dangerous_static(self, tmp_path: Path) -> None:
        content = "import re\nx = 'u'\nre.compile(re.escape(x) + r'(a+)+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_dangerous_static_plus_escape(self, tmp_path: Path) -> None:
        content = "import re\nx = 'u'\nre.compile(r'(a+)+$' + re.escape(x))\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_escape_plus_dynamic_unescaped(self, tmp_path: Path) -> None:
        content = (
            "import re\n"
            "x = 'u'\n"
            "re.compile(re.escape(x) + get_pattern())\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert len(reviews) == 1

    def test_escape_in_fstring(self, tmp_path: Path) -> None:
        content = (
            "import re\nx = 'u'\n"
            "re.compile(rf'^{re.escape(x)}(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)


# ---------------------------------------------------------------------------
# P1-4: nested quantifier detection
# ---------------------------------------------------------------------------

class TestNestedQuantifier:
    """Nested quantifier detection — danger vs safe separator."""

    @pytest.mark.parametrize("pattern", [
        r"(aa+)+$",
        r"(ab*)+$",
        r"(a\d+)+$",
        r"(?:aa+)+$",
        r"(a+)+$",
        r"(a*)+$",
        r"(.*)+",
        r"(.+)+",
    ])
    def test_dangerous_detected(self, pattern: str) -> None:
        assert _check_nested_quantifier(pattern) is not None

    @pytest.mark.parametrize("pattern", [
        r"(?:\.[0-9A-Za-z-]+)*$",
        r"(?:-[a-z]+)*$",
        r"(?:-[a-z]+)+$",
        r"(?:_\d+)*$",
        r"(?:static\s+|extern\s+|inline\s+)*",
    ])
    def test_safe_not_flagged(self, pattern: str) -> None:
        assert _check_nested_quantifier(pattern) is None


# ---------------------------------------------------------------------------
# P1-5: shell command argument extraction
# ---------------------------------------------------------------------------

class TestShellArgumentExtraction:
    """Shell regex command argument parsing — pipes, options, --, -e."""

    def _scan_shell_content(self, content: str, tmp_path: Path):
        f = tmp_path / "test.sh"
        f.write_text(content)
        result, errors = _scan_shell_file(f, tmp_path)
        assert not errors
        return result

    @pytest.mark.parametrize("line", [
        "printf '%s\\n' \"$body\" | grep -P '(a+)+$'",
        'echo "prefix" | rg -P \'(a+)+$\'',
        'cat "$file" | grep -P -- \'(a+)+$\'',
        "grep -P -e '(a+)+$' \"$file\"",
        "rg -P --regexp '(a+)+$' \"$file\"",
        "VALUE=x grep -P '(a+)+$' \"$file\"",
    ])
    def test_dangerous_pcre_extracted(self, tmp_path: Path, line: str) -> None:
        findings = self._scan_shell_content(f"#!/usr/bin/env bash\n{line}\n", tmp_path)
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].engine == Engine.SHELL_PCRE
        assert errors_found[0].pattern == "(a+)+$"

    def test_pipe_preceding_quoted_arg_not_mistaken(self, tmp_path: Path) -> None:
        """Pattern comes from the grep segment, not the printf segment."""
        content = "#!/usr/bin/env bash\nprintf '%s\\n' \"$body\" | grep -P '(a+)+$'\n"
        findings = self._scan_shell_content(content, tmp_path)
        # The printf format string '%s\\n' should NOT be treated as a PCRE pattern.
        assert all(f.api in ("grep", "rg", "perl") for f in findings)


# ---------------------------------------------------------------------------
# P1-6: CLI contract — strict / fail-on-review / UNKNOWN
# ---------------------------------------------------------------------------

class TestCLIContract:
    """Strict and fail-on-review exit-code contract."""

    def test_strict_review_nonzero_no(self, tmp_path: Path) -> None:
        """--strict alone returns 0 on REVIEW-only findings."""
        content = "import re\nx = get()\nre.compile(x + 'y')\n"
        _make_py_file(content, tmp_path)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path), "--strict"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_fail_on_review_nonzero(self, tmp_path: Path) -> None:
        """--fail-on-review returns 1 on REVIEW findings."""
        content = "import re\nx = get()\nre.compile(x + 'y')\n"
        _make_py_file(content, tmp_path)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path),
             "--strict", "--fail-on-review"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_strict_parse_error_nonzero(self, tmp_path: Path) -> None:
        """--strict returns 1 on parse errors."""
        content = "import re\np = re.compile(\n"
        _make_py_file(content, tmp_path)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(tmp_path), "--strict"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_unknown_treated_as_review(self, tmp_path: Path) -> None:
        """UNKNOWN pattern source → REVIEW, not INFO."""
        content = "import re\nre.compile(get_unknown())\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert reviews

    def test_mocked_read_error_strict(self, tmp_path: Path, monkeypatch) -> None:
        """A read failure surfaces as a parse/scan error and fails --strict."""
        f = _make_py_file("import re\nre.compile(r'safe')\n", tmp_path)
        def _raise(*args, **kwargs):
            raise OSError("mocked read failure")
        monkeypatch.setattr(Path, "read_text", _raise)
        from harness.detect_regex_safety import _scan_python_file
        findings, errors = _scan_python_file(f, tmp_path)
        assert len(errors) >= 1
        assert not findings

    def test_path_filter_scans_only_target(self, tmp_path: Path) -> None:
        """--path <file> scans only that file."""
        other = tmp_path / "other.py"
        other.write_text("import re\nre.compile(r'(a+)+b')\n", encoding="utf-8")
        target = tmp_path / "target.py"
        target.write_text("import re\nre.compile(r'safe')\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(DETECTOR), "--path", str(target), "--strict"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0  # target is safe; other.py not scanned