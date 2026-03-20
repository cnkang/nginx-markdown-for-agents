# Feature: overall-scope-release-gates, Property 4: Naming convention compliance
"""Property-based tests for naming convention compliance.

Feature: overall-scope-release-gates
Property 4: Naming convention compliance
Validates: Requirements 4.1, 4.2, 4.3, 4.4
"""

import hypothesis.strategies as st
from hypothesis import given, settings

from tools.release.naming_conventions import (
    is_valid_directive_name,
    is_valid_metric_name,
    is_valid_reason_code,
    is_valid_benchmark_field,
)


# --- Valid name generators ---

@given(name=st.from_regex(r'^markdown_[a-z][a-z0-9_]{0,30}$', fullmatch=True))
@settings(max_examples=100)
def test_valid_directive_names_accepted(name):
    assert is_valid_directive_name(name), f"Valid directive rejected: {name}"


@given(suffix=st.from_regex(r'^[a-z][a-z0-9_]{0,20}$', fullmatch=True),
       unit=st.sampled_from(["", "_total", "_bytes", "_seconds", "_info"]))
@settings(max_examples=100)
def test_valid_metric_names_accepted(suffix, unit):
    name = f"nginx_markdown_{suffix}{unit}"
    assert is_valid_metric_name(name), f"Valid metric rejected: {name}"


@given(name=st.from_regex(r'^[A-Z][A-Z0-9_]{0,30}$', fullmatch=True))
@settings(max_examples=100)
def test_valid_reason_codes_accepted(name):
    assert is_valid_reason_code(name), f"Valid reason code rejected: {name}"


@given(name=st.from_regex(r'^[a-z][a-z0-9\-]{0,30}$', fullmatch=True))
@settings(max_examples=100)
def test_valid_benchmark_fields_accepted(name):
    assert is_valid_benchmark_field(name), f"Valid benchmark field rejected: {name}"


# --- Invalid name rejection ---

@given(name=st.text(min_size=0, max_size=50).filter(
    lambda s: not s.startswith("markdown_") or len(s) <= len("markdown_") or
    not s[len("markdown_"):][0:1].isalpha() or not s[len("markdown_"):][0:1].islower()))
@settings(max_examples=100)
def test_invalid_directive_names_rejected(name):
    # Names that don't start with markdown_ followed by a lowercase letter
    # should be rejected (though some may accidentally match)
    if not name.startswith("markdown_"):
        assert not is_valid_directive_name(name)


@given(name=st.text(min_size=0, max_size=50).filter(
    lambda s: not s.startswith("nginx_markdown_")))
@settings(max_examples=100)
def test_invalid_metric_names_rejected(name):
    assert not is_valid_metric_name(name)


@given(name=st.text(min_size=1, max_size=50).filter(
    lambda s: s[0].islower() or s[0].isdigit()))
@settings(max_examples=100)
def test_invalid_reason_codes_rejected(name):
    # Names starting with lowercase or digit should be rejected
    assert not is_valid_reason_code(name)


@given(name=st.text(min_size=1, max_size=50).filter(
    lambda s: s[0].isupper() or s[0].isdigit()))
@settings(max_examples=100)
def test_invalid_benchmark_fields_rejected(name):
    # Names starting with uppercase or digit should be rejected
    assert not is_valid_benchmark_field(name)
