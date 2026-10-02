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


def test_usage_recipe_does_not_leak_shell_state() -> None:
    """The recipe must not change the state of the shell it is pasted into.

    It sets `-e` and installs an EXIT trap. Pasted directly, errexit persists
    and the trap fires when that shell exits -- so an unrelated later command
    aborts the operator's session. The subshell contains both.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    recipe = [
        line.strip().removeprefix("#").strip()
        for line in text.splitlines()
        if line.startswith("#") and ("set -e" in line or "trap " in line)
    ]
    assert recipe, "the recipe no longer sets errexit or a cleanup trap"

    # The opening paren must precede `set -e`, and a matching close must follow
    # the installer invocation, so both lines sit inside the subshell.
    paren = text.index("#   (\n")
    set_e = text.index("#     set -e")
    assert paren < set_e, "`set -e` appears before the subshell opens"

    lines = text.splitlines()
    opener = next(i for i, l in enumerate(lines) if l.strip() == "#   (")
    closer = next(i for i, l in enumerate(lines) if l.strip() == "#   )")
    assert opener < closer, "the subshell is not closed"
    body = "\n".join(lines[opener:closer])
    assert "set -e" in body, "the subshell does not contain `set -e`"
    assert "trap " in body, "the subshell does not contain the cleanup trap"
    assert "sudo " in body, "the installer invocation left the subshell"


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
