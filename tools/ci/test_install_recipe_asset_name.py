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


def test_usage_recipe_points_to_the_verified_sequence() -> None:
    """The header must point at the authenticated install sequence."""
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "INSTALLATION.md#2-shortest-success-path" in text, (
        "the header no longer points at the signed release sequence"
    )
    assert "signed SHA256SUMS" in text, (
        "the header no longer requires the signed manifest"
    )


def test_usage_recipe_does_not_offer_a_root_pipe() -> None:
    """The header must not advertise an unverified curl-then-sudo path."""
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert not re.search(r"curl[^\n]*\|\s*(?:sudo\s+)?(?:bash|sh)\b", text), (
        "the header reintroduced a curl-to-root pipe"
    )
    assert not re.search(r"sudo\s+env[^\n]*bash", text), (
        "the header reintroduced an unverified sudo bootstrap"
    )


def test_usage_recipe_uses_a_private_staging_directory() -> None:
    """The header must not reintroduce a predictable shared download path."""
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "private" in text.lower(), (
        "the header lost the private staging guidance"
    )
    assert "mktemp" in text, (
        "the header lost the private staging guidance"
    )
    assert not re.search(r'-o\s+"?/tmp/', text), (
        "the header downloads into a predictable shared /tmp name again"
    )
