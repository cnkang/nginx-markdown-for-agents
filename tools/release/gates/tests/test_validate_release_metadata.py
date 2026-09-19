"""Tests for the tag-only release metadata validator."""

from pathlib import Path

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


def test_version_argument_must_be_a_plain_version() -> None:
    import subprocess
    import sys as _sys

    result = subprocess.run(
        [
            _sys.executable,
            "tools/release/gates/validate_release_metadata.py",
            "--version",
            "../../x",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[4]),
    )
    assert result.returncode == 1
    assert "--version must look like X.Y.Z" in result.stderr


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
