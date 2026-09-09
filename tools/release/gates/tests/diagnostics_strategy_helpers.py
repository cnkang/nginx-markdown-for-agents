"""Shared hypothesis strategies for diagnostics property tests.

Both ``test_diagnostics_golden_json.py`` and
``test_diagnostics_schema_conformance.py`` used to define identical strategy
helpers inline.  They are collected here so the two suites share one canonical
generator instead of drifting.

The generic diagnostics document generators are self-contained: they reference
only helpers defined in this module.  The redaction-specific error-message
strategy used by the golden-JSON redaction tests stays with that test file
because it depends on its local forbidden-content patterns.

Schema v3 removed the dynconf diagnostic state block from the diagnostics
endpoint response (the dynamic-configuration hot-reload feature was removed;
the endpoint itself is retained).  The ``configuration`` object therefore
carries only ``static_digest``, ``effective``, and ``effective_sources``.
"""

from hypothesis import strategies as st


# --- Primitive strategies ---

_sha256_digest = st.from_regex(r"sha256:[0-9a-f]{64}", fullmatch=True)
_sha_commit = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)
_iso_datetime = st.from_regex(
    r"20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z",
    fullmatch=True,
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
            st.sampled_from(["static", "request_variable"])
        ),
        "prune_noise": draw(st.sampled_from(["static"])),
        "log_verbosity": draw(st.sampled_from(["static"])),
        "error_policy": draw(st.sampled_from(["static"])),
        "streaming_buffer": draw(st.sampled_from(["static"])),
    }


# --- Full diagnostics document strategy ---

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
        "stage": draw(st.sampled_from([
            "eligibility", "decompression", "parsing", "conversion",
            "precommit", "postcommit", "delivery",
        ])),
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


@st.composite
def _valid_diagnostics(draw):
    """Generate a complete valid diagnostics document."""
    return {
        "schema_version": 3,
        "product_version": draw(
            st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)
        ),
        "worker": {
            "pid": draw(st.integers(min_value=1, max_value=2**31)),
            "scope": "worker-local",
        },
        "build": {
            "build_kind": "release",
            "source_sha": draw(_sha_commit),
            "nginx_version": draw(
                st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)
            ),
            "rust_version": draw(
                st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)
            ),
            "feature_manifest_digest": draw(_sha256_digest),
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
            "effective": draw(_effective_config()),
            "effective_sources": draw(_effective_sources()),
        },
        "runtime": {
            "diagnostics_recording": "active",
            "inflight": draw(st.integers(min_value=0, max_value=10000)),
            "pending_output": draw(st.integers(min_value=0, max_value=10000)),
            "module_metrics": {
                "streaming_requests_total": draw(
                    st.integers(min_value=0, max_value=10000)
                ),
                "precommit_failopen_total": draw(
                    st.integers(min_value=0, max_value=10000)
                ),
                "copied_output_total": draw(
                    st.integers(min_value=0, max_value=10000)
                ),
                "diagnostics_recording_state": draw(
                    st.integers(min_value=0, max_value=2)
                ),
            },
        },
        "recent_decisions": draw(
            st.lists(_decision_entry(), min_size=0, max_size=5)
        ),
    }


__all__ = [
    "_sha256_digest",
    "_sha_commit",
    "_iso_datetime",
    "_effective_config",
    "_effective_sources",
    "_decision_entry",
    "_valid_diagnostics",
]
