#!/usr/bin/env python3
"""Regression tests for the install-script exit-path analyzer.

Covers the two classification defects found in review:

* an assignment whose right-hand side contains quotes or variable references
  (``AWK_CMD="$AWK_BIN -f script.awk"``) used to be misread as an awk
  invocation, which suppressed every ``exit 1`` for the next 30 lines, while
  an env-prefixed invocation (``LC_ALL=C awk '...'``) must still count as one;
* the ``cache_required_executable`` / ``cache_optional_executable`` exemption
  used to apply to any line with that prefix, hiding additional commands
  chained after the helper call.

Run:
    python3 -m pytest tools/tests/test_analyze_exit1_paths.py -q
"""

from __future__ import annotations

import pathlib
import sys

import pytest

TOOLS_TESTS = pathlib.Path(__file__).resolve().parent
REPO_ROOT = TOOLS_TESTS.parents[1]
sys.path.insert(0, str(TOOLS_TESTS))
sys.path.insert(0, str(REPO_ROOT))

import analyze_exit1_paths as analyzer  # noqa: E402

ASSIGNMENT_LINES = [
    "AWK_BIN=",
    'AWK_CMD="$AWK_BIN -f script.awk"',
    "AWK_CMD=${AWK_BIN}",
    "AWK_CMD=$AWK_BIN",
    "FOO='awk script'",
    "AWK_PROGRAM='{print $1}'",
]

INVOCATION_LINES = [
    "awk '{print}' file",
    "$AWK_BIN '{print}' file",
    "${AWK_BIN} 'BEGIN{print}'",
    "LC_ALL=C awk '{print}'",
    "FOO=bar awk '{print}'",
    "A=b; awk '{print}'",
    "X=$(awk '{print}')",
    "X=`awk '{print}'`",
    "if awk '{print}' file; then",
    # A separator inside the stored value ends the assignment, so the command
    # after it is still in command position.
    "AWK_CMD=${AWK_BIN}; awk '{print}'",
]

NON_AWK_LINES = [
    "sed -n 's/awk/x/p' file",
    "grep -q 'awk' file",
    "echo \"awk usage\"",
]

# Quoted text is data: substitutions and separators inside quotes do not run
# and must not read as command words.
QUOTED_DATA_LINES = [
    "printf '%s\\n' 'hint: $(awk -f x.awk file)'",
    "echo 'split; awk here'",
]


@pytest.mark.parametrize("line", QUOTED_DATA_LINES)
def test_quoted_data_is_not_an_awk_command(line: str) -> None:
    """Substitutions and separators inside quotes are data, not commands."""
    assert analyzer._is_awk_invocation(line) is False


def test_quoted_data_cannot_mask_a_later_exit1() -> None:
    """A quoted example above the line must not exempt an exit path."""
    lines = [
        "printf '%s\\n' 'hint: $(awk -f x.awk file)'\n",
        "echo failed >&2\n",
        "exit 1\n",
    ]
    line_types = analyzer._classify_lines(lines)
    issues = analyzer._analyze_shell_lines(lines, line_types)
    assert any("exit 1" in issue for issue in issues), issues


def test_double_quoted_substitution_still_counts() -> None:
    """$(awk ...) inside double quotes still executes, so it still counts."""
    assert (
        analyzer._is_awk_invocation("val=\"$(awk '{print $1}' file)\"") is True
    )


@pytest.mark.parametrize("line", ASSIGNMENT_LINES)
def test_assignment_lines_are_not_awk_invocations(line: str) -> None:
    """Assignment-only lines never count as an awk invocation."""
    assert analyzer._is_plain_assignment(line) is True
    assert analyzer._is_awk_invocation(line) is False


@pytest.mark.parametrize("line", INVOCATION_LINES)
def test_invocation_lines_are_detected(line: str) -> None:
    """Real awk invocations, including env-prefixed forms, are detected."""
    assert analyzer._is_awk_invocation(line) is True


@pytest.mark.parametrize("line", NON_AWK_LINES)
def test_non_command_awk_mentions_are_ignored(line: str) -> None:
    """awk appearing as data or in a comment-like context is not a command."""
    assert analyzer._is_awk_invocation(line) is False


def test_assignment_cannot_mask_a_later_exit1() -> None:
    """A quoted assignment must not exempt the exit paths that follow it."""
    lines = [
        'AWK_CMD="$AWK_BIN -f script.awk"\n',
    ] + ["# padding\n"] * 2 + [
        'echo "failed" >&2\n',
        "exit 1\n",
    ]
    line_types = analyzer._classify_lines(lines)
    issues = analyzer._analyze_shell_lines(lines, line_types)
    assert any("exit 1" in issue for issue in issues), issues


@pytest.mark.parametrize(
    "line",
    [
        "cache_required_executable AWK_BIN awk",
        "cache_optional_executable JQ_BIN jq",
        "  cache_required_executable SED_BIN sed",
    ],
)
def test_standalone_cache_helper_calls_are_exempt(line: str) -> None:
    """A complete helper call on its own line stays exempt."""
    assert analyzer._is_complete_cache_helper_call(line.strip()) is True
    assert (
        analyzer._check_risky_command(
            [line], 0, line.strip(), errexit_active=True
        )
        is None
    )


@pytest.mark.parametrize(
    "line",
    [
        "cache_required_executable AWK_BIN awk; rm -rf /",
        "cache_optional_executable JQ_BIN jq && rm -rf /",
        "cache_required_executable AWK_BIN awk | tee /tmp/x",
        "cache_required_executable AWK_BIN awk > /tmp/x",
        "cache_required_executable AWK_BIN awk || true; rm -rf /",
    ],
)
def test_chained_cache_helper_lines_are_analyzed(line: str) -> None:
    """A helper call chaining further commands is not fully exempt."""
    assert analyzer._is_complete_cache_helper_call(line) is False


def test_chained_cache_helper_line_reports_risky_command() -> None:
    """The trailing command on a chained helper line is still reported."""
    line = "cache_required_executable AWK_BIN awk; rm -rf /tmp/x"
    issue = analyzer._check_risky_command(
        [line], 0, line, errexit_active=True
    )
    assert issue is not None, issue
    assert "rm" in issue, issue


def test_install_script_still_passes() -> None:
    """The real installer keeps a clean analysis result."""
    install_script = REPO_ROOT / "tools" / "install.sh"
    if not install_script.is_file():
        pytest.skip("tools/install.sh not present in this checkout")
    assert analyzer.main(str(install_script)) == 0
