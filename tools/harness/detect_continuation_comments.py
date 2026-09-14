#!/usr/bin/env python3
"""Refuse a comment that a line continuation turns into code.

A shell joins a line ending in a backslash to the next one before it looks for
comments, so

    docker run --rm \\
      # a note someone added here
      rust:1.98.1 sh /src/build.sh

reaches the interpreter as `docker run --rm # a note ... rust:1.98.1 sh ...`:
everything after the `#` is a comment, the flags that follow are never passed,
and the command ends with an unexpected argument.  `bash -n` accepts it, because
the syntax is valid; only the arguments disappear.

The check reports the pattern that cannot be intentional: a continuation line
followed by a comment, with more content still to come in the same block.  A
comment that ends a command is left alone, since nothing is lost there.

Usage:
    python3 tools/harness/detect_continuation_comments.py [root]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

SCAN_SUFFIXES = (".yml", ".yaml")
SHELL_GLOBS = ("tools/**/*.sh", "packaging/**/*.sh", "scripts/**/*.sh")


def _ends_with_continuation(line: str) -> bool:
    """True when the line ends with an active backslash continuation.

    A comment line never continues, even when it ends with a backslash: the
    interpreter stops reading at the `#`, so the backslash is part of the text.
    An even number of trailing backslashes is an escaped backslash rather than a
    continuation, so the count decides.
    """
    if _is_comment(line):
        return False
    stripped = line.rstrip()
    if not stripped.endswith("\\"):
        return False
    trailing = len(stripped) - len(stripped.rstrip("\\"))
    return trailing % 2 == 1


def _is_comment(line: str) -> bool:
    """True when the line is a comment once the shell strips its indentation."""
    return line.lstrip().startswith("#")


def scan_shell_text(text: str) -> list[int]:
    """Return the line numbers of comments swallowed by a continuation."""
    lines = text.splitlines()
    findings: list[int] = []
    for index, line in enumerate(lines):
        if not _ends_with_continuation(line):
            continue
        comment = index + 1
        if comment >= len(lines) or not _is_comment(lines[comment]):
            continue
        if _command_continues_after(lines, index, comment):
            findings.append(comment + 1)
    return findings


def _indent(line: str) -> int:
    """Return how far a line's content is indented."""
    return len(line) - len(line.lstrip())


def _continuation_start(lines: list[str], comment: int) -> int:
    """Return the first line of the continuation run that ends at the comment.

    A command may span several continued lines, and each of them is indented
    more deeply than the first.  Walking back to where the run begins gives the
    indentation the whole command is compared against.
    """
    start = comment - 1
    while start > 0 and _ends_with_continuation(lines[start - 1]):
        start -= 1
    return start


def _command_continues_after(lines: list[str], opened: int, comment: int) -> bool:
    """True when the command still has content after the comment line.

    Only the rest of this command can be lost.  The command ends where the
    indentation drops to the level the command starts at, which is not always
    column one: inside a function or a `run:` block every line is indented, and
    an unrelated command there would be a false report.
    """
    _ = opened
    start = _continuation_start(lines, comment)
    for candidate in lines[comment + 1 :]:
        if not candidate.strip():
            continue
        return _indent(candidate) > _indent(lines[start])
    return False


def _workflow_runs(path: Path) -> list[tuple[str, str]]:
    """Return (name, script) for every `run:` script in a workflow."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(document, dict):
        return []
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    found: list[tuple[str, str]] = []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or [], 1):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                found.append((f"{name} step {index}", step["run"]))
    return found


def _shell_scripts(root: Path) -> list[Path]:
    """Return the tracked shell scripts this check reads."""
    found: list[Path] = []
    for pattern in SHELL_GLOBS:
        found.extend(sorted(root.glob(pattern)))
    return found


def collect_errors(root: Path) -> list[str]:
    """Return a message for every comment a continuation swallows."""
    errors: list[str] = []
    workflow_dir = root / ".github/workflows"
    for path in sorted(workflow_dir.glob("*.y*ml")):
        for name, script in _workflow_runs(path):
            for line in scan_shell_text(script):
                errors.append(
                    f"{path.relative_to(root)} ({name}): a comment on a continued "
                    f"line swallows the arguments after it (line {line} of the "
                    f"script)"
                )
    for path in _shell_scripts(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in scan_shell_text(text):
            errors.append(
                f"{path.relative_to(root)}: a comment on a continued line "
                f"swallows the arguments after it (line {line})"
            )
    return errors


def main(argv: list[str]) -> int:
    """Report every comment a line continuation turns into code."""
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    errors = collect_errors(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: no comment is swallowed by a line continuation")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
