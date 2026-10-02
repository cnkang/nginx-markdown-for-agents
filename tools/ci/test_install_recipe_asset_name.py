"""The installer's usage recipe must name the asset the release actually ships.

The published installer is named by the release workflow; the recipe inside
``tools/install.sh`` is a runnable copy-paste bootstrap, so a name that does not
match the release turns a security fix into a 404 -- every operator who follows
the header gets nothing to download.

This test reads the name the workflow builds instead of restating it, so the two
spellings cannot drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "tools" / "install.sh"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"

# Matches the shell assignment that names the release installer asset.
_WORKFLOW_ASSIGNMENT = re.compile(
    r'installer="(?P<stem>nginx-markdown-[a-z-]*?installer)-\$\{RELEASE_TAG\}\.sh"'
)


def _released_asset_stem() -> str:
    """Return the asset stem the release workflow publishes."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    stems = {m.group("stem") for m in _WORKFLOW_ASSIGNMENT.finditer(text)}
    assert len(stems) == 1, f"ambiguous installer asset name in workflow: {stems}"
    return stems.pop()


def _recipe_asset_urls() -> list[str]:
    """Return every release-download URL named in the installer's header."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    return re.findall(r"releases/download/[^\s\"']*?\.sh", text)


def test_usage_recipe_downloads_the_released_asset() -> None:
    """The recipe must name exactly the asset the workflow publishes."""
    expected = f"{_released_asset_stem()}-<tag>.sh"
    urls = _recipe_asset_urls()

    assert urls, "the usage recipe no longer documents a download URL"
    for url in urls:
        # The recipe interpolates a shell variable where the workflow has the
        # literal tag, so compare the stem, not the whole URL.
        stem = url.rsplit("/", 1)[-1]
        assert stem.endswith("-${VERSION}.sh"), f"unexpected recipe asset: {url}"
        # Exact equality, not `endswith`: a prefixed name such as
        # `evil-<stem>` also ends with the real stem, and would 404 just the same.
        assert stem.removesuffix("-${VERSION}.sh") == _released_asset_stem(), (
            f"usage recipe downloads {stem!r}, but the release publishes {expected}"
        )


def test_usage_recipe_uses_a_private_staging_directory() -> None:
    """The recipe must not reintroduce a predictable shared download path.

    A world-writable name under /tmp lets an unprivileged local user pre-create
    the file, deny the downloader write access, and have root execute their bytes
    once the download fails.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    recipe = "\n".join(
        line for line in text.splitlines() if line.startswith("#") and "curl" in line
    )
    assert "mktemp -d" in text, "the recipe lost its private staging directory"
    assert not re.search(r'-o\s+"?/tmp/', recipe), (
        "the recipe downloads into a predictable shared /tmp name again"
    )
