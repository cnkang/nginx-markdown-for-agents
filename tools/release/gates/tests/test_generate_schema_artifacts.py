"""
Schema-drift artifact generator tests.

Verifies ``generate_schema_artifacts.py``:

- Projects metrics-registry.json from the canonical family, histogram,
  streaming-transition, and build-info contract
- Generates diagnostics-field-contract.json matching
  diagnostics.schema.json effective_config properties
- The generated artifacts pass the schema-drift validator end to end
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import generate_schema_artifacts as gen  # noqa: E402
from tools.release.gates import validate_metrics_registry as metrics_validator  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_metrics_registry_matches_canonical_families():
    """The generated registry must project the canonical family contract."""
    registry = gen.generate_metrics_registry()
    canonical = json.loads(
        (REPO_ROOT / "schemas" / "metrics-v1.registry.json").read_text(
            encoding="utf-8"
        )
    )
    families = registry["families"]
    assert families == canonical["families"]
    assert registry["schema_version"] == canonical["schema_version"]
    assert registry["format"] == canonical["format"]
    assert registry["contract_source"] == "schemas/metrics-v1.registry.json"
    assert registry["implementation_sources"] == [
        "components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h"
    ]


def test_metrics_registry_histogram_contract():
    """Histogram family carries the frozen bucket boundaries."""
    registry = gen.generate_metrics_registry()
    histograms = [
        family
        for family in registry["families"]
        if family["type"] == "histogram"
    ]
    assert len(histograms) == 1
    hist = histograms[0]
    assert hist["name"] == "nginx_markdown_conversion_duration_seconds"
    canonical = json.loads(
        (REPO_ROOT / "schemas" / "metrics-v1.registry.json").read_text(
            encoding="utf-8"
        )
    )
    canonical_hist = next(
        family
        for family in canonical["families"]
        if family["type"] == "histogram"
    )
    assert hist["bucket_count"] == canonical_hist["bucket_count"]
    assert hist["bucket_boundaries"] == canonical_hist["bucket_boundaries"]
    label_names = {label["name"] for label in hist["labels"]}
    assert label_names == {"engine"}


def test_metrics_registry_streaming_transition_allowlist():
    """streaming_events_total uses the canonical closed transition allowlist."""
    registry = gen.generate_metrics_registry()
    canonical = json.loads(
        (REPO_ROOT / "schemas" / "metrics-v1.registry.json").read_text(
            encoding="utf-8"
        )
    )
    families = {
        family["name"]: family for family in registry["families"]
    }
    family = families["nginx_markdown_streaming_events_total"]
    labels = {label["name"]: label for label in family["labels"]}
    assert "transition" in labels
    canonical_family = next(
        family
        for family in canonical["families"]
        if family["name"] == "nginx_markdown_streaming_events_total"
    )
    canonical_labels = {
        label["name"]: label for label in canonical_family["labels"]
    }
    assert labels["transition"]["values"] == canonical_labels["transition"]["values"]
    assert "event" not in labels


def test_metrics_registry_projection_rejects_contract_drift():
    """The release artifact cannot silently diverge from the canonical contract."""
    canonical = json.loads(
        (REPO_ROOT / "schemas" / "metrics-v1.registry.json").read_text(
            encoding="utf-8"
        )
    )
    artifact = gen.generate_metrics_registry()
    artifact["families"][0]["type"] = "gauge"
    errors = metrics_validator.validate_registry_matches_contract(
        artifact, canonical
    )
    assert errors


def test_metrics_registry_provenance_is_independent_and_explicit():
    """Metrics evidence identifies its contract and implementation inputs."""
    canonical = json.loads(
        (REPO_ROOT / "schemas" / "metrics-v1.registry.json").read_text(
            encoding="utf-8"
        )
    )
    artifact = gen.generate_metrics_registry()
    artifact["contract_source"] = "components/other-renderer.h"
    errors = metrics_validator.validate_registry_matches_contract(
        artifact, canonical
    )
    assert any("contract_source" in error for error in errors)

    artifact = gen.generate_metrics_registry()
    artifact["source"] = "components/other-renderer.h"
    errors = metrics_validator.validate_registry_matches_contract(
        artifact, canonical
    )
    assert any("ambiguous source" in error for error in errors)


def test_metrics_registry_build_info_gauge():
    """build_info is a gauge with value 1."""
    registry = gen.generate_metrics_registry()
    families = {
        family["name"]: family for family in registry["families"]
    }
    assert families["nginx_markdown_build_info"]["type"] == "gauge"
    assert families["nginx_markdown_build_info"]["value"] == 1


def test_diagnostics_field_contract_matches_schema():
    """Field contract mirrors diagnostics.schema.json effective_config."""
    contract = gen.generate_diagnostics_field_contract()
    schema = json.loads(
        (REPO_ROOT / "schemas" / "diagnostics.schema.json").read_text(
            encoding="utf-8"
        )
    )
    props = schema["$defs"]["effective_config"]["properties"]
    assert set(contract["effective_fields"].keys()) == set(props.keys())
    assert contract["constraints"]["field_count"] == len(props)
    streaming_buffer = contract["effective_fields"]["streaming_buffer"]
    assert streaming_buffer["type"] == "integer"
    assert streaming_buffer["bounds"] == {
        "minimum": 65536,
        "maximum": 1073741824,
    }


def test_generated_artifacts_pass_schema_drift_validator(tmp_path, monkeypatch):
    """End-to-end: write artifacts then validate with the drift gate."""
    artifact_dir = tmp_path / "artifacts" / "release" / "0.9.2"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gen, "DEFAULT_VERSION", "0.9.2")
    gen.main([])

    monkeypatch.syspath_prepend(str(REPO_ROOT / "tools" / "release" / "gates"))
    import validate_schema_drift  # noqa: E402

    monkeypatch.setattr(
        validate_schema_drift,
        "RELEASE_ARTIFACT_DIR",
        artifact_dir,
    )
    monkeypatch.setattr(
        validate_schema_drift,
        "METRICS_REGISTRY",
        artifact_dir / "metrics-registry.json",
    )
    monkeypatch.setattr(
        validate_schema_drift,
        "DIAGNOSTICS_FIELD_CONTRACT",
        artifact_dir / "diagnostics-field-contract.json",
    )
    monkeypatch.setattr(
        validate_schema_drift,
        "RELEASE_ARTIFACTS",
        [
            artifact_dir / "metrics-registry.json",
            artifact_dir / "diagnostics-field-contract.json",
        ],
    )
    monkeypatch.setattr(
        validate_schema_drift,
        "DIAGNOSTICS_SCHEMA",
        REPO_ROOT / "schemas" / "diagnostics.schema.json",
    )

    errors = []
    errors.extend(validate_schema_drift.gate_release_artifact_existence())
    errors.extend(validate_schema_drift.gate_release_artifact_structure())
    errors.extend(validate_schema_drift.gate_metrics_registry())
    errors.extend(validate_schema_drift.gate_diagnostics_field_contract())
    assert errors == [], f"Schema drift validator failed: {errors}"


@pytest.mark.parametrize("drift", [False, True])
def test_check_preserves_existing_artifacts(tmp_path, monkeypatch, drift):
    """Checks detect drift without repairing it or rewriting valid files."""
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    assert gen.main(["--version", "0.9.2"]) == 0
    artifact_dir = tmp_path / "artifacts" / "release" / "0.9.2"
    if drift:
        (artifact_dir / "metrics-registry.json").write_text("{}\n")
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in artifact_dir.iterdir()
    }
    assert gen.main(["--check", "--version", "0.9.2"]) == int(drift)
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in artifact_dir.iterdir()
    }
    assert after == before


def test_check_missing_artifacts_does_not_create_directory(tmp_path, monkeypatch):
    """A missing artifact fails the check without creating output paths."""
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    assert gen.main(["--check", "--version", "0.9.2"]) == 1
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("version", ["../escape", "/tmp/escape"])
def test_generator_rejects_path_escaping_version(version):
    """CLI version input cannot escape the release artifact directory."""
    with pytest.raises(SystemExit) as exc_info:
        gen.main(["--version", version])
    assert exc_info.value.code not in (None, 0)
