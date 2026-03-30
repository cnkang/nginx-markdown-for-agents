# Feature: overall-scope-release-gates, Property 10: Scope evaluation correctness
"""Property-based tests for scope evaluation correctness.

Feature: overall-scope-release-gates
Property 10: Scope evaluation correctness
Validates: Requirements 13.2
"""

import re

import hypothesis.strategies as st
from hypothesis import given, settings

from pathlib import Path
from typing import List, Optional

from tools.release.release_constants import NON_GOALS

# Lowercase versions for case-insensitive matching
_NON_GOALS_LOWER = [ng.lower() for ng in NON_GOALS]


def _split_compound_non_goal(non_goal: str) -> list[str]:
    """Split a compound non-goal into individual components.

    Splits on ", " and " or " delimiters, trims each part,
    and returns only components that are at least 3 characters long.
    """
    # Two-pass split avoids overlapping \s+ quantifiers (S5852).
    # First split on comma, then split each fragment on " or ".
    parts = []
    for fragment in non_goal.split(","):
        for component in fragment.split(" or "):
            stripped = component.strip()
            if len(stripped) >= 3:
                parts.append(stripped)
    return parts


def _all_matchable_terms() -> list[str]:
    """Return all terms that should trigger a rejection.

    Includes full non-goals (lowercased) plus individual components
    (3+ chars) extracted from compound non-goals.
    """
    terms = set(_NON_GOALS_LOWER)
    for ng in NON_GOALS:
        for component in _split_compound_non_goal(ng):
            terms.add(component.lower())
    return sorted(terms)


_ALL_MATCHABLE_TERMS = _all_matchable_terms()


def evaluate_scope(
    proposal_name: str,
    non_goals: Optional[List[str]] = None,
) -> str:
    """Evaluate whether a proposal is in-scope or should be rejected.

    A proposal is rejected if its name matches (case-insensitive substring)
    any item on the non-goals list, OR if it matches any individual component
    of a compound non-goal at a word boundary.

    Compound non-goals (e.g. "GUI, console, or dashboard") are split on
    ", " and " or " delimiters.  Each component of at least 3 characters
    is checked as a word-boundary match against the proposal.

    Returns:
        "reject"  — proposal matches a non-goal
        "evaluate" — proposal does not match any non-goal (needs further review)
    """
    if non_goals is None:
        non_goals = list(NON_GOALS)
    proposal_lower = proposal_name.lower()

    for non_goal in non_goals:
        # Existing behavior: full non-goal substring check
        if non_goal.lower() in proposal_lower:
            return "reject"

        # New: check individual components of compound non-goals
        for component in _split_compound_non_goal(non_goal):
            comp_lower = component.lower()
            if len(comp_lower) >= 3 and re.search(
                r"\b" + re.escape(comp_lower) + r"\b", proposal_lower
            ):
                return "reject"

    return "evaluate"


def _extract_non_goals_from_scope_doc() -> tuple[str, ...]:
    """Extract bullet items from the Non-Goals section of the scope doc."""
    repo_root = Path(__file__).resolve().parents[3]
    doc_path = repo_root / "docs/project/release-gates/scope-evaluation-process.md"
    lines = doc_path.read_text(encoding="utf-8").splitlines()

    in_section = False
    collected = []
    for line in lines:
        stripped = line.strip()
        if stripped == "## 0.4.0 Non-Goals List":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            collected.append(stripped[2:].strip())

    return tuple(collected)


# --- Property tests ---


def test_non_goals_constants_match_scope_evaluation_doc():
    """Keep code constants aligned with the authoritative documentation list."""
    assert NON_GOALS == _extract_non_goals_from_scope_doc()


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
    ).filter(lambda s: all(
        not re.search(r"\b" + re.escape(t) + r"\b", s.lower())
        for t in _ALL_MATCHABLE_TERMS
    )))
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


def test_short_non_goal_component_is_rejected():
    """A short token that is an individual component of a compound non-goal is rejected."""
    assert evaluate_scope("GUI") == "reject"


def test_short_non_component_not_rejected():
    """A short word inside a non-goal word but not a word-boundary match is not rejected."""
    # "pen" appears inside "open" but is not a standalone word in any non-goal
    assert evaluate_scope("pen") == "evaluate"
