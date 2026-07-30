"""Regression tests for the 0.9.2 release gate validator."""

from __future__ import annotations

from pathlib import Path

from tools.release.gates import validate_release_gates_092 as validator


def test_find_repo_root_returns_checkout_root() -> None:
    repo = validator.find_repo_root()

    assert repo == Path(validator.__file__).resolve().parents[3]
    assert (repo / "CHANGELOG.md").is_file()
