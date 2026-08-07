"""
Schema-drift artifact generator tests.

Verifies ``generate_schema_artifacts.py``:

- Generates metrics-registry.json with exactly the 12 frozen families
  (counter/gauge/histogram), the histogram bucket contract, the
  streaming transition allowlist, and the build_info gauge value 1
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

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_metrics_registry_has_twelve_families():
    """The generated registry must contain exactly the 12 frozen families."""
    registry = gen.generate_metrics_registry()
    families = registry["families"]
    assert len(families) == 12
    assert registry["schema_version"] == 1
    assert registry["format"] == "prometheus_text_004"
    names = {family["name"] for family in families}
    assert "nginx_markdown_requests_total" in names
    assert "nginx_markdown_streaming_peak_memory_bytes" in names
    assert "nginx_markdown_build_info" in names


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
    assert hist["bucket_count"] == 10
    assert hist["bucket_boundaries"] == gen.HISTOGRAM_BUCKET_BOUNDARIES
    label_names = {label["name"] for label in hist["labels"]}
    assert label_names == {"engine"}


def test_metrics_registry_streaming_transition_allowlist():
    """streaming_events_total uses the closed transition allowlist."""
    registry = gen.generate_metrics_registry()
    families = {
        family["name"]: family for family in registry["families"]
    }
    family = families["nginx_markdown_streaming_events_total"]
    labels = {label["name"]: label for label in family["labels"]}
    assert "transition" in labels
    assert labels["transition"]["values"] == gen.STREAMING_TRANSITION_VALUES
    assert "event" not in labels


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

    hierarchy = report["five_tier_precedence_hierarchy"]
    assert len(hierarchy) == 5
    tiers = [tier["tier"] for tier in hierarchy]
    assert tiers == [1, 2, 3, 4, 5]
    sources = [tier["source"] for tier in hierarchy]
    assert sources == [
        "request_variable",
        "static",
        "dynconf",
        "http_baseline",
        "default",
    ]


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
        gen._extract_precedence_hierarchy(mutated)


def test_generated_artifacts_pass_schema_drift_validator():
    """End-to-end: write artifacts then validate with the drift gate."""
    artifact_dir = REPO_ROOT / "artifacts" / "release" / "0.9.2"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    for name, data in {
        "metrics-registry.json": gen.generate_metrics_registry(),
        "diagnostics-field-contract.json": gen.generate_diagnostics_field_contract(),
        "dynconf-precedence-report.json": gen.generate_dynconf_precedence_report(),
    }.items():
        (artifact_dir / name).write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    sys.path.insert(
        0, str(REPO_ROOT / "tools" / "release" / "gates")
    )
    import validate_schema_drift  # noqa: E402

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
