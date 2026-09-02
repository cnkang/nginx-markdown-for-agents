"""
Property 4: Naming convention compliance — property-based tests.

Uses hypothesis to generate random names and verify that the naming
convention regexes correctly accept valid names and reject invalid ones.

Each property runs at least 100 iterations.

Validates: Requirements 18.1, 18.2, 18.3, 18.4
"""

import sys
from pathlib import Path

# Ensure the tools package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from hypothesis import given, settings, assume, example  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from tools.release.gates.validate_naming import (
    is_valid_nginx_directive,
    is_valid_prometheus_metric,
    is_valid_reason_code,
    is_valid_c_macro,
    is_forbidden_label,
    FORBIDDEN_LABELS,
    ALLOWED_LABELS,
)


# --- Strategy helpers ---

def _lower_alnum_underscore():
    """Generate strings of lowercase letters, digits, and underscores."""
    return st.from_regex(r"[a-z][a-z0-9_]{0,30}", fullmatch=True)


def _upper_alnum_underscore():
    """Generate strings of uppercase letters, digits, and underscores."""
    return st.from_regex(r"[A-Z][A-Z0-9_]{0,30}", fullmatch=True)


# --- NGINX directive tests ---

@settings(max_examples=100)
@given(suffix=_lower_alnum_underscore())
def test_valid_nginx_directive_plain(suffix):
    """Valid plain directive: markdown_<suffix>."""
    name = f"markdown_{suffix}"
    assert is_valid_nginx_directive(name), f"should accept: {name}"


@settings(max_examples=100)
@given(suffix=_lower_alnum_underscore())
def test_valid_nginx_directive_streaming(suffix):
    """Valid streaming directive: markdown_streaming_<suffix>."""
    name = f"markdown_streaming_{suffix}"
    assert is_valid_nginx_directive(name), f"should accept: {name}"


@settings(max_examples=100)
@given(name=st.text(min_size=1, max_size=40))
def test_invalid_nginx_directive_no_prefix(name):
    """Names without markdown_ prefix must be rejected."""
    assume(not name.startswith("markdown_"))
    assert not is_valid_nginx_directive(name), f"should reject: {name}"


# --- Prometheus metric tests ---

@settings(max_examples=100)
@given(suffix=_lower_alnum_underscore())
def test_invalid_prometheus_metric_without_unit_suffix(suffix):
    """Metrics must end in one legal Prometheus unit suffix."""
    # Skip generated bases that already end in a reserved unit token:
    # the composition could only be rejected by the double-unit negative
    # lookahead, which this test is not exercising.
    assume(not suffix.endswith(("_total", "_bytes", "_seconds", "_info")))
    name = f"nginx_markdown_{suffix}"
    assert not is_valid_prometheus_metric(name), f"should reject: {name}"


@settings(max_examples=100)
@given(
    suffix=_lower_alnum_underscore(),
    unit=st.sampled_from([
        "_total",
        "_bytes",
        "_seconds",
        "_info",
        "_bytes_total",
        "_seconds_total",
    ]),
)
def test_valid_prometheus_metric_with_unit(suffix, unit):
    """Valid metric with unit suffix."""
    name = f"nginx_markdown_{suffix}{unit}"
    # Exclude only the composed names whose base (suffix) already ends in a
    # reserved unit token AND the appended unit creates a forbidden
    # double-unit ending (e.g. foo_total_total, foo_bytes_bytes).  A base
    # ending in _bytes or _seconds combined with _total is legal
    # (canonical counter units bytes_total / seconds_total) and must be
    # exercised.  Evaluate the base suffix combined with the FIRST token
    # of the unit rather than the fully composed name, so compound units
    # such as _bytes_total and _seconds_total are not excluded by the
    # double-unit lookahead.  Mirror the validator's two lookaheads:
    # forbidden first-token pairs, and a reserved single token followed
    # by a compound bytes_total/seconds_total unit.
    reserved = ("total", "bytes", "seconds", "info")
    unit_tokens = unit.strip("_").split("_")
    base_last = suffix.rsplit("_", 1)[-1]
    if len(unit_tokens) == 1:
        forbidden_second = {
            "total": reserved,
            "bytes": ("bytes", "seconds", "info"),
            "seconds": ("bytes", "seconds", "info"),
            "info": reserved,
        }
        assume(unit_tokens[0] not in forbidden_second.get(base_last, ()))
    else:
        assume(base_last not in reserved)
    assert is_valid_prometheus_metric(name), f"should accept: {name}"


def test_valid_prometheus_metric_canonical_counter_units():
    """Counters may use the canonical bytes_total and seconds_total units."""
    for name in (
        "nginx_markdown_response_bytes_total",
        "nginx_markdown_request_duration_seconds_total",
    ):
        assert is_valid_prometheus_metric(name), f"should accept: {name}"


def test_invalid_prometheus_metric_repeated_unit_suffix():
    """The metric base must not consume a second unit suffix."""
    for name in (
        "nginx_markdown_requests_total_total",
        "nginx_markdown_payload_bytes_seconds",
        "nginx_markdown_build_info_info",
        "nginx_markdown_response_bytes_bytes_total",
        "nginx_markdown_duration_seconds_seconds_total",
    ):
        assert not is_valid_prometheus_metric(name), f"should reject: {name}"


@settings(max_examples=100)
@given(name=st.text(min_size=1, max_size=50))
def test_invalid_prometheus_metric_no_prefix(name):
    """Names without nginx_markdown_ prefix must be rejected."""
    assume(not name.startswith("nginx_markdown_"))
    assert not is_valid_prometheus_metric(name), f"should reject: {name}"


# --- Reason code tests ---

@settings(max_examples=100)
@given(code=st.from_regex(r"[a-z][a-z0-9_]{0,30}", fullmatch=True))
def test_valid_reason_code(code):
    """Valid reason codes match lowercase snake_case."""
    assert is_valid_reason_code(code), f"should accept: {code}"


@settings(max_examples=100)
@given(code=_upper_alnum_underscore())
def test_invalid_reason_code_uppercase(code):
    """Uppercase strings must be rejected as reason codes."""
    assert not is_valid_reason_code(code), f"should reject: {code}"


# --- C macro tests ---

@settings(max_examples=100)
@given(suffix=_upper_alnum_underscore())
def test_valid_c_macro(suffix):
    """Valid C macro: NGX_HTTP_MARKDOWN_<suffix>."""
    name = f"NGX_HTTP_MARKDOWN_{suffix}"
    assert is_valid_c_macro(name), f"should accept: {name}"


@settings(max_examples=100)
@given(name=st.text(min_size=1, max_size=50))
def test_invalid_c_macro_no_prefix(name):
    """Names without NGX_HTTP_MARKDOWN_ prefix must be rejected."""
    assume(not name.startswith("NGX_HTTP_MARKDOWN_"))
    assert not is_valid_c_macro(name), f"should reject: {name}"


# --- Forbidden label tests ---

@settings(max_examples=100)
@given(label=st.sampled_from(sorted(FORBIDDEN_LABELS)))
def test_forbidden_labels_detected(label):
    """All forbidden labels must be detected."""
    assert is_forbidden_label(label), f"should detect forbidden: {label}"


def _alternating_case(label: str) -> str:
    """Return a deterministic mixed-case variant of *label*."""
    return "".join(
        ch.upper() if i % 2 == 0 else ch.lower()
        for i, ch in enumerate(label)
    )


@settings(max_examples=100)
@example(label="url", casing="upper")
@example(label="host", casing="capitalized")
@example(label="referer", casing="alternating")
@given(
    label=st.sampled_from(sorted(FORBIDDEN_LABELS)),
    casing=st.sampled_from(["upper", "capitalized", "alternating"]),
)
def test_forbidden_labels_detected_case_insensitive(label, casing):
    """Forbidden labels must still be detected under case variants."""
    if casing == "upper":
        candidate = label.upper()
    elif casing == "capitalized":
        candidate = label.capitalize()
    else:
        candidate = _alternating_case(label)

    assert is_forbidden_label(candidate), (
        f"should detect forbidden case variant: {candidate}"
    )


@settings(max_examples=100)
@given(label=st.sampled_from(sorted(ALLOWED_LABELS)))
def test_allowed_labels_not_forbidden(label):
    """Allowed labels must not be flagged as forbidden."""
    assert not is_forbidden_label(label), f"should allow: {label}"
