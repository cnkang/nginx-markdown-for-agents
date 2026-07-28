"""Tests for the tag-only release metadata validator."""

from pathlib import Path

from tools.release.gates.validate_release_metadata import validate_release_metadata


def _write_release_surfaces(root: Path, *, finalized: bool) -> None:
    """Write the smallest valid set of release metadata fixtures."""
    (root / "docs" / "project").mkdir(parents=True)
    (root / "docs" / "release").mkdir(parents=True)
    if finalized:
        changelog_heading = "## [0.9.1] - 2026-07-28"
        project_status = "**Status:** Stable release, published 2026-07-28."
        notes = "**Date**: 2026-07-28\n**Status**: Stable release\n"
    else:
        changelog_heading = "## [0.9.1] - Unreleased"
        project_status = "**Status:** Release candidate."
        notes = "**Date**: Pending release\n**Status**: Release candidate\n"
    (root / "CHANGELOG.md").write_text(f"{changelog_heading}\n", encoding="utf-8")
    (root / "docs" / "project" / "PROJECT_STATUS.md").write_text(
        "### Current Release Line 0.9.1\n\n" f"{project_status}\n",
        encoding="utf-8",
    )
    (root / "docs" / "release" / "0.9.1-release-notes.md").write_text(
        notes,
        encoding="utf-8",
    )


def test_finalized_metadata_passes(tmp_path: Path) -> None:
    """A matching stable release date and status pass the tag gate."""
    _write_release_surfaces(tmp_path, finalized=True)

    assert validate_release_metadata(tmp_path, "0.9.1") == []


def test_unreleased_metadata_fails_tag_gate(tmp_path: Path) -> None:
    """Pending release documentation cannot accompany a release tag."""
    _write_release_surfaces(tmp_path, finalized=False)

    errors = validate_release_metadata(tmp_path, "0.9.1")

    assert any("still Unreleased" in error for error in errors)
    assert any("not marked stable" in error for error in errors)
