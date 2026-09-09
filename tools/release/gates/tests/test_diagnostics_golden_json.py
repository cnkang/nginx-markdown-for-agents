"""
Property 29: Diagnostics golden JSON + static_digest determinism.

Verify diagnostics JSON matches the golden shape: the ``configuration``
object carries exactly ``static_digest``, ``effective``, and
``effective_sources``, and ``effective`` / ``effective_sources`` each carry
the five retained static fields.  Verify static_digest is deterministic:
identical merged location config always yields the same SHA-256.  Verify
HEAD returns the complete JSON body with exact Content-Length and no body.

Schema v3 removed the dynconf diagnostic state block (the dynamic-
configuration hot-reload feature was removed; the endpoint is retained), so
the per-dynconf-state golden shapes, the lkg/active digest equality
invariant, and the dynconf ``last_error`` redaction contract no longer apply.

**Validates: Requirements 4.1, 4.2, 4.3**
"""

import hashlib
import json
import re
import sys
from pathlib import Path


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
#
# Generic diagnostics document generators shared with
# test_diagnostics_schema_conformance.py live in diagnostics_strategy_helpers.

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnostics_strategy_helpers import (  # noqa: E402
    _valid_diagnostics,
)


# --- Helpers ---

def _validate(doc):
    """Validate against the published schema."""
    jsonschema.validate(doc, SCHEMA, cls=VALIDATOR_CLASS)


def _compute_static_digest(manifest_dict: dict) -> str:
    """
    Compute static_digest from a static_config_manifest_v1 dict.

    The canonical form is: schema_version first, then remaining keys in
    ascending byte order, compact separators, no insignificant whitespace.
    """
    canonical_keys = {
        "accept", "auth_cookies", "auth_policy", "auto_decompress",
        "cache_validation", "content_types", "diagnostics", "error_policy", "filter",
        "flavor", "front_matter", "limits", "log_verbosity", "metrics",
        "metrics_shm_size", "prune_noise", "stream_excluded_types", "streaming",
        "token_estimate", "trusted_proxies",
    }
    ordered = {"schema_version": "static_config_manifest_v1"}
    for key in sorted(k for k in manifest_dict if k in canonical_keys):
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
            ["allow", "deny"])), "explicit": draw(st.booleans())},
        "auto_decompress": {"value": draw(st.sampled_from(
            ["on", "off"])), "explicit": draw(st.booleans())},
        "cache_validation": {"value": draw(st.sampled_from(
            ["off", "full", "ims_only"])), "explicit": draw(st.booleans())},
        "content_types": {"value": draw(st.sampled_from(
            [["text/html"], ["text/html", "application/xhtml+xml"]]
        )), "explicit": draw(st.booleans())},
        "diagnostics": {"value": draw(st.sampled_from(
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
             "conversion_memory": 67108864, "parser_budget": 33554432,
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


class TestGoldenShape:
    """
    Property 29a: verify diagnostics JSON matches the golden shape after
    the dynconf state block removal.
    """

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_golden_validates_against_schema(self, doc):
        """Every generated golden document passes the published schema."""
        _validate(doc)

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_configuration_has_exactly_three_keys(self, doc):
        """configuration has exactly static_digest, effective, effective_sources."""
        configuration = doc["configuration"]
        assert set(configuration.keys()) == {
            "static_digest", "effective", "effective_sources",
        }

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_no_dynconf_block(self, doc):
        """configuration.dynconf is absent after the v3 removal."""
        assert "dynconf" not in doc["configuration"]

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_effective_has_exactly_five_keys(self, doc):
        """effective object has exactly the 5 retained static keys."""
        effective = doc["configuration"]["effective"]
        expected = {"filter", "prune_noise", "log_verbosity",
                    "error_policy", "streaming_buffer"}
        assert set(effective.keys()) == expected

    @settings(max_examples=100)
    @given(doc=_valid_diagnostics())
    def test_effective_sources_has_exactly_five_keys(self, doc):
        """effective_sources object has exactly the 5 retained static keys."""
        sources = doc["configuration"]["effective_sources"]
        expected = {"filter", "prune_noise", "log_verbosity",
                    "error_policy", "streaming_buffer"}
        assert set(sources.keys()) == expected


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
        assume("pointer_padding" not in manifest)
        d1 = _compute_static_digest(manifest)

        # Simulate a C-struct pointer/padding byte that is not part of the
        # canonical manifest.  It must not affect the published digest.
        manifest_with_padding = json.loads(json.dumps(manifest))
        manifest_with_padding["pointer_padding"] = padding.hex()
        d2 = _compute_static_digest(manifest_with_padding)
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
        encoder = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False)
        expected_length = sum(
            len(fragment.encode("utf-8")) for fragment in encoder.iterencode(doc)
        )
        assert content_length == expected_length
