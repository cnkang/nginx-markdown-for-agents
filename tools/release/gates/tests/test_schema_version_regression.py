"""Schema-version regression coverage (Task 8.3, LTS-R023 / LTS-R030).

Task 8.1 bumped ``schemas/metrics-v1.registry.json`` schema_version 1 -> 2
(the ``nginx_markdown_dynconf_reloads_total`` family was removed, an
incompatible contract change explicitly versioned rather than masqueraded as
the frozen v1 content) and made ``validate_metrics_registry.py`` /
``validate_schema_drift.py`` require ``schema_version == 2``.

Task 8.2 bumped ``schemas/diagnostics.schema.json`` ``schema_version`` const
2 -> 3 (dynconf diagnostic state block removed, endpoint retained) and the C
emitter (``ngx_http_markdown_diagnostics.c``) emits ``"schema_version":3``.

This suite is the AGENTS.md Rule 14/20 regression coverage for those bumps.
It asserts two things per schema:

  1. The NEW schema versions ARE emitted (schema file const/value AND the
     runtime emitter that produces documents).
  2. Old-version MASQUERADE is REJECTED — a metrics registry claiming
     schema_version == 1 with the new (dynconf-removed) family set fails
     validation, and a diagnostics document claiming schema_version == 2
     (with or without the dynconf block) fails validation against the v3
     schema.

Design: §14(d) (explicit version, not masquerade).

Validates: Requirements LTS-R023, LTS-R030
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.release.gates import validate_metrics_registry as metrics_validator  # noqa: E402

METRICS_CONTRACT_PATH = REPO_ROOT / "schemas" / "metrics-v1.registry.json"
DIAGNOSTICS_SCHEMA_PATH = REPO_ROOT / "schemas" / "diagnostics.schema.json"
DIAGNOSTICS_EMITTER_PATH = (
    REPO_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_diagnostics.c"
)
METRICS_RENDERER_PATH = (
    REPO_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_metrics_v1_renderer.h"
)

def _load_metrics_contract() -> dict:
    return json.loads(METRICS_CONTRACT_PATH.read_text(encoding="utf-8"))


def _load_diagnostics_schema() -> dict:
    return json.loads(DIAGNOSTICS_SCHEMA_PATH.read_text(encoding="utf-8"))


def _diagnostics_validator():
    schema = _load_diagnostics_schema()
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return schema, validator_class


def _minimal_valid_diagnostics() -> dict:
    """One concrete valid v3 diagnostics document matching the emitter shape."""
    return {
        "schema_version": 3,
        "product_version": "0.9.2",
        "worker": {"pid": 1234, "scope": "worker-local"},
        "build": {
            "build_kind": "release",
            "source_sha": "a" * 40,
            "nginx_version": "1.27.0",
            "rust_version": "1.97.1",
            "feature_manifest_digest": "sha256:" + "c" * 64,
            "features": [],
        },
        "configuration": {
            "static_digest": "sha256:" + "b" * 64,
            "effective": {
                "filter": "on",
                "prune_noise": "off",
                "log_verbosity": "info",
                "error_policy": "pass",
                "streaming_buffer": 2097152,
            },
            "effective_sources": {
                "filter": "static",
                "prune_noise": "static",
                "log_verbosity": "static",
                "error_policy": "static",
                "streaming_buffer": "static",
            },
        },
        "runtime": {
            "diagnostics_recording": "active",
            "inflight": 0,
            "pending_output": 0,
            "module_metrics": {
                "streaming_requests_total": 0,
                "precommit_failopen_total": 0,
                "copied_output_total": 0,
                "diagnostics_recording_state": 1,
            },
        },
        "recent_decisions": [],
    }


# ===========================================================================
# Part 1 — the NEW schema versions ARE emitted
# ===========================================================================


class TestNewVersionsEmitted:
    """New metrics/diagnostics versions are present in schema + emitter."""

    def test_metrics_registry_schema_version_is_two(self):
        """Canonical metrics contract declares schema_version == 2."""
        contract = _load_metrics_contract()
        assert contract["schema_version"] == 2

    def test_metrics_dynconf_family_removed(self):
        """The removed dynconf family is absent from the canonical contract."""
        contract = _load_metrics_contract()
        family_names = {family["name"] for family in contract["families"]}
        assert "nginx_markdown_dynconf_reloads_total" not in family_names

    def test_metrics_renderer_emits_no_dynconf_family(self):
        """The C renderer emitter carries no dynconf metric family."""
        renderer = METRICS_RENDERER_PATH.read_text(encoding="utf-8")
        assert "nginx_markdown_dynconf_reloads_total" not in renderer

    def test_metrics_validator_accepts_version_two(self):
        """The structure validator passes the canonical version-2 contract."""
        contract = _load_metrics_contract()
        # A release artifact is a projection of the canonical contract; the
        # canonical document itself satisfies the structural gate.
        errors = metrics_validator.validate_registry_structure(
            contract, contract
        )
        assert errors == [], errors

    def test_diagnostics_schema_version_const_is_three(self):
        """diagnostics.schema.json pins schema_version const to 3."""
        schema = _load_diagnostics_schema()
        const = schema["properties"]["schema_version"]["const"]
        assert const == 3

    def test_diagnostics_emitter_emits_version_three(self):
        """The C diagnostics emitter writes "schema_version":3."""
        emitter = DIAGNOSTICS_EMITTER_PATH.read_text(encoding="utf-8")
        assert '"schema_version\\":3' in emitter or \
            '\\"schema_version\\":3' in emitter or \
            '"schema_version":3' in emitter

    def test_diagnostics_schema_has_no_dynconf_configuration_field(self):
        """The retained configuration object exposes no dynconf block."""
        schema = _load_diagnostics_schema()
        config_props = schema["properties"]["configuration"]["properties"]
        assert set(config_props) == {
            "static_digest",
            "effective",
            "effective_sources",
        }

    def test_emitted_v3_document_validates(self):
        """A concrete emitted-shape v3 document validates against the schema."""
        schema, validator_class = _diagnostics_validator()
        doc = _minimal_valid_diagnostics()
        assert doc["schema_version"] == 3
        jsonschema.validate(doc, schema, cls=validator_class)


# ===========================================================================
# Part 2 — old-version MASQUERADE is REJECTED
# ===========================================================================


class TestOldVersionMasqueradeRejected:
    """New content claiming an old schema version must fail validation."""

    def test_metrics_registry_masquerading_as_v1_is_rejected(self):
        """New (dynconf-removed) family set with schema_version 1 fails."""
        contract = _load_metrics_contract()
        masquerade = copy.deepcopy(contract)
        masquerade["schema_version"] = 1  # claim the old frozen contract

        # Structural gate rejects the old version outright.
        errors = metrics_validator.validate_registry_structure(
            masquerade, contract
        )
        assert any("schema_version must be 2" in error for error in errors), errors

        # The contract-projection gate also rejects it: a document that
        # differs from the canonical contract (here, its schema_version)
        # cannot pass as the frozen v1 content.
        masquerade["contract_source"] = "schemas/metrics-v1.registry.json"
        masquerade["implementation_sources"] = [
            "components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h"
        ]
        projection_errors = metrics_validator.validate_registry_matches_contract(
            masquerade, contract
        )
        assert projection_errors, projection_errors

    def test_metrics_registry_v1_with_new_families_not_silently_valid(self):
        """A v1-labelled artifact is never accepted with the new family set."""
        contract = _load_metrics_contract()
        masquerade = copy.deepcopy(contract)
        masquerade["schema_version"] = 1
        errors = metrics_validator.validate_registry_structure(
            masquerade, contract
        )
        assert errors != []

    def test_diagnostics_document_claiming_v2_is_rejected(self):
        """A v3-shaped document claiming schema_version 2 fails the schema."""
        schema, validator_class = _diagnostics_validator()
        masquerade = _minimal_valid_diagnostics()
        masquerade["schema_version"] = 2  # claim the old version
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(masquerade, schema, cls=validator_class)

    def test_diagnostics_v2_with_reintroduced_dynconf_block_rejected(self):
        """Old-version claim plus a dynconf block is doubly rejected."""
        schema, validator_class = _diagnostics_validator()
        masquerade = _minimal_valid_diagnostics()
        masquerade["schema_version"] = 2
        masquerade["configuration"]["dynconf"] = {
            "state": "disabled",
            "generation": None,
            "source_digest": None,
            "active_digest": None,
            "lkg_digest": None,
            "last_success": None,
            "last_error": None,
            "masked_keys": [],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(masquerade, schema, cls=validator_class)

    def test_diagnostics_dynconf_block_rejected_even_at_v3(self):
        """The removed dynconf block cannot be reintroduced under v3 either."""
        schema, validator_class = _diagnostics_validator()
        doc = _minimal_valid_diagnostics()
        assert doc["schema_version"] == 3
        doc["configuration"]["dynconf"] = {"state": "disabled"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, schema, cls=validator_class)
