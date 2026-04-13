"""
Property 4: Naming convention compliance — property-based tests.

Uses hypothesis to generate random names and verify that the naming
convention regexes correctly accept valid names and reject invalid ones.

Each property runs at least 100 iterations.

Validates: Requirements 18.1, 18.2, 18.3, 18.4
"""

from hypothesis import given, settings, assume, example
from hypothesis import strategies as st

import sys
from pathlib import Path

# Ensure the tools package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from release_gates.validate_naming import (
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
def test_valid_prometheus_metric_plain(suffix):
    """Valid metric: nginx_markdown_<suffix>."""
    name = f"nginx_markdown_{suffix}"
    assert is_valid_prometheus_metric(name), f"should accept: {name}"


@settings(max_examples=100)
@given(
    suffix=_lower_alnum_underscore(),
    unit=st.sampled_from(["_total", "_bytes", "_seconds", "_info"]),
)
def test_valid_prometheus_metric_with_unit(suffix, unit):
    """Valid metric with unit suffix."""
    name = f"nginx_markdown_{suffix}{unit}"
    assert is_valid_prometheus_metric(name), f"should accept: {name}"


@settings(max_examples=100)
@given(name=st.text(min_size=1, max_size=50))
def test_invalid_prometheus_metric_no_prefix(name):
    """Names without nginx_markdown_ prefix must be rejected."""
    assume(not name.startswith("nginx_markdown_"))
    assert not is_valid_prometheus_metric(name), f"should reject: {name}"


# --- Reason code tests ---

@settings(max_examples=100)
@given(code=_upper_alnum_underscore())
def test_valid_reason_code(code):
    """Valid reason codes match uppercase SNAKE_CASE."""
    assert is_valid_reason_code(code), f"should accept: {code}"


@settings(max_examples=100)
@given(code=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True))
def test_invalid_reason_code_lowercase(code):
    """Lowercase strings must be rejected as reason codes."""
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
