"""
Property 10: Metrics event model conservation — property-based tests.

Verifies the conservation relations derivable from the frozen event model
defined in the metrics registry (Requirement 5.6):

Frozen event model:
  - Each request entering the module decision chain produces exactly one
    terminal outcome
  - Each request begins at most one conversion attempt
  - Each successful attempt produces at most one request-level successful
    delivery outcome
  - The inflight gauge returns to zero after quiescence

Conservation relations:
  - sum(requests_total) equals number of requests that entered the decision
    chain (exactly one increment per request)
  - sum(conversion_attempts_total) <= sum(requests_total)
  - sum(conversion_deliveries_total) <= sum(conversion_attempts_total)
    (deliveries is success-only)
  - conversion_duration_seconds_count <= sum(conversion_attempts_total)
  - inflight_requests == 0 after quiescence

Tests account for overload, conditional 304, passthrough, client abort,
and streaming multi-delivery scenarios.

No relation is asserted between event counters (streaming_events_total,
decompression_events_total, dynconf_reloads_total) and request counts.

**Validates: Requirements 5.6**
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List

from hypothesis import given, settings
from hypothesis import strategies as st

# Ensure the tools package is importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent.parent)
)

METRICS_REGISTRY = json.loads(
    (Path(__file__).resolve().parents[4] / "schemas" / "metrics-v1.registry.json")
    .read_text(encoding="utf-8")
)


# --- Request outcome model ---

class RequestOutcome(Enum):
    """Terminal outcomes from the frozen event model."""
    CONVERTED = auto()       # Full successful conversion and delivery
    SKIPPED = auto()         # Skipped (not eligible, accept mismatch, etc.)
    FAILED_OPEN = auto()     # Conversion failed, original HTML delivered
    FAILED_CLOSED = auto()   # Conversion failed, error response delivered
    ABORTED = auto()         # Client abort during conversion


def _registry_outcomes() -> list[str]:
    family = next(
        item
        for item in METRICS_REGISTRY["families"]
        if item["name"] == "nginx_markdown_requests_total"
    )
    return next(
        label["values"]
        for label in family["labels"]
        if label["name"] == "outcome"
    )


def test_request_outcomes_match_registry_allowlist() -> None:
    assert {outcome.name.lower() for outcome in RequestOutcome} == set(
        _registry_outcomes()
    )


class RequestScenario(Enum):
    """Scenarios that determine request path through the decision chain."""
    NORMAL_CONVERT = auto()      # Normal eligible request, full conversion
    OVERLOAD = auto()            # Rejected due to inflight limit
    CONDITIONAL_304 = auto()     # Conditional request, 304 response
    PASSTHROUGH = auto()         # Not eligible (wrong content-type, etc.)
    CLIENT_ABORT = auto()        # Client disconnects during processing
    STREAMING_CONVERT = auto()   # Streaming engine conversion
    DECOMPRESS_FAIL = auto()     # Decompression fails, fail-open


class ConversionEngine(Enum):
    """Engine used for conversion attempt."""
    FULL_BUFFER = auto()
    STREAMING = auto()


@dataclass
class RequestEvent:
    """A single request flowing through the module decision chain."""
    scenario: RequestScenario
    engine: ConversionEngine = ConversionEngine.FULL_BUFFER

    @property
    def outcome(self) -> RequestOutcome:
        """Determine the terminal outcome from the scenario."""
        mapping = {
            RequestScenario.NORMAL_CONVERT: RequestOutcome.CONVERTED,
            RequestScenario.OVERLOAD: RequestOutcome.SKIPPED,
            RequestScenario.CONDITIONAL_304: RequestOutcome.SKIPPED,
            RequestScenario.PASSTHROUGH: RequestOutcome.SKIPPED,
            RequestScenario.CLIENT_ABORT: RequestOutcome.ABORTED,
            RequestScenario.STREAMING_CONVERT: RequestOutcome.CONVERTED,
            RequestScenario.DECOMPRESS_FAIL: RequestOutcome.FAILED_OPEN,
        }
        return mapping[self.scenario]

    @property
    def attempts_conversion(self) -> bool:
        """Whether this request begins a conversion attempt."""
        # Only scenarios that actually enter the conversion engine
        # produce an attempt. Skipped/passthrough/overload/304 do not.
        return self.scenario in (
            RequestScenario.NORMAL_CONVERT,
            RequestScenario.STREAMING_CONVERT,
            RequestScenario.CLIENT_ABORT,      # Attempt started, then aborted
            RequestScenario.DECOMPRESS_FAIL,   # Attempt started, then failed
        )

    @property
    def records_duration(self) -> bool:
        """Whether this request records a conversion duration observation."""
        # Duration is recorded when the conversion timer stops,
        # regardless of success/failure — but only if an attempt was made
        return self.attempts_conversion

    @property
    def delivers_successfully(self) -> bool:
        """Whether this request produces a successful delivery."""
        # Delivery is success-only: converted Markdown produced AND
        # downstream accepted the terminal last_buf
        return self.outcome == RequestOutcome.CONVERTED


# --- Metrics state model ---

@dataclass
class MetricsSnapshot:
    """Simulated metrics state after processing a sequence of requests."""
    # Counter: total requests with terminal outcome (one per request)
    requests_total: int = 0
    # Counter: total conversion attempts (at most one per request)
    conversion_attempts_total: int = 0
    # Counter: successful deliveries only
    conversion_deliveries_total: int = 0
    # Histogram count: observations recorded (one per attempt with timer)
    conversion_duration_count: int = 0
    # Gauge: currently inflight requests
    inflight_requests: int = 0
    # Event counters (not asserted against request counts)
    streaming_events_total: int = 0
    decompression_events_total: int = 0
    dynconf_reloads_total: int = 0

    def enter_request(self, req: RequestEvent) -> None:
        """Record the decision-chain entry and any conversion admission."""
        if req.attempts_conversion:
            self.inflight_requests += 1

    def terminate_request(self, req: RequestEvent) -> None:
        """Record the terminal outcome and reconcile request-local state."""
        # requests_total: exactly one increment per request at terminal
        self.requests_total += 1

        # conversion_attempts_total: at most once per request when engine
        # selection committed and attempt latch transitions 0->1
        if req.attempts_conversion:
            self.conversion_attempts_total += 1

        # conversion_duration_seconds: observation recorded when timer stops
        if req.records_duration:
            self.conversion_duration_count += 1

        # conversion_deliveries_total: success-only
        if req.delivers_successfully:
            self.conversion_deliveries_total += 1

        # Inflight decremented at terminal state
        if req.attempts_conversion:
            self.inflight_requests -= 1

        # Event counters: independent of request counts
        if req.scenario == RequestScenario.STREAMING_CONVERT:
            # Streaming events happen during streaming conversions
            self.streaming_events_total += 1
        if req.scenario == RequestScenario.DECOMPRESS_FAIL:
            # Decompression event for the failed decompression
            self.decompression_events_total += 1

    def process_request(self, req: RequestEvent) -> None:
        """Run one request from entry through its terminal outcome."""
        self.enter_request(req)
        self.terminate_request(req)


def process_request_sequence(requests: List[RequestEvent]) -> MetricsSnapshot:
    """Process a full sequence of requests and return the quiescent state."""
    snapshot = MetricsSnapshot()
    for req in requests:
        snapshot.process_request(req)
    return snapshot


# --- Strategies ---

# Strategy for individual request scenarios
scenario_strategy = st.sampled_from(list(RequestScenario))

# Strategy for conversion engine selection
engine_strategy = st.sampled_from(list(ConversionEngine))


def request_event_strategy():
    """Generate a valid RequestEvent with appropriate engine."""
    return st.builds(
        _make_request_event,
        scenario=scenario_strategy,
        engine=engine_strategy,
    )


def _make_request_event(
    scenario: RequestScenario,
    engine: ConversionEngine,
) -> RequestEvent:
    """
    Create a request event, ensuring engine is consistent with scenario.
    Streaming scenarios must use streaming engine.
    """
    if scenario == RequestScenario.STREAMING_CONVERT:
        engine = ConversionEngine.STREAMING
    elif scenario in (
        RequestScenario.OVERLOAD,
        RequestScenario.CONDITIONAL_304,
        RequestScenario.PASSTHROUGH,
    ):
        # Non-converting scenarios don't use an engine meaningfully,
        # but we preserve whatever was generated
        pass
    return RequestEvent(scenario=scenario, engine=engine)


# Strategy for request sequences (1 to 200 requests)
request_sequence_strategy = st.lists(
    request_event_strategy(),
    min_size=1,
    max_size=200,
)

# Strategy for mixed workloads with explicit scenario distribution
mixed_workload_strategy = st.lists(
    st.one_of(
        # Normal conversions (common)
        st.just(RequestEvent(RequestScenario.NORMAL_CONVERT)),
        st.just(
            RequestEvent(RequestScenario.STREAMING_CONVERT,
                         ConversionEngine.STREAMING)
        ),
        # Skipped scenarios
        st.just(RequestEvent(RequestScenario.OVERLOAD)),
        st.just(RequestEvent(RequestScenario.CONDITIONAL_304)),
        st.just(RequestEvent(RequestScenario.PASSTHROUGH)),
        # Failure scenarios
        st.just(RequestEvent(RequestScenario.CLIENT_ABORT)),
        st.just(RequestEvent(RequestScenario.DECOMPRESS_FAIL)),
    ),
    min_size=1,
    max_size=200,
)


# --- Property tests ---

@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_requests_total_equals_request_count(requests):
    """
    Property 10a: sum(requests_total) equals the number of requests that
    entered the decision chain (exactly one increment per request).

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)
    assert snapshot.requests_total == len(requests), (
        f"requests_total ({snapshot.requests_total}) != "
        f"request count ({len(requests)})"
    )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_attempts_leq_requests(requests):
    """
    Property 10b: sum(conversion_attempts_total) <= sum(requests_total).

    Not every request that enters the decision chain begins a conversion
    attempt. Skipped, passthrough, overload, and conditional-304 requests
    do not attempt conversion.

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)
    assert snapshot.conversion_attempts_total <= snapshot.requests_total, (
        f"conversion_attempts_total ({snapshot.conversion_attempts_total}) > "
        f"requests_total ({snapshot.requests_total})"
    )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_deliveries_leq_attempts(requests):
    """
    Property 10c: sum(conversion_deliveries_total) <=
    sum(conversion_attempts_total).

    Deliveries is success-only: not all conversion attempts result in a
    successful delivery. Aborted, failed_open, and failed_closed attempts
    do not produce a delivery.

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)
    assert (
        snapshot.conversion_deliveries_total
        <= snapshot.conversion_attempts_total
    ), (
        f"conversion_deliveries_total "
        f"({snapshot.conversion_deliveries_total}) > "
        f"conversion_attempts_total "
        f"({snapshot.conversion_attempts_total})"
    )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_duration_count_leq_attempts(requests):
    """
    Property 10d: conversion_duration_seconds_count <=
    sum(conversion_attempts_total).

    Duration observation is recorded when the conversion timer stops.
    Timer only starts if a conversion attempt was made.

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)
    assert (
        snapshot.conversion_duration_count
        <= snapshot.conversion_attempts_total
    ), (
        f"conversion_duration_count "
        f"({snapshot.conversion_duration_count}) > "
        f"conversion_attempts_total "
        f"({snapshot.conversion_attempts_total})"
    )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_inflight_zero_after_quiescence(requests):
    """
    Property 10e: inflight_requests == 0 after quiescence.

    After all requests in the sequence have completed processing
    (quiescence), the inflight gauge must return to zero.

    **Validates: Requirements 5.6**
    """
    snapshot = MetricsSnapshot()
    saw_admitted_request = False
    for req in requests:
        snapshot.enter_request(req)
        if req.attempts_conversion:
            saw_admitted_request = True
            assert snapshot.inflight_requests > 0, (
                "inflight_requests must be observable after admission"
            )
        snapshot.terminate_request(req)
        assert snapshot.inflight_requests == 0, (
            "inflight_requests must be reconciled at request termination"
        )

    assert snapshot.inflight_requests == 0, (
        f"inflight_requests ({snapshot.inflight_requests}) != 0 "
        f"after processing {len(requests)} requests"
    )
    # Keep the phase assertion meaningful even when Hypothesis generates a
    # sequence containing only skipped requests.
    if not saw_admitted_request:
        probe = MetricsSnapshot()
        admitted = RequestEvent(RequestScenario.NORMAL_CONVERT)
        probe.enter_request(admitted)
        assert probe.inflight_requests == 1
        probe.terminate_request(admitted)
        assert probe.inflight_requests == 0


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_at_most_one_attempt_per_request(requests):
    """
    Property 10f: Each request begins at most one conversion attempt.

    Verify that no single request can produce more than one increment
    to conversion_attempts_total.

    **Validates: Requirements 5.6**
    """
    for req in requests:
        # Process each request individually
        snapshot = MetricsSnapshot()
        snapshot.process_request(req)
        assert snapshot.conversion_attempts_total <= 1, (
            f"Single request produced "
            f"{snapshot.conversion_attempts_total} attempts "
            f"(scenario={req.scenario.name})"
        )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_at_most_one_delivery_per_attempt(requests):
    """
    Property 10g: Each successful attempt produces at most one
    request-level successful delivery outcome.

    **Validates: Requirements 5.6**
    """
    for req in requests:
        snapshot = MetricsSnapshot()
        snapshot.process_request(req)
        assert snapshot.conversion_deliveries_total <= 1, (
            f"Single request produced "
            f"{snapshot.conversion_deliveries_total} deliveries "
            f"(scenario={req.scenario.name})"
        )


@settings(max_examples=200)
@given(requests=request_sequence_strategy)
def test_exactly_one_terminal_outcome_per_request(requests):
    """
    Property 10h: Each request entering the module decision chain
    produces exactly one terminal outcome (exactly one requests_total
    increment).

    **Validates: Requirements 5.6**
    """
    for req in requests:
        snapshot = MetricsSnapshot()
        snapshot.process_request(req)
        assert snapshot.requests_total == 1, (
            f"Single request produced "
            f"{snapshot.requests_total} terminal outcomes "
            f"(scenario={req.scenario.name})"
        )


# --- Scenario-specific conservation tests ---

@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_overload_skipped_no_attempt(requests):
    """
    Property 10i: Overloaded requests do not produce a conversion attempt.

    Tests conservation under overload conditions: requests rejected due to
    the inflight limit are counted in requests_total but NOT in
    conversion_attempts_total.

    **Validates: Requirements 5.6**
    """
    overload_count = sum(
        1 for r in requests if r.scenario == RequestScenario.OVERLOAD
    )
    attempt_count = sum(
        1 for r in requests if r.attempts_conversion
    )

    snapshot = process_request_sequence(requests)

    # Overload requests contribute to requests_total but not attempts
    assert snapshot.requests_total == len(requests)
    assert snapshot.conversion_attempts_total == attempt_count
    # Verify no overload request leaked into attempts
    assert snapshot.conversion_attempts_total <= len(requests) - overload_count


@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_conditional_304_no_attempt(requests):
    """
    Property 10j: Conditional 304 responses do not produce a conversion
    attempt.

    A conditional request that results in 304 does not enter the
    conversion engine.

    **Validates: Requirements 5.6**
    """
    conditional_count = sum(
        1 for r in requests
        if r.scenario == RequestScenario.CONDITIONAL_304
    )

    snapshot = process_request_sequence(requests)
    assert snapshot.conversion_attempts_total <= (
        len(requests) - conditional_count
    )


@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_passthrough_no_attempt(requests):
    """
    Property 10k: Passthrough requests do not produce a conversion attempt.

    Requests that bypass the module (wrong content-type, not eligible)
    do not begin a conversion.

    **Validates: Requirements 5.6**
    """
    passthrough_count = sum(
        1 for r in requests
        if r.scenario == RequestScenario.PASSTHROUGH
    )

    snapshot = process_request_sequence(requests)
    assert snapshot.conversion_attempts_total <= (
        len(requests) - passthrough_count
    )


@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_client_abort_no_delivery(requests):
    """
    Property 10l: Client-aborted requests do not produce a delivery.

    A request where the client disconnects during processing counts
    as an attempt but NOT as a successful delivery.

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)

    # Aborted requests attempt conversion but never deliver
    successful_scenarios = sum(
        1 for r in requests if r.delivers_successfully
    )
    assert snapshot.conversion_deliveries_total == successful_scenarios


@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_streaming_multi_delivery_conservation(requests):
    """
    Property 10m: Streaming conversions produce at most one delivery per
    request despite potentially sending multiple chunks.

    The streaming engine may send multiple body chunks, but
    conversion_deliveries_total counts only the single terminal
    last_buf acceptance (at most one per request).

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)

    # Each streaming conversion produces exactly one delivery (not
    # one per chunk)
    streaming_deliveries = sum(
        1 for r in requests
        if r.scenario == RequestScenario.STREAMING_CONVERT
        and r.delivers_successfully
    )
    normal_deliveries = sum(
        1 for r in requests
        if r.scenario == RequestScenario.NORMAL_CONVERT
        and r.delivers_successfully
    )
    assert snapshot.conversion_deliveries_total == (
        streaming_deliveries + normal_deliveries
    )


@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_event_counters_are_scenario_driven(requests):
    """
    Property 10n: Event counters (streaming_events_total,
    decompression_events_total, dynconf_reloads_total) are scenario-driven
    rather than proportional to the raw request count.

    Streaming events fire once per STREAMING_CONVERT request and
    decompression events fire once per DECOMPRESS_FAIL request; a workload
    without those scenarios leaves the counters at zero regardless of how
    many requests it contains.

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)

    # Concrete expected-counter checks derived from the request workload:
    # streaming events fire once per STREAMING_CONVERT request, and
    # decompression events fire once per DECOMPRESS_FAIL request.
    expected_streaming = sum(
        1 for req in requests if req.scenario == RequestScenario.STREAMING_CONVERT
    )
    expected_decomp = sum(
        1 for req in requests if req.scenario == RequestScenario.DECOMPRESS_FAIL
    )
    assert snapshot.streaming_events_total == expected_streaming
    assert snapshot.decompression_events_total == expected_decomp
    assert snapshot.dynconf_reloads_total >= 0


@settings(max_examples=200)
@given(requests=mixed_workload_strategy)
def test_failed_open_attempt_no_delivery(requests):
    """
    Property 10o: Failed-open requests count as attempts but NOT as
    deliveries.

    A decompression failure that fails open still had a conversion
    attempt (the attempt started before the failure was detected).

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)

    # Failed-open contribute to attempts but not deliveries
    non_delivering_attempts = sum(
        1 for r in requests
        if r.attempts_conversion and not r.delivers_successfully
    )
    delivering_attempts = sum(
        1 for r in requests
        if r.attempts_conversion and r.delivers_successfully
    )
    assert snapshot.conversion_deliveries_total == delivering_attempts
    assert snapshot.conversion_attempts_total == (
        delivering_attempts + non_delivering_attempts
    )


# --- Combined conservation invariant (all relations in one test) ---

@settings(max_examples=500)
@given(requests=request_sequence_strategy)
def test_all_conservation_relations_hold(requests):
    """
    Property 10 (combined): All conservation relations hold simultaneously
    for any request sequence after quiescence.

    This is the master conservation test that verifies all five relations
    from the frozen event model in a single pass.

    **Validates: Requirements 5.6**
    """
    snapshot = process_request_sequence(requests)

    # Relation 1: requests_total == number of requests
    assert snapshot.requests_total == len(requests), (
        f"R1 violated: requests_total={snapshot.requests_total} "
        f"!= len(requests)={len(requests)}"
    )

    # Relation 2: attempts <= requests
    assert snapshot.conversion_attempts_total <= snapshot.requests_total, (
        f"R2 violated: attempts={snapshot.conversion_attempts_total} "
        f"> requests={snapshot.requests_total}"
    )

    # Relation 3: deliveries <= attempts (success-only)
    assert (
        snapshot.conversion_deliveries_total
        <= snapshot.conversion_attempts_total
    ), (
        f"R3 violated: deliveries="
        f"{snapshot.conversion_deliveries_total} "
        f"> attempts={snapshot.conversion_attempts_total}"
    )

    # Relation 4: duration_count <= attempts
    assert (
        snapshot.conversion_duration_count
        <= snapshot.conversion_attempts_total
    ), (
        f"R4 violated: duration_count="
        f"{snapshot.conversion_duration_count} "
        f"> attempts={snapshot.conversion_attempts_total}"
    )

    # Relation 5: inflight == 0 after quiescence
    assert snapshot.inflight_requests == 0, (
        f"R5 violated: inflight={snapshot.inflight_requests} != 0"
    )
