"""Regression tests for the v0.8.x release-gate validator."""

import tomllib
from pathlib import Path

from tools.release.gates.validate_release_gates_080 import (
    CARGO_TOML_PATH,
    RELEASE_PACKAGES_WORKFLOW,
    ValidationResult,
    check_new_directives,
    validate_all,
)


def _cargo_package_version() -> str:
    """Read the expected release version from the independent package source."""
    cargo = tomllib.loads(CARGO_TOML_PATH.read_text(encoding="utf-8"))
    return cargo["package"]["version"]


def test_release_package_workflow_version_matches_active_release(monkeypatch):
    """The tag package workflow must use the active Cargo release version.

    Reads the version declared by the Rust package source and sets the
    environment variable to match. The workflow is then checked against that
    independent release identity.
    """
    cargo_version = _cargo_package_version()
    monkeypatch.setenv("RELEASE_GATE_EXPECTED_CARGO_VERSION", cargo_version)
    result = ValidationResult()

    validate_all(result)

    workflow_checks = [
        (status, message)
        for status, check_id, message in result.results
        if check_id == "workflow:release-version"
    ]
    assert workflow_checks == [
        ("PASS", f"release-packages.yml declares release version {cargo_version}")
    ]


def test_release_package_workflow_version_mismatch_detected(monkeypatch):
    """A mismatch between workflow and env var must be reported as FAIL."""

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


def test_release_gate_downloads_canonical_amd64_module_artifact():
    """The benchmark gate must consume the build job's amd64 artifact name."""

    workflow = Path(RELEASE_PACKAGES_WORKFLOW).read_text(encoding="utf-8")

    assert (
        "name: module-so-${{ steps.bench-nginx.outputs.bench_nginx_version }}-amd64"
        in workflow
    )
    assert (
        "name: module-so-${{ steps.bench-nginx.outputs.bench_nginx_version }}-x86_64"
        not in workflow
    )


def test_new_directive_gate_skips_directives_retired_before_current_release():
    """The 0.9 release must not require retired 0.8 directive names."""
    result = ValidationResult()

    check_new_directives(result, active_version="0.9.2")

    statuses = {
        check_id: status
        for status, check_id, _ in result.results
    }
    assert statuses["new:directive:markdown_stream_threshold"] == "SKIP"
    assert statuses["new:directive:markdown_stream_precommit_buffer"] == "SKIP"
    assert statuses["new:directive:markdown_stream_flush_min"] == "SKIP"
    assert statuses["new:directive:markdown_stream_excluded_types"] == "PASS"
