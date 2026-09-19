"""The APT verification example must stay on the current release version."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APT_README = REPO_ROOT / "packaging" / "repo" / "apt" / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _current_release_version() -> str:
    """Return the newest released version from the changelog."""
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(
        r"^ {0,3}##[ \t]+\[(\d+\.\d+\.\d+)\][ \t]*-[ \t]*\d{4}-\d{2}-\d{2}",
        text,
        re.MULTILINE,
    )
    assert match is not None, "no dated release heading in CHANGELOG.md"
    return match.group(1)


def _verification_block() -> str:
    text = APT_README.read_text(encoding="utf-8")
    marker = "Canonical release verification"
    start = text.index(marker)
    fence = text.index("```bash", start)
    end = text.index("```", fence + len("```bash"))
    # The section prose names the release too, so scan from the marker
    # through the end of the fenced block.
    return text[start:end]


def test_apt_example_downloads_and_verifies_one_version() -> None:
    block = _verification_block()
    versions = set(re.findall(r"VERSION=v(\d+\.\d+\.\d+)", block))
    versions |= set(
        re.findall(
            r"nginx-module-markdown-for-agents_(\d+\.\d+\.\d+)_nginx",
            block,
        )
    )
    expected = _current_release_version()
    assert versions == {expected}, (
        f"example versions {sorted(versions)} do not match the current "
        f"release {expected}"
    )


def test_apt_example_package_name_matches_download() -> None:
    block = _verification_block()
    downloaded = re.search(
        r"curl -fsSLo (nginx-module-markdown-for-agents_[^ \\]+)", block
    )
    selected = re.search(r"PACKAGE=\"(nginx-module-markdown-for-agents_[^\"]+)\"", block)
    assert downloaded is not None, "download line not found"
    assert selected is not None, "PACKAGE= line not found"
    assert downloaded.group(1) == selected.group(1), (
        f"download {downloaded.group(1)!r} != selected {selected.group(1)!r}"
    )


def test_apt_example_builds_urls_from_the_version_variable() -> None:
    block = _verification_block()
    assert "releases/download/${VERSION}" in block, (
        "BASE_URL must build the download URL from ${VERSION}"
    )


def test_apt_example_carries_every_version_source() -> None:
    block = _verification_block()
    assert re.search(r"VERSION=v\d+\.\d+\.\d+", block), "VERSION= assignment missing"
    assert re.search(r"gh release view v\d+\.\d+\.\d+", block), (
        "gh release view reference missing"
    )
    assert re.search(r"nginx-module-markdown-for-agents_\d+\.\d+\.\d+_nginx", block), (
        "artifact filename missing"
    )
