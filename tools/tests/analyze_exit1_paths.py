#!/usr/bin/env python3
"""
Static analysis helper for test_install_error_format.sh.

Reads the install.sh script given as sys.argv[1] and checks that every
'exit 1' call is preceded (within ~10 lines) by an emit_error or
die_with_error call.  Exits 0 if all checks pass, 1 otherwise.

Printed lines start with "PASS:" or "FAIL:" so the calling shell script
can parse them easily.
"""

import re
import sys

HELPER_FUNCS_RE = re.compile(
    r"^(die_with_error|emit_error|emit_suggest|json_output|semver_lt|sha256_file)\s*\(\)"
)

# Commands that can fail and would cause a silent abort under ``set -e``
# unless explicitly guarded with ``if ! cmd`` or ``cmd || ...``.
RISKY_COMMANDS_RE = re.compile(
    r"\b(mktemp|mkdir|chmod|curl|cp|mv|tee|ln|rm|chown)\b"
)

# Patterns that indicate ``set -e`` (or equivalent) is being enabled.
# Matches ``set -e``, ``set -euo pipefail``, ``set -o errexit``, etc.
ERREXIT_ON_RE = re.compile(r"\bset\s+(?:-[A-Za-z]*e[A-Za-z]*|-o\s+errexit)")
ERREXIT_OFF_RE = re.compile(r"\bset\s+(?:\+[A-Za-z]*e[A-Za-z]*|\+o\s+errexit)")

# Line-level guard patterns: the command is already wrapped in an ``if``
# or followed by ``||`` so failure is handled explicitly.
_GUARDED_RE = re.compile(r"(^\s*if\s+|^\s*if\s*!|;\s*then|\|\|)")


def _classify_lines(lines: list[str]) -> list[str]:
    """Classify each source line as ``'shell'`` or ``'heredoc'``.

    Tracks heredoc delimiters (``<<EOF`` / ``<<-'EOF'`` variants) so that
    ``exit 1`` occurrences inside heredoc bodies are excluded from the
    subsequent analysis pass.

    Returns a list parallel to *lines* where each element is one of the
    two string labels.
    """
    in_heredoc = False
    heredoc_end = ""
    line_types: list[str] = []

    for line in lines:
        stripped = line.rstrip("\n").strip()

        if in_heredoc:
            if stripped == heredoc_end:
                in_heredoc = False
                heredoc_end = ""
            line_types.append("heredoc")
            continue

        heredoc_match = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        if heredoc_match:
            heredoc_end = heredoc_match.group(1)
            line_types.append("shell")
            in_heredoc = True
            continue

        line_types.append("shell")

    return line_types


def _is_inside_helper_or_awk(lines: list[str], index: int) -> bool:
    """Return True if the line at *index* is inside a helper function or awk block.

    Scans up to 30 lines backwards from *index* looking for a known
    shell helper function definition (matched by :data:`HELPER_FUNCS_RE`)
    or an ``awk`` invocation.  ``exit 1`` inside these contexts is
    expected and should not be flagged.
    """
    for j in range(index, -1, -1):
        prev = lines[j].strip()
        if "awk " in prev and "'" in prev:
            return True
        if HELPER_FUNCS_RE.match(prev):
            return True
        if index - j > 30:
            break
    return False


def _has_error_helper_nearby(lines: list[str], index: int) -> bool:
    """Return True if ``emit_error`` or ``die_with_error`` appears within ~10 preceding lines.

    The check concatenates the surrounding context into a single string
    so that multi-line invocations (e.g. a call split with backslash
    continuation) are still detected.
    """
    context_start = max(0, index - 10)
    context = "".join(lines[context_start : index + 1])
    return "emit_error" in context or "die_with_error" in context


def _check_exit1_path(lines: list[str], index: int, stripped: str) -> str | None:
    """Return an issue message if ``exit 1`` at *index* lacks a preceding error helper."""
    if not re.search(r"\bexit\s+1\b", stripped):
        return None
    if _is_inside_helper_or_awk(lines, index):
        return None
    if _has_error_helper_nearby(lines, index):
        return None
    return (
        f"Line {index + 1}: 'exit 1' without preceding "
        f"emit_error/die_with_error"
    )


def _check_risky_command(
    lines: list[str], index: int, stripped: str, *, errexit_active: bool,
) -> str | None:
    """Return an issue message if a risky command at *index* is unguarded under ``set -e``."""
    if not errexit_active:
        return None
    cmd_match = RISKY_COMMANDS_RE.search(stripped)
    if cmd_match is None:
        return None
    if _is_inside_helper_or_awk(lines, index):
        return None
    if _has_error_helper_nearby(lines, index):
        return None
    if _GUARDED_RE.search(stripped):
        return None
    return (
        f"Line {index + 1}: unguarded '{cmd_match.group(1)}' under "
        f"set -e may abort without structured error output"
    )


def _update_errexit(stripped: str, errexit_active: bool) -> bool:
    """Return the updated errexit state after examining *stripped*."""
    if ERREXIT_ON_RE.search(stripped):
        return True
    if ERREXIT_OFF_RE.search(stripped):
        return False
    return errexit_active


def _analyze_shell_lines(lines: list[str], line_types: list[str]) -> list[str]:
    """Scan classified shell lines and return a list of issue messages."""
    issues: list[str] = []
    errexit_active = False

    for i, (line, ltype) in enumerate(zip(lines, line_types)):
        if ltype != "shell":
            continue

        stripped = line.strip()

        if stripped.startswith("#"):
            continue

        errexit_active = _update_errexit(stripped, errexit_active)

        for checker in (
            lambda: _check_exit1_path(lines, i, stripped),
            lambda: _check_risky_command(lines, i, stripped, errexit_active=errexit_active),
        ):
            issue = checker()
            if issue:
                issues.append(issue)

    return issues


def main(filepath: str) -> int:
    with open(filepath, encoding="utf-8") as fh:
        lines = fh.readlines()

    line_types = _classify_lines(lines)
    issues = _analyze_shell_lines(lines, line_types)

    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1

    print("PASS: All exit 1 paths use structured error helpers")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: analyze_exit1_paths.py <install_script>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
