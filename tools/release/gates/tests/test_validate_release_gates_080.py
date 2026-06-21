"""Regression tests for the v0.8.x release-gate validator."""

from tools.release.gates.validate_release_gates_080 import (
    ValidationResult,
    validate_all,
)


def test_release_package_workflow_version_matches_active_release(monkeypatch):
    """The tag package workflow must use the active Cargo release version."""
    monkeypatch.setenv("RELEASE_GATE_EXPECTED_CARGO_VERSION", "0.8.1")
    result = ValidationResult()

    validate_all(result)

    workflow_checks = [
        (status, message)
        for status, check_id, message in result.results
        if check_id == "workflow:release-version"
    ]
    assert workflow_checks == [
        ("PASS", "release-packages.yml expects Cargo version 0.8.1")
    ]
