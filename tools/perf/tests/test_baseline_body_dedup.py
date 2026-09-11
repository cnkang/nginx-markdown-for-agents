"""Keep the mirrored baseline bodies pointing at a single real file.

The response payloads recorded for the streaming-first scenarios are
byte-identical in every probe set, and the large-body payload repeats too. They
are stored once and linked everywhere else so the repository does not carry
megabytes of duplicate evidence; these tests keep that true, keep every probe
set mirrored, and keep the links inside the repository.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

BASELINES = Path(__file__).resolve().parents[3] / "perf" / "baselines"

# The probe sets that mirror the stored payloads.
PROBE_DIRS = ("module-baseline-091-raw-probes", "module-baseline-092-raw-probes")

# The payloads that exist as one stored file.
STORED_BODIES = ("streaming-first.body", "large-body.body")

# Scenario names that reuse the streaming-first payload.
VARIANT_BODIES = (
    "gzip-streaming-first.body",
    "deflate-streaming-first.body",
    "brotli-streaming-first.body",
)

SHARED_BODIES = STORED_BODIES + VARIANT_BODIES


def _expected_paths(name: str) -> list[Path]:
    """Every path that must carry the payload: the stored file and its mirrors."""
    return [BASELINES / name] + [BASELINES / probe_dir / name for probe_dir in PROBE_DIRS]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_every_probe_set_keeps_a_mirror(name: str) -> None:
    missing = [path for path in _expected_paths(name) if not path.exists()]
    assert not missing, f"{name}: missing mirrors: {missing}"


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_shared_body_copies_are_identical(name: str) -> None:
    copies = sorted(BASELINES.rglob(name))
    assert copies == sorted(_expected_paths(name)), f"{name}: unexpected copies"
    digests = {_digest(copy) for copy in copies}
    assert len(digests) == 1, f"{name}: copies diverged: {sorted(digests)}"


@pytest.mark.parametrize("name", STORED_BODIES)
def test_stored_payload_is_a_regular_file(name: str) -> None:
    canonical = BASELINES / name
    assert canonical.is_file(), f"{canonical} is the stored payload and must exist"
    assert not canonical.is_symlink(), f"{canonical} must be the stored payload"
    for copy in _expected_paths(name):
        if copy != canonical:
            assert copy.is_symlink(), f"{copy} should link to {canonical}"


@pytest.mark.parametrize("name", VARIANT_BODIES)
def test_variant_payloads_are_links(name: str) -> None:
    for copy in _expected_paths(name):
        assert copy.is_symlink(), f"{copy} should link to the stored payload"


def test_links_resolve_inside_the_baselines_directory() -> None:
    root = BASELINES.resolve()
    for path in sorted(BASELINES.rglob("*.body")):
        if path.is_symlink():
            resolved = path.resolve()
            assert resolved.is_relative_to(root), f"{path} escapes {root}"
            assert resolved.is_file(), f"{path} is a dangling link"
