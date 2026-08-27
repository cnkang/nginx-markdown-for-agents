"""Regression tests for release-gate path validation boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.release.gates import validate_artifact_registry as artifact_gate
from tools.release.gates import validate_config_directives as config_gate
from tools.release.gates import validate_fuzz_packaging as fuzz_gate
from tools.release.gates import validate_metrics_registry as metrics_registry_gate
from tools.release.gates import validate_release_candidate_evidence as candidate_gate
from tools.release.gates import validate_release_evidence_manifest as evidence_gate


@pytest.mark.parametrize(
    "loader",
    (
        artifact_gate.load_json,
        candidate_gate.load_json,
        evidence_gate.load_json,
        metrics_registry_gate._load_json,
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


def test_artifact_index_rows_bind_to_frozen_candidate_sha(monkeypatch) -> None:
    """Every candidate artifact row must identify the frozen candidate."""
    expected_sha = "a" * 40
    monkeypatch.setattr(artifact_gate, "frozen_feature_digest", lambda: "sha256:" + "b" * 64)
    monkeypatch.setattr(artifact_gate, "frozen_abi_version", lambda: 2)

    reasons = artifact_gate.validate_index(
        {
            "schema_version": artifact_gate.INDEX_SCHEMA_VERSION,
            "candidate_sha": expected_sha,
            "artifacts": [
                {
                    "artifact_type": "source",
                    "release_matrix_row_id": "source-1",
                    "artifact_id": "source.tar.gz",
                    "candidate_sha": "c" * 40,
                    "artifact_sha256": "sha256:" + "d" * 64,
                    "feature_manifest_digest": "sha256:" + "b" * 64,
                    "abi_version": 2,
                    "verification_status": "pass",
                }
            ],
        },
        expected_sha,
    )

    assert any("candidate_sha" in reason and "frozen candidate" in reason
               for reason in reasons)


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


def test_candidate_manifest_null_digests_fail_closed() -> None:
    """JSON null must not satisfy a required release digest field."""
    reasons: list[str] = []
    candidate_gate._check_manifest_digest_fields(
        {
            "feature_manifest_digest": None,
            "final_ffi_freeze_digest": None,
            "canonical_performance_environment_digest": None,
            "release_matrix_digest": None,
        },
        reasons,
    )

    assert len(reasons) == 4
    assert all("must be a sha256 digest" in reason for reason in reasons)


def test_candidate_evidence_schema_null_digest_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """Declared evidence schema digests must reject null values."""
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(candidate_gate, "REPO_ROOT", tmp_path)
    reasons: list[str] = []

    candidate_gate._check_evidence_schemas(
        {"evidence_schema_digests": {"schema.json": None}}, reasons
    )

    assert reasons == [
        "malformed: evidence schema digest for 'schema.json' "
        "must be a sha256 digest"
    ]


@pytest.mark.parametrize(
    "gate",
    (config_gate, fuzz_gate),
)
def test_read_safe_rejects_project_prefix_sibling(
    gate, tmp_path: Path, monkeypatch
) -> None:
    """A sibling such as repo-extra is outside the repository root."""
    repo_root = tmp_path / "repo"
    sibling = tmp_path / "repo-extra"
    repo_root.mkdir()
    sibling.mkdir()
    candidate = sibling / "secret.txt"
    candidate.write_text("outside", encoding="utf-8")
    monkeypatch.setattr(gate, "PROJECT_ROOT", repo_root)

    assert gate.read_safe(candidate) == ""


def test_config_directive_checks_expand_name_macros():
    """Directive registration checks must understand the canonical name header."""
    result = config_gate.ValidationResult()
    source = "ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_CURRENT),\n"
    macros = {"NGX_HTTP_MARKDOWN_DIRECTIVE_CURRENT": "markdown_filter"}

    config_gate.check_directive_in_source(
        "markdown_filter", source, macros, result
    )
    config_gate.check_directive_not_in_source(
        "markdown_filter", source, macros, result
    )

    assert result.results[0][0] == "PASS"
    assert result.results[1][0] == "FAIL"
