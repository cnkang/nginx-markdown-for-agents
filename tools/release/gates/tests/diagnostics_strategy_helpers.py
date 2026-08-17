"""Shared hypothesis strategies for diagnostics property tests.

Both ``test_diagnostics_golden_json.py`` and
``test_diagnostics_schema_conformance.py`` used to define identical strategy
helpers inline.  They are collected here so the two suites share one canonical
generator instead of drifting.

The generic diagnostics document generators are self-contained: they reference
only helpers defined in this module.  The redaction-specific error-message
strategy used by the golden-JSON redaction tests stays with that test file
because it depends on its local forbidden-content patterns.
"""

from hypothesis import strategies as st


# --- Primitive strategies ---

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
_masked_keys = st.lists(
    st.sampled_from([
        "filter", "prune_noise", "log_verbosity", "error_policy",
        "streaming_buffer",
    ]),
    unique=True,
    max_size=5,
)


# --- Dynconf state strategies ---

def _dynconf_disabled():
    """Generate a disabled dynconf state with fresh nested values per draw."""
    return st.builds(
        lambda masked_keys: {
            "state": "disabled",
            "generation": None,
            "source_digest": None,
            "active_digest": None,
            "lkg_digest": None,
            "last_success": None,
            "last_error": None,
            "masked_keys": masked_keys,
        },
        masked_keys=_masked_keys,
    )


def _dynconf_no_file():
    """Generate a no_file dynconf state with fresh nested values per draw."""
    return st.builds(
        lambda masked_keys: {
            "state": "no_file",
            "generation": None,
            "source_digest": None,
            "active_digest": None,
            "lkg_digest": None,
            "last_success": None,
            "last_error": None,
            "masked_keys": masked_keys,
        },
        masked_keys=_masked_keys,
    )


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
        "masked_keys": draw(_masked_keys),
    }


@st.composite
def _dynconf_active(draw):
    """Generate an active dynconf state (lkg_digest equals active_digest)."""
    active_digest = draw(_sha256_digest)
    return {
        "state": "active",
        "generation": draw(st.integers(min_value=1, max_value=10000)),
        "source_digest": draw(_sha256_digest),
        "active_digest": active_digest,
        "lkg_digest": active_digest,
        "last_success": draw(_iso_datetime),
        "last_error": None,
        "masked_keys": draw(_masked_keys),
    }


@st.composite
def _dynconf_lkg_preserved(draw):
    """Generate a lkg_preserved dynconf state (lkg equals active)."""
    active_digest = draw(_sha256_digest)
    return {
        "state": "lkg_preserved",
        "generation": draw(st.integers(min_value=1, max_value=10000)),
        "source_digest": draw(_sha256_digest),
        "active_digest": active_digest,
        "lkg_digest": active_digest,
        "last_success": draw(_iso_datetime),
        "last_error": draw(_error_msg),
        "masked_keys": draw(_masked_keys),
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


@st.composite
def _valid_diagnostics(draw):
    """Generate a complete valid diagnostics document."""
    return {
        "schema_version": 2,
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


__all__ = [
    "_sha256_digest",
    "_sha_commit",
    "_iso_datetime",
    "_error_msg",
    "_masked_keys",
    "_dynconf_disabled",
    "_dynconf_no_file",
    "_dynconf_invalid_without_lkg",
    "_dynconf_active",
    "_dynconf_lkg_preserved",
    "_any_dynconf_state",
    "_effective_config",
    "_effective_sources",
    "_decision_entry",
    "_valid_diagnostics",
]
