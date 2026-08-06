"""Regression tests for tools/release/gates/validate_soak_qualification.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import validate_soak_qualification as validator

REPO_ROOT = Path(validator.__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "release"
MANIFEST = FIXTURE_DIR / "soak-qualification-manifest.json"


def _run_fixture(record_name: str) -> int:
    return validator.main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(MANIFEST),
            "--record-input",
            str(FIXTURE_DIR / record_name),
        ]
    )


def test_valid_soak_record_passes() -> None:
    assert _run_fixture("soak-qualification-valid.json") == 0


def test_below_threshold_fails() -> None:
    with pytest.raises(SystemExit, match="below-threshold"):
        _run_fixture("soak-qualification-below-threshold.json")


def test_malformed_fails() -> None:
    with pytest.raises(SystemExit, match="malformed|missing-observation"):
        _run_fixture("soak-qualification-malformed.json")


def test_blocking_pending_fails() -> None:
    with pytest.raises(SystemExit, match="blocking-pending"):
        _run_fixture("soak-qualification-blocking-pending.json")


def test_stale_digest_fails() -> None:
    with pytest.raises(SystemExit, match="stale-digest"):
        _run_fixture("soak-qualification-stale-digest.json")


def test_missing_observation_fails() -> None:
    with pytest.raises(SystemExit, match="missing-observation"):
        _run_fixture("soak-qualification-missing-observation.json")


def test_manifest_contract_valid() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["duration_minutes"] == 30
    assert manifest["concurrency"] == 16
    assert {entry["id"] for entry in manifest["corpus"]} == {
        "small", "medium", "large"
    }
