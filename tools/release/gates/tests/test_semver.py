"""Tests for the shared release-identity grammar."""

from __future__ import annotations

import pytest

from tools.lib.semver import is_release_version


@pytest.mark.parametrize(
    "version",
    ["0.9.4", "1.0.0", "1.0.0-rc.1", "1.0.0-alpha.10", "1.0.0-beta2"],
)
def test_release_identity_accepts_semver(version: str) -> None:
    assert is_release_version(version)


@pytest.mark.parametrize(
    "version",
    [
        "",
        "1.0",
        "1",
        "v1.0.0",
        "01.0.0",
        "1.0.0-",
        "1.0.0+build",
        "1.0.0-rc..1",
        "1.0.0-rc.01",
        "1.0.0/../../x",
        None,
        123,
    ],
)
def test_release_identity_rejects_other_values(version: object) -> None:
    assert not is_release_version(version)
