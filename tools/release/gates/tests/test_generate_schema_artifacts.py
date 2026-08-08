"""
Schema-drift artifact generator tests.

Verifies ``generate_schema_artifacts.py``:

- Projects metrics-registry.json from the canonical family, histogram,
  streaming-transition, and build-info contract
- Generates diagnostics-field-contract.json matching
  diagnostics.schema.json effective_config properties
- Generates dynconf-precedence-report.json with the five-tier hierarchy
  and field-specific provenance rules covering every dynconf schema key
- The generated artifacts pass the schema-drift validator end to end
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

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


def test_dynconf_precedence_report_covers_all_schema_keys():
    """Precedence report covers every dynconf schema key (minus version)."""
    report = gen.generate_dynconf_precedence_report()
    schema = json.loads(
        (REPO_ROOT / "schemas" / "dynconf.schema.json").read_text(
            encoding="utf-8"
        )
    )
    schema_keys = {
        key for key in schema["properties"] if key != "schema_version"
    }
    fields = report["field_specific_provenance_rules"]["fields"]
    assert set(fields.keys()) == schema_keys

    canonical = json.loads(
        (REPO_ROOT / "schemas" / "dynconf-precedence-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        report["five_tier_precedence_hierarchy"]
        == canonical["five_tier_precedence_hierarchy"]
    )


def test_dynconf_precedence_header_drift_is_rejected():
    """The generator must reject a changed precedence source or order."""
    header = (
        REPO_ROOT
        / "components"
        / "nginx-module"
        / "src"
        / "ngx_http_markdown_dynconf_precedence.h"
    ).read_text(encoding="utf-8")
    mutated = header.replace(
        "1. NGINX request variable evaluation",
        "1. Dynconf runtime override",
        1,
    )

    with pytest.raises(ValueError, match="tier 1"):
        gen._extract_precedence_hierarchy(
            mutated,
            gen._read_json(gen.DYNCONF_PRECEDENCE_CONTRACT_PATH)[
                "five_tier_precedence_hierarchy"
            ],
        )


def test_dynconf_rust_allowlist_drift_is_rejected(monkeypatch):
    """The generator must reject keys absent from the Rust parser allowlist."""
    rust_path = gen.DYNCONF_RUST_SCHEMA_PATH
    rust_source = rust_path.read_text(encoding="utf-8")
    mutated = rust_source.replace(
        '"streaming_buffer"', '"undocumented_field"', 1
    )
    original_read_text = gen._read_text

    def read_text(path):
        if path == rust_path:
            return mutated
        return original_read_text(path)

    monkeypatch.setattr(gen, "_read_text", read_text)
    with pytest.raises(ValueError, match="Rust KNOWN_KEYS"):
        gen.generate_dynconf_precedence_report()


def test_generated_artifacts_pass_schema_drift_validator(tmp_path, monkeypatch):
    """End-to-end: write artifacts then validate with the drift gate."""
    artifact_dir = tmp_path / "artifacts" / "release" / "0.9.2"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gen, "DEFAULT_VERSION", "0.9.2")
    gen.main([])

    sys.path.insert(
        0, str(REPO_ROOT / "tools" / "release" / "gates")
    )
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
        "DYNCONF_PRECEDENCE_REPORT",
        artifact_dir / "dynconf-precedence-report.json",
    )
    monkeypatch.setattr(
        validate_schema_drift,
        "RELEASE_ARTIFACTS",
        [
            artifact_dir / "dynconf-precedence-report.json",
            artifact_dir / "metrics-registry.json",
            artifact_dir / "diagnostics-field-contract.json",
        ],
    )
    monkeypatch.setattr(
        validate_schema_drift,
        "DYNCONF_SCHEMA",
        REPO_ROOT / "schemas" / "dynconf.schema.json",
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
    errors.extend(validate_schema_drift.gate_dynconf_schema())
    assert errors == [], f"Schema drift validator failed: {errors}"


@pytest.mark.parametrize("version", ["../escape", "/tmp/escape"])
def test_generator_rejects_path_escaping_version(version):
    """CLI version input cannot escape the release artifact directory."""
    with pytest.raises(SystemExit):
        gen.main(["--version", version])
