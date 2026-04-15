#!/usr/bin/env python3
"""
Go/No-Go decision evaluator and scope assessment for 0.5.0 release gates.

Implements:
- Go/No-Go decision function (Property 13, 14)
- Scope evaluation / non-goal rejection (Property 11, 12)

Security: Input validation uses allow-lists and exact string matching only.
No dynamic code execution or user-supplied regex compilation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical P0 sub-spec identifiers
P0_SUBSPECS: tuple[str, ...] = (
    "overall-scope-release-gates-0-5-0",
    "rust-streaming-engine-core",
    "nginx-streaming-runtime-and-ffi",
    "streaming-failure-cache-semantics",
    "streaming-parity-diff-testing",
    "streaming-rollout-observability",
    "streaming-performance-evidence-and-release",
)

# Canonical streaming evidence items
STREAMING_EVIDENCE_ITEMS: tuple[str, ...] = (
    "diff_test_report",
    "bounded_memory_evidence",
    "performance_benchmark",
    "failure_path_coverage",
    "rollback_verification",
)


# ---------------------------------------------------------------------------
# Non-goal matching configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NonGoalRule:
    """A single non-goal matching rule.

    Attributes:
        value: The non-goal phrase to match against.
        match_mode: How to match — "phrase" for substring, "token" for
            word-boundary matching.  Use "token" for short words that
            would otherwise cause false positives (e.g. "gui", "helm").
    """

    value: str
    match_mode: str = "phrase"  # "phrase" or "token"


# Pre-compiled word-boundary patterns for token-mode rules (constants).
_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {}


def _get_token_pattern(word: str) -> re.Pattern[str]:
    """Return a compiled word-boundary regex for *word* (cached)."""
    if word not in _TOKEN_PATTERNS:
        _TOKEN_PATTERNS[word] = re.compile(
            r"\b" + re.escape(word) + r"\b", re.IGNORECASE
        )
    return _TOKEN_PATTERNS[word]


# Structured non-goals list.  Short / ambiguous terms use token mode;
# multi-word phrases use phrase (substring) mode.
# Covers all variants from spec Requirements 10.1 including hyphenated
# forms, abbreviations, and synonyms.
NON_GOAL_RULES: tuple[NonGoalRule, ...] = (
    # Output formats (Req 10.1 item 1)
    NonGoalRule("new output format negotiation"),
    NonGoalRule("json output"),
    NonGoalRule("text/plain output"),
    NonGoalRule("mdx output"),
    NonGoalRule("mdx", match_mode="token"),
    # Observability platforms (Req 10.1 items 2-3)
    NonGoalRule("opentelemetry"),
    NonGoalRule("tracing platform"),
    NonGoalRule("high cardinality metrics"),
    NonGoalRule("high-cardinality metrics"),
    NonGoalRule("per-request analytics"),
    # Packaging / distribution (Req 10.1 item 4)
    NonGoalRule("apt package"),
    NonGoalRule("yum package"),
    NonGoalRule("brew package"),
    NonGoalRule("helm", match_mode="token"),
    NonGoalRule("kubernetes ingress"),
    NonGoalRule("k8s ingress"),
    # UI / control (Req 10.1 items 5)
    NonGoalRule("gui", match_mode="token"),
    NonGoalRule("dashboard", match_mode="token"),
    NonGoalRule("control plane"),
    NonGoalRule("control-plane"),
    # Tokenizer / parser (Req 10.1 items 6-7)
    NonGoalRule("precise tokenizer"),
    NonGoalRule("parser ecosystem expansion"),
    # Content processing (Req 10.1 item 8)
    NonGoalRule("content-aware heuristic pruning"),
    NonGoalRule("heuristic pruning"),
    NonGoalRule("readability style extraction"),
    NonGoalRule("readability-style extraction"),
    # Agent integrations (Req 10.1 item 9)
    NonGoalRule("agent integrations"),
    NonGoalRule("control-plane ideas"),
)

# Flat tuple kept for backward compatibility with property tests that
# import NON_GOALS directly.
NON_GOALS: tuple[str, ...] = tuple(r.value for r in NON_GOAL_RULES)


@dataclass
class ReleaseStatus:
    """Represents the status of all release gate inputs."""

    p0_statuses: dict[str, bool] = field(default_factory=dict)
    streaming_evidence: dict[str, bool] = field(default_factory=dict)
    p1_statuses: dict[str, bool] = field(default_factory=dict)
    exceptions: list[str] = field(default_factory=list)


@dataclass
class GoNoGoDecision:
    """Result of a Go/No-Go evaluation."""

    decision: str  # "Go" or "No-Go"
    reason: str
    failing_p0: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)


def evaluate_go_nogo(status: ReleaseStatus) -> GoNoGoDecision:
    """Evaluate Go/No-Go decision based on P0 DoD statuses and streaming evidence.

    Rules (Property 13, 14):
    - All P0 sub-specs must pass DoD -> otherwise No-Go
    - All streaming evidence items must be sufficient -> otherwise No-Go
    - P1 statuses do NOT affect the decision
    """
    failing_p0 = [
        name
        for name in P0_SUBSPECS
        if not status.p0_statuses.get(name, False)
    ]

    missing_evidence = [
        item
        for item in STREAMING_EVIDENCE_ITEMS
        if not status.streaming_evidence.get(item, False)
    ]

    if failing_p0:
        return GoNoGoDecision(
            decision="No-Go",
            reason=f"P0 sub-specs not passing DoD: {', '.join(failing_p0)}",
            failing_p0=failing_p0,
            missing_evidence=missing_evidence,
        )

    if missing_evidence:
        return GoNoGoDecision(
            decision="No-Go",
            reason=(
                f"Streaming evidence insufficient: {', '.join(missing_evidence)}. "
                "Design intent does not substitute for actual evidence."
            ),
            failing_p0=[],
            missing_evidence=missing_evidence,
        )

    return GoNoGoDecision(
        decision="Go",
        reason="All P0 sub-specs pass DoD and all streaming evidence is sufficient.",
    )


def evaluate_scope(proposal: str) -> str:
    """Evaluate whether a proposal falls within 0.5.0 scope.

    Returns "reject" if the proposal matches any non-goal, "accept" otherwise.

    Matching strategy depends on the rule's match_mode:
    - "phrase": case-insensitive substring match (for multi-word phrases)
    - "token": word-boundary match (for short words like "gui" that would
      otherwise false-positive on "guidelines", "guide", etc.)

    Property 11: Non-goal proposals must be rejected.
    """
    proposal_lower = proposal.lower()
    for rule in NON_GOAL_RULES:
        if rule.match_mode == "token":
            if _get_token_pattern(rule.value).search(proposal):
                return "reject"
        elif rule.value in proposal_lower:
            return "reject"
    return "accept"
