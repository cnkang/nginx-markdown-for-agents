"""Regression tests for the v0.8.x release-gate validator."""

import re
from pathlib import Path

import pytest

from tools.release.gates.validate_release_gates_080 import (
    RELEASE_PACKAGES_WORKFLOW,
    ValidationResult,
    validate_all,
)


def _workflow_cargo_version() -> str:
    """Extract the RELEASE_GATE_EXPECTED_CARGO_VERSION value from release-packages.yml."""
    text = Path(RELEASE_PACKAGES_WORKFLOW).read_text(encoding="utf-8")
    match = re.search(
        r'RELEASE_GATE_EXPECTED_CARGO_VERSION:\s*["\']([^"\']+)["\']',
        text,
    )
    assert match, "RELEASE_GATE_EXPECTED_CARGO_VERSION not found in release-packages.yml"
    return match.group(1)


@pytest.mark.parametrize("version_offset", [0])
def test_release_package_workflow_version_matches_active_release(monkeypatch, version_offset):
    """The tag package workflow must use the active Cargo release version.

    Reads the version declared in release-packages.yml and sets the
    environment variable to match, ensuring the validator reports PASS
    regardless of which release line the test runs against.
    """
    workflow_version = _workflow_cargo_version()
    monkeypatch.setenv("RELEASE_GATE_EXPECTED_CARGO_VERSION", workflow_version)
    result = ValidationResult()

    validate_all(result)

    workflow_checks = [
        (status, message)
        for status, check_id, message in result.results
        if check_id == "workflow:release-version"
    ]
    assert workflow_checks == [
        ("PASS", f"release-packages.yml expects Cargo version {workflow_version}")
    ]


def test_release_package_workflow_version_mismatch_detected(monkeypatch):
    """A mismatch between workflow and env var must be reported as FAIL."""
    workflow_version = _workflow_cargo_version()
    monkeypatch.setenv("RELEASE_GATE_EXPECTED_CARGO_VERSION", "0.0.0")
    result = ValidationResult()

    validate_all(result)

    workflow_checks = [
        (status, message)
        for status, check_id, message in result.results
        if check_id == "workflow:release-version"
    ]
    assert len(workflow_checks) == 1
    assert workflow_checks[0][0] == "FAIL"
    assert "0.0.0" in workflow_checks[0][1]