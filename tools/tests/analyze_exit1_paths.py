#!/usr/bin/env python3
"""
Static analysis helper for test_install_error_format.sh.

Reads the install.sh script given as sys.argv[1] and checks that every
'exit 1' call is preceded (within ~10 lines) by an emit_error or
die_with_error call.  Exits 0 if all checks pass, 1 otherwise.

Printed lines start with "PASS:" or "FAIL:" so the calling shell script
can parse them easily.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.path_validation import validate_read_path  # noqa: E402

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

# ``cache_required_executable`` / ``cache_optional_executable`` fail with a
# structured error inside the helper itself, so a *standalone* call is not an
# unguarded risky command.  The exemption must not cover a line that chains
# further commands: ``helper x y; rm -rf /`` would otherwise hide the whole
# tail of the line from analysis.
CACHE_HELPER_CALL_RE = re.compile(
    r"^(?:cache_required_executable|cache_optional_executable)"
    r"[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]+[A-Za-z0-9._+-]+[ \t]*$"
)


def _is_complete_cache_helper_call(line: str) -> bool:
    """True when *line* is exactly one complete cache-helper invocation.

    Continuations (``;``, ``&&``, ``||``, ``|``) and redirections mean the
    line contains more than the helper call, so the exemption does not apply
    and the remaining commands are analyzed normally.
    """
    return CACHE_HELPER_CALL_RE.match(line) is not None


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

        # Delimiters are shell words, which may start with a digit.
        heredoc_match = re.search(r"(?<!<)<<-?(?!<)\s*['\"]?(\w+)['\"]?", line)
        if heredoc_match:
            heredoc_end = heredoc_match.group(1)
            line_types.append("shell")
            in_heredoc = True
            continue

        line_types.append("shell")

    return line_types


# A bare assignment (VAR=... at line start) is not an invocation; anything
# else that references awk with a quoted program is a helper context.
#
# The right-hand side may contain quotes and variable references, because a
# line such as AWK_CMD="$AWK_BIN -f script.awk" merely stores a command for
# later use.  What distinguishes an assignment from an env-prefixed command
# (``LC_ALL=C awk ...``) is what follows the stored value: after the value is
# consumed, an assignment line ends, while a command line continues.
_ASSIGNMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Shell keywords and control operators that may precede the command word.
# A procedural scan over this explicit set keeps the accepted tokens obvious.
_LEADING_KEYWORD_TOKENS = frozenset(
    {"if", "then", "elif", "else", "while", "until", "do", "time"}
)
_LEADING_OPERATORS = "!{("


def _consume_assignment_value(text: str) -> str | None:
    """Consume one assignment value and return the remainder, or None.

    A double-quoted or single-quoted value runs to its matching quote; an
    unquoted value runs to the first whitespace or command separator; an
    empty value (``NAME=``) consumes nothing.  ``None`` means the value is
    unterminated and the line cannot be classified as an assignment.
    """
    if not text:
        return ""
    quote = text[0]
    if quote in "\"'":
        index = 1
        while index < len(text):
            char = text[index]
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                return text[index + 1:]
            index += 1
        return None
    match = re.match(r"[^\s;&|`]+", text)
    if match is None:
        return text
    return text[match.end():]


def _strip_leading_assignments(line: str) -> str:
    """Return *line* with every leading ``NAME=value`` prefix removed."""
    rest = line
    while True:
        name_match = _ASSIGNMENT_NAME_RE.match(rest)
        if name_match is None:
            return rest
        remainder = _consume_assignment_value(rest[name_match.end():])
        if remainder is None:
            # An unterminated quoted value cannot be classified as a
            # standalone assignment; treat the raw text as a command so the
            # caller errs toward analysis, not toward exemption.
            return rest
        rest = remainder


def _is_plain_assignment(line: str) -> bool:
    """True when *line* is an assignment-only statement.

    ``VAR=value`` (quoted, variable-expanded, or bare) is the whole line
    here; if anything else follows the value, the line is an env-prefixed
    command such as ``LC_ALL=C awk ...`` and is not an assignment.
    """
    if _ASSIGNMENT_NAME_RE.match(line) is None:
        return False
    return _strip_leading_assignments(line).strip() == ""


def _blank_single_quoted(line: str) -> str:
    """Blank single-quoted spans, keeping the quote marks and the layout.

    Single-quoted text is fully inert: no substitution or separator runs
    inside it, so command-position probes must not read it.  Double-quoted
    spans are kept intact because ``$(...)`` and backticks still execute
    there.
    """
    chars = list(line)
    quoted = False
    for index, char in enumerate(chars):
        if char == "'":
            quoted = not quoted
            continue
        if quoted:
            chars[index] = " "
    return "".join(chars)


def _separator_length(line: str, index: int) -> int | None:
    """Length of an unquoted command separator at ``index``, else ``None``.

    ``;`` and a single ``|`` are one character, ``&&`` consumes two, and a
    lone ``&`` is ordinary text here (only ``&&`` splits a segment).
    """
    char = line[index]
    if char == ";":
        return 1
    if char == "|":
        return 1
    if char == "&" and index + 1 < len(line) and line[index + 1] == "&":
        return 2
    return None


def _split_command_segments(line: str) -> list[str]:
    """Split a line on unquoted command separators (``;``, ``&&``, ``||``, ``|``).

    Separators inside either quote form are literal text and must not split,
    while the segments keep their quoting intact: a quoted command word such
    as ``"$AWK_BIN"`` is still a command word.  A backslash escape inside
    double quotes keeps its following character readable.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is None:
            separator = _separator_length(line, index)
            if separator is not None:
                segments.append("".join(current))
                current = []
                index += separator
                continue
            if char in "\"'":
                quote = char
        elif quote == '"' and char == "\\":
            current.append(char)
            if index + 1 < len(line):
                current.append(line[index + 1])
            index += 2
            continue
        elif char == quote:
            quote = None
        current.append(char)
        index += 1
    segments.append("".join(current))
    return segments


def _strip_leading_keywords(candidate: str) -> str:
    """Drop shell keywords and control operators before the command word."""
    while True:
        word = re.match(r"[A-Za-z]+", candidate)
        if word is not None and word.group(0) in _LEADING_KEYWORD_TOKENS:
            candidate = candidate[word.end():].lstrip()
            continue
        if candidate and candidate[0] in _LEADING_OPERATORS:
            candidate = candidate[1:].lstrip()
            continue
        return candidate


def _awk_in_command_position(line: str) -> bool:
    """True when awk (or the cached $AWK_BIN) is this line's command word."""
    # Command substitution executes its contents on this line even though the
    # statement starts as an assignment: ``X=$(awk '...')`` runs awk.  Only
    # single-quoted text is inert there, so quoted examples must not read as
    # invocations while ``"$(awk ...)"`` still legitimately runs awk.
    substitution_text = _blank_single_quoted(line)
    if re.search(
        r"\$\(\s*(?:awk|\$\{?AWK_BIN\}?)(?:\s|$)", substitution_text
    ) is not None:
        return True
    if re.search(
        r"`\s*(?:awk|\$\{?AWK_BIN\}?)(?:\s|$)", substitution_text
    ) is not None:
        return True

    # Separators inside quotes are literal, so the split below is quoting
    # aware; the segments keep their quoting for the command-word checks.
    remainder = _strip_leading_assignments(line)
    for segment in _split_command_segments(remainder):
        candidate = _strip_leading_keywords(segment.strip())
        candidate = candidate.lstrip("\"'")
        if re.match(r"awk(?:\s|$)", candidate):
            return True
        if re.match(r"\$\{?AWK_BIN\}?(?:\s|$|\"|')", candidate):
            return True
    return False


def _is_awk_invocation(prev: str) -> bool:
    """True when a line invokes awk as a command (not a plain assignment)."""
    if not _awk_in_command_position(prev):
        return False
    return "'" in prev or '"' in prev


def _is_inside_helper_or_awk(lines: list[str], index: int) -> bool:
    """Return True if the line at *index* is inside a helper function or awk block.

    Scans up to 30 lines backwards from *index* looking for a known
    shell helper function definition (matched by :data:`HELPER_FUNCS_RE`)
    or an ``awk`` invocation.  ``exit 1`` inside these contexts is
    expected and should not be flagged.
    """
    for j in range(index, -1, -1):
        prev = lines[j].strip()
        if _is_awk_invocation(prev):
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
    if _is_complete_cache_helper_call(stripped):
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

        issue = _check_exit1_path(lines, i, stripped)
        if issue:
            issues.append(issue)
        issue = _check_risky_command(lines, i, stripped, errexit_active=errexit_active)
        if issue:
            issues.append(issue)

    return issues


def main(filepath: str) -> int:
    resolved = validate_read_path(filepath, purpose="install script")
    lines = resolved.read_text(encoding="utf-8").splitlines(keepends=True)

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
