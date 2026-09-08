"""Regression tests for the six-status pre-LTS report contract."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release.gates import validate_pre_lts_status as validator

ROOT = Path(__file__).resolve().parents[4]
SCHEMA = json.loads(
    (ROOT / "schemas" / "pre-lts-status.schema.json").read_text(encoding="utf-8")
)


def _record(state: str, *, conditions: list[str] | None = None, phase_state=None):
    record = {
        "state": state,
        "evidence": ["test:evidence"],
        "conditions": [] if conditions is None else conditions,
    }
    if phase_state is not None:
        record["phase_state"] = phase_state
    return record


def _valid_report() -> dict:
    return {
        "schema_version": 1,
        "release": "0.9.2",
        "captured_at": "2026-09-07T00:00:00Z",
        "branch": "dev/wip-0.9.2rc7",
        "candidate": {
            "source_sha": "a" * 40,
            "working_tree": "dirty",
            "authorization": "not-granted",
        },
        "statuses": {
            "SPEC_READY": _record("PASS"),
            "IMPLEMENTATION_COMPLETE": _record(
                "NON_BLOCKING_OBSERVATION", conditions=["review pending"]
            ),
            "SUPPORT_MATRIX_VERIFIED": _record(
                "NON_BLOCKING_OBSERVATION", conditions=["matrix rows pending"]
            ),
            "PRE_LTS_READY": _record(
                "AWAITING_SOAK_OBSERVATION_EXTERNAL", conditions=["72h soak"]
            ),
            "OBSERVATION_STATUS": _record(
                "AWAITING_SOAK_OBSERVATION_EXTERNAL",
                conditions=["user-owned observation not started"],
                phase_state="not-started",
            ),
            "LTS_DECISION_PENDING": _record(
                "NON_BLOCKING_OBSERVATION", conditions=["user decision pending"]
            ),
        },
    }


def test_valid_report_passes() -> None:
    assert validator.validate_report(_valid_report(), SCHEMA) == []


def test_pending_is_not_a_status_vocabulary_value() -> None:
    report = _valid_report()
    report["statuses"]["PRE_LTS_READY"]["state"] = "pending"
    errors = validator.validate_report(report, SCHEMA)
    assert any("outside the shared vocabulary" in error for error in errors)


def test_pass_with_outstanding_condition_is_rejected() -> None:
    report = _valid_report()
    report["statuses"]["SPEC_READY"] = _record(
        "PASS", conditions=["review still pending"]
    )
    errors = validator.validate_report(report, SCHEMA)
    assert any("PASS cannot carry" in error for error in errors)


def test_missing_condition_on_blocked_status_is_rejected() -> None:
    report = _valid_report()
    report["statuses"]["PRE_LTS_READY"] = _record(
        "AWAITING_SOAK_OBSERVATION_EXTERNAL"
    )
    errors = validator.validate_report(report, SCHEMA)
    assert any("must explain its conditions" in error for error in errors)


def test_external_observation_cannot_be_reported_as_pass() -> None:
    report = _valid_report()
    report["statuses"]["OBSERVATION_STATUS"] = _record(
        "PASS", phase_state="completed-scope"
    )
    errors = validator.validate_report(report, SCHEMA)
    assert any("external observation cannot be PASS" in error for error in errors)
