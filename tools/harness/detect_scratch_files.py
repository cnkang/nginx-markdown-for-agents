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
match) exempt intentional files; justification is mandatory.

Usage:
    python3 tools/harness/detect_scratch_files.py [--staged]

Exit codes: 0 clean, 1 violations found.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

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


def tracked_files() -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"ERROR git ls-files failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"ERROR git ls-files failed: {result.stderr}", file=sys.stderr)
        return None
    return [f for f in result.stdout.split("\0") if f]


def staged_files() -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only",
             "--diff-filter=ACR", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"ERROR git diff failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"ERROR git diff failed: {result.stderr}", file=sys.stderr)
        return None
    return [f for f in result.stdout.split("\0") if f]


def is_allowlisted(path):
    for entry in ALLOWLIST:
        parts = entry.split(":", 1)
        if len(parts) < 2 or len(parts[1].strip()) < 5:
            continue
        # Anchor to the FULL repo-relative path, not a substring: a bare
        # "build.sh" entry would otherwise exempt rebuild.sh / prebuild.sh
        # or any nested script whose name contains the anchor.
        if parts[0] and path == parts[0]:
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

    files = staged_files() if args.staged else tracked_files()
    if files is None:
        return 1
    findings = []
    for path in files:
        reason = classify(path)
        if reason and not is_allowlisted(path):
            findings.append((path, reason))

    for path, reason in findings:
        print(f"VIOLATION {path}: {reason}", file=sys.stderr)
    print(f"=== scratch-file check ({'staged' if args.staged else 'tracked'}): "
          f"{len(files)} file(s), {len(findings)} violation(s) ===",
          file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
