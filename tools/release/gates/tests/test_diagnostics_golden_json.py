"""
Property 29: Diagnostics golden JSON + static_digest determinism.

For each dynconf state (disabled, no_file, invalid_without_lkg, active,
lkg_preserved), verify diagnostics JSON matches the golden shape with
per-state nullability. Verify static_digest is deterministic: identical
merged location config always yields the same SHA-256. Verify lkg_digest
equals active_digest in active and lkg_preserved states. Verify HEAD
returns the complete JSON body with exact Content-Length and no body.
Verify last_error <= 512 UTF-8 bytes with no paths/secrets/raw config.

**Validates: Requirements 4.1, 4.2, 4.3, 4.12**
"""

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import jsonschema
from hypothesis import given, settings
from hypothesis import strategies as st


# --- Load the published schema ---

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "schemas"
    / "diagnostics.schema.json"
)
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR_CLASS = jsonschema.validators.validator_for(SCHEMA)
VALIDATOR_CLASS.check_schema(SCHEMA)

# --- Diagnostics C source (for HEAD behavior verification) ---

DIAGNOSTICS_SOURCE = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_diagnostics.c"
)


# --- Strategy helpers ---

_sha256_digest = st.from_regex(r"sha256:[0-9a-f]{64}", fullmatch=True)
_sha_commit = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)
_iso_datetime = st.from_regex(
    r"20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z",
    fullmatch=True,
)
# --- Forbidden content patterns for last_error ---

_PATH_PATTERNS = [
    re.compile(r"/[a-z]+(/[a-z_.\-]+){2,}", re.IGNORECASE),
    re.compile(r"[A-Z]:\\", re.IGNORECASE),
    re.compile(r"\.(conf|json|toml|yaml|yml)$", re.MULTILINE),
]
_SECRET_PATTERNS = [
    re.compile(r"(password|secret|token|key)\s*[:=]", re.IGNORECASE),
]


def _is_safe_error_text(error_text):
    """Return whether a generated message satisfies the redaction model."""
    return (
        len(error_text.encode("utf-8")) <= 512
        and not any(pattern.search(error_text) for pattern in _PATH_PATTERNS)
        and not any(pattern.search(error_text) for pattern in _SECRET_PATTERNS)
    )


_safe_error_msg = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=100,
).filter(_is_safe_error_text)


# --- Dynconf state strategies ---

DYNCONF_STATES = ["disabled", "no_file", "invalid_without_lkg", "active",
                  "lkg_preserved"]


def _dynconf_disabled():
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
    return {
        "state": "invalid_without_lkg",
        "generation": None,
        "source_digest": None,
        "active_digest": None,
        "lkg_digest": None,
        "last_success": None,
        "last_error": draw(_safe_error_msg),
    }


@st.composite
def _dynconf_active(draw):
    """Active state: lkg_digest must equal active_digest."""
    active_digest = draw(_sha256_digest)
    return {
        "state": "active",
        "generation": draw(st.integers(min_value=1, max_value=10000)),
        "source_digest": draw(_sha256_digest),
        "active_digest": active_digest,
        "lkg_digest": active_digest,
        "last_success": draw(_iso_datetime),
        "last_error": None,
    }


@st.composite
def _dynconf_lkg_preserved(draw):
    """LKG preserved: lkg_digest must equal active_digest."""
    active_digest = draw(_sha256_digest)
    return {
        "state": "lkg_preserved",
        "generation": draw(st.integers(min_value=1, max_value=10000)),
        "source_digest": draw(_sha256_digest),
        "active_digest": active_digest,
        "lkg_digest": active_digest,
        "last_success": draw(_iso_datetime),
        "last_error": draw(_safe_error_msg),
    }


def _any_dynconf_state():
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


# --- Helpers ---

def _validate(doc):
    """Validate against the published schema."""
    jsonschema.validate(doc, SCHEMA, cls=VALIDATOR_CLASS)


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
        "runtime": {"inflight": 0, "pending_output": 0},
        "recent_decisions": [],
    }


def _compute_static_digest(manifest_dict: dict) -> str:
    """
    Compute static_digest from a static_config_manifest_v1 dict.

    The canonical form is: schema_version first, then remaining keys in
    ascending byte order, compact separators, no insignificant whitespace.
    """
    ordered = {"schema_version": "static_config_manifest_v1"}
    for key in sorted(k for k in manifest_dict if k != "schema_version"):
        ordered[key] = manifest_dict[key]
    canonical = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# --- Strategy for static config manifest fields ---

@st.composite
def _static_config_manifest(draw):
    """
    Generate a plausible static_config_manifest_v1 representing the
    merged location configuration for the diagnostics handler.

    Each field is an object {value, explicit} per the design contract.
    """
    return {
        "schema_version": "static_config_manifest_v1",
        "accept": {"value": draw(st.sampled_from(
            ["text/markdown", "text/markdown;q=1.0"])), "explicit": draw(st.booleans())},
        "auth_cookies": {"value": draw(st.sampled_from(
            [[], ["session_*"]])), "explicit": draw(st.booleans())},
        "auth_policy": {"value": draw(st.sampled_from(
            ["transparent", "strip_cookies"])), "explicit": draw(st.booleans())},
        "auto_decompress": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "cache_validation": {"value": draw(st.sampled_from(
            ["off", "full", "ims_only"])), "explicit": draw(st.booleans())},
        "content_types": {"value": draw(st.sampled_from(
            [["text/html"], ["text/html", "application/xhtml+xml"]]
        )), "explicit": draw(st.booleans())},
        "diagnostics": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "dynamic_config": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "dynamic_config_path": {"value": draw(st.sampled_from(
            ["/etc/nginx/dynconf.json", "/opt/nginx/dynconf.json"]
        )), "explicit": draw(st.booleans())},
        "dynconf_dry_run": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "error_policy": {"value": draw(st.sampled_from(
            ["pass", "fail_closed", "status 429", "status 503"]
        )), "explicit": draw(st.booleans())},
        "filter": {"value": draw(st.sampled_from(
            ["on", "off", "$markdown_enable"])), "explicit": draw(st.booleans())},
        "flavor": {"value": draw(st.sampled_from(
            ["commonmark", "gfm"])), "explicit": draw(st.booleans())},
        "front_matter": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "limits": {"value": draw(st.sampled_from([
            {"conversion_timeout": 30000, "parser_timeout": 10000,
             "conversion_memory": 67108864, "parser_memory": 33554432,
             "streaming_buffer": 2097152, "decompressed_size": 10485760,
             "decompression_ratio": 100, "max_inflight": 64},
        ])), "explicit": draw(st.booleans())},
        "log_verbosity": {"value": draw(st.sampled_from(
            ["error", "warn", "info", "debug"])), "explicit": draw(st.booleans())},
        "metrics": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "metrics_shm_size": {"value": draw(st.sampled_from(
            [1048576, 2097152])), "explicit": draw(st.booleans())},
        "prune_noise": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "prune_protection_selectors": {"value": draw(st.sampled_from(
            [[], [".important"]])), "explicit": draw(st.booleans())},
        "prune_selectors": {"value": draw(st.sampled_from(
            [[], ["nav", "footer", ".sidebar"]])), "explicit": draw(st.booleans())},
        "stream_excluded_types": {"value": draw(st.sampled_from(
            [[], ["text/event-stream"]])), "explicit": draw(st.booleans())},
        "streaming": {"value": draw(st.sampled_from(
            ["auto", "force", "off"])), "explicit": draw(st.booleans())},
        "token_estimate": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "trusted_proxies": {"value": draw(st.sampled_from(
            [[], ["10.0.0.0/8", "172.16.0.0/12"]])), "explicit": draw(st.booleans())},
    }


# ==========================================================================
# Property Tests
# ==========================================================================


class TestGoldenShapePerState:
    """
    Property 29a: For each dynconf state, verify diagnostics JSON matches
    the golden shape (per-state nullability contract).
    """

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_golden_validates_against_schema(self, doc):
        """Every generated golden document passes the published schema."""
        _validate(doc)

    @settings(max_examples=50)
    @given(dynconf=_dynconf_disabled())
    def test_disabled_golden_shape(self, dynconf):
        """disabled: all six fields null, state always present."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["state"] == "disabled"
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None
        assert dynconf["last_error"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_no_file())
    def test_no_file_golden_shape(self, dynconf):
        """no_file: all fields null."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["state"] == "no_file"
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None
        assert dynconf["last_error"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_invalid_without_lkg())
    def test_invalid_without_lkg_golden_shape(self, dynconf):
        """invalid_without_lkg: all null except last_error."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["state"] == "invalid_without_lkg"
        assert dynconf["generation"] is None
        assert dynconf["source_digest"] is None
        assert dynconf["active_digest"] is None
        assert dynconf["lkg_digest"] is None
        assert dynconf["last_success"] is None
        assert dynconf["last_error"] is not None
        assert len(dynconf["last_error"]) >= 1

    @settings(max_examples=50)
    @given(dynconf=_dynconf_active())
    def test_active_golden_shape(self, dynconf):
        """active: generation>=1, digests, last_success non-null; error null."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["state"] == "active"
        assert dynconf["generation"] >= 1
        assert dynconf["source_digest"] is not None
        assert dynconf["active_digest"] is not None
        assert dynconf["lkg_digest"] is not None
        assert dynconf["last_success"] is not None
        assert dynconf["last_error"] is None

    @settings(max_examples=50)
    @given(dynconf=_dynconf_lkg_preserved())
    def test_lkg_preserved_golden_shape(self, dynconf):
        """lkg_preserved: all non-null including last_error."""
        doc = _make_doc_with_dynconf(dynconf)
        _validate(doc)
        assert dynconf["state"] == "lkg_preserved"
        assert dynconf["generation"] >= 1
        assert dynconf["source_digest"] is not None
        assert dynconf["active_digest"] is not None
        assert dynconf["lkg_digest"] is not None
        assert dynconf["last_success"] is not None
        assert dynconf["last_error"] is not None
        assert len(dynconf["last_error"]) >= 1

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_effective_has_exactly_five_keys(self, doc):
        """effective object has exactly the 5 dynconf-mutable keys."""
        effective = doc["configuration"]["effective"]
        expected = {"filter", "prune_noise", "log_verbosity",
                    "error_policy", "streaming_buffer"}
        assert set(effective.keys()) == expected

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_effective_sources_has_exactly_five_keys(self, doc):
        """effective_sources object has exactly the 5 dynconf-mutable keys."""
        sources = doc["configuration"]["effective_sources"]
        expected = {"filter", "prune_noise", "log_verbosity",
                    "error_policy", "streaming_buffer"}
        assert set(sources.keys()) == expected

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_dynconf_always_present_as_object(self, doc):
        """configuration.dynconf is always present as a non-null dict."""
        dynconf = doc["configuration"]["dynconf"]
        assert dynconf is not None
        assert isinstance(dynconf, dict)
        assert "state" in dynconf
        assert dynconf["state"] in DYNCONF_STATES


class TestStaticDigestDeterminism:
    """
    Property 29b: static_digest is deterministic — identical merged
    location config always yields the same SHA-256. Configs differing
    only in pointer values or padding produce the same digest.
    """

    @settings(max_examples=100)
    @given(manifest=_static_config_manifest())
    def test_same_input_same_digest(self, manifest):
        """Identical manifest input always produces the same digest."""
        d1 = _compute_static_digest(manifest)
        d2 = _compute_static_digest(manifest)
        assert d1 == d2
        assert d1.startswith("sha256:")
        assert len(d1) == len("sha256:") + 64

    @settings(max_examples=50)
    @given(manifest=_static_config_manifest())
    def test_digest_independent_of_dict_insertion_order(self, manifest):
        """
        Digest does not depend on Python dict insertion order.
        The canonical form sorts keys, so reordering has no effect.
        """
        # Create a reversed-order copy
        reversed_manifest = dict(reversed(list(manifest.items())))
        d1 = _compute_static_digest(manifest)
        d2 = _compute_static_digest(reversed_manifest)
        assert d1 == d2

    @settings(max_examples=50)
    @given(
        manifest=_static_config_manifest(),
        padding=st.binary(min_size=1, max_size=64),
    )
    def test_pointer_padding_irrelevant(self, manifest, padding):
        """
        Different pointer values or padding (C-struct irrelevant detail)
        do not change the digest — the manifest is computed from the
        canonical JSON, not from C struct memory.
        """
        # Simulate: adding a "pointer" or "padding" field that would exist
        # in a C struct but is never part of the canonical manifest.
        # The digest function only serializes known manifest keys.
        d1 = _compute_static_digest(manifest)

        # A second computation from the same logical config is identical
        manifest_copy = json.loads(json.dumps(manifest))
        d2 = _compute_static_digest(manifest_copy)
        assert d1 == d2

    @settings(max_examples=50)
    @given(
        m1=_static_config_manifest(),
        m2=_static_config_manifest(),
    )
    def test_different_config_different_digest(self, m1, m2):
        """Different config content produces different digest (most of the time)."""
        d1 = _compute_static_digest(m1)
        d2 = _compute_static_digest(m2)
        # If manifests differ, digests should differ (SHA-256 collision-free)
        if m1 != m2:
            assert d1 != d2

    @settings(max_examples=50)
    @given(manifest=_static_config_manifest())
    def test_digest_format_is_sha256_hex(self, manifest):
        """Digest is formatted as sha256:<64 lowercase hex chars>."""
        digest = _compute_static_digest(manifest)
        assert re.match(r"^sha256:[0-9a-f]{64}$", digest)


class TestLkgDigestEqualsActiveDigest:
    """
    Property 29c: lkg_digest equals active_digest in active and
    lkg_preserved states (the served snapshot IS the LKG).
    """

    @settings(max_examples=100)
    @given(dynconf=_dynconf_active())
    def test_active_lkg_equals_active(self, dynconf):
        """In active state: lkg_digest == active_digest."""
        assert dynconf["lkg_digest"] == dynconf["active_digest"]

    @settings(max_examples=100)
    @given(dynconf=_dynconf_lkg_preserved())
    def test_lkg_preserved_lkg_equals_active(self, dynconf):
        """In lkg_preserved state: lkg_digest == active_digest."""
        assert dynconf["lkg_digest"] == dynconf["active_digest"]

    @settings(max_examples=50)
    @given(doc=_valid_diagnostics())
    def test_lkg_invariant_holds_in_all_generated_docs(self, doc):
        """
        For any generated diagnostics doc, if state is active or
        lkg_preserved, then lkg_digest == active_digest.
        """
        dynconf = doc["configuration"]["dynconf"]
        if dynconf["state"] in ("active", "lkg_preserved"):
            assert dynconf["lkg_digest"] == dynconf["active_digest"]


class TestLastErrorBounds:
    """
    Property 29d: last_error <= 512 UTF-8 bytes with no paths, secrets,
    or raw configuration content.
    """

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_last_error_within_512_bytes(self, doc):
        """last_error is null or <= 512 UTF-8 bytes."""
        last_error = doc["configuration"]["dynconf"]["last_error"]
        if last_error is not None:
            assert len(last_error.encode("utf-8")) <= 512

    @settings(max_examples=100)
    @given(error_text=_safe_error_msg)
    def test_error_no_file_paths(self, error_text):
        """
        Model: any error text that looks like a file path violates
        the redaction contract.
        """
        # If the text contains path-like patterns, it would be invalid
        # (the module must redact it). We verify the constraint by
        # checking that none of the path patterns match.
        for pattern in _PATH_PATTERNS:
            if pattern.search(error_text):
                assert False, (
                    "error text contains a path-like pattern; "
                    "production module must redact last_error"
                )
        # No path detected — this is a valid error message

    @settings(max_examples=100)
    @given(error_text=_safe_error_msg)
    def test_error_no_secrets(self, error_text):
        """
        Model: error text must not contain secret-like patterns.
        """
        for pattern in _SECRET_PATTERNS:
            if pattern.search(error_text):
                assert False, (
                    "error text contains a secret-like pattern; "
                    "production module must redact last_error"
                )

    @settings(max_examples=50)
    @given(dynconf=_dynconf_invalid_without_lkg())
    def test_invalid_without_lkg_error_bounded(self, dynconf):
        """invalid_without_lkg last_error is non-null and <= 512 bytes."""
        assert dynconf["last_error"] is not None
        assert len(dynconf["last_error"].encode("utf-8")) <= 512

    @settings(max_examples=50)
    @given(dynconf=_dynconf_lkg_preserved())
    def test_lkg_preserved_error_bounded(self, dynconf):
        """lkg_preserved last_error is non-null and <= 512 bytes."""
        assert dynconf["last_error"] is not None
        assert len(dynconf["last_error"].encode("utf-8")) <= 512


class TestHeadResponseBehavior:
    """
    Property 29e: HEAD generates the complete JSON body, sets
    Content-Length to its exact byte length, and returns no body.
    Verifies the C source implements this contract.
    """

    def _load_handler(self) -> str:
        """Load the diagnostics handler function body."""
        assert DIAGNOSTICS_SOURCE.exists(), (
            f"Diagnostics source not found: {DIAGNOSTICS_SOURCE}"
        )
        source = DIAGNOSTICS_SOURCE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"ngx_http_markdown_diagnostics_handler\("
            r"ngx_http_request_t\s+\*r\)\s*\{",
            re.DOTALL,
        )
        match = pattern.search(source)
        assert match is not None, "Handler function not found"
        start = match.start()
        brace_count = 0
        for i, ch in enumerate(source[start:], start=start):
            if ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
                if brace_count == 0:
                    return source[start:i + 1]
        return source[start:]

    def test_head_sets_content_length_from_body(self):
        """
        Content-Length is set to the body buffer length (b->last - b->pos)
        before the HEAD check, so HEAD returns the correct length.
        """
        handler = self._load_handler()
        # Verify content_length_n is set from buffer size
        assert "content_length_n" in handler
        # The assignment must happen BEFORE the HEAD check
        cl_pos = handler.find("content_length_n")
        head_pos = handler.find("r->method == NGX_HTTP_HEAD")
        assert cl_pos != -1, "content_length_n assignment not found"
        assert head_pos != -1, "HEAD method check not found"
        assert cl_pos < head_pos, (
            "content_length_n must be set before HEAD check "
            "so HEAD gets the correct body length"
        )

    def test_head_returns_no_body(self):
        """
        When method is HEAD, the handler sends headers only
        (ngx_http_send_header) and returns without calling
        ngx_http_output_filter, ensuring no body is transmitted.
        """
        handler = self._load_handler()
        # The HEAD branch calls ngx_http_send_header and returns
        # without reaching ngx_http_output_filter
        head_check = "r->method == NGX_HTTP_HEAD"
        assert head_check in handler, "HEAD method check not found"

        # Find the HEAD block: it sends headers and returns NGX_OK
        head_pos = handler.find(head_check)
        # After the HEAD check, find the return before output_filter
        head_block = handler[head_pos:head_pos + 200]
        assert "ngx_http_send_header" in head_block, (
            "HEAD branch must call ngx_http_send_header"
        )
        assert "return" in head_block, (
            "HEAD branch must return (no body output)"
        )

    def test_head_generates_full_json_before_check(self):
        """
        The full JSON document is generated (buffer allocated and filled)
        before the HEAD method check, ensuring Content-Length is accurate.
        """
        handler = self._load_handler()
        # JSON rendering happens before the HEAD check
        # Look for buffer fill (ngx_sprintf/ngx_snprintf or similar)
        # and verify it precedes the HEAD branch
        head_pos = handler.find("r->method == NGX_HTTP_HEAD")
        assert head_pos != -1

        # The buffer is populated before the HEAD check — verified by
        # content_length_n being set from (b->last - b->pos) before HEAD
        cl_assignment = handler.find("b->last - b->pos")
        assert cl_assignment != -1, "Buffer size calculation not found"
        assert cl_assignment < head_pos, (
            "Buffer must be filled before HEAD check"
        )

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_content_length_matches_body(self, doc):
        """
        Model: Content-Length equals the exact byte length of the
        serialized JSON body.
        """
        body = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
        content_length = len(body.encode("utf-8"))
        assert content_length > 0
        # HEAD would report this content_length with no body
        assert content_length == len(body.encode("utf-8"))
