"""
Property 7: Diagnostics schema conformance — property-based tests.

Uses hypothesis to generate arbitrary module states and verify that
diagnostics JSON documents validate against the published schema at
schemas/diagnostics.schema.json.

Schema v3 removed the dynconf diagnostic state block: the dynamic-
configuration hot-reload feature was removed and the ``configuration``
object now carries only ``static_digest``, ``effective``, and
``effective_sources``.  The diagnostics endpoint itself is retained.

The seven top-level fields are frozen.  Additive state is published inside the
optional ``extensions`` container, which accepts any keys, so consumers can
ignore what they do not recognise without a schema version bump.

Validates: Requirements 4.3
"""

import json
import sys
from pathlib import Path

# Ensure the tools package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import jsonschema
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# --- Load the published schema ---

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "schemas"
    / "diagnostics.schema.json"
)
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

# Use the Draft 2020-12 validator
VALIDATOR_CLASS = jsonschema.validators.validator_for(SCHEMA)
VALIDATOR_CLASS.check_schema(SCHEMA)


def _validate(doc):
    """Validate a document against the diagnostics schema."""
    jsonschema.validate(doc, SCHEMA, cls=VALIDATOR_CLASS)


def _invalid(doc):
    """Assert that a document does NOT validate against the schema."""
    try:
        jsonschema.validate(doc, SCHEMA, cls=VALIDATOR_CLASS)
    except jsonschema.ValidationError:
        return
    raise AssertionError(f"Expected validation failure but document passed: {doc}")


# --- Strategy helpers ---
#
# Generic diagnostics document generators are shared with
# test_diagnostics_golden_json.py through diagnostics_strategy_helpers so
# the two suites use one canonical strategy set instead of drifting
# copies.  The digest, datetime, effective-config, effective-sources,
# decision-entry, and valid-diagnostics strategies all live there.

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnostics_strategy_helpers import (  # noqa: E402
    _effective_config,
    _effective_sources,
    _valid_diagnostics,
)


# ==========================================================================
# Property tests
# ==========================================================================


class TestTopLevelStructure:
    """Verify the seven frozen fields plus the optional additive container."""

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_valid_document_validates_against_schema(self, doc):
        """Generated valid diagnostics documents pass schema validation."""
        _validate(doc)

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_exactly_seven_top_level_fields(self, doc):
        """Every valid document has exactly 7 top-level fields."""
        assert len(doc) == 7
        expected_keys = {
            "schema_version",
            "product_version",
            "worker",
            "build",
            "configuration",
            "runtime",
            "recent_decisions",
        }
        assert set(doc.keys()) == expected_keys

    @settings(max_examples=50)
    @given(
        doc=_valid_diagnostics(),
        forbidden=st.sampled_from([
            "profile",
            "streaming_config",
            "streaming_metrics",
            "dynconf",
            "dynconf_state",
        ]),
    )
    def test_no_forbidden_top_level_fields(self, doc, forbidden):
        """Documents with forbidden fields must be rejected by the schema."""
        doc[forbidden] = {"some": "value"}
        _invalid(doc)

    @settings(max_examples=50)
    @given(doc=_valid_diagnostics(), payload=st.dictionaries(
        st.text(min_size=1, max_size=12),
        st.one_of(st.integers(), st.text(), st.dictionaries(
            st.text(min_size=1, max_size=8), st.integers(), max_size=3)),
        min_size=1, max_size=4,
    ))
    def test_extensions_container_accepts_unknown_keys(self, doc, payload):
        """Additive state may be published under extensions with any keys."""
        doc["extensions"] = payload
        _validate(doc)

    @settings(max_examples=50)
    @given(doc=_valid_diagnostics(), added=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12))
    def test_unknown_top_level_field_is_still_rejected(self, doc, added):
        """A new top-level field must fail: additive state belongs in extensions."""
        assume(added not in doc)
        doc[added] = {"some": "value"}
        _invalid(doc)


class TestConfigurationStructure:
    """Verify the configuration object has exactly the retained fields."""

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_configuration_has_exactly_three_keys(self, doc):
        """configuration has exactly static_digest, effective, effective_sources."""
        configuration = doc["configuration"]
        assert set(configuration.keys()) == {
            "static_digest",
            "effective",
            "effective_sources",
        }

    @settings(max_examples=50)
    @given(doc=_valid_diagnostics())
    def test_dynconf_block_rejected(self, doc):
        """A reintroduced dynconf block is rejected by the schema."""
        doc["configuration"]["dynconf"] = {
            "state": "disabled",
            "generation": None,
            "source_digest": None,
            "active_digest": None,
            "lkg_digest": None,
            "last_success": None,
            "last_error": None,
            "masked_keys": [],
        }
        _invalid(doc)


class TestEffectiveConfigSchema:
    """Verify effective + effective_sources schema."""

    @settings(max_examples=100)
    @given(effective=_effective_config(), sources=_effective_sources())
    def test_valid_effective_and_sources_pass(self, effective, sources):
        """Valid effective config and sources pass schema validation."""
        doc = _make_doc_with_config(effective, sources)
        _validate(doc)

    @settings(max_examples=50)
    @given(effective=_effective_config(), sources=_effective_sources())
    def test_effective_has_exactly_five_keys(self, effective, sources):
        """effective has exactly five keys."""
        assert len(effective) == 5
        expected = {
            "filter", "prune_noise", "log_verbosity",
            "error_policy", "streaming_buffer",
        }
        assert set(effective.keys()) == expected

    @settings(max_examples=50)
    @given(effective=_effective_config(), sources=_effective_sources())
    def test_effective_sources_has_exactly_five_keys(self, effective, sources):
        """effective_sources has exactly five keys."""
        assert len(sources) == 5
        expected = {
            "filter", "prune_noise", "log_verbosity",
            "error_policy", "streaming_buffer",
        }
        assert set(sources.keys()) == expected

    @settings(max_examples=50)
    @given(effective=_effective_config(), sources=_effective_sources())
    def test_additional_properties_rejected_in_effective(self, effective, sources):
        """additionalProperties: false rejects unknown keys in effective."""
        effective["unknown_field"] = "value"
        doc = _make_doc_with_config(effective, sources)
        _invalid(doc)

    @settings(max_examples=50)
    @given(effective=_effective_config(), sources=_effective_sources())
    def test_additional_properties_rejected_in_sources(self, effective, sources):
        """additionalProperties: false rejects unknown keys in sources."""
        sources["unknown_field"] = "static"
        doc = _make_doc_with_config(effective, sources)
        _invalid(doc)

    @settings(max_examples=50)
    @given(effective=_effective_config())
    def test_filter_provenance_includes_request_variable(self, effective):
        """filter source may be request_variable."""
        sources = {
            "filter": "request_variable",
            "prune_noise": "static",
            "log_verbosity": "static",
            "error_policy": "static",
            "streaming_buffer": "static",
        }
        doc = _make_doc_with_config(effective, sources)
        _validate(doc)


class TestStreamingBufferBounds:
    """Verify streaming_buffer accepts only integer byte count in range."""

    @settings(max_examples=100)
    @given(
        value=st.integers(min_value=65536, max_value=1073741824)
    )
    def test_valid_streaming_buffer_integer(self, value):
        """Integer values in 65536..1073741824 are accepted."""
        effective = {
            "filter": "on",
            "prune_noise": "off",
            "log_verbosity": "info",
            "error_policy": "pass",
            "streaming_buffer": value,
        }
        sources = _static_sources()
        doc = _make_doc_with_config(effective, sources)
        _validate(doc)

    @settings(max_examples=50)
    @given(value=st.integers(min_value=0, max_value=65535))
    def test_streaming_buffer_below_minimum_rejected(self, value):
        """Values below 65536 are rejected."""
        effective = _base_effective()
        effective["streaming_buffer"] = value
        doc = _make_doc_with_config(effective, _static_sources())
        _invalid(doc)

    @settings(max_examples=50)
    @given(value=st.integers(min_value=1073741825, max_value=2**33))
    def test_streaming_buffer_above_maximum_rejected(self, value):
        """Values above 1073741824 are rejected."""
        effective = _base_effective()
        effective["streaming_buffer"] = value
        doc = _make_doc_with_config(effective, _static_sources())
        _invalid(doc)

    @settings(max_examples=50)
    @given(
        size_str=st.sampled_from([
            "64k", "2m", "1g", "65536", "1048576",
            "2M", "64K", "1G", "128k",
        ])
    )
    def test_streaming_buffer_rejects_size_strings(self, size_str):
        """Size strings like '64k', '2m', '1g' are rejected (must be integer)."""
        effective = _base_effective()
        effective["streaming_buffer"] = size_str
        doc = _make_doc_with_config(effective, _static_sources())
        _invalid(doc)

    @settings(max_examples=50)
    @given(
        field=st.sampled_from([
            "prune_noise", "log_verbosity", "error_policy", "streaming_buffer"
        ])
    )
    def test_request_variable_rejected_for_non_filter_fields(self, field):
        """request_variable is rejected for every effective field except filter."""
        sources = _static_sources()
        sources[field] = "request_variable"
        effective = _base_effective()
        doc = _make_doc_with_config(effective, sources)
        _invalid(doc)


# ==========================================================================
# Helper functions for building minimal valid documents
# ==========================================================================

def _base_effective():
    """Return a minimal valid effective config."""
    return {
        "filter": "on",
        "prune_noise": "off",
        "log_verbosity": "info",
        "error_policy": "pass",
        "streaming_buffer": 2097152,
    }


def _static_sources():
    """Return effective_sources with all fields set to static."""
    return {
        "filter": "static",
        "prune_noise": "static",
        "log_verbosity": "static",
        "error_policy": "static",
        "streaming_buffer": "static",
    }


def _make_doc_with_config(effective, sources):
    """Build a minimal valid document with the given effective/sources."""
    return {
        "schema_version": 3,
        "product_version": "0.9.2",
        "worker": {"pid": 1234, "scope": "worker-local"},
        "build": {
            "build_kind": "release",
            "source_sha": "a" * 40,
            "nginx_version": "1.27.0",
            "rust_version": "1.91.0",
            "feature_manifest_digest": "sha256:" + "c" * 64,
            "features": [],
        },
        "configuration": {
            "static_digest": "sha256:" + "b" * 64,
            "effective": effective,
            "effective_sources": sources,
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
