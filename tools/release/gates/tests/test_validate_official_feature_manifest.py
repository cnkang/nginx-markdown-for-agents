"""Regression tests for the official feature manifest gate.

The gate now resolves its manifest and Cargo paths through
``_manifest_path`` / ``_cargo_toml_path`` helpers, so tests monkey-patch
those helpers rather than spawning a subprocess or rewriting the source
file on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import validate_official_feature_manifest as validator


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch) -> Path:
    """Point the gate's path helpers at a tmp_path fixture."""
    manifest = tmp_path / "official-build-feature-manifest.json"
    cargo_dir = tmp_path / "rust-converter"
    cargo_dir.mkdir(parents=True, exist_ok=True)
    cargo_dir.joinpath("Cargo.toml").write_text(
        '[features]\ndefault = ["incremental", "streaming", "prune_noise_regions"]\n',
        encoding="utf-8",
    )

    def _manifest_path() -> Path:
        return manifest

    def _cargo_toml_path() -> Path:
        return cargo_dir / "Cargo.toml"

    monkeypatch.setattr(validator, "_manifest_path", _manifest_path)
    monkeypatch.setattr(validator, "_cargo_toml_path", _cargo_toml_path)

    return manifest


def test_missing_manifest_without_write_fails_closed(
    isolated_paths: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validation must not create a missing artifact unless --write is set."""
    assert validator.main([]) == 1
    assert not isolated_paths.exists()
    assert "feature manifest missing" in capsys.readouterr().err


def test_write_mode_generates_then_validates_manifest(
    isolated_paths: Path,
) -> None:
    """--write must generate the expected artifact from valid Cargo inputs."""
    assert validator.main(["--write"]) == 0
    assert json.loads(isolated_paths.read_text(encoding="utf-8")) == {
        "incremental": True,
        "streaming": True,
        "prune_noise_regions": True,
    }


def test_cargo_default_features_cannot_add_unmanifested_feature() -> None:
    """An unreviewed feature name in the default list must be rejected."""
    failures: list[str] = []
    validator.check_cargo_features(
        '[features]\ndefault = ["incremental", "streaming", "prune_noise_regions", "unreviewed"]\n',
        failures,
    )

    assert any("unreviewed" in failure for failure in failures)


def test_cargo_dependency_feature_consumers_reject_forbidden_names() -> None:
    """Target-specific dependency feature requests share the same allowlist."""
    failures: list[str] = []
    validator.check_cargo_features(
        '[features]\n'
        'default = ["incremental", "streaming", "prune_noise_regions"]\n'
        '[target."cfg(unix)".dependencies.parser]\n'
        'version = "1"\n'
        'features = ["brotli"]\n',
        failures,
    )

    assert any("forbidden Cargo feature name 'brotli'" in failure for failure in failures)


def test_non_string_dependency_feature_fails_closed() -> None:
    """Malformed feature arrays produce a validation error, not a crash."""
    failures: list[str] = []
    validator.check_cargo_features(
        '[features]\n'
        'default = ["incremental", "streaming", "prune_noise_regions"]\n'
        '[dependencies.parser]\n'
        'version = "1"\n'
        'features = [1]\n',
        failures,
    )

    assert any("non-string" in failure for failure in failures)
