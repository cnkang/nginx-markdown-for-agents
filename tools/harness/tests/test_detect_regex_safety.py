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
        content = "import re\nx = get_pattern()\nre.compile(rf'^{x}$')\n"
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
            from lib.path_validation import validate_read_path
            validated_f = validate_read_path(f, purpose="test file")
            _findings, errors = _scan_python_file(validated_f, tmp_path)
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


class TestQuantifierAtomBinding:
    """Quantifier-to-atom binding preservation after _merge_literal_atoms fix."""

    @pytest.mark.parametrize("pattern", [
        r"(?:-ab+)*$",
        r"(?:prefix\d+)*$",
        r"(?:foo[0-9]+)*$",
        r"(?:/item-[a-z]+)*$",
    ])
    def test_safe_separator_with_quantified_literal(self, pattern: str) -> None:
        assert _check_nested_quantifier(pattern) is None

    @pytest.mark.parametrize("pattern", [
        r"(aa+)+$",
        r"(a\d+)+$",
        r"(?:aa+)+$",
    ])
    def test_dangerous_nested_still_detected(self, pattern: str) -> None:
        assert _check_nested_quantifier(pattern) is not None


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


# ---------------------------------------------------------------------------
# Cross-segment static pattern analysis (Section VI)
# ---------------------------------------------------------------------------

class TestCrossSegmentAnalysis:
    """Test that static concatenation is analyzed as a complete pattern."""

    def test_cross_segment_nested_quantifier(self, tmp_path: Path) -> None:
        """r'(a+' + r')+$' should produce ERROR for the full pattern."""
        content = "import re\nre.compile(r'(a+' + r')+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_cross_segment_split_atom(self, tmp_path: Path) -> None:
        """r'(a' + r'+)+$' should produce ERROR for the full pattern."""
        content = "import re\nre.compile(r'(a' + r'+)+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_cross_segment_three_parts(self, tmp_path: Path) -> None:
        """r'(' + r'a+' + r')+$' should produce ERROR."""
        content = "import re\nre.compile(r'(' + r'a+' + r')+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_cross_segment_safe(self, tmp_path: Path) -> None:
        """r'^[a-z' + r']+$' should NOT produce an error."""
        content = "import re\nre.compile(r'^[a-z' + r']+$')\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings


# ---------------------------------------------------------------------------
# Binding invalidation (Section VII)
# ---------------------------------------------------------------------------

class TestBindingInvalidation:
    """Test that compiled pattern bindings are invalidated on reassignment."""

    def test_compiled_reassignment_to_dynamic(self, tmp_path: Path) -> None:
        """p = re.compile(...) then p = get() → p.search() is REVIEW."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "p = get_runtime_pattern()\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # p is no longer a known compiled pattern — should not use old pattern
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors  # Should NOT still flag with old safe pattern
        # A REVIEW must be emitted on the compiled-method call line.
        reviews = [
            f for f in findings
            if f.severity == Severity.REVIEW
            and f.api == "compiled.search"
            and f.line == 4
        ]
        assert len(reviews) == 1
        assert reviews[0].pattern_source in (PatternSource.DYNAMIC, PatternSource.UNKNOWN)
        assert reviews[0].compile_line == 2

    def test_static_reassignment_to_unknown(self, tmp_path: Path) -> None:
        """PATTERN = r'^safe$' then PATTERN = OTHER → re.search(PATTERN) is REVIEW."""
        content = (
            "import re\n"
            "OTHER = get_value()\n"
            "PATTERN = r'^safe$'\n"
            "PATTERN = OTHER\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # After reassignment to dynamic, PATTERN should be REVIEW
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert reviews


# ---------------------------------------------------------------------------
# Scope-aware bindings (adversarial — unified lexical model)
# ---------------------------------------------------------------------------

class TestScopeAwareBindings:
    """Test that the unified lexical binding model respects scope boundaries."""

    def test_function_shadow_does_not_delete_module_binding(self, tmp_path: Path) -> None:
        """Function-local p = object() does not invalidate module-level compiled p."""
        content = (
            "import re\n"
            "p = re.compile(r'(a+)+$')\n"
            "def configure():\n"
            "    p = object()\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert len(compiled_errors) == 1

    def test_function_compiled_binding_does_not_leak(self, tmp_path: Path) -> None:
        """Compiled binding inside a function must not leak to module scope."""
        content = (
            "import re\n"
            "def build():\n"
            "    p = re.compile(r'(a+)+$')\n"
            "    p.search('data')\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        inner_errors = [f for f in compiled_errors if f.line == 4]
        assert len(inner_errors) == 1
        outer_errors = [f for f in compiled_errors if f.line == 5]
        assert not outer_errors

    def test_parameter_shadows_re_alias(self, tmp_path: Path) -> None:
        """def f(re): re.compile(...) — parameter shadows module alias."""
        content = (
            "import re\n"
            "def f(re):\n"
            "    re.compile(r'(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found

    def test_parameter_shadows_compiled(self, tmp_path: Path) -> None:
        """def f(p): p.search(...) — parameter shadows compiled binding."""
        content = (
            "import re\n"
            "def f(p):\n"
            "    p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_findings = [
            f for f in findings if "compiled" in f.api
        ]
        assert not compiled_findings

    def test_for_target_shadows_compiled(self, tmp_path: Path) -> None:
        """for p in items: shadows compiled p; p.search() after must not use old pattern."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "for p in items:\n"
            "    pass\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors

    def test_with_target_shadows_compiled(self, tmp_path: Path) -> None:
        """with factory() as p: shadows compiled p; p.search() after must not use old pattern."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "with factory() as p:\n"
            "    pass\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors

    def test_except_target_shadows(self, tmp_path: Path) -> None:
        """except Error as p: shadows any outer compiled p."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "try:\n"
            "    pass\n"
            "except Error as p:\n"
            "    pass\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors

    def test_lambda_parameter_shadow(self, tmp_path: Path) -> None:
        """lambda re: re.compile(...) — parameter shadows module alias."""
        content = (
            "import re\n"
            "f = lambda re: re.compile(r'(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found


# ---------------------------------------------------------------------------
# Re-alias reassignment invalidation (adversarial)
# ---------------------------------------------------------------------------

class TestReAliasReassignment:
    """Test that reassigning re module/function aliases invalidates bindings."""

    def test_re_alias_reassignment_invalidates(self, tmp_path: Path) -> None:
        """import re; re = custom_regex; re.compile(...) — no finding."""
        content = (
            "import re\n"
            "re = custom_regex\n"
            "re.compile(r'(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found

    def test_from_re_function_alias_reassignment(self, tmp_path: Path) -> None:
        """from re import compile as rc; rc = custom; rc(...) — no finding."""
        content = (
            "from re import compile as regex_compile\n"
            "regex_compile = custom\n"
            "regex_compile(r'(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found

    def test_function_import_does_not_leak(self, tmp_path: Path) -> None:
        """import re inside a function does not make module-level re valid."""
        content = (
            "def f():\n"
            "    import re\n"
            "    re.compile(r'(a+)+$')\n"
            "re.compile(r'(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        inner_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and f.line == 3
        ]
        assert len(inner_errors) == 1
        outer_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and f.line == 4
        ]
        assert not outer_errors


# ---------------------------------------------------------------------------
# Unknown reassignment invalidation (adversarial)
# ---------------------------------------------------------------------------

class TestUnknownReassignmentInvalidation:
    """Test that reassigning a static binding to unknown invalidates it."""

    def test_unknown_reassignment_makes_safe_pattern_review(self, tmp_path: Path) -> None:
        """PATTERN = r'^safe$'; PATTERN = EXTERNAL → REVIEW."""
        self._assert_unknown_pattern_reassignment_is_reviewed(
            "import re\n"
            "PATTERN = r'^safe$'\n"
            "PATTERN = EXTERNAL\n"
            "re.search(PATTERN, 'data')\n",
            tmp_path,
        )

    def test_unknown_reassignment_makes_dangerous_pattern_review(self, tmp_path: Path) -> None:
        """PATTERN = r'(a+)+$'; PATTERN = EXTERNAL → REVIEW, not ERROR."""
        self._assert_unknown_pattern_reassignment_is_reviewed(
            "import re\n"
            "PATTERN = r'(a+)+$'\n"
            "PATTERN = EXTERNAL\n"
            "re.search(PATTERN, 'data')\n",
            tmp_path,
        )

    def test_augassign_invalidates(self, tmp_path: Path) -> None:
        """PATTERN = r'^safe$'; PATTERN += suffix → REVIEW."""
        self._assert_unknown_pattern_reassignment_is_reviewed(
            "import re\n"
            "PATTERN = r'^safe$'\n"
            "PATTERN += suffix\n"
            "re.search(PATTERN, 'data')\n",
            tmp_path,
        )

    def _assert_unknown_pattern_reassignment_is_reviewed(
        self, content: str, tmp_path: Path,
    ) -> None:
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert reviews

    def test_delete_removes_binding(self, tmp_path: Path) -> None:
        """PATTERN = r'(a+)+$'; del PATTERN → no ERROR with old literal."""
        content = (
            "import re\n"
            "PATTERN = r'(a+)+$'\n"
            "del PATTERN\n"
            "re.search(PATTERN, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found


# ---------------------------------------------------------------------------
# Dynamic compiled review (adversarial)
# ---------------------------------------------------------------------------

class TestDynamicCompiledReview:
    """Test that reassigning a compiled binding emits a REVIEW on use."""

    def test_compiled_reassignment_to_dynamic_produces_review(self, tmp_path: Path) -> None:
        """p = re.compile(safe); p = get_runtime_pattern(); p.search() → REVIEW."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "p = get_runtime_pattern()\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors
        reviews = [
            f for f in findings
            if f.severity == Severity.REVIEW
            and f.api == "compiled.search"
            and f.line == 4
        ]
        assert len(reviews) == 1
        assert reviews[0].pattern_source in (PatternSource.DYNAMIC, PatternSource.UNKNOWN)
        assert reviews[0].compile_line == 2

    def test_compiled_reassignment_to_attribute_produces_review(self, tmp_path: Path) -> None:
        """p = re.compile(safe); p = factory.pattern; p.match() → REVIEW."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "p = factory.pattern\n"
            "p.match('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors
        reviews = [
            f for f in findings
            if f.severity == Severity.REVIEW
            and f.api == "compiled.match"
            and f.line == 4
        ]
        assert len(reviews) == 1
        assert reviews[0].pattern_source in (PatternSource.DYNAMIC, PatternSource.UNKNOWN)
        assert reviews[0].compile_line == 2

    def test_compiled_annotation_reassignment_produces_review(self, tmp_path: Path) -> None:
        """p = re.compile(safe); p: Pattern = unknown_pattern; p.sub() → REVIEW."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "p: Pattern = unknown_pattern\n"
            "p.sub('', 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api
        ]
        assert not compiled_errors
        reviews = [
            f for f in findings
            if f.severity == Severity.REVIEW
            and f.api == "compiled.sub"
            and f.line == 4
        ]
        assert len(reviews) == 1
        assert reviews[0].pattern_source in (PatternSource.DYNAMIC, PatternSource.UNKNOWN)
        assert reviews[0].compile_line == 2


# ---------------------------------------------------------------------------
# Escaped dynamic composition (Section VIII)
# ---------------------------------------------------------------------------

class TestEscapedDynamicComposition:
    """Test structural analysis of re.escape compositions."""

    def test_pure_escape_safe(self, tmp_path: Path) -> None:
        """re.compile(re.escape(value)) → no finding."""
        content = "import re\nvalue = 'x'\nre.compile(re.escape(value))\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_escape_plus_nested_quantifier_error(self, tmp_path: Path) -> None:
        """A dangerous static scaffold around an escaped atom is rejected."""
        content = (
            "import re\n"
            "value = 'x'\n"
            "re.compile(r'(' + re.escape(value) + r'+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_escape_with_safe_suffix(self, tmp_path: Path) -> None:
        """An escaped value plus a bounded static suffix is safe."""
        content = (
            "import re\n"
            "value = 'x'\n"
            "re.compile(re.escape(value) + r'-suffix$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_safe_escaped_assignment_stays_safe_when_compiled(
        self, tmp_path: Path,
    ) -> None:
        """Safe escaped composition remains modeled through a name binding."""
        content = (
            "import re\n"
            "value = get_value()\n"
            "pattern = rf'^{re.escape(value)}$'\n"
            "compiled = re.compile(pattern)\n"
            "compiled.search('bounded input')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_static_pattern_collection_is_analyzed_without_review(
        self, tmp_path: Path,
    ) -> None:
        """Loop variables over a static pattern tuple are not dynamic."""
        content = (
            "import re\n"
            "patterns = (r'^foo$', r'^bar[0-9]+$')\n"
            "for pattern in patterns:\n"
            "    re.search(pattern, 'bounded input')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_static_pattern_rows_are_analyzed_without_review(
        self, tmp_path: Path,
    ) -> None:
        """Destructured loop variables retain static pattern alternatives."""
        content = (
            "import re\n"
            "checks = [(r'^foo$', 'foo'), (r'^bar$', 'bar')]\n"
            "for pattern, label in checks:\n"
            "    re.search(pattern, label)\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_static_pattern_comprehension_is_not_dynamic(
        self, tmp_path: Path,
    ) -> None:
        """Generator targets over static patterns retain alternatives."""
        content = (
            "import re\n"
            "patterns = (r'^foo$', r'^bar$')\n"
            "matches = any(re.search(pattern, 'foo') for pattern in patterns)\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_static_collection_value_in_fstring_is_expanded(
        self, tmp_path: Path,
    ) -> None:
        """A loop-bound f-string is checked as finite static alternatives."""
        content = (
            "import re\n"
            "methods = ('GET', 'HEAD')\n"
            "for method in methods:\n"
            "    re.search(rf'^{method}\\s+', 'GET /')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_static_collection_value_in_concatenation_is_expanded(
        self, tmp_path: Path,
    ) -> None:
        """A loop-bound concatenation is checked as static alternatives."""
        content = (
            "import re\n"
            "prefixes = (r'^foo', r'^bar')\n"
            "for prefix in prefixes:\n"
            "    re.search(prefix + r'\\s+$', 'foo ')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_dangerous_static_fstring_alternative_still_errors(
        self, tmp_path: Path,
    ) -> None:
        """Every pattern produced by f-string expansion is safety-checked."""
        content = (
            "import re\n"
            "atoms = (r'a+', r'b')\n"
            "for atom in atoms:\n"
            "    re.search(rf'({atom})+$', 'aaaa')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_dangerous_static_collection_still_errors(
        self, tmp_path: Path,
    ) -> None:
        """Every static collection alternative remains safety-checked."""
        content = (
            "import re\n"
            "patterns = (r'^safe$', r'(a+)+$')\n"
            "for pattern in patterns:\n"
            "    re.search(pattern, 'bounded input')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)

    def test_empty_static_loop_does_not_suppress_prior_pattern(
        self, tmp_path: Path,
    ) -> None:
        """An empty loop must not turn a later pattern use into no finding."""
        content = (
            "import re\n"
            "pattern = r'(a+)+$'\n"
            "for pattern in ():\n"
            "    pass\n"
            "re.search(pattern, 'aaaa')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.REVIEW for f in findings)

    def test_static_alternative_expansion_is_bounded(
        self, tmp_path: Path,
    ) -> None:
        """Collections above the analysis cap remain conservative REVIEW."""
        patterns = tuple(f"^{index}$" for index in range(257))
        content = (
            "import re\n"
            f"patterns = {patterns!r}\n"
            "for pattern in patterns:\n"
            "    re.search(pattern, 'bounded input')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.REVIEW for f in findings)

    def test_escape_with_dangerous_static_error(self, tmp_path: Path) -> None:
        """re.compile(re.escape(value) + r'(a+)+$') → ERROR (static segment danger)."""
        content = (
            "import re\n"
            "value = 'x'\n"
            "re.compile(re.escape(value) + r'(a+)+$')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert any(f.severity == Severity.ERROR for f in findings)


# ---------------------------------------------------------------------------
# Per-API flags positional detection (Section V)
# ---------------------------------------------------------------------------

class TestAPISignatures:
    """Test that flags are correctly identified at different positional indices."""

    def test_split_flags_positional(self, tmp_path: Path) -> None:
        """re.split(r'foo.*', data, 0, re.DOTALL) → flags at index 3."""
        self._assert_positional_regex_flags_are_reviewed(
            "import re\n" "re.split(r'foo.*', 'data', 0, re.DOTALL)\n", tmp_path
        )

    def test_sub_flags_positional(self, tmp_path: Path) -> None:
        """re.sub(r'foo.*', 'x', data, 0, re.DOTALL) → flags at index 4."""
        self._assert_positional_regex_flags_are_reviewed(
            "import re\n" "re.sub(r'foo.*', 'x', 'data', 0, re.DOTALL)\n", tmp_path
        )

    def test_subn_flags_positional(self, tmp_path: Path) -> None:
        """re.subn(r'foo.*', 'x', data, 0, re.DOTALL) → flags at index 4."""
        self._assert_positional_regex_flags_are_reviewed(
            "import re\n" "re.subn(r'foo.*', 'x', 'data', 0, re.DOTALL)\n",
            tmp_path,
        )

    def test_search_flags_positional(self, tmp_path: Path) -> None:
        """re.search(r'foo.*', data, re.DOTALL) → flags at index 2."""
        self._assert_positional_regex_flags_are_reviewed(
            "import re\n" "re.search(r'foo.*', 'data', re.DOTALL)\n", tmp_path
        )

    def _assert_positional_regex_flags_are_reviewed(
        self, content: str, tmp_path: Path,
    ) -> None:
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        reviews = [f for f in findings if f.severity == Severity.REVIEW]
        assert len(reviews) == 1
        assert "DOTALL" in reviews[0].reason

    def test_lazy_dotall_pattern_does_not_emit_greedy_review(
        self, tmp_path: Path,
    ) -> None:
        """A delimited lazy dot-star is not mislabeled as greedy DOTALL."""
        content = (
            "import re\n"
            "re.search(r'BEGIN.*?END', 'BEGIN value END', re.DOTALL)\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    @pytest.mark.parametrize(
        "pattern",
        [r"literal\.\*", r"[.*]+", r"BEGIN.*+END"],
    )
    def test_non_greedy_dot_star_forms_do_not_emit_dotall_review(
        self, pattern: str, tmp_path: Path,
    ) -> None:
        """Literal, character-class, and possessive forms are not greedy."""
        content = f"import re\nre.search({pattern!r}, 'data', re.DOTALL)\n"
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        assert not findings

    def test_sub_repl_not_mistaken_for_string(self, tmp_path: Path) -> None:
        """re.sub(r'safe', 'repl', data) — 'repl' is not the string input."""
        content = (
            "import re\n"
            "re.sub(r'^safe$', 'replacement', open('x').read())\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # No finding for safe pattern; input scope should be file content
        # (from string at index 2, not repl at index 1)


# ---------------------------------------------------------------------------
# Shell parser improvements (Section IX)
# ---------------------------------------------------------------------------

class TestShellParserImprovements:
    """Test shell parser enhancements for combined options, long options, etc."""

    def _scan_shell_content(self, content: str, tmp_path: Path):
        f = tmp_path / "test.sh"
        f.write_text(content)
        result, errors = _scan_shell_file(f, tmp_path)
        assert not errors
        return result

    def test_combined_Pe(self, tmp_path: Path) -> None:
        """grep -Pe '(a+)+$' should detect PCRE + pattern."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -Pe '(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_combined_Pne(self, tmp_path: Path) -> None:
        """grep -Pne '(a+)+$' should detect PCRE + pattern."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -Pne '(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_long_regexp_equals(self, tmp_path: Path) -> None:
        """grep -P --regexp='(a+)+$' should detect PCRE + pattern."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P --regexp='(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_perl_regexp_long_option(self, tmp_path: Path) -> None:
        """grep --perl-regexp '(a+)+$' should detect PCRE."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep --perl-regexp '(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1

    def test_pcre2_long_option(self, tmp_path: Path) -> None:
        """rg --pcre2 '(a+)+$' should detect PCRE."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\nrg --pcre2 '(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1

    def test_option_value_not_mistaken(self, tmp_path: Path) -> None:
        """grep -P -m 1 '(a+)+$' — -m consumes '1', not the pattern."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P -m 1 '(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_backslash_continuation(self, tmp_path: Path) -> None:
        r"""Backslash continuation should join lines."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P \\\n    '(a+)+$' file\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_no_space_pipe(self, tmp_path: Path) -> None:
        """cmd|grep -P '(a+)+$' — pipe without space."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\necho test|grep -P '(a+)+$'\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1

    def test_pattern_option_before_pcre_flag(self, tmp_path: Path) -> None:
        """grep -e '(a+)+$' -P input.txt — -e before -P must still detect PCRE."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -e '(a+)+$' -P input.txt\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_multiple_e_patterns(self, tmp_path: Path) -> None:
        """grep -P -e '(a+)+$' -e '(b+)+$' input.txt — both patterns detected."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P -e '(a+)+$' -e '(b+)+$' input.txt\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 2
        patterns = {f.pattern for f in errors_found}
        assert "(a+)+$" in patterns
        assert "(b+)+$" in patterns

    def test_pattern_file_option_reads_dangerous_pattern(self, tmp_path: Path) -> None:
        """grep -P -f patterns.txt input.txt — dangerous pattern in file → ERROR."""
        patterns_file = tmp_path / "patterns.txt"
        patterns_file.write_text("(a+)+$\n", encoding="utf-8")
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P -f patterns.txt input.txt\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_pattern_file_missing_produces_review_and_error(self, tmp_path: Path) -> None:
        """grep -P -f missing.txt input.txt — missing pattern file → REVIEW + ScanError."""
        f = tmp_path / "test.sh"
        f.write_text("#!/usr/bin/env bash\ngrep -P -f missing.txt input.txt\n")
        result, errors = _scan_shell_file(f, tmp_path)
        reviews = [r for r in result if r.severity == Severity.REVIEW]
        assert reviews
        assert any("cannot read" in e.message.lower() or "no such file" in e.message.lower()
                   for e in errors)

    def test_long_file_option_equals(self, tmp_path: Path) -> None:
        """grep -P --file=patterns.txt input.txt — dangerous pattern via =."""
        patterns_file = tmp_path / "patterns.txt"
        patterns_file.write_text("(a+)+$\n", encoding="utf-8")
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P --file=patterns.txt input.txt\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_color_no_value(self, tmp_path: Path) -> None:
        """grep -P --color '(a+)+$' input.txt — bare --color doesn't consume next token."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P --color '(a+)+$' input.txt\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_color_with_value(self, tmp_path: Path) -> None:
        """grep -P --color=always '(a+)+$' input.txt — --color=always consumes via =."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P --color=always '(a+)+$' input.txt\n", tmp_path,
        )
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors_found) == 1
        assert errors_found[0].pattern == "(a+)+$"

    def test_unknown_option_low_confidence(self, tmp_path: Path) -> None:
        """grep -P --unknown-opt '(a+)+$' input.txt — unknown option produces finding."""
        findings = self._scan_shell_content(
            "#!/usr/bin/env bash\ngrep -P --unknown-opt '(a+)+$' input.txt\n", tmp_path,
        )
        all_findings = [f for f in findings if f.severity in (Severity.ERROR, Severity.REVIEW)]
        assert all_findings

    def test_unbalanced_quote_scan_error(self, tmp_path: Path) -> None:
        """grep -P '(a+)+$ input.txt — unbalanced quote produces REVIEW or ScanError."""
        f = tmp_path / "test.sh"
        f.write_text("#!/usr/bin/env bash\ngrep -P '(a+)+$ input.txt\n")
        result, errors = _scan_shell_file(f, tmp_path)
        has_finding = any(
            f2.severity in (Severity.ERROR, Severity.REVIEW) for f2 in result
        )
        has_error = len(errors) > 0
        assert has_finding or has_error


# ---------------------------------------------------------------------------
# Shell engine & option adversarial (Section XIII)
# ---------------------------------------------------------------------------

class TestShellEngineResolution:
    """grep/rg default engines are NOT PCRE; PCRE flags enable PCRE."""

    def _scan(self, content: str, tmp_path: Path):
        f = tmp_path / "test.sh"
        f.write_text(content)
        return _scan_shell_file(f, tmp_path)

    def test_grep_e_without_pcre_no_error(self, tmp_path: Path) -> None:
        """grep -e '(a+)+$' input.txt → no PCRE ERROR."""
        self._assert_non_pcre_shell_patterns_are_not_errors(
            "#!/usr/bin/env bash\ngrep -e '(a+)+$' input.txt\n", tmp_path
        )

    def test_rg_e_without_pcre2_no_error(self, tmp_path: Path) -> None:
        """rg -e '(a+)+$' input.txt → no PCRE ERROR."""
        self._assert_non_pcre_shell_patterns_are_not_errors(
            "#!/usr/bin/env bash\nrg -e '(a+)+$' input.txt\n", tmp_path
        )

    def _assert_non_pcre_shell_patterns_are_not_errors(
        self, content: str, tmp_path: Path,
    ) -> None:
        findings, _ = self._scan(content, tmp_path)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors

    def test_grep_e_then_p_is_error(self, tmp_path: Path) -> None:
        """grep -e '(a+)+$' -P input.txt → ERROR (PCRE enabled)."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -e '(a+)+$' -P input.txt\n", tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].engine == Engine.SHELL_PCRE

    def test_grep_p_then_e_is_error(self, tmp_path: Path) -> None:
        """grep -P -e '(a+)+$' input.txt → ERROR."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P -e '(a+)+$' input.txt\n", tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1

    def test_rg_e_then_pcre2_is_error(self, tmp_path: Path) -> None:
        """rg -e '(a+)+$' --pcre2 input.txt → ERROR."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\nrg -e '(a+)+$' --pcre2 input.txt\n", tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1

    def test_perl_e_is_pcre(self, tmp_path: Path) -> None:
        """perl -e '(a+)+$' → ERROR (perl is inherently PCRE)."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\nperl -e '(a+)+$'\n", tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].engine == Engine.SHELL_PCRE


class TestShellOptionContracts:
    """Boolean, required-value, and unknown option handling."""

    def _scan(self, content: str, tmp_path: Path):
        f = tmp_path / "test.sh"
        f.write_text(content)
        return _scan_shell_file(f, tmp_path)

    def test_boolean_options(self, tmp_path: Path) -> None:
        """grep -P -n -q -i '(a+)+$' input.txt → ERROR on the pattern."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P -n -q -i '(a+)+$' input.txt\n", tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].pattern == "(a+)+$"

    @pytest.mark.parametrize(
        "command",
        [
            "grep -qE '^[a-z]+$' input.txt",
            "grep --extended-regexp --quiet '^[a-z]+$' input.txt",
            "grep -Fq 'literal[not-regex]' input.txt",
            "grep --fixed-strings --quiet 'literal[not-regex]' input.txt",
        ],
    )
    def test_grep_engine_flags_are_known(
        self, command: str, tmp_path: Path,
    ) -> None:
        """Standard grep engine flags must not downgrade parse confidence."""
        findings, errors = self._scan(
            f"#!/usr/bin/env bash\n{command}\n", tmp_path,
        )
        assert not errors
        assert not findings

    def test_perl_in_place_cluster_does_not_promote_filename(
        self, tmp_path: Path,
    ) -> None:
        """A Perl program supplied by -e leaves following paths as inputs."""
        self._assert_ordinary_multiline_shell_text_has_no_findings(
            "#!/usr/bin/env bash\n"
            "perl -0pi -e 's@old@new@' \"${temp_detector}\"\n",
            tmp_path,
        )

    def test_required_value_option_consumes_next(self, tmp_path: Path) -> None:
        """grep -P --directories skip '(a+)+$' input.txt → ERROR on the pattern."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P --directories skip '(a+)+$' input.txt\n",
            tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].pattern == "(a+)+$"

    def test_unknown_option_does_not_hide_pattern(self, tmp_path: Path) -> None:
        """grep -P --unknown-opt skip '(a+)+$' input.txt → still ERROR on pattern."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P --unknown-opt skip '(a+)+$' input.txt\n",
            tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(f.pattern == "(a+)+$" for f in errors)

    def test_double_dash_separator(self, tmp_path: Path) -> None:
        """grep -P -- '(a+)+$' input.txt → pattern after -- is analyzed."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P -- '(a+)+$' input.txt\n", tmp_path,
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].pattern == "(a+)+$"

    def test_unbalanced_multiline_text_does_not_create_rg_command(
        self, tmp_path: Path,
    ) -> None:
        """The substring ``rg`` in ordinary text is not an rg command."""
        self._assert_ordinary_multiline_shell_text_has_no_findings(
            "#!/usr/bin/env bash\n"
            'config_output="nginx version: nginx/1.26.3\n'
            "configure arguments: --prefix=/etc/nginx --with-compat\"\n",
            tmp_path,
        )

    def _assert_ordinary_multiline_shell_text_has_no_findings(
        self, content: str, tmp_path: Path,
    ) -> None:
        findings, errors = self._scan(content, tmp_path)
        assert not errors
        assert not findings


class TestShellPatternFiles:
    """Pattern-file resolution, boundary validation, encoding."""

    def _scan(self, content: str, tmp_path: Path, extra_files=None):
        if extra_files:
            for rel, c in extra_files.items():
                p = tmp_path / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(c, encoding="utf-8")
        f = tmp_path / "test.sh"
        f.write_text(content, encoding="utf-8")
        return _scan_shell_file(f, tmp_path)

    def test_relative_to_script_directory(self, tmp_path: Path) -> None:
        """grep -P -f patterns.txt input.txt reads patterns.txt next to script."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f patterns.txt input.txt\n",
            tmp_path, {"patterns.txt": "(a+)+$\n"},
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) == 1
        assert errors[0].pattern == "(a+)+$"

    def test_multiple_pattern_files(self, tmp_path: Path) -> None:
        """grep -P -f p1.txt -f p2.txt input.txt reads both files."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f p1.txt -f p2.txt input.txt\n",
            tmp_path, {"p1.txt": "(a+)+$\n", "p2.txt": "(b+)+$\n"},
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        patterns = {f.pattern for f in errors}
        assert "(a+)+$" in patterns
        assert "(b+)+$" in patterns

    def test_dangerous_pattern_in_second_file(self, tmp_path: Path) -> None:
        """The second pattern file's dangerous pattern is detected."""
        findings, _ = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f safe.txt -f danger.txt input.txt\n",
            tmp_path, {"safe.txt": "^safe$\n", "danger.txt": "(a+)+$\n"},
        )
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(f.pattern == "(a+)+$" for f in errors)

    def test_missing_file_produces_scan_error(self, tmp_path: Path) -> None:
        """A missing pattern file produces a ScanError and a REVIEW."""
        findings, errors = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f missing.txt input.txt\n", tmp_path,
        )
        assert any("cannot read" in e.message.lower() or "no such" in e.message.lower()
                    for e in errors)
        assert any(f.severity == Severity.REVIEW for f in findings)

    def test_invalid_utf8_produces_scan_error(self, tmp_path: Path) -> None:
        """A non-UTF-8 pattern file produces a ScanError and a REVIEW."""
        bad = tmp_path / "bad.txt"
        bad.write_bytes(b"(a+)+\xff\xfe")
        findings, errors = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f bad.txt input.txt\n", tmp_path,
        )
        assert any("utf-8" in e.message.lower() for e in errors)
        assert any(f.severity == Severity.REVIEW for f in findings)

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        """grep -P -f ../../etc/passwd input.txt → rejected."""
        findings, errors = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f ../../etc/passwd input.txt\n", tmp_path,
        )
        assert any("traversal" in e.message.lower() for e in errors)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        """grep -P -f /tmp/external input.txt → rejected."""
        findings, errors = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f /tmp/external input.txt\n", tmp_path,
        )
        assert any("absolute" in e.message.lower() for e in errors)

    def test_directory_path_produces_scan_error(self, tmp_path: Path) -> None:
        """A directory as a pattern file produces a ScanError."""
        (tmp_path / "subdir").mkdir()
        findings, errors = self._scan(
            "#!/usr/bin/env bash\ngrep -P -f subdir input.txt\n", tmp_path,
        )
        assert any("cannot read" in e.message.lower() or "direct" in e.message.lower()
                    for e in errors)


# ---------------------------------------------------------------------------
# Python AST adversarial — compiled reassignment, lexical delete, eval scope
# (Section XIII)
# ---------------------------------------------------------------------------

class TestPythonAstAdversarial:
    """Adversarial cases for the unified lexical binding model."""

    def test_augassign_compiled_reassignment_produces_review(self, tmp_path: Path) -> None:
        """p = re.compile(safe); p += something; p.findall() → REVIEW."""
        content = (
            "import re\n"
            "p = re.compile(r'^safe$')\n"
            "p += something\n"
            "p.findall('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        reviews = [
            f for f in findings
            if f.severity == Severity.REVIEW
            and f.api == "compiled.findall"
            and f.line == 4
        ]
        assert len(reviews) == 1
        assert reviews[0].pattern_source in (PatternSource.DYNAMIC, PatternSource.UNKNOWN)
        assert reviews[0].compile_line == 2

    def test_function_local_del_preserves_module_error(self, tmp_path: Path) -> None:
        """del p inside a function must not delete the module-level compiled p."""
        content = (
            "import re\n"
            "p = re.compile(r'(a+)+$')\n"
            "def cleanup():\n"
            "    del p\n"
            "p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and "compiled" in f.api and f.line == 5
        ]
        assert len(compiled_errors) == 1

    def test_module_level_del_removes_binding(self, tmp_path: Path) -> None:
        """del p at module scope removes the binding; later use is not ERROR."""
        content = (
            "import re\n"
            "p = re.compile(r'(a+)+$')\n"
            "del p\n"
            "re.search(p, 'data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        compiled_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and f.line == 4
        ]
        assert not compiled_errors

    def test_function_local_del_shadows_outer_binding(self, tmp_path: Path) -> None:
        """compile + del inside a function; the post-del call must not use it."""
        content = (
            "import re\n"
            "def f():\n"
            "    p = re.compile(r'(a+)+$')\n"
            "    del p\n"
            "    p.search('data')\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # The compile call at line 3 is still an ERROR; the post-del call is not.
        compile_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and f.api == "compile" and f.line == 3
        ]
        assert len(compile_errors) == 1
        post_call_errors = [
            f for f in findings
            if f.severity == Severity.ERROR and f.line == 5
        ]
        assert not post_call_errors

    def test_default_positional_argument_regex(self, tmp_path: Path) -> None:
        """def f(pattern=re.compile(r'(a+)+$')) → ERROR on the default."""
        content = (
            "import re\n"
            "def parse(pattern=re.compile(r'(a+)+$')):\n"
            "    pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR and f.line == 2]
        assert len(errors_found) == 1

    def test_default_kwonly_argument_regex(self, tmp_path: Path) -> None:
        """def f(*, pattern=re.compile(r'(a+)+$')) → ERROR on the default."""
        content = (
            "import re\n"
            "def parse(*, pattern=re.compile(r'(a+)+$')):\n"
            "    pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR and f.line == 2]
        assert len(errors_found) == 1

    def test_async_function_default_argument_regex(self, tmp_path: Path) -> None:
        """async def f(pattern=re.compile(r'(a+)+$')) → ERROR on the default."""
        content = (
            "import re\n"
            "async def parse(pattern=re.compile(r'(a+)+$')):\n"
            "    pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR and f.line == 2]
        assert len(errors_found) == 1

    def test_lambda_default_argument_regex(self, tmp_path: Path) -> None:
        """lambda pattern=re.compile(r'(a+)+$'): None → ERROR on the default."""
        content = (
            "import re\n"
            "handler = lambda pattern=re.compile(r'(a+)+$'): None\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR and f.line == 2]
        assert len(errors_found) == 1

    def test_decorator_regex(self, tmp_path: Path) -> None:
        """@decorator(re.compile(r'(a+)+$')) → ERROR in the enclosing scope."""
        content = (
            "import re\n"
            "@decorator(re.compile(r'(a+)+$'))\n"
            "def f():\n"
            "    pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR and f.line == 2]
        assert len(errors_found) == 1

    def test_default_evaluated_before_parameter_shadow(self, tmp_path: Path) -> None:
        """def f(re=re.compile(r'(a+)+$')) → the default ``re`` is the module."""
        content = (
            "import re\n"
            "def f(re=re.compile(r'(a+)+$')):\n"
            "    pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        errors_found = [f for f in findings if f.severity == Severity.ERROR and f.line == 2]
        assert len(errors_found) == 1

    def test_decorator_uses_enclosing_function_scope(self, tmp_path: Path) -> None:
        """decorator's ``re`` resolves to the enclosing function parameter."""
        content = (
            "import re\n"
            "def f(re):\n"
            "    @decorator(re.compile(r'(a+)+$'))\n"
            "    def inner():\n"
            "        pass\n"
        )
        findings, errors = _scan_py(content, tmp_path)
        assert not errors
        # ``re`` is shadowed by the parameter → not the module alias → no ERROR.
        errors_found = [f for f in findings if f.severity == Severity.ERROR]
        assert not errors_found
