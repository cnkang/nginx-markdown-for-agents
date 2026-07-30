"""Regression tests for the 0.9.2 release gate validator."""

from __future__ import annotations

from pathlib import Path

from tools.release.gates import validate_release_gates_092 as validator


def test_find_repo_root_returns_checkout_root() -> None:
    """The validator must resolve paths from the checkout, not the cwd."""
    repo = validator.find_repo_root()

    assert repo == Path(validator.__file__).resolve().parents[3]
    assert (repo / "CHANGELOG.md").is_file()


def test_version_consistency_fails_when_sources_are_missing(tmp_path: Path) -> None:
    """Version consistency must fail closed when either source is absent."""
    result = validator.check_version_consistency(tmp_path)

    assert result["status"] == "fail"
    assert "Cargo.toml: file not found" in result["message"]
    assert "CHANGELOG.md: file not found" in result["message"]
