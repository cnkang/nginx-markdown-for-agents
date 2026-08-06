"""Regression tests for the official feature manifest gate."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release.gates import validate_official_feature_manifest as validator


def test_missing_manifest_without_write_fails_closed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Validation must not create a missing artifact unless --write is set."""
    manifest_path = tmp_path / "official-build-feature-manifest.json"
    monkeypatch.setattr(validator, "MANIFEST_PATH", manifest_path)

    assert validator.main([]) == 1
    assert not manifest_path.exists()
    assert "feature manifest missing" in capsys.readouterr().err


def test_write_mode_generates_then_validates_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """--write must generate the expected artifact from valid Cargo inputs."""
    manifest_path = tmp_path / "official-build-feature-manifest.json"
    cargo_path = tmp_path / "Cargo.toml"
    cargo_path.write_text(
        '[features]\ndefault = ["incremental", "streaming", '
        '"prune_noise_regions"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(validator, "CARGO_TOML_PATH", cargo_path)

    assert validator.main(["--write"]) == 0
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == validator.EXPECTED
