#!/usr/bin/env python3
"""
detect_scratch_files.py — Rule 70 (build-safety).

One-off scratch files (analysis scripts, PR drafts, editor/system junk)
must never enter functional commits.  The 0.9.2 pre-freeze window
committed five CodeRabbit-digest helper scripts and a PR body draft
(0e32598a, 8df10b9c) that remained tracked at HEAD; no gate noticed.

Signals checked:
  1. one-off scripts at the repository root (all module tooling lives
     under tools/; a root-level *.py/*.sh is almost always scratch);
  2. scratch-named files anywhere: pr_body*, notes/todo/draft/scratch/
     tmp/temp prefixes, digest/process helpers;
  3. editor and system junk: *.bak, *.orig, *.rej, *~, .DS_Store,
     Thumbs.db, *.swp.

Modes:
  (default)      audit all git-tracked files
  --staged       audit only staged additions/copies/renames (pre-commit)

Allowlist entries ("path:justification", exact repository-relative path
match) exempt intentional files; justification is mandatory.  Entries are
partitioned before use: well-formed entries are merged into the effective
allowlist, and a malformed entry is reported as a configuration error so the
run fails closed instead of silently dropping the exemption or the audit.

Git output is consumed as raw bytes split on NUL, because path bytes are not
guaranteed to be valid UTF-8; entries decode with the filesystem codec and
surrogate escapes, the repository's NUL-safe scan convention.

Usage:
    python3 tools/harness/detect_scratch_files.py [--staged]

Exit codes: 0 clean, 1 violations or allowlist configuration errors.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)

# Basename patterns (case-insensitive, full match) that indicate scratch.
SCRATCH_BASENAME_RE = re.compile(
    r"^(?:pr_body|notes?|todo|draft|scratch|tmp|temp)(?:[._-].*)?$",
    re.IGNORECASE,
)

# Editor / system junk suffixes or exact names.
JUNK_RE = re.compile(
    r"(\.(bak|orig|rej|swp|pyc)$|~$|"
    r"^(\.DS_Store|Thumbs\.db)$)",
    re.IGNORECASE,
)

# Root-level one-off script extensions.
ROOT_SCRIPT_RE = re.compile(r"\.(py|sh)$", re.IGNORECASE)

# "path:justification" — exact repository-relative path match,
# justification mandatory.
ALLOWLIST = [
    "build.sh:ClusterFuzzLite/OSS-Fuzz requires build.sh at the repository "
    "root as its container entrypoint (fuzz-infrastructure rules)",
]


def _decode_stream(value: bytes | str) -> str:
    """Return a text form of a captured subprocess stream.

    The scan requests byte streams, where names survive as raw bytes; a
    text-mode value still decodes defensively so a caller substitution cannot
    raise from the failure path.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _list_paths(git_args: list[str], failure_label: str) -> list[str] | None:
    """Return NUL-delimited git path output as decoded entries, else None.

    The bytes of `git ls-files -z` / `git diff -z` are not guaranteed to be
    valid UTF-8, so the stream is read as bytes, split on NUL, and decoded
    with the filesystem codec plus surrogate escapes.  This keeps every
    tracked name (spaces, quoting, non-UTF-8 bytes) in the scan scope instead
    of raising an uncaught UnicodeDecodeError from `text=True`.
    """
    git = resolve_approved_executable("git")
    if git is None:
        print("ERROR approved git executable is unavailable", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            [git, *git_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=False,
        )
    except (OSError, UnicodeError) as exc:
        print(f"ERROR {failure_label} failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"ERROR {failure_label} failed: {_decode_stream(result.stderr)}",
            file=sys.stderr,
        )
        return None
    return [
        os.fsdecode(entry)
        for entry in result.stdout.split(b"\0")
        if entry
    ]


def tracked_files() -> list[str] | None:
    return _list_paths(["ls-files", "-z"], "git ls-files")


def staged_files() -> list[str] | None:
    return _list_paths(
        ["diff", "--cached", "--name-only", "--diff-filter=ACR", "-z"],
        "git diff",
    )


def partition_allowlist(
    entries: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split allowlist entries into accepted entries and format errors.

    An entry is `path:justification` with a non-empty path and a
    justification of at least five characters.  Each malformed entry is
    returned as a readable error so the caller can fail closed: dropping the
    entry silently would remove an exemption an operator believes is active.
    """
    source = ALLOWLIST if entries is None else entries
    accepted: list[str] = []
    errors: list[str] = []
    for entry in source:
        path, separator, justification = entry.partition(":")
        if not separator or not path or len(justification.strip()) < 5:
            errors.append(
                f"allowlist entry {entry!r} is malformed: expected "
                "'path:justification' with a justification of at least "
                "five characters"
            )
            continue
        accepted.append(entry)
    return accepted, errors


def is_allowlisted(path, entries: list[str] | None = None) -> bool:
    """Return whether a full repo-relative path carries a valid exemption."""
    accepted, _ = partition_allowlist(entries)
    for entry in accepted:
        anchor = entry.split(":", 1)[0]
        # Anchor to the FULL repo-relative path, not a substring: a bare
        # "build.sh" entry would otherwise exempt rebuild.sh / prebuild.sh
        # or any nested script whose name contains the anchor.
        if path == anchor:
            return True
    return False


def classify(path):
    """Return a violation reason for a repo-relative path, else None."""
    name = Path(path).name
    if JUNK_RE.search(name):
        return "editor/system junk"
    # The parse/process/digest prefix signals one-off analysis scripts;
    # test sources legitimately use parse_* names, so restrict this rule
    # to non-test script/doc files.
    test_like = (
        "_test." in name
        or name.startswith("test_")
        or "/tests/" in f"/{path}"
    )
    if (
        not test_like
        and re.search(r"\.(py|sh|md)$", name, re.IGNORECASE)
        and re.match(r"^(parse|process|digest)[_-]", name, re.IGNORECASE)
    ):
        return "one-off analysis script/doc"
    if SCRATCH_BASENAME_RE.match(name) and not test_like:
        return "scratch-named file"
    if "/" not in path and ROOT_SCRIPT_RE.search(name):
        return "one-off script at repository root"
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Rule 70 scratch-file hygiene check"
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="check only staged additions/copies/renames (pre-commit mode)",
    )
    args = parser.parse_args()

    # Fail closed on a malformed allowlist: a dropped entry silently disables
    # an exemption (or hides an audit), so report it instead of continuing.
    accepted_allowlist, allowlist_errors = partition_allowlist()
    for error in allowlist_errors:
        print(f"ERROR {error}", file=sys.stderr)

    files = staged_files() if args.staged else tracked_files()
    if files is None:
        return 1
    findings = []
    for path in files:
        reason = classify(path)
        if reason and not is_allowlisted(path, accepted_allowlist):
            findings.append((path, reason))

    for path, reason in findings:
        print(f"VIOLATION {path}: {reason}", file=sys.stderr)
    print(f"=== scratch-file check ({'staged' if args.staged else 'tracked'}): "
          f"{len(files)} file(s), {len(findings)} violation(s), "
          f"{len(allowlist_errors)} allowlist error(s) ===",
          file=sys.stderr)
    return 1 if findings or allowlist_errors else 0


if __name__ == "__main__":
    sys.exit(main())
