"""
Property 35: conversion_deliveries_total success-only and
streaming_events_total transition label — property-based tests.

Verifies the delivery counter semantics defined in Requirement 5.8:

- conversion_deliveries_total increments ONLY on successful delivery
  (converted Markdown produced AND downstream accepted normal terminal
  last_buf)
- It does NOT increment for: abort-terminal, HTML passthrough, failed-open
  original HTML, or FAILED_CLOSED response
- Non-delivery outcomes are recorded only by requests_total{outcome=...}
  (no double-count as delivery)
- streaming_events_total uses a label named `transition` (NOT `event`)
  with a closed allowlist: {commit, fallback, safe_finish_start,
  abort_start, resume_success, resume_failure}
- No value outside the allowlist is emitted

**Validates: Requirements 5.8**
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Dict, Set

from hypothesis import given, settings
from hypothesis import strategies as st

# Ensure the tools package is importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent.parent)
)

REPO_ROOT = Path(__file__).resolve().parents[4]
METRICS_CONTRACT_PATH = REPO_ROOT / "schemas" / "metrics-v1.registry.json"


def _canonical_transition_allowlist() -> frozenset[str]:
    """Load transition labels from the checked-in Metrics v1 contract."""
    contract = json.loads(METRICS_CONTRACT_PATH.read_text(encoding="utf-8"))
    family = next(
        family
        for family in contract["families"]
        if family["name"] == "nginx_markdown_streaming_events_total"
    )
    transition = next(
        label for label in family["labels"] if label["name"] == "transition"
    )
    return frozenset(transition["values"])


# --- Terminal outcome model ---

class TerminalOutcome(Enum):
    """
    All possible terminal outcomes for a request entering the module
    decision chain. Exactly one per request.
    """
    NORMAL_MARKDOWN_SUCCESS = auto()   # Converted Markdown delivered
    ABORT_TERMINAL = auto()            # Abort-terminal accepted (no MD)
    HTML_PASSTHROUGH = auto()          # HTML passthrough (no conversion)
    FAILED_OPEN_ORIGINAL_HTML = auto() # Failed-open, original HTML sent
    FAILED_CLOSED = auto()             # FAILED_CLOSED error response


# The outcomes that produce a successful delivery
DELIVERY_OUTCOMES: Set[TerminalOutcome] = {
    TerminalOutcome.NORMAL_MARKDOWN_SUCCESS,
}

# The outcomes that do NOT produce a delivery
NON_DELIVERY_OUTCOMES: Set[TerminalOutcome] = {
    TerminalOutcome.ABORT_TERMINAL,
    TerminalOutcome.HTML_PASSTHROUGH,
    TerminalOutcome.FAILED_OPEN_ORIGINAL_HTML,
    TerminalOutcome.FAILED_CLOSED,
}


# --- streaming_events_total transition label model ---

# The closed allowlist for the `transition` label on streaming_events_total
TRANSITION_ALLOWLIST: Set[str] = _canonical_transition_allowlist()

# The 19 State Machine Events (DIFFERENT from transition label values)
# These must NEVER appear as streaming_events_total transition labels
STATE_MACHINE_EVENTS: Set[str] = frozenset({
    "header_filter_entry",
    "body_chunk_received",
    "last_buf_received",
    "resume_called",
    "client_abort_detected",
    "eof_detected",
    "timer_expired",
    "allocation_failed",
    "converter_error",
    "downstream_error",
    "safe_finish_complete",
    "safe_finish_failed",
    "abort_complete",
    "replay_complete",
    "replay_failed",
    "postcommit_error",
    "invariant_violated",
    "decompression_error",
    "budget_exceeded",
})


class StreamingTransition(Enum):
    """Streaming lifecycle transitions that produce metric events."""
    COMMIT = "commit"
    FALLBACK = "fallback"
    SAFE_FINISH_START = "safe_finish_start"
    ABORT_START = "abort_start"
    RESUME_SUCCESS = "resume_success"
    RESUME_FAILURE = "resume_failure"


# --- Request model ---

@dataclass
class RequestTerminal:
    """
    A request that has reached terminal state in the module decision chain.
    Models the metric increment behavior at the terminal point.
    """
    outcome: TerminalOutcome

    @property
    def increments_delivery_counter(self) -> bool:
        """
        Whether this terminal outcome increments
        conversion_deliveries_total.

        Per Requirement 5.8: ONLY when converted Markdown was produced
        AND downstream accepted the normal terminal last_buf.
        """
        return self.outcome in DELIVERY_OUTCOMES

    @property
    def requests_total_outcome_label(self) -> str:
        """The outcome label value for requests_total."""
        mapping = {
            TerminalOutcome.NORMAL_MARKDOWN_SUCCESS: "converted",
            TerminalOutcome.ABORT_TERMINAL: "aborted",
            TerminalOutcome.HTML_PASSTHROUGH: "skipped",
            TerminalOutcome.FAILED_OPEN_ORIGINAL_HTML: "failed_open",
            TerminalOutcome.FAILED_CLOSED: "failed_closed",
        }
        return mapping[self.outcome]


@dataclass
class StreamingEvent:
    """A streaming lifecycle transition that increments streaming_events_total."""
    transition: StreamingTransition
    reason: str = "none"

    @property
    def transition_label(self) -> str:
        """The label value emitted for the transition label."""
        return self.transition.value


# --- Metrics state model ---

@dataclass
class DeliveryMetricsSnapshot:
    """
    Simulated metrics state focused on delivery counter and
    streaming event semantics.
    """
    # Counter: successful deliveries only
    conversion_deliveries_total: int = 0
    # Counter: requests_total by outcome (for no-double-count check)
    requests_total_by_outcome: Dict[str, int] = field(default_factory=dict)
    # Counter: streaming events by transition label
    streaming_events_by_transition: Dict[str, int] = field(
        default_factory=dict
    )

    def process_terminal(self, req: RequestTerminal) -> None:
        """
        Record metrics at the terminal point of a request.

        Per Requirement 5.8:
        - conversion_deliveries_total only for successful delivery
        - requests_total always increments (with outcome label)
        """
        # requests_total: always exactly one increment
        outcome_label = req.requests_total_outcome_label
        self.requests_total_by_outcome[outcome_label] = (
            self.requests_total_by_outcome.get(outcome_label, 0) + 1
        )

        # conversion_deliveries_total: success-only
        if req.increments_delivery_counter:
            self.conversion_deliveries_total += 1

    def record_streaming_event(self, event: StreamingEvent) -> None:
        """Record a streaming lifecycle transition."""
        label = event.transition_label
        self.streaming_events_by_transition[label] = (
            self.streaming_events_by_transition.get(label, 0) + 1
        )


# --- Strategies ---

outcome_strategy = st.sampled_from(list(TerminalOutcome))

request_terminal_strategy = st.builds(
    RequestTerminal,
    outcome=outcome_strategy,
)

streaming_transition_strategy = st.sampled_from(list(StreamingTransition))

streaming_event_strategy = st.builds(
    StreamingEvent,
    transition=streaming_transition_strategy,
    reason=st.sampled_from(["none", "timeout", "budget", "downstream"]),
)

# Sequences of terminal requests (1–200)
request_sequence_strategy = st.lists(
    request_terminal_strategy,
    min_size=1,
    max_size=200,
)

# Sequences of streaming events (0–100)
streaming_event_sequence_strategy = st.lists(
    streaming_event_strategy,
    min_size=0,
    max_size=100,
)


# --- Property tests: delivery counter semantics ---

@settings(max_examples=200)
@given(outcome=st.just(TerminalOutcome.ABORT_TERMINAL))
def test_abort_terminal_does_not_increment_delivery(outcome):
    """
    Property 35a: Abort-terminal accepted does NOT increment
    conversion_deliveries_total.

    No converted Markdown content was delivered to the client.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    req = RequestTerminal(outcome=outcome)
    snapshot.process_terminal(req)
    assert snapshot.conversion_deliveries_total == 0, (
        f"Abort-terminal should NOT produce a delivery, got "
        f"conversion_deliveries_total={snapshot.conversion_deliveries_total}"
    )


@settings(max_examples=200)
@given(outcome=st.just(TerminalOutcome.HTML_PASSTHROUGH))
def test_html_passthrough_does_not_increment_delivery(outcome):
    """
    Property 35b: HTML passthrough terminal accepted does NOT increment
    conversion_deliveries_total.

    No conversion was performed; original HTML was passed through.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    req = RequestTerminal(outcome=outcome)
    snapshot.process_terminal(req)
    assert snapshot.conversion_deliveries_total == 0, (
        f"HTML passthrough should NOT produce a delivery, got "
        f"conversion_deliveries_total={snapshot.conversion_deliveries_total}"
    )


@settings(max_examples=200)
@given(outcome=st.just(TerminalOutcome.FAILED_OPEN_ORIGINAL_HTML))
def test_failed_open_does_not_increment_delivery(outcome):
    """
    Property 35c: Failed-open original HTML accepted does NOT increment
    conversion_deliveries_total.

    Conversion failed and the original HTML was delivered instead —
    no converted Markdown was produced.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    req = RequestTerminal(outcome=outcome)
    snapshot.process_terminal(req)
    assert snapshot.conversion_deliveries_total == 0, (
        f"Failed-open should NOT produce a delivery, got "
        f"conversion_deliveries_total={snapshot.conversion_deliveries_total}"
    )


@settings(max_examples=200)
@given(outcome=st.just(TerminalOutcome.FAILED_CLOSED))
def test_failed_closed_does_not_increment_delivery(outcome):
    """
    Property 35d: FAILED_CLOSED response accepted does NOT increment
    conversion_deliveries_total.

    An error response was returned; no converted Markdown was delivered.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    req = RequestTerminal(outcome=outcome)
    snapshot.process_terminal(req)
    assert snapshot.conversion_deliveries_total == 0, (
        f"FAILED_CLOSED should NOT produce a delivery, got "
        f"conversion_deliveries_total={snapshot.conversion_deliveries_total}"
    )


@settings(max_examples=200)
@given(outcome=st.just(TerminalOutcome.NORMAL_MARKDOWN_SUCCESS))
def test_normal_markdown_success_increments_delivery_once(outcome):
    """
    Property 35e: Normal Markdown terminal success increments
    conversion_deliveries_total exactly once.

    Converted Markdown was produced AND downstream accepted the
    normal terminal last_buf.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    req = RequestTerminal(outcome=outcome)
    snapshot.process_terminal(req)
    assert snapshot.conversion_deliveries_total == 1, (
        f"Normal Markdown success should produce exactly one delivery, "
        f"got conversion_deliveries_total="
        f"{snapshot.conversion_deliveries_total}"
    )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_non_delivery_outcomes_no_double_count(requests):
    """
    Property 35f: Non-delivery outcomes are recorded ONLY by
    requests_total{outcome=...} — they do NOT also appear as a
    delivery (no double-counting).

    Every request produces exactly one requests_total increment.
    Only successful deliveries also increment conversion_deliveries_total.
    The delivery counter never exceeds the count of success-outcome
    requests.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    for req in requests:
        snapshot.process_terminal(req)

    # Total requests_total == len(requests)
    total_requests = sum(snapshot.requests_total_by_outcome.values())
    assert total_requests == len(requests), (
        f"requests_total sum ({total_requests}) != "
        f"request count ({len(requests)})"
    )

    # Deliveries == count of success outcomes only
    success_count = sum(
        1 for req in requests
        if req.outcome == TerminalOutcome.NORMAL_MARKDOWN_SUCCESS
    )
    assert snapshot.conversion_deliveries_total == success_count, (
        f"conversion_deliveries_total "
        f"({snapshot.conversion_deliveries_total}) != "
        f"success count ({success_count})"
    )

    # No non-delivery outcome contributed to the delivery counter
    non_delivery_count = sum(
        1 for req in requests
        if req.outcome in NON_DELIVERY_OUTCOMES
    )
    assert non_delivery_count == (len(requests) - success_count), (
        f"Non-delivery count mismatch"
    )


@settings(max_examples=200)
@given(
    non_delivery_outcome=st.sampled_from(list(NON_DELIVERY_OUTCOMES))
)
def test_each_non_delivery_outcome_excluded_individually(
    non_delivery_outcome,
):
    """
    Property 35f-exhaustive: Each individual non-delivery outcome type
    is verified to never increment the delivery counter, regardless
    of which non-delivery outcome it is.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    req = RequestTerminal(outcome=non_delivery_outcome)
    snapshot.process_terminal(req)

    assert snapshot.conversion_deliveries_total == 0, (
        f"Non-delivery outcome {non_delivery_outcome.name} should NOT "
        f"increment delivery counter, got "
        f"{snapshot.conversion_deliveries_total}"
    )
    # But requests_total IS incremented
    total = sum(snapshot.requests_total_by_outcome.values())
    assert total == 1, (
        f"requests_total should increment for non-delivery outcome, "
        f"got {total}"
    )


# --- Property tests: streaming_events_total transition label ---

@settings(max_examples=200)
@given(events=streaming_event_sequence_strategy)
def test_streaming_events_transition_label_in_allowlist(events):
    """
    Property 35g: streaming_events_total uses a label named `transition`
    whose values come from the closed allowlist {commit, fallback,
    safe_finish_start, abort_start, resume_success, resume_failure}.

    No value outside this allowlist is emitted.

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    for event in events:
        snapshot.record_streaming_event(event)

    # All recorded transition labels must be in the allowlist
    for label in snapshot.streaming_events_by_transition.keys():
        assert label in TRANSITION_ALLOWLIST, (
            f"Transition label '{label}' is not in the closed "
            f"allowlist: {sorted(TRANSITION_ALLOWLIST)}"
        )


@settings(max_examples=200)
@given(transition=streaming_transition_strategy)
def test_streaming_transition_label_name_is_transition_not_event(
    transition,
):
    """
    Property 35h: The label is named `transition` (NOT `event`).

    The streaming_events_total metric uses a label dimension called
    'transition' to distinguish it from the 19 formal State Machine
    Events which have a different taxonomy and meaning.

    **Validates: Requirements 5.8**
    """
    event = StreamingEvent(transition=transition)
    label_value = event.transition_label

    # The label value must be one of the transition allowlist values
    # (NOT one of the State Machine Event names)
    assert label_value in TRANSITION_ALLOWLIST, (
        f"Label value '{label_value}' not in transition allowlist"
    )
    assert label_value not in STATE_MACHINE_EVENTS, (
        f"Label value '{label_value}' collides with State Machine "
        f"Events — transition labels must be distinct"
    )


@settings(max_examples=200)
@given(
    invalid_label=st.sampled_from(sorted(STATE_MACHINE_EVENTS))
)
def test_state_machine_events_not_valid_transitions(invalid_label):
    """
    Property 35i: The 19 State Machine Event names are NOT valid
    values for the streaming_events_total transition label.

    The `transition` label records metrics-lifecycle transitions,
    never the formal State Machine Event enum.

    **Validates: Requirements 5.8**
    """
    assert invalid_label not in TRANSITION_ALLOWLIST, (
        f"State Machine Event '{invalid_label}' should NOT be in "
        f"the transition allowlist, but it is"
    )


@settings(max_examples=200)
@given(transition=streaming_transition_strategy)
def test_transition_allowlist_completeness(transition):
    """
    Property 35j: Every StreamingTransition enum value maps to a
    value in the closed allowlist.

    Ensures the enum and allowlist are synchronized — no transition
    value falls outside the allowlist.

    **Validates: Requirements 5.8**
    """
    assert transition.value in TRANSITION_ALLOWLIST, (
        f"StreamingTransition.{transition.name} = '{transition.value}' "
        f"is not in TRANSITION_ALLOWLIST"
    )


def test_transition_allowlist_exact_size():
    """
    Property 35k: The transition allowlist contains exactly 6 values.

    The closed set is: commit, fallback, safe_finish_start,
    abort_start, resume_success, resume_failure.

    **Validates: Requirements 5.8**
    """
    expected = {
        "commit",
        "fallback",
        "safe_finish_start",
        "abort_start",
        "resume_success",
        "resume_failure",
    }
    assert len(TRANSITION_ALLOWLIST) == len(expected)


def test_transition_allowlist_exact_values():
    """
    Property 35l: The transition allowlist contains the exact specified
    values with no additions or omissions.

    **Validates: Requirements 5.8**
    """
    expected = {
        "commit",
        "fallback",
        "safe_finish_start",
        "abort_start",
        "resume_success",
        "resume_failure",
    }
    assert TRANSITION_ALLOWLIST == expected, (
        f"Transition allowlist mismatch.\n"
        f"  Expected: {sorted(expected)}\n"
        f"  Actual: {sorted(TRANSITION_ALLOWLIST)}"
    )


def test_transition_and_state_machine_events_disjoint():
    """
    Property 35m: The transition allowlist and the State Machine Events
    set are completely disjoint — no value belongs to both taxonomies.

    **Validates: Requirements 5.8**
    """
    overlap = TRANSITION_ALLOWLIST & STATE_MACHINE_EVENTS
    assert len(overlap) == 0, (
        f"Transition allowlist and State Machine Events overlap: "
        f"{sorted(overlap)}"
    )


# --- Combined delivery semantics property ---

@settings(max_examples=500)
@given(requests=request_sequence_strategy)
def test_delivery_counter_semantics_combined(requests):
    """
    Property 35 (combined): All delivery counter semantics hold
    simultaneously for any request sequence.

    1. conversion_deliveries_total == count of NORMAL_MARKDOWN_SUCCESS
    2. No non-delivery outcome increments the delivery counter
    3. Every request increments requests_total exactly once
    4. The sum of deliveries + non-deliveries == total requests

    **Validates: Requirements 5.8**
    """
    snapshot = DeliveryMetricsSnapshot()
    for req in requests:
        snapshot.process_terminal(req)

    # Count outcomes
    success_count = sum(
        1 for req in requests
        if req.outcome == TerminalOutcome.NORMAL_MARKDOWN_SUCCESS
    )
    non_delivery_count = sum(
        1 for req in requests
        if req.outcome in NON_DELIVERY_OUTCOMES
    )

    # 1. Deliveries == success count only
    assert snapshot.conversion_deliveries_total == success_count, (
        f"Deliveries ({snapshot.conversion_deliveries_total}) != "
        f"success count ({success_count})"
    )

    # 2. Non-delivery outcomes never leaked into delivery counter
    assert snapshot.conversion_deliveries_total <= len(requests), (
        f"Deliveries exceed total requests"
    )
    assert (
        snapshot.conversion_deliveries_total
        == len(requests) - non_delivery_count
    ), (
        f"Delivery count ({snapshot.conversion_deliveries_total}) != "
        f"total ({len(requests)}) - non_delivery ({non_delivery_count})"
    )

    # 3. requests_total == total requests
    total_requests = sum(snapshot.requests_total_by_outcome.values())
    assert total_requests == len(requests)

    # 4. Partition is exhaustive
    assert success_count + non_delivery_count == len(requests)
