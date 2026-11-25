"""Tests for the shared release-identity grammar."""

from __future__ import annotations

import pytest

from tools.lib.semver import is_numeric_line, is_release_version, release_core


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


@pytest.mark.parametrize("version", ["1.0.0", "0.9.4"])
def test_numeric_line_accepts_three_numeric_parts(version: str) -> None:
    assert is_numeric_line(version)


@pytest.mark.parametrize(
    "version", ["1.0.0-rc.1", "1.0", "01.0.0", "1.0.0+build", None]
)
def test_numeric_line_rejects_other_values(version: object) -> None:
    assert not is_numeric_line(version)


@pytest.mark.parametrize(
    ("version", "core"),
    [("1.0.0-rc.1", "1.0.0"), ("1.0.0", "1.0.0"), ("0.9.4", "0.9.4")],
)
def test_release_core_returns_the_numeric_line(version: str, core: str) -> None:
    assert release_core(version) == core


def test_release_core_rejects_a_non_numeric_core() -> None:
    with pytest.raises(ValueError, match="numeric core"):
        release_core("01.0.0")
