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
DIRECT_REF = re.compile(r"(?:^|[\s;&|(])\./([A-Za-z0-9_./-]+\.(?:sh|py))(?=[\s;&|)]|$)")
BARE_REF = re.compile(
    r"(?:^|[\s;&|(])([A-Za-z0-9_-]+/(?:[A-Za-z0-9_./-]+)\.(?:sh|py))(?=[\s;&|)]|$)"
)
# bash/sh/... open the script as input and work without the bit; the
# wrappers run the script itself, so they keep the strict requirement.
INTERPRETERS = ("bash", "sh", "zsh", "dash", "python", "python3")
EXEC_WRAPPERS = ("env", "exec", "command")
CONTROL_PREFIXES = ("if", "then", "elif", "while", "until", "do", "else", "!")
SEGMENT_SPLIT = re.compile(r"(?:;|&&|\|\||\||\()")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _command_word_at(line: str, match_start: int) -> bool:
    """True when a script path at *match_start* is a command word.

    Shared by the `./path` and bare `tools/...` forms.  A command word
    sits at the start of the line, after a shell separator, after YAML
    `run:` (also the `- run:` list form), or after leading environment
    assignments or an execution wrapper (`env`, `exec`, `command`).
    Interpreter invocations (`bash script`) open the file as input and
    ordinary arguments (`echo path`) do not execute it, so neither
    requires the executable bit.
    """
    segment = SEGMENT_SPLIT.split(line[:match_start])[-1].strip()
    segment = segment.removesuffix("./")
    segment = segment.removeprefix("- ").strip()
    if segment.startswith("run:"):
        segment = segment[4:].strip()
    tokens = segment.split()
    while tokens and tokens[0] in CONTROL_PREFIXES:
        tokens = tokens[1:]
    while tokens and ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return True
    if tokens[0] in INTERPRETERS:
        return False
    if tokens[0] in EXEC_WRAPPERS:
        return _wrapper_resolves_to_path(tokens[0], tokens[1:])
    return False


def _wrapper_resolves_to_path(wrapper: str, rest: list[str]) -> bool:
    """True when *rest* still makes *wrapper* execute the referenced path."""
    if wrapper == "command" and rest[:1] in (["-v"], ["-V"]):
        # A `command -v` query does not execute the script.
        return False
    index = 0
    consumed_argument = False
    while index < len(rest) and (
        rest[index].startswith("-") or ASSIGNMENT.match(rest[index])
    ):
        if wrapper == "exec" and rest[index] == "-a":
            # `exec -a NAME cmd`: the next token is NAME, so a path
            # here is the wrapper's argument, not a command.
            consumed_argument = True
        index += 1
    rest = rest[index:]
    if not rest:
        return not consumed_argument
    # Only a trailing execution wrapper still resolves to the script;
    # an interpreter or any other command makes the path its argument.
    return rest[0] in EXEC_WRAPPERS


def _line_refs(line: str, continuation: bool) -> list[str]:
    """Extract every executable-style script reference in one line.

    A path on a continued line is an argument (for example a pytest
    target), not a command word: the executable bit is not required.
    """
    refs = [
        match.group(1)
        for match in DIRECT_REF.finditer(line)
        if _command_word_at(line, match.start(1))
    ]
    if continuation:
        return refs
    refs.extend(
        match.group(1)
        for match in BARE_REF.finditer(line)
        if _command_word_at(line, match.start(1))
    )
    return refs


def _reference_lines(path: Path) -> list[tuple[str, int]]:
    """Yield (script, line_number) for every executable reference in *path*."""
    refs: list[tuple[str, int]] = []
    continuation = False
    lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        is_continuation = continuation
        continuation = stripped.endswith("\\")
        if stripped and not stripped.startswith("#"):
            refs.extend((script, number) for script in _line_refs(stripped, is_continuation))
    return refs


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
