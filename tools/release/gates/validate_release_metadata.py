#!/usr/bin/env python3
"""Validate that a tag is accompanied by finalized release metadata."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# Repository-relative directory holding the per-version release notes.
RELEASE_NOTES_DIR = ("docs", "releases")


def _validate_changelog(
    path: Path,
    version: str,
    changelog: str,
) -> tuple[list[str], str | None]:
    """Validate the version heading and return its release date."""
    errors: list[str] = []
    changelog_match = re.search(
        rf"^## \[{re.escape(version)}\] - "
        rf"(?P<date>Unreleased|\d{{4}}-\d{{2}}-\d{{2}})\s*$",
        "\n".join(_unfenced_lines(changelog)),
        re.MULTILINE,
    )
    if changelog_match is None:
        errors.append(f"{path}: missing release heading for {version}")
        release_date = None
    else:
        release_date = changelog_match.group("date")
        if release_date == "Unreleased":
            errors.append(f"{path}: {version} is still Unreleased")
        else:
            try:
                date.fromisoformat(release_date)
            except ValueError:
                errors.append(
                    f"{path}: invalid release date {release_date!r} for {version}"
                )
    return errors, release_date


def _validate_project_status(
    path: Path,
    version: str,
    text: str,
    release_date: str | None = None,
) -> list[str]:
    """Validate the current release line in project status documentation.

    The status field must start with ``Stable release`` so negated wording
    cannot pass, and the publication date must appear in the section.
    """
    project_match = re.search(
        rf"^### Current Release Line {re.escape(version)}\s*$"
        rf"(?P<body>.*?)(?=^### |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if project_match is None:
        return [f"{path}: missing current release line {version}"]
    body = project_match.group("body")
    status = re.search(r"^\*\*Status:\*\*(?P<value>.*)$", body, re.MULTILINE)
    if status is None:
        return [f"{path}: missing **Status:** line for {version}"]
    value_text = " ".join(status.group("value").split())
    first_sentence = re.split(r"[.;,]", value_text, maxsplit=1)[0].strip().casefold()
    non_final = re.search(
        r"\b(?:candidate|pending|unreleased|unpublished)\b"
        r"|\bnot\s+(?:yet\s+)?(?:published|released)\b",
        value_text,
        re.IGNORECASE,
    )
    if first_sentence != "stable release" or non_final is not None:
        return [f"{path}: {version} is not marked stable"]
    if release_date is not None and release_date not in body:
        return [f"{path}: publication date {release_date} missing from the {version} section"]
    return []


def _fence_token(line: str) -> tuple[str, int, str] | None:
    """Return (char, run length, trailing text) for a fence-run line."""
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate.startswith(("```", "~~~")):
        return None
    char = candidate[0]
    run = len(candidate) - len(candidate.lstrip(char))
    return char, run, candidate[run:]


def _unfenced_lines(text: str) -> list[str]:
    """Return prose lines only; fenced examples cannot satisfy metadata checks.

    A closing fence must use the same character, be at least as long as the
    fence it closes, and carry no trailing text.
    """
    lines: list[str] = []
    open_fence: tuple[str, int] | None = None
    for line in text.splitlines():
        token = _fence_token(line)
        if token is None:
            if open_fence is None:
                lines.append(line)
            continue
        char, run, rest = token
        if open_fence is None:
            # A backtick info string cannot contain a backtick.
            if char == "`" and "`" in rest:
                lines.append(line)
            else:
                open_fence = (char, run)
        elif open_fence[0] == char and run >= open_fence[1] and rest.strip() == "":
            open_fence = None
    return lines


def _release_note_field(text: str, field: str) -> str | None:
    """Return a markdown release-note field without a backtracking regex."""
    prefix = f"**{field}**:"
    for line in _unfenced_lines(text):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _validate_release_notes(
    path: Path,
    version: str,
    release_date: str | None,
    text: str,
) -> list[str]:
    """Validate release-note date and final status fields."""
    errors: list[str] = []
    unfenced = "\n".join(_unfenced_lines(text))
    if re.search(rf"^# Release Notes: {re.escape(version)}\s*$", unfenced, re.MULTILINE) is None:
        errors.append(f"{path}: missing '# Release Notes: {version}' title")
    notes_date = _release_note_field(text, "Date")
    notes_status = _release_note_field(text, "Status")
    if notes_date is None:
        errors.append(f"{path}: missing release date")
    else:
        if release_date is not None and notes_date != release_date:
            errors.append(
                f"{path}: date {notes_date!r} "
                f"does not match CHANGELOG date {release_date!r}"
            )
    if notes_status is None:
        errors.append(f"{path}: missing release status")
    elif notes_status.casefold() != "stable release":
        errors.append(f"{path}: {version} is not marked Stable release")
    return errors


def validate_release_metadata(repo_root: Path, version: str) -> list[str]:
    """Return release metadata errors for ``version``.

    The release gate uses this validator only after a tag is created.  The
    corresponding pre-release documentation gate accepts an ``Unreleased`` /
    release-candidate state; this validator requires the final state so a tag
    cannot publish documentation that still describes a pending release.
    """
    changelog_path = repo_root / "CHANGELOG.md"
    project_status_path = repo_root / "docs" / "project" / "PROJECT_STATUS.md"
    release_notes_path = (
        repo_root.joinpath(*RELEASE_NOTES_DIR) / f"{version}-release-notes.md"
    )
    metadata_paths = (changelog_path, project_status_path, release_notes_path)
    missing = [path for path in metadata_paths if not path.exists()]
    if missing:
        return [f"required release metadata file not found: {path}" for path in missing]

    changelog = changelog_path.read_text(encoding="utf-8", errors="ignore")
    project_status = project_status_path.read_text(encoding="utf-8", errors="ignore")
    release_notes = release_notes_path.read_text(encoding="utf-8", errors="ignore")
    errors, release_date = _validate_changelog(changelog_path, version, changelog)
    concrete_date = release_date if release_date != "Unreleased" else None
    errors.extend(
        _validate_project_status(
            project_status_path, version, project_status, concrete_date
        )
    )
    errors.extend(
        _validate_release_notes(release_notes_path, version, release_date, release_notes)
    )

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
    if re.fullmatch(r"\d+\.\d+\.\d+", args.version) is None:
        print(
            f"ERROR: --version must look like X.Y.Z, got {args.version!r}",
            file=sys.stderr,
        )
        return 1
    errors = validate_release_metadata(args.repo_root.resolve(), args.version)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Release metadata for {args.version} is finalized and consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
