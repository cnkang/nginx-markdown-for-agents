#!/usr/bin/env python3
"""
detect_script_exec_bits.py — release-integrity guard.

A script invoked as an executable (`./path/to/script.sh`, or a bare
`tools/...` command word) needs the executable bit in the git index.
The bit in the working tree is not enough: CI checks out exactly what the
index records.  A reference can therefore work locally (the working tree
keeps the bit) and fail in CI with exit code 126 ("Permission denied")
on the first run — which is how the upstream-trailer verification step
first executed.

Signals checked:
  1. GitHub workflow `run:` lines that invoke `./relative/script.{sh,py}`
     directly (a `bash`/`sh`/`python3` prefix does not need the bit);
  2. workflow `run:` lines whose command word is a bare `tools/`,
     `tests/` or `scripts/` script path;
  3. Makefile recipe lines with the same two shapes.

Only the git index mode is authoritative: the file must be recorded as
100755.  Paths that are not tracked are skipped (the scratch-file gate
covers those), and non-regular entries (symlinks) are skipped as well.

Usage:
    python3 tools/harness/detect_script_exec_bits.py

Exit codes: 0 clean, 1 violations.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPT_SUFFIX = re.compile(r"\.(sh|py)$")
REF_DELIM = r"[\s;&|(){]"
REF_TAIL = r"[\s;&|)]"
DIRECT_REF = re.compile(
    rf"(?:^|{REF_DELIM})\./([A-Za-z0-9_./-]+\.(?:sh|py))(?={REF_TAIL}|$)"
)
BARE_REF = re.compile(
    rf"(?:^|{REF_DELIM})([A-Za-z0-9_-]+/(?:[A-Za-z0-9_./-]+)\.(?:sh|py))"
    rf"(?={REF_TAIL}|$)"
)
QUOTED_REF = re.compile(
    rf"(?:^|{REF_DELIM})(['\"])((?:\./)?[A-Za-z0-9_./-]+\.(?:sh|py))\1"
)
# bash/sh/... open the script as input and work without the bit; the
# wrappers run the script itself, so they keep the strict requirement.
INTERPRETERS = ("bash", "sh", "zsh", "dash", "python", "python3")
EXEC_WRAPPERS = (
    "env", "exec", "command", "sudo", "timeout", "nice", "nohup", "xargs",
    "eval",
)
SHELL_INTERPRETERS = ("bash", "sh", "zsh", "dash")
ESCAPED_SEPARATOR = re.compile(r"\\([\s;|&(){}])")
CONTROL_PREFIXES = (
    "if", "then", "elif", "while", "until", "do", "else", "!", "time", "coproc",
)
SEGMENT_SPLIT = re.compile(r"(?:;|&&|&|\|&|\|\||\||\(|\)|\{|\})")
REDIRECT_RE = re.compile(r"^\d*[<>]")
QUOTED_SPAN_RE = re.compile(r"""\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'""")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_data_context(line: str, path_start: int) -> bool:
    """True when the path sits in a data position, never executed.

    Two such positions: an element of an array assignment
    (``arr=(path ...)``) and a ``case`` pattern alternation
    (``foo|path)`` before the pattern list closes).  Neither executes
    the path, so the executable bit is not required.
    """
    prefix = line[:path_start]
    stripped = prefix.rstrip()
    if prefix.count("(") > prefix.count(")"):
        opener = prefix.rfind("(")
        head = prefix[:opener].rstrip()
        token = head.split()[-1] if head.split() else ""
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=", token)
            and ")" not in prefix[opener:]
        ):
            return True
    if stripped.endswith("|"):
        case_match = re.search(r"\bcase\b.*?\bin\b", prefix)
        if case_match and ")" not in prefix[case_match.end():]:
            return True
    return False


def _command_word_at(
    line: str, match_start: int, scan_line: str | None = None,
) -> bool:
    """True when a script path at *match_start* is a command word.

    Shared by the `./path` and bare `tools/...` forms.  A command word
    sits at the start of the line, after a shell separator, after YAML
    `run:` (also the `- run:` list form), or after leading environment
    assignments or an execution wrapper (`env`, `exec`, `command`).
    Interpreter invocations (`bash script`) open the file as input and
    ordinary arguments (`echo path`) do not execute it, so neither
    requires the executable bit.
    """
    tokens = _segment_tokens(line, match_start, scan_line)
    if tokens is None:
        return False
    tokens = _strip_command_prefixes(tokens)
    if tokens is None:
        return False
    if not tokens:
        return True
    if tokens[0] in INTERPRETERS:
        # Only a shell `-c` makes the following token a command string;
        # `python3 -c "..."` leaves later paths as plain arguments.
        if tokens[-1] != "-c" or tokens[0] not in SHELL_INTERPRETERS:
            return False
        # `bash -c "cmd" arg`: once the command string closed, the next
        # path is a positional argument ($0), not an executed command.
        closed = line[:match_start].removesuffix("./").rstrip()
        return not closed.endswith(("'", '"'))
    if tokens[0] in EXEC_WRAPPERS:
        return _wrapper_resolves_to_path(tokens[0], tokens[1:])
    return False


def _segment_tokens(
    line: str, match_start: int, scan_line: str | None = None,
) -> list[str] | None:
    """Command tokens before a path, or None for a known data context.

    Quoted spans are blanked first so their operators cannot split the
    segment, and YAML list markers are rejected outright.
    """
    if _is_data_context(line, match_start):
        return None
    source = scan_line if scan_line is not None else line
    head = ESCAPED_SEPARATOR.sub("x", source[:match_start])
    head = QUOTED_SPAN_RE.sub(" ", head)
    segment = SEGMENT_SPLIT.split(head)[-1].strip()
    segment = segment.removesuffix("./")
    if segment == "-":
        return None
    if segment.startswith("- "):
        # A YAML step's single-line form: `- run: cmd`.
        segment = segment[2:].strip()
    if segment.startswith("run:"):
        segment = segment[4:].strip()
    return segment.split()


def _strip_command_prefixes(tokens: list[str]) -> list[str] | None:
    """Drop control keywords, options, assignments, and redirects.

    Returns None when the referenced path is itself a redirection
    operand (not an executed command).
    """
    while tokens and tokens[0] in CONTROL_PREFIXES:
        tokens = tokens[1:]
    while tokens and tokens[0].startswith("-") and len(tokens[0]) > 1:
        # Options directly after a control keyword (`time -p ./x`).
        tokens = tokens[1:]
    while tokens and ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    while tokens and REDIRECT_RE.match(tokens[0]):
        token = tokens[0]
        tokens = tokens[1:]
        if re.fullmatch(r"\d*[<>]+", token):
            # A bare redirection operator consumes the following token;
            # when that token is the referenced path, the path is the
            # redirect target, not an executed command.
            if tokens:
                tokens = tokens[1:]
            else:
                return None
    return tokens


# Wrapper options that consume the FOLLOWING token as an operand
# (`env -u NAME`, `env -C DIR`, `exec -a NAME`).  When such an option is
# the last token before the referenced path, the path is that operand,
# not an executed command.
OPERAND_OPTIONS = {
    "env": ("-u", "--unset", "-C", "--chdir"),
    "exec": ("-a",),
    "sudo": ("-u", "--user", "-g", "--group"),
    "nice": ("-n", "--adjustment"),
    "timeout": ("-k", "--kill-after", "-s", "--signal"),
    "xargs": (
        "-n", "--max-args", "-I", "--replace", "-P", "--max-procs",
        "-a", "--arg-file",
    ),
}

# Wrappers whose first positional token is an operand, not the command
# (`timeout 1 cmd`: the duration comes first).
POSITIONAL_OPERANDS = {"timeout": 1}


def _skip_wrapper_options(
    wrapper: str, rest: list[str],
) -> tuple[list[str], bool]:
    """Drop wrapper options and their operands from *rest*.

    Returns the remaining tokens and whether the referenced path itself
    occupies the last option's operand slot (in which case it is that
    option's argument, not an executed command).
    """
    operands = OPERAND_OPTIONS.get(wrapper, ())
    index = 0
    operand_is_path = False
    while index < len(rest):
        token = rest[index]
        if token == "--":
            # End-of-options: the next token is the command word.
            index += 1
            break
        if ASSIGNMENT.match(token):
            index += 1
            continue
        if not token.startswith("-") and not REDIRECT_RE.match(token):
            break
        index += 1
        if token in operands:
            if index >= len(rest):
                operand_is_path = True
                break
            index += 1
    return rest[index:], operand_is_path


def _wrapper_resolves_to_path(wrapper: str, rest: list[str]) -> bool:
    """True when *rest* still makes *wrapper* execute the referenced path."""
    if wrapper == "command" and rest[:1] in (["-v"], ["-V"]):
        # A `command -v` query does not execute the script.
        return False
    rest, operand_is_path = _skip_wrapper_options(wrapper, rest)
    skip = POSITIONAL_OPERANDS.get(wrapper, 0)
    rest = rest[skip:] if len(rest) >= skip else []
    if not rest:
        return not operand_is_path
    if rest[0] in INTERPRETERS:
        # A shell `-c` still makes the following token a command
        # string; other interpreter invocations open the file instead.
        return rest[-1] == "-c" and rest[0] in SHELL_INTERPRETERS
    if rest[0] in EXEC_WRAPPERS:
        return _wrapper_resolves_to_path(rest[0], rest[1:])
    # Any other command makes the path its argument.
    return False


EVAL_PREFIX = re.compile(
    r"(?:^|[\s;&|(])(?:eval|(?:bash|sh|zsh|dash)\s+-c)\s+$"
)
EVAL_STRING_RE = re.compile(r"(['\"])(.*?)\1")


SSQ_SPAN_RE = re.compile(r"'[^']*'")
SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _substitution_refs(line: str) -> list[str]:
    """References inside command substitutions that execute.

    `$()` and backticks run shell code inside double quotes and in bare
    segments; inside single quotes they are literal text and skipped.
    """
    refs: list[str] = []
    sq_masked = SSQ_SPAN_RE.sub(lambda m: chr(1) * len(m.group(0)), line)
    for match in SUBST_RE.finditer(line):
        if sq_masked[match.start()] == chr(1):
            continue  # inside single quotes: literal text
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        refs.extend(_line_refs(inner, False))
    return refs


def _eval_string_refs(line: str) -> list[str]:
    """References inside `eval "..."` / `bash -c "..."` command strings.

    The quoted string carries shell code, so its content is scanned like
    a command line instead of being treated as literal text.
    """
    refs: list[str] = []
    for match in EVAL_STRING_RE.finditer(line):
        head = line[: match.start()]
        if not EVAL_PREFIX.search(head):
            continue
        tokens = _strip_command_prefixes(
            _segment_tokens(head + "x", len(head))
            or []
        )
        if not (
            tokens == ["eval"]
            or (tokens and tokens[0] in SHELL_INTERPRETERS and tokens[1:] == ["-c"])
        ):
            continue
        refs.extend(_line_refs(match.group(2), False))
    return refs


def _quoted_ref(
    line: str, match: re.Match[str], scan_line: str | None = None,
) -> str | None:
    """The quoted path when a quoted command word executes it, else None.

    `"script"` at a command position executes the script after quote
    removal, while the same text as an argument or assignment value
    (``echo "path"``, ``X="path"``) does not.
    """
    quote_start = match.start(1)
    head = (scan_line if scan_line is not None else line)[:quote_start]
    if head and not head[-1].isspace() and head.rstrip().endswith("="):
        return None
    if head.rstrip().endswith("-"):
        # A YAML list marker (`- "path"`), not a command word.
        return None
    if not _command_word_at(line, quote_start, scan_line):
        return None
    return match.group(2).removeprefix("./")


def _line_refs(line: str, continuation: bool) -> list[str]:
    """Extract every executable-style script reference in one line.

    A path on a continued line is an argument (for example a pytest
    target), not a command word: the executable bit is not required.
    """
    masked = QUOTED_SPAN_RE.sub(lambda m: chr(1) * len(m.group(0)), line)

    def inside_quoted_span(pos: int) -> bool:
        return pos < len(line) and masked[pos] == chr(1)

    refs = [
        match.group(1)
        for match in DIRECT_REF.finditer(line)
        if not inside_quoted_span(match.start(1))
        and _command_word_at(line, match.start(1), masked)
    ]
    refs.extend(
        quoted
        for match in QUOTED_REF.finditer(line)
        if (quoted := _quoted_ref(line, match, masked)) is not None
    )
    refs.extend(_eval_string_refs(line))
    refs.extend(_substitution_refs(line))
    if continuation:
        return refs
    refs.extend(
        match.group(1)
        for match in BARE_REF.finditer(line)
        if not inside_quoted_span(match.start(1))
        and _command_word_at(line, match.start(1), masked)
    )
    return refs


def _reference_lines(path: Path) -> list[tuple[str, int]]:
    """Yield (script, line_number) for every executable reference in *path*."""
    refs: list[tuple[str, int]] = []
    continuation = False
    interp_chain = False
    lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        is_continuation = continuation
        continuation = stripped.endswith("\\")
        if not stripped or stripped.startswith("#"):
            continue
        content = _strip_make_prefixes(stripped)
        masked = QUOTED_SPAN_RE.sub(lambda m: " " * len(m.group(0)), content)
        segments = SEGMENT_SPLIT.split(masked)
        last_tokens = segments[-1].strip().split()
        if len(segments) > 1:
            # A separator starts a fresh command segment.
            interp_chain = False
        if not is_continuation:
            # A new logical command: is it an interpreter invocation
            # whose later lines are arguments (pytest-style)?
            chain_tokens = _strip_command_prefixes(list(last_tokens))
            interp_chain = bool(chain_tokens) and (
                chain_tokens[0] in INTERPRETERS
                or (
                    chain_tokens[0] in EXEC_WRAPPERS
                    and chain_tokens[1:2]
                    and chain_tokens[1] in INTERPRETERS
                )
            )
        refs.extend(
            (script, number)
            for script in _line_refs(content, is_continuation and interp_chain)
        )
    return refs


def _strip_make_prefixes(line: str) -> str:
    """Drop Make recipe prefixes that precede the command word.

    `@cmd`, `+cmd`, and `-cmd` all execute *cmd*; a YAML list marker is
    `- cmd` (with a space) and is left for the data-context guard.
    """
    if line.startswith(("@", "+")):
        return line.lstrip("@+-")
    if line.startswith("-") and len(line) > 1 and not line[1].isspace():
        return line[1:].lstrip("@+-")
    return line


def _index_modes(paths: list[str]) -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-files", "-s", "--"] + paths,
        capture_output=True,
        text=True,
        check=False,
    )
    modes: dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            modes[parts[3]] = parts[0]
    return modes


def _collect_refs(root: Path) -> dict[str, list[str]]:
    wanted: dict[str, list[str]] = {}
    sources = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    makefile = root / "Makefile"
    if makefile.exists():
        sources.append(makefile)
    for source in sources:
        for script, number in _reference_lines(source):
            wanted.setdefault(script, []).append(f"{source.relative_to(root)}:{number}")
    return wanted


def _violations(wanted: dict[str, list[str]]) -> list[tuple[str, str, list[str]]]:
    modes = _index_modes(sorted(wanted))
    found = []
    for script in sorted(wanted):
        mode = modes.get(script)
        if mode != "100644" or not SCRIPT_SUFFIX.search(script):
            # untracked, already executable, or a special entry: not this
            # gate's concern.
            continue
        found.append((script, mode, wanted[script]))
    return found


def _report(violations: list[tuple[str, str, list[str]]]) -> int:
    for script, mode, refs in violations:
        print(f"ERROR: {script} is invoked as an executable but tracked with mode {mode}")
        for ref in refs:
            print(f"       referenced at {ref}")
        print(f"       Fix: chmod +x {script} && git add {script}")
    return 1


def main() -> int:
    wanted = _collect_refs(Path.cwd())
    if not wanted:
        print("OK: no executable script references found")
        return 0
    violations = _violations(wanted)
    if violations:
        return _report(violations)
    print(f"OK: {len(wanted)} executable script reference(s) have the executable bit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
