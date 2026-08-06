# Property-based tests for error policy value acceptance.
"""Property-based tests for the error policy value set (Property 26).

These tests encode the documented `markdown_error_policy` value contract
from the design as a Python model and verify properties of that model.
They do NOT link against C code — the mapping is maintained here as a
single source of truth that must stay in sync with the C handler
(ngx_http_markdown_config_handlers_impl.h) and the design document.

Validates: Requirements 15.2

Run:
    python3 -m pytest tests/property/test_error_policy_values.py -v
"""

from enum import IntEnum, unique

import hypothesis.strategies as st
from hypothesis import given, settings

# ---------------------------------------------------------------------------
# Python model of the C handler value contract
# ---------------------------------------------------------------------------


@unique
class ErrorPolicyKind(IntEnum):
    """Classification of a markdown_error_policy value."""

    ACCEPTED = 0
    REJECTED = 1


# The exact accepted value set: pass, fail_closed, status 429, status 503.
ACCEPTED_SINGLE = frozenset({"pass", "fail_closed"})
ACCEPTED_STATUS_CODES = frozenset({429, 503})
# The exact rejected status codes named by the handler contract.
REJECTED_NAMED_STATUS_CODES = frozenset({418, 500, 502, 0, 65536})
# Accepted status code range check (the handler only accepts the two codes).
ALLOWED_STATUS_RANGE = range(100, 600)


def classify_policy(value):
    """Classify a policy value per the documented handler contract."""
    if isinstance(value, str) and value in ACCEPTED_SINGLE:
        return (ErrorPolicyKind.ACCEPTED, None)
    if isinstance(value, str) and value.startswith("status "):
        try:
            code = int(value.split(" ", 1)[1])
        except ValueError:
            return (ErrorPolicyKind.REJECTED, None)
        if code in ACCEPTED_STATUS_CODES:
            return (ErrorPolicyKind.ACCEPTED, code)
        return (ErrorPolicyKind.REJECTED, code)
    return (ErrorPolicyKind.REJECTED, None)


# ---------------------------------------------------------------------------
# Property 26: error policy value acceptance
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(st.sampled_from(sorted(ACCEPTED_SINGLE)))
def test_accepted_single_values(accepted):
    """The two single-word values are accepted without error."""
    kind, _ = classify_policy(accepted)
    assert kind == ErrorPolicyKind.ACCEPTED


@settings(max_examples=200)
@given(st.sampled_from(sorted(ACCEPTED_STATUS_CODES)))
def test_accepted_status_values(status_code):
    """status 429 and status 503 are accepted without error."""
    kind, code = classify_policy(f"status {status_code}")
    assert kind == ErrorPolicyKind.ACCEPTED
    assert code == status_code


@settings(max_examples=200)
@given(st.sampled_from(sorted(REJECTED_NAMED_STATUS_CODES)))
def test_rejected_named_status_values(status_code):
    """Named rejected codes (418, 500, 502, 0, 65536) are rejected."""
    kind, _ = classify_policy(f"status {status_code}")
    assert kind == ErrorPolicyKind.REJECTED


@settings(max_examples=200)
@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=24))
def test_any_other_value_rejected(value):
    """Any value outside the accepted set is rejected."""
    if value in ACCEPTED_SINGLE:
        return
    if value.startswith("status ") and _parse_status(value) in ACCEPTED_STATUS_CODES:
        return
    kind, _ = classify_policy(value)
    assert kind == ErrorPolicyKind.REJECTED, f"value {value!r} must be rejected"


def _parse_status(value):
    try:
        return int(value.split(" ", 1)[1])
    except (ValueError, IndexError):
        return None


@settings(max_examples=200)
@given(st.integers(min_value=100, max_value=599))
def test_status_outside_accepted_set_rejected(status_code):
    """status <code> for any code outside {429, 503} is rejected."""
    if status_code in ACCEPTED_STATUS_CODES:
        return
    kind, _ = classify_policy(f"status {status_code}")
    assert kind == ErrorPolicyKind.REJECTED, (
        f"status {status_code} must be rejected (only 429/503 accepted)"
    )


def test_value_set_is_exactly_four():
    """The accepted value set is exactly {pass, fail_closed, status 429,
    status 503} — no silent extras."""
    accepted = set()
    for candidate in [
        "pass",
        "fail_closed",
        "status 429",
        "status 503",
        "reject",
        "status 500",
        "status 418",
        "fail_open",
        "",
    ]:
        kind, _ = classify_policy(candidate)
        if kind == ErrorPolicyKind.ACCEPTED:
            accepted.add(candidate)
    assert accepted == {"pass", "fail_closed", "status 429", "status 503"}
