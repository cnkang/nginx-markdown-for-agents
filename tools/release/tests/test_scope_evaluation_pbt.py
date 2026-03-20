# Feature: overall-scope-release-gates, Property 10: Scope evaluation correctness
"""Property-based tests for scope evaluation correctness.

Feature: overall-scope-release-gates
Property 10: Scope evaluation correctness
Validates: Requirements 13.2
"""

import hypothesis.strategies as st
from hypothesis import given, settings


# The 0.4.0 non-goals list — proposals matching these must be rejected.
# Source: docs/project/release-gates/scope-evaluation-process.md
NON_GOALS = [
    "True streaming HTML-to-Markdown conversion",
    "New output format negotiation (JSON, text/plain, MDX)",
    "OpenTelemetry tracing",
    "High-cardinality metrics",
    "GUI, console, or dashboard",
    "Cross-web-server ecosystem support (Apache, Caddy, Envoy, Traefik)",
    "Enterprise control plane or policy center",
    "AI post-processing capabilities (summarization, rewriting, extraction)",
    "Complex shadow streaming replacement",
    "Positioning 0.4.0 as a \"1.0.0 pre-release\"",
]

# Lowercase versions for case-insensitive matching
_NON_GOALS_LOWER = [ng.lower() for ng in NON_GOALS]


def evaluate_scope(proposal_name: str, non_goals: list[str] | None = None) -> str:
    """Evaluate whether a proposal is in-scope or should be rejected.

    A proposal is rejected if its name matches (case-insensitive substring)
    any item on the non-goals list.

    Returns:
        "reject"  — proposal matches a non-goal
        "evaluate" — proposal does not match any non-goal (needs further review)
    """
    if non_goals is None:
        non_goals = NON_GOALS
    proposal_lower = proposal_name.lower()
    return next(
        (
            "reject"
            for non_goal in non_goals
            if non_goal.lower() in proposal_lower
            or proposal_lower in non_goal.lower()
        ),
        "evaluate",
    )


# --- Property tests ---


@given(non_goal=st.sampled_from(NON_GOALS))
@settings(max_examples=100)
def test_exact_non_goal_proposals_are_rejected(non_goal):
    """Any proposal whose name exactly matches a non-goal is rejected."""
    result = evaluate_scope(non_goal)
    assert result == "reject", (
        f"Proposal '{non_goal}' should be rejected but got '{result}'"
    )


@given(
    non_goal=st.sampled_from(NON_GOALS),
    prefix=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Zs")),
        min_size=0,
        max_size=10,
    ),
    suffix=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Zs")),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=100)
def test_proposals_containing_non_goal_text_are_rejected(non_goal, prefix, suffix):
    """A proposal that contains a non-goal as a substring is rejected."""
    proposal = f"{prefix}{non_goal}{suffix}"
    result = evaluate_scope(proposal)
    assert result == "reject", (
        f"Proposal '{proposal}' contains non-goal '{non_goal}' "
        f"but got '{result}'"
    )


@given(
    non_goal=st.sampled_from(NON_GOALS),
    case_flip=st.booleans(),
)
@settings(max_examples=100)
def test_case_insensitive_non_goal_matching(non_goal, case_flip):
    """Non-goal matching is case-insensitive."""
    proposal = non_goal.upper() if case_flip else non_goal.lower()
    result = evaluate_scope(proposal)
    assert result == "reject", (
        f"Proposal '{proposal}' (case variant of '{non_goal}') "
        f"should be rejected but got '{result}'"
    )


@given(name=st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "Zs", "Pd"),
            blacklist_characters="\x00",
        ),
        min_size=1,
        max_size=60,
    ).filter(lambda s: all(ng not in s.lower() for ng in _NON_GOALS_LOWER) and all(s.lower() not in ng for ng in _NON_GOALS_LOWER)))
@settings(max_examples=100)
def test_proposals_not_matching_non_goals_are_not_rejected(name):
    """Proposals that don't match any non-goal should not be rejected."""
    result = evaluate_scope(name)
    assert result == "evaluate", (
        f"Proposal '{name}' does not match any non-goal "
        f"but got '{result}' instead of 'evaluate'"
    )


@given(data=st.data())
@settings(max_examples=100)
def test_scope_evaluation_deterministic(data):
    """Evaluating the same proposal twice yields the same result."""
    proposal = data.draw(
        st.text(min_size=1, max_size=80).filter(lambda s: "\x00" not in s)
    )
    result1 = evaluate_scope(proposal)
    result2 = evaluate_scope(proposal)
    assert result1 == result2, (
        f"Non-deterministic result for '{proposal}': "
        f"first={result1}, second={result2}"
    )
