"""Tests for candidate-bound short-soak manifest generation."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools.release.gates import generate_soak_scenario_manifest as generator

ROOT = Path(generator.__file__).resolve().parents[3]
SCOPE = json.loads(
    (ROOT / "release/scope/short-soak-scope.json").read_text(encoding="utf-8")
)
SHA = "a" * 40
CREATED_AT = "2026-08-12T00:00:00Z"


def _scope(duration: float | int) -> dict:
    scope = dict(SCOPE)
    scope["duration_minutes"] = duration
    return scope


def test_build_manifest_binds_scope_to_candidate() -> None:
    manifest = generator.build_manifest(
        SCOPE, SHA, ROOT / "release/scope/short-soak-scope.json", CREATED_AT
    )

    assert manifest["candidate_sha"] == SHA
    assert manifest["created_at"] == CREATED_AT
    assert manifest["duration_minutes"] == 30
    assert manifest["concurrency"] == 16
    assert manifest["corpus"] == [
        {"id": "small", "conversion_memory_bytes": 33554432},
        {"id": "medium", "conversion_memory_bytes": 67108864},
        {"id": "large", "conversion_memory_bytes": 100663296},
    ]


def test_build_manifest_rejects_invalid_candidate_sha() -> None:
    with pytest.raises(ValueError, match="candidate SHA"):
        generator.build_manifest(
            SCOPE,
            "not-a-sha",
            ROOT / "release/scope/short-soak-scope.json",
            CREATED_AT,
        )


def test_build_manifest_rejects_missing_scenario_memory() -> None:
    scope = dict(SCOPE)
    scope["scenario_memory_bytes"] = {"small": 1}

    with pytest.raises(ValueError, match="all scenario memory"):
        generator.build_manifest(
            scope,
            SHA,
            ROOT / "release/scope/short-soak-scope.json",
            CREATED_AT,
        )


def test_output_path_is_version_scoped() -> None:
    output = generator._output_path("0.9.2")
    assert output == ROOT / (
        "artifacts/release/0.9.2/short-soak-scenario-manifest.json"
    )


def test_output_path_rejects_untrusted_version_components() -> None:
    with pytest.raises(ValueError, match="Invalid release version"):
        generator._output_path("0.9.2/../../outside")

    assert generator._output_path("0.9.2") == ROOT / (
        "artifacts/release/0.9.2/short-soak-scenario-manifest.json"
    )


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf])
def test_scope_rejects_non_finite_duration(duration: float) -> None:
    scope = _scope(duration)
    with pytest.raises(ValueError, match="positive finite"):
        generator._validate_scope(scope)


def test_scope_accepts_positive_finite_duration() -> None:
    generator._validate_scope(_scope(30))


@pytest.mark.parametrize("field", ["duration_minutes", "concurrency"])
def test_scope_rejects_boolean_numeric_values(field: str) -> None:
    scope = dict(SCOPE)
    scope[field] = True
    with pytest.raises(ValueError):
        generator.build_manifest(
            scope,
            SHA,
            ROOT / "release/scope/short-soak-scope.json",
            CREATED_AT,
        )
