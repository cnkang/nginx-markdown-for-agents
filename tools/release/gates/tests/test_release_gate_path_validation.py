"""Regression tests for release-gate path validation boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.release.gates import validate_artifact_registry as artifact_gate
from tools.release.gates import validate_release_candidate_evidence as candidate_gate
from tools.release.gates import validate_release_evidence_manifest as evidence_gate


@pytest.mark.parametrize(
    "loader",
    (
        artifact_gate.load_json,
        candidate_gate.load_json,
        evidence_gate.load_json,
    ),
)
def test_release_gate_loaders_reject_parent_paths(loader) -> None:
    """User-supplied JSON paths must not escape through parent components."""
    with pytest.raises(ValueError, match="Refusing path"):
        loader(Path("../outside.json"), "test manifest")


def test_artifact_digest_does_not_follow_external_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """Artifact metadata must not make the gate read outside the repository."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    external = tmp_path / "external.bin"
    external.write_bytes(b"external artifact")
    (repo_root / "artifact.bin").symlink_to(external)
    monkeypatch.setattr(artifact_gate, "REPO_ROOT", repo_root)

    reasons: list[str] = []
    artifact_gate._check_local_artifact_digest(
        {
            "artifact_id": "artifact.bin",
            "artifact_type": "source",
            "artifact_sha256": "sha256:" + hashlib.sha256(
                external.read_bytes()
            ).hexdigest(),
        },
        0,
        reasons,
    )

    assert any("escapes repository root" in reason for reason in reasons)


def test_candidate_digest_does_not_follow_external_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """Required-input metadata must not make the gate read outside the repo."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    external = tmp_path / "external.json"
    external.write_text("{}", encoding="utf-8")
    (repo_root / "input.json").symlink_to(external)
    monkeypatch.setattr(candidate_gate, "REPO_ROOT", repo_root)

    reasons: list[str] = []
    candidate_gate._check_required_inputs(
        {
            "required_inputs": ["input.json"],
            "input_digests": {"input.json": "sha256:" + "0" * 64},
        },
        reasons,
    )

    assert any("escapes repository root" in reason for reason in reasons)
