#!/usr/bin/env python3
"""Validate that a tag is accompanied by finalized release metadata."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def validate_release_metadata(repo_root: Path, version: str) -> list[str]:
    """Return release metadata errors for ``version``.

    The release gate uses this validator only after a tag is created.  The
    corresponding pre-release documentation gate accepts an ``Unreleased`` /
    release-candidate state; this validator requires the final state so a tag
    cannot publish documentation that still describes a pending release.
    """
    changelog_path = repo_root / "CHANGELOG.md"
    project_status_path = repo_root / "docs" / "project" / "PROJECT_STATUS.md"
    release_notes_path = repo_root / "docs" / "release" / f"{version}-release-notes.md"

    changelog = changelog_path.read_text(encoding="utf-8", errors="ignore")
    project_status = project_status_path.read_text(
        encoding="utf-8", errors="ignore"
    )
    release_notes = release_notes_path.read_text(
        encoding="utf-8", errors="ignore"
    )
    errors: list[str] = []

    changelog_match = re.search(
        rf"^## \[{re.escape(version)}\] - "
        rf"(?P<date>Unreleased|\d{{4}}-\d{{2}}-\d{{2}})\s*$",
        changelog,
        re.MULTILINE,
    )
    if changelog_match is None:
        errors.append(f"{changelog_path}: missing release heading for {version}")
        release_date = None
    else:
        release_date = changelog_match.group("date")
        if release_date == "Unreleased":
            errors.append(f"{changelog_path}: {version} is still Unreleased")

    project_match = re.search(
        rf"^### Current Release Line {re.escape(version)}\s*$"
        rf"(?P<body>.*?)(?=^### |\Z)",
        project_status,
        re.MULTILINE | re.DOTALL,
    )
    if project_match is None:
        errors.append(f"{project_status_path}: missing current release line {version}")
    elif not re.search(r"\bstable release\b", project_match.group("body"), re.I):
        errors.append(f"{project_status_path}: {version} is not marked stable")

    notes_date_match = re.search(r"^\*\*Date\*\*:\s*(.+?)\s*$", release_notes, re.M)
    notes_status_match = re.search(
        r"^\*\*Status\*\*:\s*(.+?)\s*$", release_notes, re.M
    )
    if notes_date_match is None:
        errors.append(f"{release_notes_path}: missing release date")
    elif release_date is not None and notes_date_match.group(1) != release_date:
        errors.append(
            f"{release_notes_path}: date {notes_date_match.group(1)!r} "
            f"does not match CHANGELOG date {release_date!r}"
        )
    if notes_status_match is None:
        errors.append(f"{release_notes_path}: missing release status")
    elif not re.fullmatch(r"Stable release", notes_status_match.group(1), re.I):
        errors.append(f"{release_notes_path}: {version} is not marked Stable release")

    return errors


def main() -> int:
    """Parse command-line arguments and validate the current repository."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        required=True,
        help="Release version without a v prefix",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Repository root (default: detected from this script)",
    )
    args = parser.parse_args()
    errors = validate_release_metadata(args.repo_root.resolve(), args.version)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Release metadata for {args.version} is finalized and consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
