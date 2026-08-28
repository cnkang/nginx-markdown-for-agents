"""Regression tests for the shared release feature-manifest digest helper."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.release.gates.feature_manifest_digest import (
    CANONICAL_FEATURE_MANIFEST,
    calculate_feature_manifest_digest,
)


def test_digest_matches_canonical_manifest(tmp_path: Path) -> None:
    """The helper validates and hashes the one release feature contract."""
    manifest_path = tmp_path / "feature-manifest.json"
    manifest_path.write_text(
        json.dumps(CANONICAL_FEATURE_MANIFEST), encoding="utf-8"
    )
    canonical = json.dumps(
        CANONICAL_FEATURE_MANIFEST, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    assert calculate_feature_manifest_digest(manifest_path) == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )


def test_digest_rejects_noncanonical_manifest(tmp_path: Path) -> None:
    """A manifest with an extra or changed feature fails closed."""
    manifest_path = tmp_path / "feature-manifest.json"
    manifest_path.write_text(
        json.dumps({**CANONICAL_FEATURE_MANIFEST, "legacy": True}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match the release feature"):
        calculate_feature_manifest_digest(manifest_path)


def test_digest_rejects_numeric_boolean_coercion(tmp_path: Path) -> None:
    """JSON numbers must not pass the strict boolean feature contract."""
    manifest_path = tmp_path / "feature-manifest.json"
    manifest_path.write_text(
        json.dumps({"prune_noise_regions": 1, "streaming": True}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match the release feature"):
        calculate_feature_manifest_digest(manifest_path)


def test_digest_reports_missing_manifest(tmp_path: Path) -> None:
    """A missing artifact manifest produces a useful validation error."""
    with pytest.raises(ValueError, match="unable to read feature manifest"):
        calculate_feature_manifest_digest(tmp_path / "missing.json")
