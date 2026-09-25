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


def _valid_record() -> dict:
    """Build a minimal passing record with the pinned identity."""
    return {
        "schema_version": fuzz_validator.SCHEMA_VERSION,
        "candidate_sha": CANDIDATE_SHA,
        "blocking_pass": True,
        "toolchain_identity": dict(
            fuzz_validator._EXPECTED_FUZZ_TOOLCHAIN_IDENTITY),
    }


def test_final_evidence_accepts_valid_candidate_bound_toolchain(
        tmp_path: Path, monkeypatch) -> None:
    """A valid record from the pinned toolchain reaches the consumer as pass."""
    _write_fuzz_record(tmp_path, _valid_record())

    assert _fuzz_status(tmp_path, monkeypatch) == "pass"


@pytest.mark.parametrize("mutation", [
    "missing-identity",
    "malformed-identity",
    "stale-candidate",
    "old-schema",
])
def test_final_evidence_rejects_incomplete_or_unbound_fuzz_record(
        mutation: str, tmp_path: Path, monkeypatch) -> None:
    """The consumer must not trust only the producer's blocking_pass flag."""
    record = _valid_record()
    if mutation == "missing-identity":
        record.pop("toolchain_identity")
    elif mutation == "malformed-identity":
        record["toolchain_identity"]["llvm_version"] = "22.1.8"
    elif mutation == "stale-candidate":
        record["candidate_sha"] = "b" * 40
    else:
        record["schema_version"] = "release.fuzz-qualification.v1"
    _write_fuzz_record(tmp_path, record)

    assert _fuzz_status(tmp_path, monkeypatch) == "fail"
