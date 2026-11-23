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


def test_stable_release_tag_is_accepted() -> None:
    """A final release tag keeps working."""
    _verify(ref_name="v1.0.0")


@pytest.mark.parametrize(
    "ref_name",
    ["v1.0.0-rc.1", "v1.0.0-rc.2", "v1.0.0-beta2", "v1.0.0-alpha.1"],
)
def test_prerelease_tag_is_accepted(ref_name: str) -> None:
    """A SemVer prerelease tag is a valid promotion reference."""
    _verify(ref_name=ref_name)


@pytest.mark.parametrize(
    "ref_name",
    [
        "1.0.0",  # no v prefix
        "v1.0",  # missing patch
        "v1.0.0-",  # trailing separator
        "v1.0.0-rc..1",  # empty identifier
        "v1.0.0+build.1",  # build metadata has no package identity
        "v1.0.0-rc.01",  # numeric identifier with a leading zero
    ],
)
def test_malformed_release_tag_is_rejected(ref_name: str) -> None:
    """A malformed tag never reaches the promotion step."""
    with pytest.raises(ValueError, match="version tag"):
        _verify(ref_name=ref_name)


def test_prerelease_tag_still_requires_the_exact_approved_sha() -> None:
    """A prerelease tag does not relax the exact-SHA requirement."""
    with pytest.raises(ValueError, match="does not match"):
        _verify(ref_name="v1.0.0-rc.1", tag_sha="c" * 40)


def test_uppercase_or_short_approved_sha_is_rejected() -> None:
    """The approved candidate must be a full lowercase commit SHA."""
    with pytest.raises(ValueError, match="approved candidate SHA"):
        _verify(approved_sha="A" * 40)
    with pytest.raises(ValueError, match="approved candidate SHA"):
        _verify(approved_sha="abcdef0")
