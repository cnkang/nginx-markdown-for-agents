"""Tests for the protected release-candidate promotion gate."""

from __future__ import annotations

import pytest

from tools.release.gates.verify_tag_candidate import verify_tag_candidate


def _verify(**overrides: str) -> None:
    values = {
        "tag_sha": "a" * 40,
        "approved_sha": "a" * 40,
        "ref_type": "tag",
        "ref_name": "v0.9.2",
        "default_branch": "main",
        "branch_relation": "identical",
    }
    values.update(overrides)
    verify_tag_candidate(**values)


def test_exact_approved_tag_candidate_passes() -> None:
    """The exact protected candidate is eligible for promotion."""
    _verify()


def test_older_green_main_ancestor_is_not_accepted() -> None:
    """Main ancestry alone must not promote an older qualified commit."""
    with pytest.raises(ValueError, match="does not match"):
        _verify(tag_sha="b" * 40, branch_relation="ahead")


def test_branch_named_like_a_release_tag_is_not_accepted() -> None:
    """A branch name must not be confused with a version tag ref."""
    with pytest.raises(ValueError, match="requires a tag ref"):
        _verify(ref_type="branch", ref_name="v0.9.2")


def test_arbitrary_commit_sha_is_not_accepted() -> None:
    """The promotion anchor must be a complete immutable commit SHA."""
    with pytest.raises(ValueError, match="tag SHA must be"):
        _verify(tag_sha="not-a-commit")


def test_tag_not_contained_in_main_is_not_accepted() -> None:
    """An approved-looking tag outside protected main history still blocks."""
    with pytest.raises(ValueError, match="not contained"):
        _verify(branch_relation="behind")

