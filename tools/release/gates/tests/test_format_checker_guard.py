"""Regression tests for the fail-closed jsonschema format-checker guard.

The guard exists because a bare ``jsonschema`` install registers no
``date-time`` checker, and ``jsonschema`` then SILENTLY skips every declared
``format`` constraint.  These tests pin the fail-closed behavior: the guard
raises when a required format is unregistered, and the production call sites
propagate that failure instead of validating without format checking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import FormatChecker

from tools.release.gates import format_checker_guard as guard
from tools.release.gates import validate_pre_lts_status as pre_lts
from tools.release.gates import validate_release_evidence_manifest as manifest

REPO_ROOT = Path(__file__).resolve().parents[4]
PRE_LTS_SCHEMA = REPO_ROOT / "schemas" / "pre-lts-status.schema.json"


def test_guard_returns_checker_with_required_formats_registered() -> None:
    """In a correctly provisioned environment the guard yields a checker."""
    checker = guard.require_format_checker()

    for name in guard.REQUIRED_FORMATS:
        assert name in checker.checkers


def test_guard_rejects_unregistered_formats(monkeypatch) -> None:
    """A checker missing a required format raises a clear, actionable error."""
    empty = FormatChecker(formats=())
    monkeypatch.setattr(
        "jsonschema.FormatChecker", lambda *args, **kwargs: empty
    )

    with pytest.raises(ValueError) as excinfo:
        guard.require_format_checker()

    message = str(excinfo.value)
    assert "date-time" in message
    assert "[format]" in message


def _valid_report() -> dict:
    return {
        "schema_version": 1,
        "release": "0.9.2",
        "captured_at": "2026-09-07T00:00:00Z",
        "branch": "dev/wip-0.9.2rc10",
        "candidate": {
            "source_sha": "a" * 40,
            "working_tree": "dirty",
            "authorization": "not-granted",
        },
        "statuses": {
            "SPEC_READY": {"state": "PASS", "evidence": ["t"], "conditions": []},
            "IMPLEMENTATION_COMPLETE": {
                "state": "NON_BLOCKING_OBSERVATION",
                "evidence": ["t"],
                "conditions": ["pending"],
            },
            "SUPPORT_MATRIX_VERIFIED": {
                "state": "NON_BLOCKING_OBSERVATION",
                "evidence": ["t"],
                "conditions": ["pending"],
            },
            "PRE_LTS_READY": {
                "state": "AWAITING_SOAK_OBSERVATION_EXTERNAL",
                "evidence": ["t"],
                "conditions": ["soak"],
            },
            "OBSERVATION_STATUS": {
                "state": "AWAITING_SOAK_OBSERVATION_EXTERNAL",
                "evidence": ["t"],
                "conditions": ["not started"],
                "phase_state": "not-started",
            },
            "LTS_DECISION_PENDING": {
                "state": "NON_BLOCKING_OBSERVATION",
                "evidence": ["t"],
                "conditions": ["decision"],
            },
        },
    }


def test_pre_lts_validator_fails_closed_without_format_checker(
    monkeypatch,
) -> None:
    """With the format extras missing, validation raises instead of passing."""
    schema = json.loads(PRE_LTS_SCHEMA.read_text(encoding="utf-8"))
    empty = FormatChecker(formats=())
    monkeypatch.setattr(
        "jsonschema.FormatChecker", lambda *args, **kwargs: empty
    )
    report = _valid_report()

    with pytest.raises(ValueError) as excinfo:
        pre_lts.validate_report(report, schema)

    assert "date-time" in str(excinfo.value)


def test_pre_lts_validator_still_rejects_bad_date_with_checker(
    monkeypatch,
) -> None:
    """Positive control: with the checker registered, a bad date is caught."""
    schema = json.loads(PRE_LTS_SCHEMA.read_text(encoding="utf-8"))
    report = _valid_report()
    report["captured_at"] = "2026-02-30T00:00:00Z"

    errors = pre_lts.validate_report(report, schema)

    assert any("date-time" in error for error in errors), errors


def test_evidence_manifest_fails_closed_without_format_checker(
    monkeypatch,
) -> None:
    """The evidence-manifest path shares the same fail-closed guarantee."""
    empty = FormatChecker(formats=())
    monkeypatch.setattr(manifest, "_FORMAT_CHECKER", None)
    monkeypatch.setattr(
        "jsonschema.FormatChecker", lambda *args, **kwargs: empty
    )

    with pytest.raises(ValueError) as excinfo:
        manifest._format_checker()

    assert "date-time" in str(excinfo.value)
