"""
Property 11: Scope evaluation correctness — non-goal rejection.

Uses hypothesis to generate random proposal names and verify that
proposals matching non-goals are rejected.

Validates: Requirements 10.4, 13.2
"""

import sys
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from tools.release.gates.go_nogo_evaluator import (
    evaluate_scope,
    NON_GOALS,
    NON_GOAL_RULES,
)


@settings(max_examples=100)
@given(non_goal=st.sampled_from(NON_GOALS))
def test_non_goal_proposals_rejected(non_goal):
    """Proposals containing a non-goal keyword must be rejected.

    Wraps each non-goal in a sentence with clear word boundaries so that
    both phrase-mode and token-mode rules trigger correctly.
    """
    proposal = f"Add support for {non_goal} in 0.5.0"
    result = evaluate_scope(proposal)
    assert result == "reject", (
        f"should reject proposal containing '{non_goal}': {proposal}"
    )


@settings(max_examples=100)
@given(non_goal=st.sampled_from(NON_GOALS))
def test_non_goal_case_insensitive(non_goal):
    """Non-goal matching must be case-insensitive."""
    proposal = f"Implement {non_goal.upper()} feature"
    result = evaluate_scope(proposal)
    assert result == "reject", f"should reject (case-insensitive): {proposal}"


@settings(max_examples=100)
@given(
    proposal=st.text(
        min_size=1,
        max_size=60,
        alphabet=st.characters(whitelist_categories=("L", "N", "Z")),
    )
)
def test_streaming_related_proposals_not_auto_rejected(proposal):
    """Proposals that don't match any non-goal should be accepted."""
    proposal_lower = proposal.lower()
    for ng in NON_GOALS:
        assume(ng not in proposal_lower)
    # Also skip if any token-mode rule matches at word boundary
    for rule in NON_GOAL_RULES:
        if rule.match_mode == "token":
            from tools.release.gates.go_nogo_evaluator import _get_token_pattern

            assume(not _get_token_pattern(rule.value).search(proposal))
    result = evaluate_scope(proposal)
    assert result == "accept", f"should accept: {proposal}"


# --- Regression tests for false-positive prevention (Finding 4) ---


def test_streaming_guidelines_not_rejected():
    """'streaming guidelines' must not be rejected by the 'gui' rule."""
    assert evaluate_scope("streaming guidelines") == "accept"


def test_build_guide_not_rejected():
    """'build guide' must not be rejected by the 'gui' rule."""
    assert evaluate_scope("build guide") == "accept"


def test_gui_dashboard_rejected():
    """'add GUI dashboard' must be rejected."""
    assert evaluate_scope("add GUI dashboard") == "reject"


def test_helm_in_overwhelm_not_rejected():
    """'overwhelm' must not be rejected by the 'helm chart' rule."""
    assert evaluate_scope("overwhelm the system with requests") == "accept"
