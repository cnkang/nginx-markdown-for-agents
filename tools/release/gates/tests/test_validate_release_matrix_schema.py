"""Regression tests for the release-matrix schema gate subprocess boundary."""

from __future__ import annotations

import subprocess

from tools.release.gates import validate_release_matrix_schema as validator


def test_run_normalization_fails_closed_when_process_cannot_start(monkeypatch):
    """A normalizer spawn failure must become a gate failure, not an exception."""

    def raise_oserror(*args, **kwargs):
        raise OSError("executable unavailable")

    monkeypatch.setattr(subprocess, "run", raise_oserror)

    ok, message = validator.run_normalization({"schema_version": 1})

    assert not ok
    assert "normalizer execution failed" in message


def test_malformed_schema_types_are_reported_without_attribute_error():
    """Malformed schema nodes produce structured failures, not a crash."""
    failures: list[str] = []
    validator.check_schema_shape(
        {
            "properties": {"schema_version": "not-an-object"},
            "$defs": {"entry": {"properties": []}},
        },
        failures,
    )

    assert any("schema_version property must be an object" in item for item in failures)
    assert any("entry.properties must be an object" in item for item in failures)
