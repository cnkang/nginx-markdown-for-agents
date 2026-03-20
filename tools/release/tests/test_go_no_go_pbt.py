# Feature: overall-scope-release-gates, Property 8: P1 exclusion does not block release
"""Property-based tests for P1 exclusion logic.

Feature: overall-scope-release-gates
Property 8: P1 exclusion does not block release
Validates: Requirements 7.5
"""

import hypothesis.strategies as st
from hypothesis import given, settings


# The P0 sub-specs that must all pass for a Go decision
P0_SUBSPECS = [
    "packaging-and-first-run",
    "benchmark-corpus-and-evidence",
    "rollout-safety-controlled-enablement",
    "prometheus-module-metrics",
]

P1_SUBSPEC = "parser-path-optimization"


def go_no_go_decision(p0_statuses: dict[str, bool], p1_status: bool) -> str:
    """Determine Go/No-Go decision.

    Returns "Go" if all P0 sub-specs pass, regardless of P1 status.
    Returns "No-Go" if any P0 sub-spec fails.
    """
    if all(p0_statuses.get(name, False) for name in P0_SUBSPECS):
        return "Go"
    return "No-Go"


@given(
    p0_statuses=st.fixed_dictionaries(
        {name: st.booleans() for name in P0_SUBSPECS}
    ),
    p1_status=st.booleans(),
)
@settings(max_examples=100)
def test_p1_exclusion_does_not_block_release(p0_statuses, p1_status):
    """Property 8: Go decision depends only on P0 status, not P1."""
    decision = go_no_go_decision(p0_statuses, p1_status)

    all_p0_pass = all(p0_statuses.values())

    if all_p0_pass:
        assert decision == "Go", (
            f"All P0 pass but decision is {decision} "
            f"(P1={p1_status})"
        )
    else:
        assert decision == "No-Go", (
            f"Not all P0 pass but decision is {decision}"
        )


@given(p1_status=st.booleans())
@settings(max_examples=100)
def test_all_p0_pass_always_go_regardless_of_p1(p1_status):
    """When all P0 pass, decision is always Go regardless of P1."""
    p0_statuses = {name: True for name in P0_SUBSPECS}
    decision = go_no_go_decision(p0_statuses, p1_status)
    assert decision == "Go"


@given(
    failing_spec=st.sampled_from(P0_SUBSPECS),
    p1_status=st.booleans(),
)
@settings(max_examples=100)
def test_any_p0_fail_always_no_go(failing_spec, p1_status):
    """When any P0 fails, decision is always No-Go."""
    p0_statuses = {name: True for name in P0_SUBSPECS}
    p0_statuses[failing_spec] = False
    decision = go_no_go_decision(p0_statuses, p1_status)
    assert decision == "No-Go"
