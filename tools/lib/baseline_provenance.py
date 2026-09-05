"""Shared provenance validation for performance baseline tooling."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .executable_validation import resolve_approved_executable

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_SOURCE_RUN_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"/actions/runs/(?P<run_id>[1-9][0-9]*)"
    r"/attempts/(?P<attempt>[1-9][0-9]*)$"
)


def _github_repository(repo_root: Path | None) -> str | None:
    """Return the checkout's GitHub repository slug when it is discoverable."""
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repository and re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        return repository.lower()

    if repo_root is None:
        return None
    git = resolve_approved_executable("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_root),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    remote = result.stdout.strip()
    match = re.search(
        r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote,
        re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def validate_source_run(
    source_run: object, *, repo_root: Path | None = None,
) -> list[str]:
    """Validate a concrete GitHub Actions run URL for this repository."""
    if not isinstance(source_run, str) or not source_run.strip():
        return ["source_run must be a non-empty string"]

    value = source_run.strip()
    match = _SOURCE_RUN_RE.fullmatch(value)
    if match is None:
        return [
            "source_run must be an HTTPS GitHub Actions URL with numeric "
            "run and attempt IDs (https://github.com/<owner>/<repo>/actions/"
            "runs/<run-id>/attempts/<attempt>)"
        ]

    expected_repository = _github_repository(repo_root)
    if (
        expected_repository is not None
        and match.group("repository").lower() != expected_repository
    ):
        return [
            "source_run repository does not match the current GitHub "
            f"repository {expected_repository!r}"
        ]
    return []


def validate_iso_utc(timestamp: object, *, field: str) -> str:
    """Validate an ISO-8601 timestamp with an explicit UTC offset."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError(f"{field} must be a non-empty ISO-8601 string")

    value = timestamp.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field} is not valid ISO-8601: {value!r} ({exc})"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must include an explicit UTC offset")
    return value


def validate_raw_commit_match(
    raw_report: object, source_git_commit: str,
) -> list[str]:
    """Verify a raw report's short SHA is a prefix of the full source SHA."""
    if not isinstance(raw_report, dict):
        return ["raw report must be an object"]
    module_benchmark = raw_report.get("module_benchmark", {})
    if not isinstance(module_benchmark, dict):
        return ["raw report is missing a 'module_benchmark' object"]

    raw_commit = module_benchmark.get("git_commit")
    if not isinstance(raw_commit, str) or not _SHORT_SHA_RE.fullmatch(raw_commit):
        return [
            "raw report module_benchmark.git_commit must be a lowercase "
            "hex Git short SHA between 7 and 40 characters"
        ]
    if not _FULL_SHA_RE.fullmatch(source_git_commit) or not (
        source_git_commit.startswith(raw_commit)
    ):
        return [
            f"raw report module_benchmark.git_commit={raw_commit!r} does not "
            "match the declared source_git_commit prefix"
        ]
    return []
