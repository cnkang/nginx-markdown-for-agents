"""Tests for the policy-to-release-contract matrix projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.matrix import generate_release_contract_matrix as projection


def policy_matrix() -> dict:
    """Return representative policy rows for every target conversion."""
    rows = []
    for os_name, libc, arch, artifact_type in (
        ("debian12", "glibc", "amd64", "deb-package"),
        ("alpine3.20", "musl", "arm64", "docker-image"),
        ("macos", "darwin", "arm64", "homebrew-formula"),
        ("any", "n/a", "any", "source"),
    ):
        row = {
            "nginx_version": "1.26.3",
            "nginx_channel": "stable",
            "os": os_name,
            "libc": libc,
            "arch": arch,
            "artifact_type": artifact_type,
            "test_level": "smoke-test",
            "support_tier": "supported",
            "release_blocking": True,
            "owner_workflow": ".github/workflows/release-packages.yml",
            "feature_manifest_digest": "sha256:" + "a" * 64,
            "abi_version": 2,
        }
        if artifact_type == "docker-image":
            row["image_ref"] = "nginx:1.26.3-alpine"
            row["image_digest"] = "sha256:" + "b" * 64
        rows.append(row)
    return {"schema_version": "1.0", "entries": rows}


def test_build_projection_records_source_and_converts_targets() -> None:
    """Verify that the projection records source metadata, converts targets, and preserves Docker image metadata."""
    source = policy_matrix()
    result = projection.build_projection(source)

    assert result["schema_version"] == 1
    assert result["generated_from"] == {
        "path": "tools/release-matrix.json",
        "sha256": projection.canonical_digest(source),
    }
    assert [entry["target"] for entry in result["entries"]] == [
        "aarch64-apple-darwin",
        "aarch64-unknown-linux-musl",
        "any",
        "x86_64-unknown-linux-gnu",
    ]
    assert result["entries"][2]["os"] == "linux"
    assert result["entries"][2]["libc"] == "any"
    docker_entry = next(
        entry for entry in result["entries"] if entry["artifact_type"] == "docker-image"
    )
    assert docker_entry["image_ref"] == "nginx:1.26.3-alpine"
    assert docker_entry["image_digest"] == "sha256:" + "b" * 64


def test_write_then_check_uses_deterministic_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI writes and then accepts the same deterministic projection."""
    source_path = tmp_path / "tools/release-matrix.json"
    schema_path = tmp_path / "tools/release-matrix.schema.json"
    output_path = tmp_path / "docs/releases/release-matrix.json"
    source_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    source_path.write_text(json.dumps(policy_matrix()), encoding="utf-8")
    schema_path.write_text(json.dumps({"type": "object"}), encoding="utf-8")

    monkeypatch.setattr(projection, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(projection, "SOURCE_MATRIX_PATH", source_path)
    monkeypatch.setattr(projection, "SOURCE_SCHEMA_PATH", schema_path)
    monkeypatch.setattr(projection, "PROJECTION_PATH", output_path)

    assert projection.main(["--write"]) == 0
    assert output_path.stat().st_mode & 0o777 == 0o644
    assert projection.main(["--check"]) == 0


def test_check_rejects_stale_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The freshness gate rejects an output that no longer matches source."""
    source_path = tmp_path / "tools/release-matrix.json"
    schema_path = tmp_path / "tools/release-matrix.schema.json"
    output_path = tmp_path / "docs/releases/release-matrix.json"
    source_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    source_path.write_text(json.dumps(policy_matrix()), encoding="utf-8")
    schema_path.write_text(json.dumps({"type": "object"}), encoding="utf-8")

    monkeypatch.setattr(projection, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(projection, "SOURCE_MATRIX_PATH", source_path)
    monkeypatch.setattr(projection, "SOURCE_SCHEMA_PATH", schema_path)
    monkeypatch.setattr(projection, "PROJECTION_PATH", output_path)

    assert projection.main(["--write"]) == 0
    changed = json.loads(output_path.read_text(encoding="utf-8"))
    changed["entries"][0]["os"] = "stale"
    output_path.write_text(json.dumps(changed), encoding="utf-8")

    assert projection.main(["--check"]) == 1


def test_source_row_must_use_platform_independent_identity() -> None:
    """A source row with a platform claim must not be silently projected."""
    source = policy_matrix()
    source["entries"][-1]["os"] = "linux"
    with pytest.raises(ValueError, match="source row"):
        projection.build_projection(source)
