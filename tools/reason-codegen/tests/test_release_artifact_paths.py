"""Tests for version-scoped reason-code release artifacts."""

import importlib.util
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPO_ROOT / "tools" / "reason-codegen" / "generate.py"
PACKAGE_MANIFEST_PATH = (
    REPO_ROOT / "components" / "rust-converter" / "Cargo.toml"
)
SPEC = importlib.util.spec_from_file_location(
    "reason_codegen_generate", GENERATOR_PATH
)
assert SPEC is not None
assert SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


def test_release_artifact_paths_follow_package_version():
    package = tomllib.loads(PACKAGE_MANIFEST_PATH.read_text(encoding="utf-8"))[
        "package"
    ]
    version = package["version"]

    assert GENERATOR.RELEASE_VERSION == version
    assert GENERATOR.MANIFEST_OUTPUT == (
        REPO_ROOT
        / "artifacts"
        / "release"
        / version
        / "reason-registry-report.json"
    )
    assert GENERATOR.LISTING_OUTPUT == (
        REPO_ROOT
        / "artifacts"
        / "release"
        / version
        / "generated-reason-artifacts.json"
    )

    listing = GENERATOR.generate_listing("test-hash")
    manifest_paths = {
        entry["path"] for entry in listing["generated_artifacts"]
    }
    assert (
        f"artifacts/release/{version}/reason-registry-report.json"
        in manifest_paths
    )


def test_release_version_rejects_non_ascii_digits(monkeypatch, tmp_path):
    package_manifest = tmp_path / "Cargo.toml"
    package_manifest.write_text(
        '[package]\nversion = "٠.٩.٤"\n', encoding="utf-8"
    )
    monkeypatch.setattr(GENERATOR, "PACKAGE_MANIFEST_PATH", package_manifest)

    with pytest.raises(RuntimeError, match="Invalid package release version"):
        GENERATOR._load_release_version()
