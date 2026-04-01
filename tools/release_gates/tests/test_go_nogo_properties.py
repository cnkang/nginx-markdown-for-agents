"""
Property 13: Streaming evidence sufficiency determines Go/No-Go.
Property 14: P1 does not block release.

Uses hypothesis to generate random release status combinations and verify
the Go/No-Go decision logic.

Each property runs at least 100 iterations.

Validates: Requirements 7.4, 7.5, 5.5
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from release_gates.go_nogo_evaluator import (
    evaluate_go_nogo,
    ReleaseStatus,
    P0_SUBSPECS,
    STREAMING_EVIDENCE_ITEMS,
)


def _make_status(
    p0_pass: dict[str, bool],
    evidence_pass: dict[str, bool],
    p1_pass: dict[str, bool] | None = None,
) -> ReleaseStatus:
    """Helper to build a ReleaseStatus."""
    return ReleaseStatus(
        p0_statuses=p0_pass,
        streaming_evidence=evidence_pass,
        p1_statuses=p1_pass or {},
    )


# Strategy: random boolean for each P0 sub-spec
p0_strategy = st.fixed_dictionaries(
    {name: st.booleans() for name in P0_SUBSPECS}
)

# Strategy: random boolean for each evidence item
evidence_strategy = st.fixed_dictionaries(
    {item: st.booleans() for item in STREAMING_EVIDENCE_ITEMS}
)

# Strategy: random P1 statuses (variable number of P1 items)
p1_strategy = st.dictionaries(
    keys=st.sampled_from(["p1_item_a", "p1_item_b", "p1_item_c", "p1_item_d"]),
    values=st.booleans(),
    min_size=0,
    max_size=4,
)


# --- Property 13: Streaming evidence sufficiency ---

@settings(max_examples=100)
@given(p0=p0_strategy, evidence=evidence_strategy)
def test_all_pass_means_go(p0, evidence):
    """When all P0 pass and all evidence is sufficient → Go."""
    all_p0_pass = all(p0.values())
    all_evidence_pass = all(evidence.values())

    status = _make_status(p0, evidence)
    decision = evaluate_go_nogo(status)

    if all_p0_pass and all_evidence_pass:
        assert decision.decision == "Go", (
            f"Expected Go when all P0 pass and all evidence sufficient, "
            f"got {decision.decision}: {decision.reason}"
        )
    else:
        assert decision.decision == "No-Go", (
            f"Expected No-Go when not all gates pass, "
            f"got {decision.decision}: {decision.reason}"
        )


@settings(max_examples=100)
@given(evidence=evidence_strategy)
def test_p0_failure_means_nogo(evidence):
    """When any P0 fails → No-Go regardless of evidence."""
    # Force one P0 to fail
    p0 = dict.fromkeys(P0_SUBSPECS, True)
    p0[P0_SUBSPECS[0]] = False

    status = _make_status(p0, evidence)
    decision = evaluate_go_nogo(status)
    assert decision.decision == "No-Go", (
        f"Expected No-Go when P0 fails, got {decision.decision}"
    )


@settings(max_examples=100)
@given(p0=p0_strategy)
def test_evidence_failure_means_nogo(p0):
    """When any evidence is insufficient → No-Go regardless of P0."""
    # Force all P0 to pass but one evidence to fail
    p0_all_pass = dict.fromkeys(P0_SUBSPECS, True)
    evidence = dict.fromkeys(STREAMING_EVIDENCE_ITEMS, True)
    evidence[STREAMING_EVIDENCE_ITEMS[0]] = False

    status = _make_status(p0_all_pass, evidence)
    decision = evaluate_go_nogo(status)
    assert decision.decision == "No-Go", (
        f"Expected No-Go when evidence insufficient, got {decision.decision}"
    )


# --- Property 14: P1 does not block release ---

@settings(max_examples=100)
@given(p1=p1_strategy)
def test_p1_does_not_affect_go(p1):
    """Go/No-Go must be Go when all P0 pass and evidence sufficient, regardless of P1."""
    p0_all_pass = dict.fromkeys(P0_SUBSPECS, True)
    evidence_all_pass = dict.fromkeys(STREAMING_EVIDENCE_ITEMS, True)

    status = _make_status(p0_all_pass, evidence_all_pass, p1)
    decision = evaluate_go_nogo(status)
    assert decision.decision == "Go", (
        f"P1 status should not affect Go decision, got {decision.decision} "
        f"with P1={p1}"
    )


@settings(max_examples=100)
@given(p1=p1_strategy)
def test_p1_does_not_rescue_nogo(p1):
    """P1 passing cannot rescue a No-Go caused by P0 failure."""
    p0 = dict.fromkeys(P0_SUBSPECS, True)
    p0[P0_SUBSPECS[2]] = False  # Force one P0 failure
    evidence_all_pass = dict.fromkeys(STREAMING_EVIDENCE_ITEMS, True)

    # Even if all P1 pass, decision must be No-Go
    p1_all_pass = dict.fromkeys(p1, True)
    status = _make_status(p0, evidence_all_pass, p1_all_pass)
    decision = evaluate_go_nogo(status)
    assert decision.decision == "No-Go", (
        f"P1 passing should not rescue No-Go, got {decision.decision}"
    )
