#!/usr/bin/env python3
"""Validate candidate or finalized release metadata for the selected phase."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.semver import SEMVER_PATTERN  # noqa: E402

# Repository-relative directory holding the per-version release notes.
RELEASE_NOTES_DIR = ("docs", "releases")
_STABLE_RELEASE_CLAIM_RE = re.compile(r"\bstable release\b", re.IGNORECASE)
_NEGATED_STABLE_RELEASE_RE = re.compile(
    r"\bnot\s+(?:a\s+)?stable release\b", re.IGNORECASE
)


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
        rf"(?P<body>.*?)(?=^#{{1,3}}[ \t]+|\Z)",
        "\n".join(_unfenced_lines(text)),
        re.MULTILINE | re.DOTALL,
    )
    if project_match is None:
        return [f"{path}: missing current release line {version}"]
    body = project_match.group("body")
    status_matches = re.findall(
        r"^\*\*Status:\*\*(?P<value>.*)$",
        body,
        re.MULTILINE,
    )
    if not status_matches:
        return [f"{path}: missing **Status:** line for {version}"]
    if len(status_matches) > 1:
        return [
            f"{path}: {version} section must contain exactly one Status field"
        ]
    value_text = " ".join(status_matches[0].split())
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


def _advance_fence_state(
    token: tuple[str, int, str],
    open_fence: tuple[str, int] | None,
) -> tuple[bool, tuple[str, int] | None]:
    """Return whether a fence token is prose and the next open fence."""
    char, run, rest = token
    if open_fence is None:
        if char == "`" and "`" in rest:
            return True, None
        return False, (char, run)
    if open_fence[0] == char and run >= open_fence[1] and rest.strip() == "":
        return False, None
    return False, open_fence


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
        is_prose, open_fence = _advance_fence_state(token, open_fence)
        if is_prose:
            lines.append(line)
    return lines


def _release_note_field(text: str, field: str) -> str | None:
    """Return a markdown release-note field without a backtracking regex."""
    prefix = f"**{field}**:"
    for line in _unfenced_lines(text):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _release_note_fields(text: str, field: str) -> list[str]:
    """Return all unfenced markdown release-note field values."""
    prefix = f"**{field}**:"
    return [
        line[len(prefix):].strip()
        for line in _unfenced_lines(text)
        if line.startswith(prefix)
    ]


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


_FUTURE_PUBLICATION_PREFIX = (
    r"\b(?:to|will|shall|may|might|can|could|should|must|due\s+to|"
    r"going\s+to|about\s+to|expected\s+to|scheduled\s+to|planned\s+to)"
    r"\s+(?:have\s+)?(?:be(?:en)?\s+)?"
)
_FUTURE_PUBLICATION_PREFIX_RE = re.compile(
    _FUTURE_PUBLICATION_PREFIX + r"$", re.IGNORECASE
)
_FUTURE_PUBLICATION_RE = re.compile(
    _FUTURE_PUBLICATION_PREFIX + r"(?:published|released)\b",
    re.IGNORECASE,
)
_NEGATED_PUBLICATION_PREFIX_RE = re.compile(
    r"\b(?:not(?:\s+yet)?|never)(?:\s+been)?\s+$"
    r"|\b(?:has|have|had)\s+(?:not|n['’]t)\s+"
    r"(?:yet\s+)?(?:been\s+)?$",
    re.IGNORECASE,
)
_PUBLISHED_DATE_RE = re.compile(
    r"\bpublished(?:\s+on)?\s+(?P<date>\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)


def _published_dates(value: str) -> list[str]:
    """Return dates stated as completed publication dates."""
    return [
        match["date"]
        for match in _PUBLISHED_DATE_RE.finditer(value)
        if _FUTURE_PUBLICATION_PREFIX_RE.search(value[: match.start()]) is None
        and _NEGATED_PUBLICATION_PREFIX_RE.search(value[: match.start()]) is None
    ]


def _prerelease_status_errors(
    value: str, path: Path, release_date: str | None = None
) -> list[str]:
    """Require a published candidate status without a stable claim."""
    normalized = " ".join(value.split())
    unnegated_status = _NEGATED_STABLE_RELEASE_RE.sub("", normalized)
    if _STABLE_RELEASE_CLAIM_RE.search(unnegated_status) is not None:
        return [f"{path}: a prerelease cannot be marked stable"]
    if _FUTURE_PUBLICATION_RE.search(normalized) is not None:
        return [
            f"{path}: final prerelease status must describe a published release"
        ]
    unpublished_statuses = re.finditer(
        r"\b(?:"
        r"not\s+(?:yet\s+)?(?:published|released)"
        r"|never(?:\s+been)?\s+(?:published|released)"
        r"|(?:has|have|had)(?:\s+not|n['’]t)\s+"
        r"(?:yet\s+)?been\s+(?:published|released)"
        r"|unpublished|unreleased"
        r"|(?<!no )pending\s+publication"
        r"|(?<!no )pending\s+release(?!\s+(?:blockers?|feedback)\b)"
        r"|(?<!no )(?:publication|release)"
        r"\s+(?:(?:is|are|remains?|stays?)\s+)?pending"
        r"|awaiting\s+publication"
        r"|awaiting\s+release(?!\s+feedback\b)"
        r")\b",
        normalized,
        re.IGNORECASE,
    )
    if any(
        re.search(
            r"\b(?:not(?:\s+yet)?|never|no\s+(?:longer|more)|"
            r"(?:is|are|was|were|has|have|had)n['’]t)\s+$",
            normalized[: status.start()],
            re.IGNORECASE,
        ) is None
        for status in unpublished_statuses
    ):
        return [
            f"{path}: final prerelease status must describe a published release"
        ]
    if re.match(
        r"^(?:Release candidate|Pre-release)(?:[.,]|$)",
        normalized,
        re.IGNORECASE,
    ) is None:
        return [f"{path}: prerelease status must identify a release candidate"]
    if release_date is not None:
        mismatched_dates = [
            date for date in _published_dates(normalized) if date != release_date
        ]
        if mismatched_dates:
            return [
                f"{path}: publication statement date {mismatched_dates[0]!r} "
                f"does not match CHANGELOG date {release_date!r}"
            ]
    return []


def _validate_prerelease_project_status(
    path: Path,
    version: str,
    text: str,
    release_date: str | None,
) -> list[str]:
    """Validate the current release line for a published prerelease."""
    prose = "\n".join(_unfenced_lines(text))
    sections = list(
        re.finditer(
            rf"^### Current Release Line {re.escape(version)}\s*$"
            rf"(?P<body>.*?)(?=^#{{1,3}}[ \t]+|\Z)",
            prose,
            re.MULTILINE | re.DOTALL,
        )
    )
    if not sections:
        return [f"{path}: missing current release line {version}"]
    if len(sections) > 1:
        return [
            f"{path}: {version} section must occur exactly once"
        ]
    body = sections[0].group("body")
    status_matches = re.findall(
        r"^\*\*Status:\*\*(?P<value>.*)$",
        body,
        re.MULTILINE,
    )
    if not status_matches:
        return [f"{path}: missing **Status:** line for {version}"]
    if len(status_matches) > 1:
        return [
            f"{path}: {version} section must contain exactly one Status field"
        ]
    errors = _prerelease_status_errors(
        status_matches[0], path, release_date
    )
    if release_date is not None and not _published_dates(status_matches[0]):
        errors.append(
            f"{path}: publication date {release_date} is missing or mismatched "
            f"in the {version} publication statement"
        )
    return errors


def _validate_prerelease_notes(
    path: Path,
    version: str,
    release_date: str | None,
    text: str,
) -> list[str]:
    """Validate release-note date and candidate status for a prerelease."""
    errors: list[str] = []
    unfenced = "\n".join(_unfenced_lines(text))
    if re.search(
        rf"^# Release Notes: {re.escape(version)}\s*$", unfenced, re.MULTILINE
    ) is None:
        errors.append(f"{path}: missing '# Release Notes: {version}' title")
    date_fields = _release_note_fields(text, "Date")
    status_fields = _release_note_fields(text, "Status")
    if not date_fields:
        errors.append(f"{path}: missing release date")
    elif len(date_fields) > 1:
        errors.append(f"{path}: release notes must contain exactly one Date field")
    elif release_date is not None and date_fields[0] != release_date:
        errors.append(
            f"{path}: date {date_fields[0]!r} does not match CHANGELOG date "
            f"{release_date!r}"
        )
    if not status_fields:
        errors.append(f"{path}: missing release status")
    else:
        if len(status_fields) > 1:
            errors.append(
                f"{path}: release notes must contain exactly one Status field"
            )
        else:
            errors.extend(
                _prerelease_status_errors(status_fields[0], path, release_date)
            )
    return errors


def _candidate_status_errors(value: str | None, path: Path) -> list[str]:
    """Require a pending status without positive publication or stable claims."""
    if value is None or re.match(
        r"^(?:Release candidate|Pending release|Unreleased)(?:[.,]|$)", value
    ) is None:
        return [f"{path}: candidate status must identify a pending release"]
    remainder = re.sub(
        r"\bnot\s+(?:yet\s+)?(?:published|released)\b|\bunpublished\b",
        "", value, flags=re.IGNORECASE,
    )
    if re.search(r"\b(?:stable|published|released)\b", remainder, re.IGNORECASE):
        return [f"{path}: candidate status cannot claim publication or stable status"]
    return []


def _status_field_values(text: str) -> list[str]:
    """Return one value per ``**Status:**`` line without a backtracking regex."""
    prefix = "**Status:**"
    values: list[str] = []
    for line in text.splitlines():
        if line.startswith(prefix):
            values.append(line[len(prefix):].lstrip(" \t"))
    return values


def _candidate_project_status_errors(
    project: str, version: str, path: Path
) -> list[str]:
    """Require one matching release section with one pending status field."""
    project_prose = "\n".join(_unfenced_lines(project))
    sections = list(
        re.finditer(
            rf"^### Current Release Line {re.escape(version)}\s*$(.*?)(?=^#{{1,3}}[ \t]+|\Z)",
            project_prose,
            re.MULTILINE | re.DOTALL,
        )
    )
    if not sections:
        return _candidate_status_errors(None, path)
    if len(sections) > 1:
        return [
            f"{path}: candidate metadata must contain exactly one "
            f"Current Release Line {version} section"
        ]

    section = sections[0]
    status_matches = _status_field_values(section.group(1))
    if not status_matches:
        return _candidate_status_errors(None, path)
    if len(status_matches) > 1:
        return [
            f"{path}: active candidate section must contain "
            "exactly one Status field"
        ]
    return _candidate_status_errors(status_matches[0], path)


def _validate_candidate_metadata(
    version: str, changelog: str, project: str, notes: str, paths: tuple[Path, ...]
) -> list[str]:
    """Require consistent, truthful metadata for an artifact-only candidate."""
    errors: list[str] = []
    changelog_path, project_path, notes_path = paths
    headings = re.findall(
        r"^## \[([^\]]+)\] - (.+)$",
        "\n".join(_unfenced_lines(changelog)),
        re.MULTILINE,
    )
    if not headings or headings[0] != (version, "Unreleased"):
        errors.append(f"{changelog_path}: active candidate must be [{version}] - Unreleased")
    if sum(heading_version == version for heading_version, _ in headings) != 1:
        errors.append(f"{changelog_path}: candidate heading must occur exactly once")
    errors.extend(_candidate_project_status_errors(project, version, project_path))
    if f"# Release Notes: {version}" not in _unfenced_lines(notes):
        errors.append(f"{notes_path}: missing '# Release Notes: {version}' title")
    note_statuses = _release_note_fields(notes, "Status")
    if not note_statuses:
        errors.extend(_candidate_status_errors(None, notes_path))
    elif len(note_statuses) > 1:
        errors.append(
            f"{notes_path}: candidate release notes must contain exactly one "
            "Status field"
        )
    else:
        errors.extend(_candidate_status_errors(note_statuses[0], notes_path))
    if _release_note_field(notes, "Date") is not None:
        errors.append(f"{notes_path}: candidate cannot declare a publication Date")
    target = _release_note_field(notes, "Target release date")
    if target is not None and target != "TBD (at release)":
        try:
            planned_date, qualifier = target.split(" ", 1)
            date.fromisoformat(planned_date)
            if qualifier != "(planned, publication pending)":
                raise ValueError("missing planned qualifier")
        except ValueError:
            errors.append(f"{notes_path}: target date must be valid and explicitly planned")
    return errors


def validate_release_metadata(repo_root: Path, version: str, phase: str = "final") -> list[str]:
    """Return release metadata errors for ``version``.

    Candidate phase validates pending metadata for artifact-only dispatch.
    Final phase requires published metadata, so a tag cannot publish
    documentation that still describes a pending release.  A prerelease
    identity keeps the candidate status in the final phase: the release is
    published, but it is not a stable release.
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
    if phase == "candidate":
        return _validate_candidate_metadata(
            version, changelog, project_status, release_notes, metadata_paths
        )
    if phase != "final":
        return [f"unknown release metadata phase: {phase}"]
    errors, release_date = _validate_changelog(changelog_path, version, changelog)
    concrete_date = release_date if release_date != "Unreleased" else None
    if "-" in version:
        errors.extend(
            _validate_prerelease_project_status(
                project_status_path, version, project_status, concrete_date
            )
        )
        errors.extend(
            _validate_prerelease_notes(
                release_notes_path, version, release_date, release_notes
            )
        )
        return errors
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
        "--phase",
        choices=("candidate", "final"),
        default="final",
        help="Candidate dispatch or finalized tag metadata (default: final)",
    )
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
    if SEMVER_PATTERN.fullmatch(args.version) is None:
        print(
            f"ERROR: --version must be a SemVer release identity, "
            f"got {args.version!r}",
            file=sys.stderr,
        )
        return 1
    errors = validate_release_metadata(args.repo_root.resolve(), args.version, args.phase)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Release metadata for {args.version} is consistent for phase {args.phase}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
