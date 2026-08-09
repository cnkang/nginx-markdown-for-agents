"""Tests for candidate-bound short-soak manifest generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import generate_soak_scenario_manifest as generator

ROOT = Path(generator.__file__).resolve().parents[3]
SCOPE = json.loads(
    (ROOT / "release/scope/short-soak-scope.json").read_text(encoding="utf-8")
)
SHA = "a" * 40


def test_build_manifest_binds_scope_to_candidate() -> None:
    manifest = generator.build_manifest(
        SCOPE, SHA, ROOT / "release/scope/short-soak-scope.json"
    )

    assert manifest["candidate_sha"] == SHA
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
            SCOPE, "not-a-sha", ROOT / "release/scope/short-soak-scope.json"
        )


def test_build_manifest_rejects_missing_scenario_memory() -> None:
    scope = dict(SCOPE)
    scope["scenario_memory_bytes"] = {"small": 1}

    with pytest.raises(ValueError, match="all scenario memory"):
        generator.build_manifest(
            scope, SHA, ROOT / "release/scope/short-soak-scope.json"
        )


def test_output_path_is_version_scoped() -> None:
    output = generator._output_path("0.9.2", None)
    assert output == ROOT / (
        "artifacts/release/0.9.2/short-soak-scenario-manifest.json"
    )

    with pytest.raises(ValueError, match="output must be"):
        generator._output_path("0.9.2", "artifacts/other/manifest.json")


def test_output_path_rejects_untrusted_version_components() -> None:
    with pytest.raises(ValueError, match="Invalid release version"):
        generator._output_path("0.9.2/../../outside", None)


def test_output_path_accepts_only_the_canonical_output() -> None:
    expected = generator._output_path(
        "0.9.2", "artifacts/release/0.9.2/short-soak-scenario-manifest.json"
    )

    assert expected == ROOT / (
        "artifacts/release/0.9.2/short-soak-scenario-manifest.json"
    )
