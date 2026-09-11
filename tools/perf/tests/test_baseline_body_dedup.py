"""Keep the mirrored baseline bodies pointing at a single committed file.

The response payloads recorded for the streaming-first scenarios are
byte-identical in every probe set, and the large-body payload repeats too. They
are committed once and linked everywhere else, so the repository does not carry
megabytes of duplicate evidence.

These checks read the committed tree instead of the working directory: the
canonical benchmark job materializes fresh probe files over these paths and then
runs this suite, so a regenerated directory must not look like a regression,
while a committed duplicate copy still must.
"""

from __future__ import annotations

import posixpath
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINES_REL = "perf/baselines"

# The probe sets that mirror the stored payloads.
PROBE_DIRS = ("module-baseline-091-raw-probes", "module-baseline-092-raw-probes")

# The payloads that are committed as one stored file.
STORED_BODIES = ("streaming-first.body", "large-body.body")

# Scenario names that reuse the streaming-first payload.
VARIANT_BODIES = (
    "gzip-streaming-first.body",
    "deflate-streaming-first.body",
    "brotli-streaming-first.body",
)

SHARED_BODIES = STORED_BODIES + VARIANT_BODIES

FILE_MODE = "100644"
LINK_MODE = "120000"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _committed_entry(path: str) -> tuple[str, bytes]:
    """Mode and content of one committed path, relative to the repository root."""
    tree = _git("ls-tree", "HEAD", "--", path).strip()
    assert tree, f"{path} is missing from HEAD"
    mode, _otype, oid = tree.split()[0:3]
    return mode, _blob(oid)


def _blob(oid: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "blob", oid],
        check=True,
        capture_output=True,
    ).stdout


def _expected_paths(name: str) -> list[str]:
    """Every committed path that must carry the payload."""
    return [f"{BASELINES_REL}/{name}"] + [
        f"{BASELINES_REL}/{probe_dir}/{name}" for probe_dir in PROBE_DIRS
    ]


def _resolve(path: str, limit: int = 5) -> tuple[str, bytes]:
    """Follow committed links; return the path they land on and its bytes."""
    seen: set[str] = set()
    for _ in range(limit):
        assert path not in seen, f"link loop through {path}"
        seen.add(path)
        mode, content = _committed_entry(path)
        if mode != LINK_MODE:
            return path, content
        target = content.decode("utf-8").strip()
        path = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        assert path.startswith(f"{BASELINES_REL}/"), f"{path} escapes {BASELINES_REL}"
    raise AssertionError(f"link chain from {path} did not resolve")


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_every_probe_set_keeps_a_committed_mirror(name: str) -> None:
    for path in _expected_paths(name):
        assert _git("ls-tree", "HEAD", "--", path).strip(), f"{path} is not committed"


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_shared_payload_copies_are_committed_as_links(name: str) -> None:
    stored = f"{BASELINES_REL}/{name}"
    for path in _expected_paths(name):
        if path == stored and name in STORED_BODIES:
            continue
        mode, _content = _committed_entry(path)
        assert mode == LINK_MODE, f"{path} must be a link, not a copy"
        landed, _bytes = _resolve(path)
        assert landed.rsplit("/", 1)[-1] in STORED_BODIES, (
            f"{path} must resolve to a stored payload, not to {landed}"
        )


@pytest.mark.parametrize("name", STORED_BODIES)
def test_stored_payloads_are_committed_as_files(name: str) -> None:
    mode, content = _committed_entry(f"{BASELINES_REL}/{name}")
    assert mode == FILE_MODE, f"{BASELINES_REL}/{name} must be the stored payload"
    assert content, f"{BASELINES_REL}/{name} must hold the payload bytes"


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_shared_payload_bytes_match_the_stored_payload(name: str) -> None:
    _landed, stored_bytes = _resolve(f"{BASELINES_REL}/{name}")
    assert stored_bytes, f"{BASELINES_REL}/{name} resolves to no bytes"
    for path in _expected_paths(name):
        _landed, content = _resolve(path)
        assert content == stored_bytes, f"{path} does not read the stored payload"


@pytest.mark.parametrize("name", SHARED_BODIES)
def test_no_unexpected_copy_of_a_shared_payload(name: str) -> None:
    tracked = _git("ls-files", "--", BASELINES_REL).splitlines()
    found = sorted(path for path in tracked if path.endswith(f"/{name}"))
    assert found == sorted(_expected_paths(name)), f"{name}: unexpected committed copies"


def _main() -> int:  # pragma: no cover - convenience for manual runs
    print("baseline dedupe checks read the committed tree at HEAD")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
