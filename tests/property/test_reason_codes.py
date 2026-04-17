# Feature: rollout-safety — Property-based tests for reason code system
"""Property-based tests for the reason code system.

These tests encode the documented reason code mapping table from the design
as a Python model and verify properties of that model.  They do NOT link
against C code — the mapping is maintained here as a single source of truth
that must stay in sync with the C implementation and the design document.

Feature: rollout-safety
Validates: Requirements 1.1, 1.5, 4.2, 13.3

Run:
    python3 -m pytest tests/property/test_reason_codes.py -v
"""

import re
from enum import IntEnum, unique

import hypothesis.strategies as st
from hypothesis import given, settings


# ---------------------------------------------------------------------------
# Python model of C enums (mirrors ngx_http_markdown_filter_module.h)
# ---------------------------------------------------------------------------

@unique
class Eligibility(IntEnum):
    """Mirrors ngx_http_markdown_eligibility_t."""
    ELIGIBLE = 0
    INELIGIBLE_METHOD = 1
    INELIGIBLE_STATUS = 2
    INELIGIBLE_CONTENT_TYPE = 3
    INELIGIBLE_SIZE = 4
    INELIGIBLE_STREAMING = 5
    INELIGIBLE_AUTH = 6
    INELIGIBLE_RANGE = 7
    INELIGIBLE_CONFIG = 8


@unique
class ErrorCategory(IntEnum):
    """Mirrors ngx_http_markdown_error_category_t."""
    ERROR_CONVERSION = 0
    ERROR_RESOURCE_LIMIT = 1
    ERROR_SYSTEM = 2


# ---------------------------------------------------------------------------
# Reason code format pattern (uppercase snake_case)
# ---------------------------------------------------------------------------

REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


# ---------------------------------------------------------------------------
# Documented mapping tables (design.md § Data Models)
# ---------------------------------------------------------------------------

ELIGIBILITY_TO_REASON = {
    Eligibility.INELIGIBLE_CONFIG: "SKIP_CONFIG",
    Eligibility.INELIGIBLE_METHOD: "SKIP_METHOD",
    Eligibility.INELIGIBLE_STATUS: "SKIP_STATUS",
    Eligibility.INELIGIBLE_CONTENT_TYPE: "SKIP_CONTENT_TYPE",
    Eligibility.INELIGIBLE_SIZE: "SKIP_SIZE",
    Eligibility.INELIGIBLE_STREAMING: "SKIP_STREAMING",
    Eligibility.INELIGIBLE_AUTH: "SKIP_AUTH",
    Eligibility.INELIGIBLE_RANGE: "SKIP_RANGE",
    Eligibility.ELIGIBLE: "ELIGIBLE_CONVERTED",
}

ERROR_CATEGORY_TO_REASON = {
    ErrorCategory.ERROR_CONVERSION: "FAIL_CONVERSION",
    ErrorCategory.ERROR_RESOURCE_LIMIT: "FAIL_RESOURCE_LIMIT",
    ErrorCategory.ERROR_SYSTEM: "FAIL_SYSTEM",
}

# Reason codes not derived from enums (Accept negotiation + eligible outcomes)
ACCEPT_SKIP_REASON = "SKIP_ACCEPT"
ELIGIBLE_CONVERTED_REASON = "ELIGIBLE_CONVERTED"
ELIGIBLE_FAILED_OPEN_REASON = "ELIGIBLE_FAILED_OPEN"
ELIGIBLE_FAILED_CLOSED_REASON = "ELIGIBLE_FAILED_CLOSED"

# All 15 defined reason codes (complete set from design)
ALL_REASON_CODES = sorted(
    set(ELIGIBILITY_TO_REASON.values())
    | set(ERROR_CATEGORY_TO_REASON.values())
    | {
        ACCEPT_SKIP_REASON,
        ELIGIBLE_FAILED_OPEN_REASON,
        ELIGIBLE_FAILED_CLOSED_REASON,
    }
)


# ---------------------------------------------------------------------------
# Request state model (design.md § Request State Model)
# ---------------------------------------------------------------------------

REQUEST_STATE_NOT_ENABLED = "NOT_ENABLED"
REQUEST_STATE_SKIPPED = "SKIPPED"
REQUEST_STATE_CONVERTED = "CONVERTED"
REQUEST_STATE_FAILED = "FAILED"

REASON_TO_REQUEST_STATE = {
    "SKIP_CONFIG": REQUEST_STATE_NOT_ENABLED,
    "SKIP_METHOD": REQUEST_STATE_SKIPPED,
    "SKIP_STATUS": REQUEST_STATE_SKIPPED,
    "SKIP_CONTENT_TYPE": REQUEST_STATE_SKIPPED,
    "SKIP_SIZE": REQUEST_STATE_SKIPPED,
    "SKIP_STREAMING": REQUEST_STATE_SKIPPED,
    "SKIP_AUTH": REQUEST_STATE_SKIPPED,
    "SKIP_RANGE": REQUEST_STATE_SKIPPED,
    "SKIP_ACCEPT": REQUEST_STATE_SKIPPED,
    "ELIGIBLE_CONVERTED": REQUEST_STATE_CONVERTED,
    "ELIGIBLE_FAILED_OPEN": REQUEST_STATE_FAILED,
    "ELIGIBLE_FAILED_CLOSED": REQUEST_STATE_FAILED,
}

# Failure sub-classification codes are not request-state codes; they are
# additional context attached to FAILED outcomes.  They are still reason
# codes that must satisfy the format property.


# ---------------------------------------------------------------------------
# Shared Hypothesis strategies
# ---------------------------------------------------------------------------

eligibility_values = st.sampled_from(list(Eligibility))
error_category_values = st.sampled_from(list(ErrorCategory))
all_reason_codes_strategy = st.sampled_from(ALL_REASON_CODES)


# ---------------------------------------------------------------------------
# Helper: lookup reason code from eligibility or error category
# ---------------------------------------------------------------------------

def reason_from_eligibility(eligibility: Eligibility) -> str:
    """Return the reason code for an eligibility enum value.

    Returns ``FAIL_SYSTEM`` for unmapped inputs instead of raising
    ``KeyError``.
    """
    return ELIGIBILITY_TO_REASON.get(eligibility, "FAIL_SYSTEM")


def reason_from_error_category(category: ErrorCategory) -> str:
    """Return the reason code for an error category enum value.

    Returns ``FAIL_SYSTEM`` for unmapped inputs instead of raising
    ``KeyError``.
    """
    return ERROR_CATEGORY_TO_REASON.get(category, "FAIL_SYSTEM")


# ---------------------------------------------------------------------------
# Property 1: Reason code completeness and format
# ---------------------------------------------------------------------------
# **Validates: Requirements 1.1, 1.5, 13.3**


@given(eligibility=eligibility_values)
@settings(max_examples=100)
def test_eligibility_reason_code_non_empty_and_format(eligibility):
    """Every eligibility enum value maps to a non-empty uppercase snake_case
    reason code.

    **Validates: Requirements 1.1, 1.5**
    """
    code = reason_from_eligibility(eligibility)
    assert len(code) > 0, f"Empty reason code for {eligibility.name}"
    assert REASON_CODE_PATTERN.match(code), (
        f"Reason code '{code}' for {eligibility.name} does not match "
        f"^[A-Z][A-Z0-9_]*$"
    )


@given(category=error_category_values)
@settings(max_examples=100)
def test_error_category_reason_code_non_empty_and_format(category):
    """Every error category enum value maps to a non-empty uppercase
    snake_case reason code.

    **Validates: Requirements 1.1, 1.5**
    """
    code = reason_from_error_category(category)
    assert len(code) > 0, f"Empty reason code for {category.name}"
    assert REASON_CODE_PATTERN.match(code), (
        f"Reason code '{code}' for {category.name} does not match "
        f"^[A-Z][A-Z0-9_]*$"
    )


def test_accept_skip_reason_code_format():
    """The Accept-based skip reason code is non-empty and matches the
    uppercase snake_case pattern.

    **Validates: Requirements 1.1, 1.5, 13.3**
    """
    assert len(ACCEPT_SKIP_REASON) > 0
    assert REASON_CODE_PATTERN.match(ACCEPT_SKIP_REASON), (
        f"SKIP_ACCEPT '{ACCEPT_SKIP_REASON}' does not match ^[A-Z][A-Z0-9_]*$"
    )


def test_eligible_outcome_reason_codes_format():
    """The three eligible outcome reason codes are non-empty and match the
    uppercase snake_case pattern.

    **Validates: Requirements 1.1, 1.5, 13.3**
    """
    for code in (
        ELIGIBLE_CONVERTED_REASON,
        ELIGIBLE_FAILED_OPEN_REASON,
        ELIGIBLE_FAILED_CLOSED_REASON,
    ):
        assert len(code) > 0, "Empty eligible outcome reason code"
        assert REASON_CODE_PATTERN.match(code), (
            f"Eligible outcome code '{code}' does not match ^[A-Z][A-Z0-9_]*$"
        )


def test_all_reason_codes_complete():
    """The complete set of reason codes contains exactly 15 entries as
    specified in the design document mapping table.

    **Validates: Requirements 1.1, 13.3**
    """
    assert len(ALL_REASON_CODES) == 15, (
        f"Expected 15 reason codes, got {len(ALL_REASON_CODES)}: "
        f"{ALL_REASON_CODES}"
    )


@given(code=all_reason_codes_strategy)
@settings(max_examples=100)
def test_all_defined_reason_codes_match_format(code):
    """Every defined reason code in the complete set matches the uppercase
    snake_case pattern.

    **Validates: Requirements 1.5, 13.3**
    """
    assert len(code) > 0, "Empty reason code in ALL_REASON_CODES"
    assert REASON_CODE_PATTERN.match(code), (
        f"Reason code '{code}' does not match ^[A-Z][A-Z0-9_]*$"
    )


def test_eligibility_enum_completeness():
    """Every value in the Eligibility enum has a mapping in the reason code
    table — no enum value is left unmapped.

    **Validates: Requirements 1.1, 13.3**
    """
    for member in Eligibility:
        assert member in ELIGIBILITY_TO_REASON, (
            f"Eligibility.{member.name} has no reason code mapping"
        )


def test_error_category_enum_completeness():
    """Every value in the ErrorCategory enum has a mapping in the reason code
    table — no enum value is left unmapped.

    **Validates: Requirements 1.1, 13.3**
    """
    for member in ErrorCategory:
        assert member in ERROR_CATEGORY_TO_REASON, (
            f"ErrorCategory.{member.name} has no reason code mapping"
        )


# ---------------------------------------------------------------------------
# Property 7: Reason code to request state is a total function
# ---------------------------------------------------------------------------
# **Validates: Requirements 4.2**

# Strategy that draws only from the 12 reason codes that map to request states
# (excludes FAIL_CONVERSION, FAIL_RESOURCE_LIMIT, FAIL_SYSTEM which are
# failure sub-classification codes, not primary request state codes).
request_state_reason_codes_strategy = st.sampled_from(
    sorted(REASON_TO_REQUEST_STATE.keys())
)

VALID_REQUEST_STATES = {
    REQUEST_STATE_NOT_ENABLED,
    REQUEST_STATE_SKIPPED,
    REQUEST_STATE_CONVERTED,
    REQUEST_STATE_FAILED,
}


@given(reason_code=request_state_reason_codes_strategy)
@settings(max_examples=100)
def test_reason_code_maps_to_exactly_one_request_state(reason_code):
    """Every defined reason code maps to exactly one of the four request
    states (NOT_ENABLED, SKIPPED, CONVERTED, FAILED).

    **Validates: Requirements 4.2**
    """
    state = REASON_TO_REQUEST_STATE[reason_code]
    assert state in VALID_REQUEST_STATES, (
        f"Reason code '{reason_code}' maps to '{state}' which is not a "
        f"valid request state. Expected one of {VALID_REQUEST_STATES}"
    )


@given(reason_code=request_state_reason_codes_strategy)
@settings(max_examples=100)
def test_skip_config_maps_to_not_enabled(reason_code):
    """SKIP_CONFIG maps to NOT_ENABLED; no other reason code maps to
    NOT_ENABLED.

    **Validates: Requirements 4.2**
    """
    state = REASON_TO_REQUEST_STATE[reason_code]
    if reason_code == "SKIP_CONFIG":
        assert state == REQUEST_STATE_NOT_ENABLED, (
            f"SKIP_CONFIG should map to NOT_ENABLED, got '{state}'"
        )
    else:
        assert state != REQUEST_STATE_NOT_ENABLED, (
            f"Only SKIP_CONFIG should map to NOT_ENABLED, but "
            f"'{reason_code}' also maps to NOT_ENABLED"
        )


@given(reason_code=request_state_reason_codes_strategy)
@settings(max_examples=100)
def test_skip_codes_map_to_correct_state(reason_code):
    """All SKIP_* codes (except SKIP_CONFIG) map to SKIPPED, ELIGIBLE_CONVERTED
    maps to CONVERTED, and ELIGIBLE_FAILED_* codes map to FAILED.

    **Validates: Requirements 4.2**
    """
    state = REASON_TO_REQUEST_STATE[reason_code]

    if reason_code == "SKIP_CONFIG":
        assert state == REQUEST_STATE_NOT_ENABLED
    elif reason_code.startswith("SKIP_"):
        assert state == REQUEST_STATE_SKIPPED, (
            f"SKIP code '{reason_code}' should map to SKIPPED, got '{state}'"
        )
    elif reason_code == "ELIGIBLE_CONVERTED":
        assert state == REQUEST_STATE_CONVERTED, (
            f"ELIGIBLE_CONVERTED should map to CONVERTED, got '{state}'"
        )
    elif reason_code in ("ELIGIBLE_FAILED_OPEN", "ELIGIBLE_FAILED_CLOSED"):
        assert state == REQUEST_STATE_FAILED, (
            f"'{reason_code}' should map to FAILED, got '{state}'"
        )
    else:
        raise AssertionError(
            f"Unexpected reason code '{reason_code}' in request state mapping"
        )


def test_request_state_mapping_is_total():
    """The REASON_TO_REQUEST_STATE mapping covers exactly the 12 primary
    reason codes (all reason codes except failure sub-classification codes).

    **Validates: Requirements 4.2**
    """
    expected_codes = {
        "SKIP_CONFIG",
        "SKIP_METHOD",
        "SKIP_STATUS",
        "SKIP_CONTENT_TYPE",
        "SKIP_SIZE",
        "SKIP_STREAMING",
        "SKIP_AUTH",
        "SKIP_RANGE",
        "SKIP_ACCEPT",
        "ELIGIBLE_CONVERTED",
        "ELIGIBLE_FAILED_OPEN",
        "ELIGIBLE_FAILED_CLOSED",
    }
    actual_codes = set(REASON_TO_REQUEST_STATE.keys())
    assert actual_codes == expected_codes, (
        f"REASON_TO_REQUEST_STATE keys mismatch.\n"
        f"Missing: {expected_codes - actual_codes}\n"
        f"Extra: {actual_codes - expected_codes}"
    )


def test_all_request_states_are_covered():
    """Every one of the four request states is the target of at least one
    reason code mapping.

    **Validates: Requirements 4.2**
    """
    mapped_states = set(REASON_TO_REQUEST_STATE.values())
    assert mapped_states == VALID_REQUEST_STATES, (
        f"Not all request states are covered by reason code mappings.\n"
        f"Missing states: {VALID_REQUEST_STATES - mapped_states}\n"
        f"Mapped states: {mapped_states}"
    )


# ---------------------------------------------------------------------------
# Verbosity level constants (mirrors markdown_log_verbosity directive values)
# ---------------------------------------------------------------------------

LOG_ERROR = 0
LOG_WARN = 1
LOG_INFO = 2
LOG_DEBUG = 3

ALL_VERBOSITY_LEVELS = [LOG_ERROR, LOG_WARN, LOG_INFO, LOG_DEBUG]


# ---------------------------------------------------------------------------
# Helpers for verbosity gating (Property 4)
# ---------------------------------------------------------------------------

def is_failure_outcome(reason_code: str) -> bool:
    """Return True if the reason code represents a failure outcome.

    Failure outcomes are codes starting with ``ELIGIBLE_FAILED`` or
    ``FAIL_``.  All other codes (``SKIP_*`` and ``ELIGIBLE_CONVERTED``)
    are non-failure outcomes.
    """
    return reason_code.startswith("ELIGIBLE_FAILED") or reason_code.startswith(
        "FAIL_"
    )


def should_emit(verbosity: int, reason_code: str) -> bool:
    """Return True if a decision log entry should be emitted for the given
    verbosity level and reason code.

    Gating rules (from design § Verbosity gating):
      - ``info`` (2) / ``debug`` (3): emit for ALL outcomes
      - ``warn`` (1) / ``error`` (0): emit ONLY for failure outcomes
    """
    return True if verbosity >= LOG_INFO else is_failure_outcome(reason_code)


# ---------------------------------------------------------------------------
# Hypothesis strategies for verbosity levels
# ---------------------------------------------------------------------------

verbosity_levels_strategy = st.sampled_from(ALL_VERBOSITY_LEVELS)


# ---------------------------------------------------------------------------
# Property 4: Verbosity gating controls log emission
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.1, 3.4**

FAILURE_PRIMARY_REASON_CODES = [
    rc for rc in ALL_REASON_CODES 
    if is_failure_outcome(rc) and not rc.startswith("FAIL_")
]
ALL_FAILURE_REASON_CODES = [
    rc for rc in ALL_REASON_CODES if is_failure_outcome(rc)
]
NON_FAILURE_REASON_CODES = [
    rc for rc in ALL_REASON_CODES if not is_failure_outcome(rc)
]


@given(
    reason_code=all_reason_codes_strategy,
    verbosity=verbosity_levels_strategy,
)
@settings(max_examples=200)
def test_verbosity_gating_controls_emission(reason_code, verbosity):
    """For any (outcome, verbosity_level) pair, a decision log entry is
    emitted if and only if: (a) verbosity is info or debug (all outcomes
    logged), or (b) verbosity is warn or error and the outcome is a failure.

    **Validates: Requirements 3.1, 3.4**
    """
    emit = should_emit(verbosity, reason_code)
    failure = is_failure_outcome(reason_code)

    if verbosity >= LOG_INFO:
        assert emit is True, (
            f"At verbosity {verbosity} (info/debug), all outcomes should be "
            f"emitted, but '{reason_code}' was suppressed"
        )
    elif failure:
        assert emit is True, (
            f"At verbosity {verbosity} (warn/error), failure outcome "
            f"'{reason_code}' should be emitted but was suppressed"
        )
    else:
        assert emit is False, (
            f"At verbosity {verbosity} (warn/error), non-failure outcome "
            f"'{reason_code}' should be suppressed but was emitted"
        )


@given(
    reason_code=st.sampled_from(NON_FAILURE_REASON_CODES),
    verbosity=st.sampled_from([LOG_ERROR, LOG_WARN]),
)
@settings(max_examples=100)
def test_non_failure_suppressed_at_warn_and_error(
    reason_code, verbosity
):
    """Non-failure outcomes (SKIP_* and ELIGIBLE_CONVERTED) shall not produce
    log entries at warn or error verbosity.

    **Validates: Requirements 3.1, 3.4**
    """
    assert should_emit(verbosity, reason_code) is False, (
        f"Non-failure outcome '{reason_code}' should be suppressed at "
        f"verbosity {verbosity}"
    )


@given(
    reason_code=st.sampled_from(ALL_FAILURE_REASON_CODES),
    verbosity=verbosity_levels_strategy,
)
@settings(max_examples=100)
def test_failure_outcomes_always_emitted(reason_code, verbosity):
    """Failure outcomes are emitted at every verbosity level.

    **Validates: Requirements 3.1, 3.4**
    """
    assert should_emit(verbosity, reason_code) is True, (
        f"Failure outcome '{reason_code}' should be emitted at verbosity "
        f"{verbosity} but was suppressed"
    )


# ---------------------------------------------------------------------------
# NGINX log level constants (mirrors ngx_log.h)
# ---------------------------------------------------------------------------

NGX_LOG_INFO = 7
NGX_LOG_WARN = 5


# ---------------------------------------------------------------------------
# Helper: expected NGINX log level for a reason code (Property 6)
# ---------------------------------------------------------------------------

def expected_nginx_log_level(reason_code: str) -> int:
    """Return the expected NGINX log level for a given reason code.

    From the design (§ NGINX log level mapping):
      - Non-failure outcomes (SKIP_*, ELIGIBLE_CONVERTED): NGX_LOG_INFO (7)
      - Failure outcomes (ELIGIBLE_FAILED_*, FAIL_*): NGX_LOG_WARN (5)
    """
    return NGX_LOG_WARN if is_failure_outcome(reason_code) else NGX_LOG_INFO


# ---------------------------------------------------------------------------
# Property 6: NGINX log level matches outcome type
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.5**


@given(reason_code=all_reason_codes_strategy)
@settings(max_examples=100)
def test_nginx_log_level_matches_outcome_type(reason_code):
    """For any emitted decision log entry, the NGINX log level shall be
    NGX_LOG_INFO for non-failure outcomes and NGX_LOG_WARN for failure
    outcomes.

    **Validates: Requirements 3.5**
    """
    level = expected_nginx_log_level(reason_code)
    if is_failure_outcome(reason_code):
        assert level == NGX_LOG_WARN, (
            f"Failure outcome '{reason_code}' should use NGX_LOG_WARN (5), "
            f"got {level}"
        )
    else:
        assert level == NGX_LOG_INFO, (
            f"Non-failure outcome '{reason_code}' should use NGX_LOG_INFO (7), "
            f"got {level}"
        )


@given(
    reason_code=st.sampled_from(NON_FAILURE_REASON_CODES),
)
@settings(max_examples=100)
def test_non_failure_uses_info_level(reason_code):
    """Non-failure outcomes (SKIP_* and ELIGIBLE_CONVERTED) shall use
    NGX_LOG_INFO.

    **Validates: Requirements 3.5**
    """
    assert expected_nginx_log_level(reason_code) == NGX_LOG_INFO, (
        f"Non-failure outcome '{reason_code}' should use NGX_LOG_INFO (7)"
    )


@given(
    reason_code=st.sampled_from(ALL_FAILURE_REASON_CODES),
)
@settings(max_examples=100)
def test_failure_uses_warn_level(reason_code):
    """Failure outcomes (ELIGIBLE_FAILED_OPEN, ELIGIBLE_FAILED_CLOSED,
    FAIL_*) shall use NGX_LOG_WARN.

    **Validates: Requirements 3.5**
    """
    assert expected_nginx_log_level(reason_code) == NGX_LOG_WARN, (
        f"Failure outcome '{reason_code}' should use NGX_LOG_WARN (5)"
    )


# ---------------------------------------------------------------------------
# Decision chain check order (design.md § Decision Chain Model)
# ---------------------------------------------------------------------------
# The documented check order from the design Mermaid diagram and the C
# implementation in ngx_http_markdown_check_eligibility():
#   1. CONFIG  (markdown_filter enabled?)
#   2. METHOD  (GET/HEAD?)
#   3. STATUS  (200 OK / 206 Partial Content?)
#   4. RANGE   (Range request?)
#   5. STREAMING (unbounded streaming?)
#   6. CONTENT_TYPE (text/html?)
#   7. SIZE    (within max_size?)
#   8. AUTH    (auth policy allows?)
#   9. ACCEPT  (Accept header requests Markdown?)
#
# The first failing check determines the outcome.

DECISION_CHAIN_ORDER = [
    Eligibility.INELIGIBLE_CONFIG,
    Eligibility.INELIGIBLE_METHOD,
    Eligibility.INELIGIBLE_STATUS,
    Eligibility.INELIGIBLE_RANGE,
    Eligibility.INELIGIBLE_STREAMING,
    Eligibility.INELIGIBLE_CONTENT_TYPE,
    Eligibility.INELIGIBLE_SIZE,
    Eligibility.INELIGIBLE_AUTH,
]

# Map each eligibility value to its position in the decision chain
CHAIN_POSITION = {e: i for i, e in enumerate(DECISION_CHAIN_ORDER)}


def first_failing_check(failing_checks):
    """Given a set of failing eligibility checks, return the one that
    appears first in the documented decision chain order.
    """
    best = None
    best_pos = len(DECISION_CHAIN_ORDER)
    for check in failing_checks:
        pos = CHAIN_POSITION.get(check)
        if pos is not None and pos < best_pos:
            best = check
            best_pos = pos
    return best


# Strategy: generate a non-empty subset of ineligible checks (at least 2)
ineligible_checks = [e for e in Eligibility if e != Eligibility.ELIGIBLE]
failing_subset_strategy = st.lists(
    st.sampled_from(ineligible_checks),
    min_size=2,
    max_size=len(ineligible_checks),
    unique=True,
)


# ---------------------------------------------------------------------------
# Property 2: Decision chain stops at first failing check
# ---------------------------------------------------------------------------
# **Validates: Requirements 2.2, 2.3**


@given(failing_checks=failing_subset_strategy)
@settings(max_examples=200)
def test_decision_chain_first_failing_check(failing_checks):
    """For any request with multiple failing eligibility conditions, the
    reason code assigned shall correspond to the first check in the
    documented decision chain order that fails.

    **Validates: Requirements 2.2, 2.3**
    """
    expected_first = first_failing_check(failing_checks)
    assert expected_first is not None, (
        f"Could not determine first failing check from {failing_checks}"
    )
    expected_reason = ELIGIBILITY_TO_REASON[expected_first]
    assert REASON_CODE_PATTERN.match(expected_reason), (
        f"Expected reason code '{expected_reason}' does not match format"
    )

    # Verify that the first failing check has a lower chain position
    # than all other failing checks
    first_pos = CHAIN_POSITION[expected_first]
    for check in failing_checks:
        if check != expected_first:
            other_pos = CHAIN_POSITION[check]
            assert first_pos <= other_pos, (
                f"Check {expected_first.name} (pos {first_pos}) should come "
                f"before {check.name} (pos {other_pos}) in the chain"
            )


@given(failing_checks=failing_subset_strategy)
@settings(max_examples=100)
def test_property2_first_check_reason_is_deterministic(failing_checks):
    """The first-failing-check determination is deterministic: the same set
    of failing checks always produces the same reason code.

    **Validates: Requirements 2.2, 2.3**
    """
    result1 = first_failing_check(failing_checks)
    result2 = first_failing_check(failing_checks)
    assert result1 == result2, (
        f"Non-deterministic: got {result1} and {result2} for same input"
    )
    reason1 = ELIGIBILITY_TO_REASON[result1]
    reason2 = ELIGIBILITY_TO_REASON[result2]
    assert reason1 == reason2


def test_property2_chain_order_matches_design():
    """The decision chain order encoded in tests matches the design document
    Mermaid diagram exactly.

    **Validates: Requirements 2.2**
    """
    expected_reasons_in_order = [
        "SKIP_CONFIG",
        "SKIP_METHOD",
        "SKIP_STATUS",
        "SKIP_RANGE",
        "SKIP_STREAMING",
        "SKIP_CONTENT_TYPE",
        "SKIP_SIZE",
        "SKIP_AUTH",
    ]
    actual_reasons_in_order = [
        ELIGIBILITY_TO_REASON[e] for e in DECISION_CHAIN_ORDER
    ]
    assert actual_reasons_in_order == expected_reasons_in_order, (
        f"Chain order mismatch.\n"
        f"Expected: {expected_reasons_in_order}\n"
        f"Actual:   {actual_reasons_in_order}"
    )


# ---------------------------------------------------------------------------
# Property 3: Failure outcome depends on on_error policy
# ---------------------------------------------------------------------------
# **Validates: Requirements 2.5**

ON_ERROR_PASS = "pass"
ON_ERROR_REJECT = "reject"

on_error_policy_strategy = st.sampled_from([ON_ERROR_PASS, ON_ERROR_REJECT])


def failure_outcome_for_policy(on_error: str) -> str:
    """Return the expected reason code for a failed conversion given the
    on_error policy setting.

    From the design (§ Decision Chain Model):
      - pass  → ELIGIBLE_FAILED_OPEN
      - reject → ELIGIBLE_FAILED_CLOSED
    """
    if on_error == ON_ERROR_PASS:
        return ELIGIBLE_FAILED_OPEN_REASON
    return ELIGIBLE_FAILED_CLOSED_REASON


@given(on_error=on_error_policy_strategy)
@settings(max_examples=100)
def test_property3_failure_outcome_depends_on_policy(on_error):
    """For any eligible request where conversion fails, the reason code
    shall be ELIGIBLE_FAILED_OPEN when on_error is 'pass', and
    ELIGIBLE_FAILED_CLOSED when on_error is 'reject'.

    **Validates: Requirements 2.5**
    """
    outcome = failure_outcome_for_policy(on_error)
    if on_error == ON_ERROR_PASS:
        assert outcome == ELIGIBLE_FAILED_OPEN_REASON, (
            f"on_error=pass should produce ELIGIBLE_FAILED_OPEN, "
            f"got '{outcome}'"
        )
    else:
        assert outcome == ELIGIBLE_FAILED_CLOSED_REASON, (
            f"on_error=reject should produce ELIGIBLE_FAILED_CLOSED, "
            f"got '{outcome}'"
        )


@given(on_error=on_error_policy_strategy)
@settings(max_examples=100)
def test_property3_failure_outcome_is_always_failed_state(on_error):
    """Regardless of on_error policy, a failed conversion always maps to
    the FAILED request state.

    **Validates: Requirements 2.5**
    """
    outcome = failure_outcome_for_policy(on_error)
    state = REASON_TO_REQUEST_STATE[outcome]
    assert state == REQUEST_STATE_FAILED, (
        f"Failed conversion with on_error={on_error} produced reason "
        f"'{outcome}' which maps to state '{state}', expected FAILED"
    )


@given(on_error=on_error_policy_strategy)
@settings(max_examples=100)
def test_property3_pass_and_reject_produce_different_codes(on_error):
    """The two on_error policies produce distinct reason codes — they are
    never the same string.

    **Validates: Requirements 2.5**
    """
    pass_outcome = failure_outcome_for_policy(ON_ERROR_PASS)
    reject_outcome = failure_outcome_for_policy(ON_ERROR_REJECT)
    assert pass_outcome != reject_outcome, (
        "pass and reject should produce different reason codes"
    )
    # Verify the current policy maps to the correct one
    outcome = failure_outcome_for_policy(on_error)
    if on_error == ON_ERROR_PASS:
        assert outcome == pass_outcome
    else:
        assert outcome == reject_outcome


# ---------------------------------------------------------------------------
# Property 5: Decision log entry contains required fields
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.2, 3.3**

# Base fields always present in a decision log entry
BASE_FIELDS = ["reason", "method", "uri", "content_type"]

# Extended fields present only at debug verbosity
EXTENDED_FIELDS = ["filter_value", "accept", "status"]

# Strategies for generating random request contexts
http_methods = st.sampled_from(["GET", "HEAD", "POST", "PUT", "DELETE"])
uri_strategy = st.from_regex(r"/[a-z0-9/_-]{1,50}", fullmatch=True)
content_type_strategy = st.sampled_from([
    "text/html",
    "text/html; charset=utf-8",
    "application/json",
    "text/plain",
])
accept_strategy = st.sampled_from([
    "text/markdown",
    "text/html",
    "text/markdown; q=0.9, text/html; q=0.8",
    "*/*",
])
status_strategy = st.sampled_from([200, 301, 302, 404, 500])
filter_value_strategy = st.sampled_from(["on", "off", "1", "0"])


def format_decision_log_entry(
    reason_code, method, uri, content_type,
    verbosity, filter_value=None, accept=None, status=None,
    error_category=None
):
    """Format a decision log entry string matching the design specification.

    Base format:
        markdown decision: reason=<CODE> method=<METHOD> uri=<URI>
            content_type=<TYPE>

    Base format with error category (failure outcomes):
        markdown decision: reason=<CODE> category=<FAIL_*>
            method=<METHOD> uri=<URI> content_type=<TYPE>

    Debug extended format adds:
        filter_value=<VALUE> accept=<ACCEPT> status=<STATUS>
    """
    entry = f"markdown decision: reason={reason_code}"
    if error_category is not None:
        entry += f" category={error_category}"
    entry += (
        f" method={method} "
        f"uri={uri} content_type={content_type}"
    )
    if verbosity == LOG_DEBUG:
        entry += (
            f" filter_value={filter_value} accept={accept} "
            f"status={status}"
        )
    return entry


def parse_log_fields(entry):
    """Parse a decision log entry string into a dict of field names."""
    # Strip the "markdown decision: " prefix
    prefix = "markdown decision: "
    if entry.startswith(prefix):
        entry = entry[len(prefix):]
    fields = {}
    for part in entry.split():
        if "=" in part:
            key, _, _ = part.partition("=")
            fields[key] = True
    return fields


@given(
    reason_code=all_reason_codes_strategy,
    method=http_methods,
    uri=uri_strategy,
    content_type=content_type_strategy,
    verbosity=verbosity_levels_strategy,
    filter_value=filter_value_strategy,
    accept=accept_strategy,
    status=status_strategy,
)
@settings(max_examples=200)
def test_property5_base_fields_always_present(
    reason_code, method, uri, content_type,
    verbosity, filter_value, accept, status
):
    """For any emitted decision log entry, the entry shall contain the
    fields reason, method, uri, and content_type.

    **Validates: Requirements 3.2, 3.3**
    """
    # Only test entries that would actually be emitted
    if not should_emit(verbosity, reason_code):
        return

    entry = format_decision_log_entry(
        reason_code, method, uri, content_type,
        verbosity, filter_value, accept, status
    )
    fields = parse_log_fields(entry)
    for field in BASE_FIELDS:
        assert field in fields, (
            f"Base field '{field}' missing from log entry: {entry}"
        )


@given(
    reason_code=all_reason_codes_strategy,
    method=http_methods,
    uri=uri_strategy,
    content_type=content_type_strategy,
    filter_value=filter_value_strategy,
    accept=accept_strategy,
    status=status_strategy,
)
@settings(max_examples=200)
def test_property5_debug_includes_extended_fields(
    reason_code, method, uri, content_type,
    filter_value, accept, status
):
    """When verbosity is debug, the entry shall additionally contain
    filter_value, accept, and status fields.

    **Validates: Requirements 3.2, 3.3**
    """
    entry = format_decision_log_entry(
        reason_code, method, uri, content_type,
        LOG_DEBUG, filter_value, accept, status
    )
    fields = parse_log_fields(entry)
    for field in BASE_FIELDS + EXTENDED_FIELDS:
        assert field in fields, (
            f"Field '{field}' missing from debug log entry: {entry}"
        )


@given(
    reason_code=all_reason_codes_strategy,
    method=http_methods,
    uri=uri_strategy,
    content_type=content_type_strategy,
    verbosity=st.sampled_from([LOG_ERROR, LOG_WARN, LOG_INFO]),
    filter_value=filter_value_strategy,
    accept=accept_strategy,
    status=status_strategy,
)
@settings(max_examples=200)
def test_property5_non_debug_excludes_extended_fields(
    reason_code, method, uri, content_type,
    verbosity, filter_value, accept, status
):
    """When verbosity is not debug, the entry shall NOT contain the
    extended fields (filter_value, accept, status).

    **Validates: Requirements 3.2, 3.3**
    """
    if not should_emit(verbosity, reason_code):
        return

    entry = format_decision_log_entry(
        reason_code, method, uri, content_type,
        verbosity, filter_value, accept, status
    )
    fields = parse_log_fields(entry)
    for field in EXTENDED_FIELDS:
        assert field not in fields, (
            f"Extended field '{field}' should not be present at "
            f"verbosity {verbosity}: {entry}"
        )


# ---------------------------------------------------------------------------
# Property 8: Failure decision log entries include error category
# ---------------------------------------------------------------------------
# **Validates: Requirements 3.4, 13.1**

# Strategy for failure reason codes that carry a category
failure_with_category_strategy = st.sampled_from([
    ELIGIBLE_FAILED_OPEN_REASON,
    ELIGIBLE_FAILED_CLOSED_REASON,
])

error_category_reason_strategy = st.sampled_from(
    list(ERROR_CATEGORY_TO_REASON.values())
)


@given(
    reason_code=failure_with_category_strategy,
    error_category=error_category_reason_strategy,
    method=http_methods,
    uri=uri_strategy,
    content_type=content_type_strategy,
    verbosity=verbosity_levels_strategy,
    filter_value=filter_value_strategy,
    accept=accept_strategy,
    status=status_strategy,
)
@settings(max_examples=200)
def test_property8_failure_entries_include_category(
    reason_code, error_category, method, uri, content_type,
    verbosity, filter_value, accept, status
):
    """For failure outcomes (ELIGIBLE_FAILED_OPEN, ELIGIBLE_FAILED_CLOSED),
    the decision log entry shall include a category= field with the FAIL_*
    sub-classification code.

    **Validates: Requirements 3.4, 13.1**
    """
    if not should_emit(verbosity, reason_code):
        return

    entry = format_decision_log_entry(
        reason_code, method, uri, content_type,
        verbosity, filter_value, accept, status,
        error_category=error_category
    )
    fields = parse_log_fields(entry)
    assert "category" in fields, (
        f"Failure entry missing 'category' field: {entry}"
    )
    assert f"category={error_category}" in entry, (
        f"Expected category={error_category} in entry: {entry}"
    )


@given(
    reason_code=st.sampled_from(NON_FAILURE_REASON_CODES),
    method=http_methods,
    uri=uri_strategy,
    content_type=content_type_strategy,
    verbosity=verbosity_levels_strategy,
    filter_value=filter_value_strategy,
    accept=accept_strategy,
    status=status_strategy,
)
@settings(max_examples=200)
def test_property8_non_failure_entries_no_category(
    reason_code, method, uri, content_type,
    verbosity, filter_value, accept, status
):
    """Non-failure outcomes (SKIP_*, ELIGIBLE_CONVERTED) shall NOT include
    a category= field in the decision log entry.

    **Validates: Requirements 3.4**
    """
    if not should_emit(verbosity, reason_code):
        return

    entry = format_decision_log_entry(
        reason_code, method, uri, content_type,
        verbosity, filter_value, accept, status,
        error_category=None
    )
    fields = parse_log_fields(entry)
    assert "category" not in fields, (
        f"Non-failure entry should not have 'category' field: {entry}"
    )
