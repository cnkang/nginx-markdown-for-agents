"""Consumer-side tests for candidate-bound release evidence generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import generate_release_gate_manifests as generator
from tools.release.gates import validate_fuzz_qualification as fuzz_validator

CANDIDATE_SHA = "a" * 40
GENERATED_AT = "2026-09-25T12:00:00+00:00"


def _write_fuzz_record(tmp_path: Path, record: dict) -> None:
    """Write one record at the path consumed by final-evidence generation."""
    (tmp_path / generator.FUZZ_QUALIFICATION_RECORD_NAME).write_text(
        json.dumps(record), encoding="utf-8")


def _write_fuzz_manifest(tmp_path: Path, manifest: dict) -> None:
    """Write the candidate-bound blocking fuzz target manifest."""
    (tmp_path / "blocking-fuzz-target-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")


def _fuzz_status(tmp_path: Path, monkeypatch) -> str:
    """Return the final evidence status for its blocking fuzz domain."""
    monkeypatch.setattr(
        generator, "_release_state",
        lambda: ("0.9.2", tmp_path, tmp_path),
    )
    evidence, _ = generator.build_final_evidence(CANDIDATE_SHA, GENERATED_AT)
    return next(
        entry["status"] for entry in evidence["entries"]
        if entry["domain"] == "fuzz"
    )


def _valid_manifest() -> dict:
    """Build a minimal blocking fuzz target manifest matching the record."""
    return {
        "schema_version": "release.blocking-fuzz-target-manifest.v1",
        "candidate_sha": CANDIDATE_SHA,
        "created_at": GENERATED_AT,
        "targets": [
            {"name": "parser_html", "seed": 12345,
             "required_minutes": 15, "required_executions": 100000,
             "blocking": True},
            {"name": "convert_html", "seed": 12345,
             "required_minutes": 15, "required_executions": 100000,
             "blocking": True},
        ],
        "threshold_reference": "test",
    }


def _valid_record() -> dict:
    """Build a minimal passing record with the pinned identity."""
    return {
        "schema_version": fuzz_validator.SCHEMA_VERSION,
        "candidate_sha": CANDIDATE_SHA,
        "blocking_pass": True,
        "toolchain_identity": dict(
            fuzz_validator._EXPECTED_FUZZ_TOOLCHAIN_IDENTITY),
        "per_target": [
            {"target": "parser_html", "seed": 12345,
             "elapsed_seconds_total": 950, "executions_total": 150000,
             "crashes": 0, "sanitizer_findings": 0,
             "corpus_dir": "", "seed_path": "",
             "raw_log_ref": "", "status": "pass"},
            {"target": "convert_html", "seed": 12345,
             "elapsed_seconds_total": 950, "executions_total": 150000,
             "crashes": 0, "sanitizer_findings": 0,
             "corpus_dir": "", "seed_path": "",
             "raw_log_ref": "", "status": "pass"},
        ],
    }


def test_final_evidence_accepts_valid_candidate_bound_toolchain(
        tmp_path: Path, monkeypatch) -> None:
    """A valid record from the pinned toolchain reaches the consumer as pass."""
    _write_fuzz_record(tmp_path, _valid_record())
    _write_fuzz_manifest(tmp_path, _valid_manifest())

    assert _fuzz_status(tmp_path, monkeypatch) == "pass"


@pytest.mark.parametrize("mutation", [
    "missing-identity",
    "malformed-identity",
    "stale-candidate",
    "old-schema",
    "missing-manifest",
    "missing-per-target",
    "duplicate-target",
    "failed-blocking-target",
])
def test_final_evidence_rejects_incomplete_or_unbound_fuzz_record(
        mutation: str, tmp_path: Path, monkeypatch) -> None:
    """The consumer must not trust only the producer's blocking_pass flag."""
    record = _valid_record()
    manifest = _valid_manifest()
    if mutation == "missing-identity":
        record.pop("toolchain_identity")
    elif mutation == "malformed-identity":
        record["toolchain_identity"]["llvm_version"] = "22.1.8"
    elif mutation == "stale-candidate":
        record["candidate_sha"] = "b" * 40
    elif mutation == "old-schema":
        record["schema_version"] = "release.fuzz-qualification.v1"
    elif mutation == "missing-manifest":
        # Don't write the manifest
        _write_fuzz_record(tmp_path, record)
        assert _fuzz_status(tmp_path, monkeypatch) == "fail"
        return
    elif mutation == "missing-per-target":
        record.pop("per_target")
    elif mutation == "duplicate-target":
        record["per_target"].append(dict(record["per_target"][0]))
    elif mutation == "failed-blocking-target":
        record["per_target"][0]["status"] = "fail"
    _write_fuzz_record(tmp_path, record)
    _write_fuzz_manifest(tmp_path, manifest)

    assert _fuzz_status(tmp_path, monkeypatch) == "fail"
