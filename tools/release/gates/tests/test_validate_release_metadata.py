"""Tests for the tag-only release metadata validator."""

from pathlib import Path
import subprocess
import sys

import pytest

from tools.release.gates.validate_release_metadata import (
    RELEASE_NOTES_DIR,
    validate_release_metadata,
)


def _write_release_surfaces(root: Path, *, finalized: bool) -> None:
    """Write the smallest valid set of release metadata fixtures."""
    (root / "docs" / "project").mkdir(parents=True)
    (root / "docs" / "releases").mkdir(parents=True)
    if finalized:
        changelog_heading = "## [0.9.1] - 2026-07-28"
        project_status = "**Status:** Stable release, published 2026-07-28."
        notes = (
            "# Release Notes: 0.9.1\n"
            "**Date**: 2026-07-28\n**Status**: Stable release\n"
        )
    else:
        changelog_heading = "## [0.9.1] - Unreleased"
        project_status = "**Status:** Release candidate."
        notes = (
            "# Release Notes: 0.9.1 Development Candidate\n"
            "**Date**: Pending release\n**Status**: Release candidate\n"
        )
    (root / "CHANGELOG.md").write_text(f"{changelog_heading}\n", encoding="utf-8")
    (root / "docs" / "project" / "PROJECT_STATUS.md").write_text(
        "### Current Release Line 0.9.1\n\n" f"{project_status}\n",
        encoding="utf-8",
    )
    (root / "docs" / "releases" / "0.9.1-release-notes.md").write_text(
        notes,
        encoding="utf-8",
    )


def _write_prerelease_surfaces(root: Path, version: str, date: str) -> None:
    """Write a published prerelease metadata fixture."""
    (root / "docs" / "project").mkdir(parents=True)
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text(
        f"## [{version}] - {date}\n", encoding="utf-8"
    )
    (root / "docs" / "project" / "PROJECT_STATUS.md").write_text(
        f"### Current Release Line {version}\n\n"
        f"**Status:** Release candidate, published {date}.\n",
        encoding="utf-8",
    )
    (root / "docs" / "releases" / f"{version}-release-notes.md").write_text(
        f"# Release Notes: {version}\n"
        f"**Date**: {date}\n**Status**: Release candidate\n",
        encoding="utf-8",
    )


def test_finalized_prerelease_metadata_passes(tmp_path: Path) -> None:
    """A published release candidate passes the final phase as a prerelease."""
    _write_prerelease_surfaces(tmp_path, "1.0.0-rc.1", "2026-12-01")

    assert validate_release_metadata(tmp_path, "1.0.0-rc.1") == []


def test_finalized_prerelease_rejects_stable_status(tmp_path: Path) -> None:
    """A prerelease cannot claim a stable release status."""
    _write_prerelease_surfaces(tmp_path, "1.0.0-rc.1", "2026-12-01")
    notes = tmp_path / "docs" / "releases" / "1.0.0-rc.1-release-notes.md"
    notes.write_text(
        "# Release Notes: 1.0.0-rc.1\n"
        "**Date**: 2026-12-01\n**Status**: Stable release\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "1.0.0-rc.1")

    assert any("cannot be marked stable" in error for error in errors), errors


def test_finalized_prerelease_requires_a_date(tmp_path: Path) -> None:
    """A published prerelease needs a dated changelog heading."""
    _write_prerelease_surfaces(tmp_path, "1.0.0-rc.1", "2026-12-01")
    (tmp_path / "CHANGELOG.md").write_text(
        "## [1.0.0-rc.1] - Unreleased\n", encoding="utf-8"
    )

    errors = validate_release_metadata(tmp_path, "1.0.0-rc.1")

    assert any("still Unreleased" in error for error in errors), errors


def test_finalized_prerelease_rejects_pending_status(tmp_path: Path) -> None:
    """A tagged prerelease cannot still describe itself as unpublished."""
    _write_prerelease_surfaces(tmp_path, "1.0.0-rc.1", "2026-12-01")
    (tmp_path / "docs" / "project" / "PROJECT_STATUS.md").write_text(
        "### Current Release Line 1.0.0-rc.1\n\n"
        "**Status:** Release candidate, not yet published.\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "1.0.0-rc.1")

    assert any("must describe a published release" in error for error in errors), errors


def test_finalized_prerelease_rejects_duplicate_sections(tmp_path: Path) -> None:
    """A second matching project-status section fails the final phase."""
    _write_prerelease_surfaces(tmp_path, "1.0.0-rc.1", "2026-12-01")
    status_path = tmp_path / "docs" / "project" / "PROJECT_STATUS.md"
    status_path.write_text(
        status_path.read_text(encoding="utf-8")
        + "\n### Current Release Line 1.0.0-rc.1\n\n"
        "**Status:** Stable release, published 2026-12-01.\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "1.0.0-rc.1")

    assert any("must occur exactly once" in error for error in errors), errors


def test_finalized_prerelease_ignores_fenced_project_status(tmp_path: Path) -> None:
    """A fenced example cannot satisfy the project-status section check."""
    _write_prerelease_surfaces(tmp_path, "1.0.0-rc.1", "2026-12-01")
    (tmp_path / "docs" / "project" / "PROJECT_STATUS.md").write_text(
        "```\n### Current Release Line 1.0.0-rc.1\n\n"
        "**Status:** Release candidate, published 2026-12-01.\n```\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "1.0.0-rc.1")

    assert any("missing current release line" in error for error in errors), errors


def test_release_notes_directory_matches_repository_layout() -> None:
    """The declared notes directory must exist in the real repository.

    A docs/release vs docs/releases drift passed CI once because the unit
    fixtures used the same wrong directory as the validator; this guard
    pins the validator's declaration to the repository's actual layout.
    """
    repo_root = Path(__file__).resolve().parents[4]
    assert repo_root.joinpath(*RELEASE_NOTES_DIR).is_dir(), (
        "the release-notes directory declared by the validator does not "
        "exist in the repository"
    )


def test_finalized_metadata_passes(tmp_path: Path) -> None:
    """A matching stable release date and status pass the tag gate."""
    _write_release_surfaces(tmp_path, finalized=True)

    assert validate_release_metadata(tmp_path, "0.9.1") == []


def test_impossible_release_date_fails_tag_gate(tmp_path: Path) -> None:
    """A syntactically valid but impossible date cannot pass the tag gate."""
    _write_release_surfaces(tmp_path, finalized=True)
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        changelog.replace("2026-07-28", "2026-99-99"), encoding="utf-8"
    )

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("invalid release date" in error for error in errors)


def test_unreleased_metadata_fails_tag_gate(tmp_path: Path) -> None:
    """Pending release documentation cannot accompany a release tag."""
    _write_release_surfaces(tmp_path, finalized=False)

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("still Unreleased" in error for error in errors)
    assert any("not marked stable" in error for error in errors)


def test_negated_status_fails_tag_gate(tmp_path: Path) -> None:
    """Negated status text cannot pass the positive status-field check."""
    _write_release_surfaces(tmp_path, finalized=True)
    status = tmp_path / "docs" / "project" / "PROJECT_STATUS.md"
    status.write_text(
        "### Current Release Line 0.9.1\n\n**Status:** Not stable release, published 2026-07-28.\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("not marked stable" in error for error in errors), errors


@pytest.mark.parametrize(
    "status_lines,error_fragment",
    [
        ("**Status:**", "not marked stable"),
        (
            "**Status:** Stable release, published 2026-07-28.\n**Status:**",
            "exactly one Status field",
        ),
    ],
)
def test_status_fields_must_be_nonempty_and_unique_in_tag_gate(
    tmp_path: Path, status_lines: str, error_fragment: str
) -> None:
    _write_release_surfaces(tmp_path, finalized=True)
    status = tmp_path / "docs" / "project" / "PROJECT_STATUS.md"
    status.write_text(
        f"### Current Release Line 0.9.1\n\n{status_lines}\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any(error_fragment in error for error in errors), errors


def test_publication_date_must_appear_in_status_section(tmp_path: Path) -> None:
    """The status section must carry the changelog's publication date."""
    _write_release_surfaces(tmp_path, finalized=True)
    status = tmp_path / "docs" / "project" / "PROJECT_STATUS.md"
    status.write_text(
        "### Current Release Line 0.9.1\n\n**Status:** Stable release.\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("publication date" in error for error in errors), errors


def test_wrong_release_notes_title_fails_tag_gate(tmp_path: Path) -> None:
    """The release-notes title must name the released version."""
    _write_release_surfaces(tmp_path, finalized=True)
    notes = tmp_path / "docs" / "releases" / "0.9.1-release-notes.md"
    notes.write_text(
        "# Release Notes: 0.9.0 Development Candidate\n"
        "**Date**: 2026-07-28\n**Status**: Stable release\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("Release Notes: 0.9.1" in error for error in errors), errors


def test_stable_release_candidate_qualifier_fails_tag_gate(tmp_path: Path) -> None:
    """A non-final qualifier cannot ride along with the stable wording."""
    _write_release_surfaces(tmp_path, finalized=True)
    status = tmp_path / "docs" / "project" / "PROJECT_STATUS.md"
    status.write_text(
        "### Current Release Line 0.9.1\n\n"
        "**Status:** Stable release candidate, published 2026-07-28.\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("not marked stable" in error for error in errors), errors


def test_negated_publication_wording_fails_tag_gate(tmp_path: Path) -> None:
    """Negated publication wording cannot ride along with the stable text."""
    _write_release_surfaces(tmp_path, finalized=True)
    status = tmp_path / "docs" / "project" / "PROJECT_STATUS.md"
    for value in ("Stable release, not published.", "Stable release, unpublished."):
        status.write_text(
            f"### Current Release Line 0.9.1\n\n"
            f"**Status:** {value} Published 2026-07-28.\n",
            encoding="utf-8",
        )
        errors = validate_release_metadata(tmp_path, "0.9.1")
        assert any("not marked stable" in error for error in errors), value


def test_fenced_metadata_cannot_satisfy_the_gate(tmp_path: Path) -> None:
    """Metadata-looking fenced examples cannot substitute for real fields."""
    _write_release_surfaces(tmp_path, finalized=True)
    notes = tmp_path / "docs" / "releases" / "0.9.1-release-notes.md"
    notes.write_text(
        "```text\n# Release Notes: 0.9.1\n**Date**: 2026-07-28\n"
        "**Status**: Stable release\n```\n",
        encoding="utf-8",
    )
    errors = validate_release_metadata(tmp_path, "0.9.1")
    assert any("Release Notes: 0.9.1" in error for error in errors), errors
    assert any("missing release date" in error for error in errors), errors


def test_fenced_changelog_heading_cannot_satisfy_the_gate(tmp_path: Path) -> None:
    """A fenced changelog heading cannot stand in for the real release line."""
    _write_release_surfaces(tmp_path, finalized=True)
    (tmp_path / "CHANGELOG.md").write_text(
        "```md\n## [0.9.1] - 2026-07-28\n```\n", encoding="utf-8"
    )
    errors = validate_release_metadata(tmp_path, "0.9.1")
    assert any("missing release heading" in error for error in errors), errors


@pytest.mark.parametrize(
    "version",
    ["../../x", "1.0", "1.0.0+build", "01.0.0", "1.0.0-"],
)
def test_version_argument_rejects_non_release_identities(version: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "tools/release/gates/validate_release_metadata.py",
            "--version",
            version,
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 1
    assert "--version must be a SemVer release identity" in result.stderr


def test_version_argument_accepts_a_prerelease_identity(tmp_path: Path) -> None:
    """A prerelease identity passes the argument gate and fails only on metadata."""
    result = subprocess.run(
        [
            sys.executable,
            "tools/release/gates/validate_release_metadata.py",
            "--version",
            "1.0.0-rc.1",
            "--repo-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 1
    assert "--version must be a SemVer release identity" not in result.stderr
    assert "required release metadata file not found" in result.stderr


def test_fence_close_with_trailing_text_does_not_close() -> None:
    """A closer with trailing text is not a closing fence per CommonMark."""
    from tools.release.gates.validate_release_metadata import _release_note_field

    text = (
        "```\n"
        "**Date**: 2026-07-28\n"
        "```no-close\n"
        "**Status**: Stable release\n"
        "```\n"
    )
    assert _release_note_field(text, "Date") is None
    assert _release_note_field(text, "Status") is None


def _write_candidate(root: Path) -> None:
    """Write realistic pending metadata for artifact-only qualification."""
    _write_release_surfaces(root, finalized=False)
    (root / "docs/releases/0.9.1-release-notes.md").write_text(
        "# Release Notes: 0.9.1\n"
        "**Target release date**: 2026-10-31 (planned, publication pending)\n"
        "**Status**: Pending release. 0.9.1 is not yet published.\n",
        encoding="utf-8",
    )


def test_candidate_metadata_passes_but_final_phase_still_rejects(tmp_path: Path) -> None:
    _write_candidate(tmp_path)
    assert validate_release_metadata(tmp_path, "0.9.1", "candidate") == []
    assert validate_release_metadata(tmp_path, "0.9.1")


def test_candidate_status_must_be_on_the_same_line(tmp_path: Path) -> None:
    _write_candidate(tmp_path)
    status_path = tmp_path / "docs/project/PROJECT_STATUS.md"
    status_path.write_text(
        status_path.read_text(encoding="utf-8").replace(
            "**Status:** Release candidate.",
            "**Status:**\nRelease candidate.",
        ),
        encoding="utf-8",
    )
    assert validate_release_metadata(tmp_path, "0.9.1", "candidate")


@pytest.mark.parametrize(
    "duplicate_status_line",
    ["**Status:** Stable release.", "**Status:**"],
)
def test_candidate_status_must_appear_exactly_once(
    tmp_path: Path, duplicate_status_line: str
) -> None:
    _write_candidate(tmp_path)
    status_path = tmp_path / "docs/project/PROJECT_STATUS.md"
    status_line = "**Status:** Release candidate."
    status_path.write_text(
        status_path.read_text(encoding="utf-8").replace(
            status_line,
            f"{status_line}\n{duplicate_status_line}",
        ),
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1", "candidate")

    duplicate_status_error = (
        f"{status_path}: active candidate section must contain "
        "exactly one Status field"
    )
    assert duplicate_status_error in errors


def test_candidate_status_rejects_duplicate_current_release_sections(
    tmp_path: Path,
) -> None:
    _write_candidate(tmp_path)
    status_path = tmp_path / "docs/project/PROJECT_STATUS.md"
    status_path.write_text(
        status_path.read_text(encoding="utf-8")
        + "\n### Current Release Line 0.9.1\n\n"
        + "**Status:** Release candidate.\n",
        encoding="utf-8",
    )

    errors = validate_release_metadata(tmp_path, "0.9.1", "candidate")

    duplicate_section_error = (
        f"{status_path}: candidate metadata must contain exactly one "
        "Current Release Line 0.9.1 section"
    )
    assert duplicate_section_error in errors


@pytest.mark.parametrize("path,old,new", [
    ("CHANGELOG.md", "0.9.1", "0.9.2"),
    ("CHANGELOG.md", "Unreleased", "2026-10-31"),
    ("docs/project/PROJECT_STATUS.md", "0.9.1", "0.9.2"),
    ("docs/project/PROJECT_STATUS.md", "Release candidate.", "Stable release."),
    ("docs/project/PROJECT_STATUS.md", "Release candidate.", "Release candidate. Published today."),
    ("docs/releases/0.9.1-release-notes.md", "Notes: 0.9.1", "Notes: 0.9.2"),
    ("docs/releases/0.9.1-release-notes.md", "Pending release.", "Stable release."),
    ("docs/releases/0.9.1-release-notes.md", "Pending release.", "Pending release. Published today."),
    ("docs/releases/0.9.1-release-notes.md", "2026-10-31", "2026-99-99"),
    ("docs/releases/0.9.1-release-notes.md", "Target release date", "Date"),
])
def test_candidate_metadata_rejects_mismatch_or_final_claim(
    tmp_path: Path, path: str, old: str, new: str
) -> None:
    _write_candidate(tmp_path)
    surface = tmp_path / path
    surface.write_text(surface.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    assert validate_release_metadata(tmp_path, "0.9.1", "candidate")


@pytest.mark.parametrize("event,ref_type,final_metadata,require_blocking,phase,blocking", [
    ("push", "tag", "false", "false", "final", True),
    ("workflow_dispatch", "branch", "false", "false", "candidate", False),
    ("workflow_dispatch", "tag", "false", "true", "candidate", True),
    ("workflow_dispatch", "branch", "true", "false", "final", True),
    ("pull_request", "branch", "true", "true", None, False),
])
def test_workflow_routes_metadata_phase_without_bypassing_validation(
    tmp_path: Path, event: str, ref_type: str, final_metadata: str,
    require_blocking: str, phase: str | None, blocking: bool,
) -> None:
    """Exercise phase routing and mandatory evidence in the workflow shell."""
    import os
    import yaml

    root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load((root / ".github/workflows/release-packages.yml").read_text())
    step = next(s for s in workflow["jobs"]["release-gate"]["steps"] if s.get("name") == "Run release gates")
    run = step["run"]
    start = run.index('case "${GITHUB_EVENT_NAME}:${GITHUB_REF_TYPE}"')
    evidence_argument = '"${EVIDENCE_ARGS[@]}"'
    stop = run.index(evidence_argument, start) + len(evidence_argument)
    probe = tmp_path / "python3"
    probe.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    probe.chmod(0o755)
    candidate_sha = "b" * 40
    run_url = "https://github.com/example/repo/actions/runs/100"
    result = subprocess.run(
        ["bash", "-e", "-c", run[start:stop]], text=True, capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "GITHUB_EVENT_NAME": event, "GITHUB_REF_TYPE": ref_type,
             "RELEASE_GATE_EXPECTED_CARGO_VERSION": "0.9.3",
             "GITHUB_SHA": candidate_sha, "RUN_URL": run_url,
             "RUNNER_TEMP": str(tmp_path),
             "FINAL_METADATA_INPUT": final_metadata,
             "REQUIRE_BLOCKING_INPUT": require_blocking},
    )
    if phase is None:
        assert result.returncode != 0
        assert "unsupported release metadata event" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        arguments = result.stdout.splitlines()
        assert arguments[:5] == [
            "tools/release/gates/validate_release_metadata.py", "--version", "0.9.3", "--phase", phase
        ]
        expected_evidence = [
            "tools/release/matrix/validate_compatibility_evidence.py",
            "--evidence-dir", str(tmp_path / "compat-evidence"),
            "--require-ci", "--candidate-sha", candidate_sha, "--workflow-run-url", run_url,
        ]
        if blocking:
            expected_evidence.append("--require-blocking")
        assert arguments[5:] == expected_evidence
