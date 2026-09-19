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

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path  # noqa: E402

SCAN_SUFFIXES = (".yml", ".yaml")
SHELL_GLOBS = (
    "*.sh",
    "tools/**/*.sh",
    "packaging/**/*.sh",
    "scripts/**/*.sh",
    "tests/**/*.sh",
    "examples/**/*.sh",
    "components/**/*.sh",
    ".clusterfuzzlite/**/*.sh",
)

# Single tokens that are shell keywords, syntax, or no-argument commands.  A
# bare sibling command in this set loses nothing when a comment ends the
# command, so reporting it as a swallowed argument would be a false positive.
# Any other single token stays ambiguous and fails closed.
_SINGLE_TOKEN_COMMANDS = frozenset(
    {
        "true",
        "false",
        ":",
        "echo",
        "exit",
        "return",
        "break",
        "continue",
        "fi",
        "done",
        "esac",
        "}",
        "then",
        "else",
        "elif",
        "do",
        ";;",
    }
)


def _ends_with_continuation(line: str) -> bool:
    """True when the line ends with an active backslash continuation.

    A comment line never continues, even when it ends with a backslash: the
    interpreter stops reading at the `#`, so the backslash is part of the text.
    An even number of trailing backslashes is an escaped backslash rather than a
    continuation, so the count decides.
    """
    if _is_comment(line):
        return False
    if not line.endswith("\\"):
        # A backslash followed by whitespace escapes that whitespace instead of
        # continuing the line, so the line has to be judged as written.
        return False
    trailing = len(line) - len(line.rstrip("\\"))
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
        if _indent(candidate) > _indent(lines[start]):
            return True
        # A same-indent option or value is still very likely to be an argument
        # to the continued command.  Sibling commands in a function commonly
        # start with a verb (``echo``, ``make``), so keep this heuristic narrow
        # enough to avoid turning those into false positives.
        if _indent(candidate) == _indent(lines[start]) and _looks_like_argument(
            candidate
        ):
            return True
        return False
    return False


def _looks_like_argument(line: str) -> bool:
    """Return whether a same-indent line has the shape of a command argument.

    A bare single token is ambiguous: it is either the positional value the
    comment swallowed (an image name such as ``alpine``) or a bare sibling
    command, so the check fails closed instead of guessing -- except for the
    small set of shell keywords and no-argument commands whose bare form is
    never a positional value.  A multi-word line still reads as a sibling
    command (``echo``, ``make``) unless its first token carries an argument
    marker.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("-", "$", "'", '"', "`")):
        return True
    words = stripped.split()
    if len(words) == 1:
        return stripped not in _SINGLE_TOKEN_COMMANDS
    first = words[0]
    return any(marker in first for marker in ("/", ":", "="))


def _workflow_runs(path: Path) -> list[tuple[str, str]]:
    """Return (name, script) for every `run:` script in a workflow.

    Reading failures propagate: a workflow this cannot parse is not a workflow
    without `run:` steps, and treating it as one would skip its commands.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("workflow document must be a mapping")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError("workflow jobs must be a mapping")
    found: list[tuple[str, str]] = []
    for name, job in jobs.items():
        found.extend(_workflow_job_runs(name, job))
    return found


def _workflow_job_runs(name: object, job: object) -> list[tuple[str, str]]:
    """Validate one workflow job and return its shell run blocks."""
    if not isinstance(job, dict):
        raise ValueError(f"job {name!r} must be a mapping")
    if "steps" not in job:
        # Reusable-workflow jobs use ``uses`` instead of ``steps`` and have no
        # shell text for this detector to inspect.
        if isinstance(job.get("uses"), str) and job["uses"].strip():
            return []
        raise ValueError(f"job {name!r} has no steps or reusable workflow")
    steps = job["steps"]
    if not isinstance(steps, list):
        raise ValueError(f"job {name!r} steps must be a list")
    found: list[tuple[str, str]] = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"job {name!r} step {index} must be a mapping")
        if "run" in step and not isinstance(step["run"], str):
            raise ValueError(f"job {name!r} step {index} run must be a string")
        run = step.get("run")
        if isinstance(run, str):
            found.append((f"{name} step {index}", run))
    return found


def _tracked_paths(root: Path) -> list[str] | None:
    """Return the tracked files under `root`, or None for a non-Git root.

    `git ls-files -z` output is NUL-delimited, so entries survive names with
    spaces or quoting; the bytes are decoded with the filesystem codec and
    surrogate escapes, matching the repository's NUL-safe scan convention.
    The scan runs from the root directory instead of passing it as a command
    argument, so a root that looks like an option is never read as one.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [os.fsdecode(entry) for entry in result.stdout.split(b"\0") if entry]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate one configured glob to the path shape `Path.glob` matches.

    The configured patterns only use `*` segments and `**` directory spans;
    `*` stays inside one path component, while `**/` spans zero or more and
    already carries the separator that follows it.
    """
    parts: list[str] = []
    previous_spanned = False
    for index, segment in enumerate(pattern.split("/")):
        if index and not previous_spanned:
            parts.append("/")
        if segment == "**":
            parts.append("(?:[^/]+/)*")
            previous_spanned = True
        else:
            parts.append(re.escape(segment).replace(r"\*", "[^/]*"))
            previous_spanned = False
    return re.compile("^" + "".join(parts) + "$")


def _glob_shell_scripts(root: Path) -> list[Path]:
    """Return the shell scripts a checkout-level glob finds under root."""
    found: set[Path] = set()
    for pattern in SHELL_GLOBS:
        found.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found)


def _tracked_shell_scripts(root: Path, tracked: list[str]) -> list[Path]:
    """Return the tracked paths a configured shell glob selects."""
    patterns = [_glob_to_regex(pattern) for pattern in SHELL_GLOBS]
    selected: set[Path] = set()
    for relative in tracked:
        if not any(pattern.match(relative) for pattern in patterns):
            continue
        path = root / relative
        if path.is_file():
            selected.add(path)
    return sorted(selected)


def _shell_scripts(root: Path) -> list[Path]:
    """Return the tracked shell scripts this check reads.

    Discovery prefers `git ls-files` so a scratch file that is not committed
    cannot join the blocking scan, and the configured globs filter those paths
    the way a checkout-level glob would.  A root that is not a Git work tree —
    the explicit fixture mode used by tests — keeps glob discovery so fixture
    scripts stay visible without a repository.
    """
    tracked = _tracked_paths(root)
    if tracked is None:
        return _glob_shell_scripts(root)
    return _tracked_shell_scripts(root, tracked)


def _workflow_file_errors(path: Path, root: Path) -> list[str]:
    """Return continuation errors found in one workflow file."""
    try:
        runs = _workflow_runs(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # Its commands are unknown rather than absent, so this is reported once
        # for the workflow instead of being skipped.
        return [f"{path.relative_to(root)}: cannot be read: {exc}"]
    errors: list[str] = []
    for name, script in runs:
        for line in scan_shell_text(script):
            errors.append(
                f"{path.relative_to(root)} ({name}): a comment on a continued "
                f"line swallows the arguments after it (line {line} of the "
                f"script)"
            )
    return errors


def _workflow_errors(root: Path, workflow_dir: Path) -> list[str]:
    """Return continuation errors found in workflow run blocks."""
    if not workflow_dir.exists():
        # The workflow scripts are unknown rather than absent; a scan without
        # them would silently skip every `run:` block.
        return [f"{workflow_dir.relative_to(root)}: workflow directory is missing"]
    if not workflow_dir.is_dir():
        return [f"{workflow_dir.relative_to(root)}: workflow path is not a directory"]
    errors: list[str] = []
    for path in sorted(workflow_dir.glob("*.y*ml")):
        errors.extend(_workflow_file_errors(path, root))
    return errors


def _shell_file_errors(path: Path, root: Path) -> list[str]:
    """Return continuation errors found in one shell script."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Its commands were not read, so the scan is incomplete here.
        return [f"{path.relative_to(root)}: cannot be read: {exc}"]
    return [
        f"{path.relative_to(root)}: a comment on a continued line "
        f"swallows the arguments after it (line {line})"
        for line in scan_shell_text(text)
    ]


def collect_errors(root: Path) -> list[str]:
    """Return a message for every comment a continuation swallows."""
    if not root.is_dir():
        return [f"{root}: scan root is missing or not a directory"]
    errors = _workflow_errors(root, root / ".github/workflows")
    for path in _shell_scripts(root):
        errors.extend(_shell_file_errors(path, root))
    return errors


def main(argv: list[str]) -> int:
    """Report every comment a line continuation turns into code."""
    if len(argv) > 1:
        try:
            root = Path(
                validate_read_path(
                    argv[1], must_exist=False, purpose="scan root"
                )
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        root = Path(".")
    errors = collect_errors(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: no comment is swallowed by a line continuation")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
