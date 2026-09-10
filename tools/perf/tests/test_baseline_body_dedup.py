"""Keep the mirrored baseline bodies pointing at a single real file.

The streaming-first and large-body payloads are recorded once per baseline set,
and every copy is byte-identical. They are stored as symlinks to the top-level
file so the repository does not carry megabytes of duplicate evidence; these
tests keep that true and keep the links inside the repository.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

BASELINES = Path(__file__).resolve().parents[3] / "perf" / "baselines"

# Payload families whose copies must stay byte-identical to one another.
SHARED_BODIES = ("streaming-first.body", "large-body.body")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_shared_body_copies_are_identical(name: str) -> None:
    copies = sorted(BASELINES.rglob(name))
    assert len(copies) >= 2, f"{name}: expected several copies, found {len(copies)}"
    digests = {_digest(copy) for copy in copies}
    assert len(digests) == 1, f"{name}: copies diverged: {sorted(digests)}"


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_only_the_top_level_copy_is_a_regular_file(name: str) -> None:
    for copy in sorted(BASELINES.rglob(name)):
        if copy.parent == BASELINES:
            assert not copy.is_symlink(), f"{copy} should be the stored payload"
        else:
            assert copy.is_symlink(), f"{copy} should link to the stored payload"


def test_links_resolve_inside_the_baselines_directory() -> None:
    root = BASELINES.resolve()
    for path in sorted(BASELINES.rglob("*.body")):
        if path.is_symlink():
            resolved = path.resolve()
            assert resolved.is_relative_to(root), f"{path} escapes {root}"
            assert resolved.is_file(), f"{path} is a dangling link"
