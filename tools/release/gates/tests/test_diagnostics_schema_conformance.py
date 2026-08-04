"""
Property 7: Diagnostics schema conformance — property-based tests.

Uses hypothesis to generate arbitrary module states and verify that
diagnostics JSON documents validate against the published schema at
schemas/diagnostics.schema.json.

Validates: Requirements 4.3
"""

import json
import sys
from pathlib import Path

# Ensure the tools package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import jsonschema
from hypothesis import given, settings, assume, example
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

_sha256_digest = st.from_regex(r"sha256:[0-9a-f]{64}", fullmatch=True)
_sha_commit = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)
_iso_datetime = st.from_regex(
    r"20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z",
    fullmatch=True,
)
_error_msg = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=100,
)


# --- Dynconf state strategies ---

def _dynconf_disabled():
    """Generate a disabled dynconf state."""
    return st.just({
        "state": "disabled",
        "generation": None,
        "source_digest": None,
        "active_digest": None,
        "lkg_digest": None,
        "last_success": None,
        "last_error": None,
    })


def _dynconf_no_file():
    """Generate a no_file dynconf state."""
    return st.just({
        "state": "no_file",
        "generation": None,
        "source_digest": None,
        "active_digest": None,
        "lkg_digest": None,
        "last_success": None,
        "last_error": None,
    })


@st.composite
def _dynconf_invalid_without_lkg(draw):
    """Generate an invalid_without_lkg dynconf state."""
    return {
        "state": "invalid_without_lkg",
        "generation": None,
        "source_digest": None,
        "active_digest": None,
        "lkg_digest": None,
        "last_success": None,
        "last_error": draw(_error_msg),
    }


@st.composite
def _dynconf_active(draw):
    """Generate an active dynconf state."""
    return {
        "state": "active",
        "generation": draw(st.integers(min_value=1, max_value=10000)),
        "source_digest": draw(_sha256_digest),
        "active_digest": draw(_sha256_digest),
        "lkg_digest": draw(_sha256_digest),
        "last_success": draw(_iso_datetime),
        "last_error": None,
    }


@st.composite
def _dynconf_lkg_preserved(draw):
    """Generate a lkg_preserved dynconf state."""
    return {
        "state": "lkg_preserved",
        "generation": draw(st.integers(min_value=1, max_value=10000)),
        "source_digest": draw(_sha256_digest),
        "active_digest": draw(_sha256_digest),
        "lkg_digest": draw(_sha256_digest),
        "last_success": draw(_iso_datetime),
        "last_error": draw(_error_msg),
    }


def _any_dynconf_state():
    """Generate any valid dynconf state."""
    return st.one_of(
        _dynconf_disabled(),
        _dynconf_no_file(),
        _dynconf_invalid_without_lkg(),
        _dynconf_active(),
        _dynconf_lkg_preserved(),
    )


# --- Effective config and sources strategies ---

@st.composite
def _effective_config(draw):
    """Generate a valid effective config object."""
    return {
        "filter": draw(st.sampled_from(["on", "off"])),
        "prune_noise": draw(st.sampled_from(["on", "off"])),
        "log_verbosity": draw(
            st.sampled_from(["error", "warn", "info", "debug"])
        ),
        "error_policy": draw(
            st.sampled_from(["pass", "fail_closed", "status 429", "status 503"])
        ),
        "streaming_buffer": draw(
            st.integers(min_value=65536, max_value=1073741824)
        ),
    }


@st.composite
def _effective_sources(draw):
    """Generate valid effective sources with correct provenance enum."""
    return {
        "filter": draw(
            st.sampled_from(["static", "dynconf", "request_variable"])
        ),
        "prune_noise": draw(st.sampled_from(["static", "dynconf"])),
        "log_verbosity": draw(st.sampled_from(["static", "dynconf"])),
        "error_policy": draw(st.sampled_from(["static", "dynconf"])),
        "streaming_buffer": draw(st.sampled_from(["static", "dynconf"])),
    }


# --- Full diagnostics document strategy ---

@st.composite
def _valid_diagnostics(draw):
    """Generate a complete valid diagnostics document."""
    return {
        "schema_version": 1,
        "product_version": draw(
            st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)
        ),
        "worker": {
            "pid": draw(st.integers(min_value=1, max_value=2**31)),
            "scope": "worker-local",
        },
        "build": {
            "source_sha": draw(_sha_commit),
            "nginx_version": draw(
                st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)
            ),
            "rust_version": draw(
                st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)
            ),
            "features": draw(
                st.lists(
                    st.from_regex(r"[a-z_]+", fullmatch=True),
                    min_size=0,
                    max_size=5,
                )
            ),
        },
        "configuration": {
            "static_digest": draw(_sha256_digest),
            "dynconf": draw(_any_dynconf_state()),
            "effective": draw(_effective_config()),
            "effective_sources": draw(_effective_sources()),
        },
        "runtime": {
            "inflight": draw(st.integers(min_value=0, max_value=10000)),
            "pending_output": draw(st.integers(min_value=0, max_value=10000)),
        },
        "recent_decisions": draw(
            st.lists(_decision_entry(), min_size=0, max_size=5)
        ),
    }


@st.composite
def _decision_entry(draw):
    """Generate a valid decision entry."""
    outcome = draw(
        st.sampled_from([
            "converted", "skipped", "failed_open", "failed_closed", "aborted"
        ])
    )
    is_failure = outcome in ("failed_open", "failed_closed", "aborted")
    return {
        "timestamp": draw(_iso_datetime),
        "outcome": outcome,
        "stage": draw(
            st.sampled_from([
                "eligibility", "decompression", "parsing", "conversion",
                "precommit", "postcommit", "delivery", "dynconf",
            ])
        ),
        "reason": draw(
            st.from_regex(r"[a-z][a-z0-9_]{2,30}", fullmatch=True)
        ),
        "error_origin": draw(
            st.sampled_from([
                "allocation", "downstream", "invariant", "format",
                "truncated", "timeout", "memory_budget", "internal",
            ])
        ) if is_failure else None,
        "duration_ms": draw(
            st.floats(min_value=0.0, max_value=60000.0, allow_nan=False)
        ),
    }


# ==========================================================================
# Property tests
# ==========================================================================


class TestTopLevelStructure:
    """Verify exactly 7 top-level fields and no forbidden fields."""

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
            "dynconf_state",
        ]),
    )
    def test_no_forbidden_top_level_fields(self, doc, forbidden):
        """Documents with forbidden fields must be rejected by the schema."""
        doc[forbidden] = {"some": "value"}
        _invalid(doc)


class TestDynconfStateDiscrimination:
    """Verify dynconf state discriminated correctly for each state value."""

    @settings(max_examples=50)
    @given(doc=_valid_diagnostics())
    def test_dynconf_always_present_never_null(self, doc):
        """configuration.dynconf is always a non-null object."""
        dynconf = doc["configuration"]["dynconf"]
        assert dynconf is not None
        assert isinstance(dynconf, dict)
        assert "state" in dynconf

    @settings(max_examples=50)
    @given(dynconf=_dynconf_disabled())
    def test_disabled_state_all_null(self, dynconf):
        """disabled: all six fields null."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None
        assert dynconf["last_error"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_no_file())
    def test_no_file_state_all_null(self, dynconf):
        """no_file: all fields null (no valid snapshot)."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None
        assert dynconf["last_error"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_invalid_without_lkg())
    def test_invalid_without_lkg_nulls_except_error(self, dynconf):
        """invalid_without_lkg: all null except last_error."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None
        assert dynconf["last_error"] is not None
        assert len(dynconf["last_error"]) >= 1

    @settings(max_examples=50)
    @given(dynconf=_dynconf_active())
    def test_active_state_non_null_fields(self, dynconf):
        """active: generation, digests, last_success non-null; last_error null."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is not None
        assert dynconf["generation"] >= 1
        assert dynconf["source_digest"] is not None
        assert dynconf["active_digest"] is not None
        assert dynconf["lkg_digest"] is not None
        assert dynconf["last_success"] is not None
        assert dynconf["last_error"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_lkg_preserved())
    def test_lkg_preserved_all_non_null(self, dynconf):
        """lkg_preserved: all fields non-null including last_error."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is not None
        assert dynconf["generation"] >= 1
        assert dynconf["source_digest"] is not None
        assert dynconf["active_digest"] is not None
        assert dynconf["lkg_digest"] is not None
        assert dynconf["last_success"] is not None
        assert dynconf["last_error"] is not None
        assert len(dynconf["last_error"]) >= 1

    @settings(max_examples=50)
    @given(dynconf=_dynconf_active())
    def test_active_non_null_rejected_when_null(self, dynconf):
        """Schema rejects active state when generation is null."""
        dynconf["generation"] = None
        doc = _make_doc_with_dynconf(dynconf)
        _invalid(doc)

    @settings(max_examples=50)
    @given(dynconf=_dynconf_disabled())
    def test_disabled_rejected_with_non_null_generation(self, dynconf):
        """Schema rejects disabled state when generation is non-null."""
        dynconf["generation"] = 5
        doc = _make_doc_with_dynconf(dynconf)
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


class TestNullFieldsWithoutSnapshot:
    """Verify null fields when no valid snapshot exists."""

    @settings(max_examples=50)
    @given(dynconf=_dynconf_no_file())
    def test_no_file_all_snapshot_fields_null(self, dynconf):
        """no_file state: generation, all digests, last_success are null."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_invalid_without_lkg())
    def test_invalid_without_lkg_all_snapshot_fields_null(self, dynconf):
        """invalid_without_lkg state: generation, all digests, last_success null."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None

    @settings(max_examples=30)
    @given(dynconf=_dynconf_no_file())
    def test_no_file_rejects_non_null_generation(self, dynconf):
        """Schema rejects no_file with non-null generation."""
        dynconf["generation"] = 1
        doc = _make_doc_with_dynconf(dynconf)
        _invalid(doc)

    @settings(max_examples=30)
    @given(dynconf=_dynconf_invalid_without_lkg())
    def test_invalid_without_lkg_rejects_non_null_digest(self, dynconf):
        """Schema rejects invalid_without_lkg with non-null active_digest."""
        dynconf["active_digest"] = "sha256:" + "a" * 64
        doc = _make_doc_with_dynconf(dynconf)
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


def _make_doc_with_dynconf(dynconf):
    """Build a minimal valid document with the given dynconf state."""
    return {
        "schema_version": 1,
        "product_version": "0.9.2",
        "worker": {"pid": 1234, "scope": "worker-local"},
        "build": {
            "source_sha": "a" * 40,
            "nginx_version": "1.27.0",
            "rust_version": "1.91.0",
            "features": ["streaming"],
        },
        "configuration": {
            "static_digest": "sha256:" + "b" * 64,
            "dynconf": dynconf,
            "effective": _base_effective(),
            "effective_sources": _static_sources(),
        },
        "runtime": {"inflight": 0, "pending_output": 0},
        "recent_decisions": [],
    }


def _make_doc_with_config(effective, sources):
    """Build a minimal valid document with the given effective/sources."""
    return {
        "schema_version": 1,
        "product_version": "0.9.2",
        "worker": {"pid": 1234, "scope": "worker-local"},
        "build": {
            "source_sha": "a" * 40,
            "nginx_version": "1.27.0",
            "rust_version": "1.91.0",
            "features": [],
        },
        "configuration": {
            "static_digest": "sha256:" + "b" * 64,
            "dynconf": {
                "state": "disabled",
                "generation": None,
                "source_digest": None,
                "active_digest": None,
                "lkg_digest": None,
                "last_success": None,
                "last_error": None,
            },
            "effective": effective,
            "effective_sources": sources,
        },
        "runtime": {"inflight": 0, "pending_output": 0},
        "recent_decisions": [],
    }
