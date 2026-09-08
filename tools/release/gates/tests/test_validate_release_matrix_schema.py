"""Regression tests for the release-matrix schema gate subprocess boundary."""

from __future__ import annotations

import subprocess
import copy
import json
from pathlib import Path

import jsonschema
import pytest

from tools.release.gates import validate_release_matrix_schema as validator


@pytest.mark.parametrize("legacy", [False, True])
def test_support_metadata_constraints_match_legacy_and_canonical(legacy):
    """Both row spellings enforce provenance, enums, and support consistency."""
    schema = json.loads((Path(__file__).resolve().parents[4]
                         / "schemas/release-matrix.schema.json").read_text())
    entry = {
        "nginx_version": "1.30.4", "os": "linux", "libc": "glibc",
        "target": "x86_64-unknown-linux-gnu", "artifact_type": "dynamic-module",
        "feature_manifest_digest": "sha256:" + "a" * 64, "abi_version": 3,
        "verification_state": "pending", "support_stage": "best-effort",
        "date": "2026-09-08", "source": "nginx.org",
        "provenance": {"kind": "nginx.org", "reference": "release evidence"},
        "support_tier": "best-effort",
    }
    key = "matrix" if legacy else "entries"
    if legacy:
        for canonical, alias in (("nginx_version", "nginx"), ("os", "os_type"),
                                 ("target", "arch")):
            entry[alias] = entry.pop(canonical)
    checker = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker())
    assert checker.is_valid({"schema_version": 1, key: [entry]})
    for field, value in (("verification_state", "unknown"),
                         ("support_stage", "unknown"), ("date", "yesterday"),
                         ("source", ""), ("provenance", {"kind": "other"})):
        invalid = copy.deepcopy(entry)
        invalid[field] = value
        assert not checker.is_valid({"schema_version": 1, key: [invalid]})
    entry.update(support_stage="excluded", support_tier="full")
    assert not checker.is_valid({"schema_version": 1, key: [entry]})
    entry["support_tier"] = "best-effort"
    assert checker.is_valid({"schema_version": 1, key: [entry]})


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
