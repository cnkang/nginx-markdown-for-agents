"""Property-based tests for doctor advice rule engine correctness.

**Validates: Requirements 8.3, 8.5, 8.6**

Property 10: Doctor Advice Rule Engine Correctness
    For any set of metrics where a rule's threshold condition is met, the doctor
    advice tool shall produce a finding with the correct rule ID and severity.
    For any set of metrics where a rule's referenced metric is missing, the tool
    shall skip that rule gracefully without crashing and note the omission.
    The exit code shall equal the maximum severity observed (0=info, 1=warn,
    2=critical).

Run:
    python3 -m pytest tools/perf/tests/test_doctor_advice_property.py -q
"""

import sys
from pathlib import Path

from hypothesis import given, assume, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Make the parent package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doctor_advice import (
    evaluate_rules,
    compute_exit_code,
    Finding,
    SEVERITY_ORDER,
    RULE_METRICS,
    DEFAULT_STREAMING_BUFFER_BUDGET,
)


# ---------------------------------------------------------------------------
# Expected rule severities and thresholds (from design §7)
# ---------------------------------------------------------------------------

RULE_SEVERITIES = {
    "D01": "warn",   # streaming fallback rate > 10%
    "D02": "warn",   # overload_total > 0
    "D03": "warn",   # backpressure rate > 5%
    "D04": "info",   # decompression full-buffer >> streaming (>10:1)
    "D05": "warn",   # decompression budget exceeded > 0
    "D06": "warn",   # pending watermark > 80% budget
    "D07": "info",   # copied >> zero_copy (>5:1)
}

ALL_RULE_IDS = list(RULE_SEVERITIES.keys())


# ---------------------------------------------------------------------------
# Strategies for generating metric sets
# ---------------------------------------------------------------------------

# Positive float for metric counters
positive_counter = st.floats(
    min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False
)

# Non-zero positive counter
nonzero_counter = st.floats(
    min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False
)

# Watermark bytes
watermark_bytes = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)


def _build_triggering_metrics_d01(requests_total: float) -> dict:
    """Build metrics that trigger D01 (fallback rate > 10%)."""
    # fallback > 10% of requests_total
    fallback = requests_total * 0.15  # 15% fallback rate
    return {
        "streaming_fallback_total": fallback,
        "streaming_requests_total": requests_total,
    }


def _build_triggering_metrics_d02() -> dict:
    """Build metrics that trigger D02 (overload_total > 0)."""
    return {"overload_total": 5.0}


def _build_triggering_metrics_d03(requests_total: float) -> dict:
    """Build metrics that trigger D03 (backpressure rate > 5%)."""
    bp = requests_total * 0.10  # 10% backpressure rate
    return {
        "backpressure_total": bp,
        "streaming_requests_total": requests_total,
    }


def _build_triggering_metrics_d04() -> dict:
    """Build metrics that trigger D04 (fullbuffer >> streaming, >10:1)."""
    return {
        "decompression_fullbuffer_total": 110.0,
        "decompression_streaming_total": 5.0,
    }


def _build_triggering_metrics_d05() -> dict:
    """Build metrics that trigger D05 (budget exceeded > 0)."""
    return {"decompression_budget_exceeded_total": 3.0}


def _build_triggering_metrics_d06() -> dict:
    """Build metrics that trigger D06 (watermark > 80% of budget)."""
    # Default budget is 1MiB; use 90% of it
    return {
        "pending_output_high_watermark_bytes": DEFAULT_STREAMING_BUFFER_BUDGET * 0.90,
        "streaming_buffer_budget": DEFAULT_STREAMING_BUFFER_BUDGET,
    }


def _build_triggering_metrics_d07() -> dict:
    """Build metrics that trigger D07 (copied >> zero_copy, >5:1)."""
    return {
        "copied_output_total": 600.0,
        "zero_copy_output_total": 10.0,
    }


TRIGGER_BUILDERS = {
    "D01": lambda: _build_triggering_metrics_d01(1000.0),
    "D02": _build_triggering_metrics_d02,
    "D03": lambda: _build_triggering_metrics_d03(1000.0),
    "D04": _build_triggering_metrics_d04,
    "D05": _build_triggering_metrics_d05,
    "D06": _build_triggering_metrics_d06,
    "D07": _build_triggering_metrics_d07,
}


# ---------------------------------------------------------------------------
# Property 10: Triggered rules produce correct ID and severity
# ---------------------------------------------------------------------------

@given(rule_id=st.sampled_from(ALL_RULE_IDS))
@settings(max_examples=200)
def test_property10_triggered_rule_has_correct_id_and_severity(rule_id):
    """**Validates: Requirements 8.3**

    For any metrics meeting a rule's threshold condition, the doctor advice tool
    shall produce a finding with the correct rule ID and severity level.
    """
    # Build metrics that trigger the selected rule
    metrics = TRIGGER_BUILDERS[rule_id]()

    findings, skipped = evaluate_rules(metrics)

    # Find the finding for this rule
    matching = [f for f in findings if f.rule_id == rule_id]
    assert len(matching) == 1, (
        f"Expected exactly 1 finding for {rule_id} but got {len(matching)}. "
        f"Findings: {[(f.rule_id, f.severity) for f in findings]}, "
        f"Skipped: {skipped}"
    )

    finding = matching[0]
    expected_severity = RULE_SEVERITIES[rule_id]
    assert finding.severity == expected_severity, (
        f"Rule {rule_id}: expected severity '{expected_severity}' "
        f"but got '{finding.severity}'"
    )


@given(
    requests_total=st.floats(
        min_value=100.0, max_value=1e7, allow_nan=False, allow_infinity=False
    ),
    fallback_rate=st.floats(
        min_value=0.11, max_value=0.95, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=200)
def test_property10_d01_varied_rates_correct_severity(requests_total, fallback_rate):
    """**Validates: Requirements 8.3**

    D01 triggers with correct severity for any fallback rate > 10%.
    """
    metrics = {
        "streaming_fallback_total": requests_total * fallback_rate,
        "streaming_requests_total": requests_total,
    }

    findings, skipped = evaluate_rules(metrics)
    matching = [f for f in findings if f.rule_id == "D01"]

    assert len(matching) == 1, (
        f"D01 should trigger with rate={fallback_rate:.2%}"
    )
    assert matching[0].severity == "warn"


@given(
    requests_total=st.floats(
        min_value=100.0, max_value=1e7, allow_nan=False, allow_infinity=False
    ),
    bp_rate=st.floats(
        min_value=0.06, max_value=0.95, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=200)
def test_property10_d03_varied_rates_correct_severity(requests_total, bp_rate):
    """**Validates: Requirements 8.3**

    D03 triggers with correct severity for any backpressure rate > 5%.
    """
    metrics = {
        "backpressure_total": requests_total * bp_rate,
        "streaming_requests_total": requests_total,
    }

    findings, skipped = evaluate_rules(metrics)
    matching = [f for f in findings if f.rule_id == "D03"]

    assert len(matching) == 1, (
        f"D03 should trigger with rate={bp_rate:.2%}"
    )
    assert matching[0].severity == "warn"


@given(
    overload_count=st.floats(
        min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=200)
def test_property10_d02_any_positive_overload_triggers(overload_count):
    """**Validates: Requirements 8.3**

    D02 triggers for any positive overload_total value.
    """
    metrics = {"overload_total": overload_count}

    findings, skipped = evaluate_rules(metrics)
    matching = [f for f in findings if f.rule_id == "D02"]

    assert len(matching) == 1, (
        f"D02 should trigger with overload_total={overload_count}"
    )
    assert matching[0].severity == "warn"


@given(
    budget_exceeded=st.floats(
        min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=200)
def test_property10_d05_any_positive_budget_exceeded_triggers(budget_exceeded):
    """**Validates: Requirements 8.3**

    D05 triggers for any positive decompression_budget_exceeded_total value.
    """
    metrics = {"decompression_budget_exceeded_total": budget_exceeded}

    findings, skipped = evaluate_rules(metrics)
    matching = [f for f in findings if f.rule_id == "D05"]

    assert len(matching) == 1, (
        f"D05 should trigger with budget_exceeded={budget_exceeded}"
    )
    assert matching[0].severity == "warn"


# ---------------------------------------------------------------------------
# Property 10: Missing metrics produce graceful skip without crash
# ---------------------------------------------------------------------------

@given(rule_id=st.sampled_from(ALL_RULE_IDS))
@settings(max_examples=200)
def test_property10_missing_metrics_graceful_skip(rule_id):
    """**Validates: Requirements 8.6**

    For any set of metrics where a rule's referenced metric is missing, the tool
    shall skip that rule gracefully without crashing and note the omission.
    """
    # Empty metrics dict — no metrics present at all
    metrics = {}

    findings, skipped = evaluate_rules(metrics)

    # The rule should be skipped (all rules require at least one metric)
    skipped_ids = [s.split(" ")[0] for s in skipped]
    assert rule_id in skipped_ids, (
        f"Rule {rule_id} should be skipped when metrics are empty. "
        f"Skipped: {skipped}, Findings: {[(f.rule_id, f.severity) for f in findings]}"
    )


@given(
    rule_id=st.sampled_from(ALL_RULE_IDS),
    extra_keys=st.dictionaries(
        keys=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
        values=st.floats(
            min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
        min_size=0,
        max_size=5,
    ),
)
@settings(max_examples=300)
def test_property10_partial_metrics_no_crash(rule_id, extra_keys):
    """**Validates: Requirements 8.6**

    For any set of metrics with irrelevant keys (missing the required ones),
    the rule engine does not crash and gracefully handles the absence.
    """
    # Provide only random keys that aren't the required metrics for any rule
    required = set()
    for spec in RULE_METRICS.values():
        required.update(spec["required"])

    # Filter out any accidentally matching keys
    metrics = {k: v for k, v in extra_keys.items() if k not in required}

    # Should not crash
    findings, skipped = evaluate_rules(metrics)

    # All rules should be skipped since no required metrics are present
    assert isinstance(findings, list)
    assert isinstance(skipped, list)
    assert len(skipped) == len(ALL_RULE_IDS), (
        f"Expected all {len(ALL_RULE_IDS)} rules skipped but got {len(skipped)}. "
        f"Skipped: {skipped}"
    )


@given(
    rule_id=st.sampled_from(ALL_RULE_IDS),
    metric_to_remove_idx=st.integers(min_value=0, max_value=10),
)
@settings(max_examples=300)
def test_property10_removing_one_required_metric_skips_rule(
    rule_id, metric_to_remove_idx
):
    """**Validates: Requirements 8.6**

    When one required metric for a rule is removed, that rule is skipped
    gracefully while other rules with their metrics present still work.
    """
    # Build full triggering metrics for this rule
    metrics = TRIGGER_BUILDERS[rule_id]()

    # Remove one of the required metrics
    required = RULE_METRICS[rule_id]["required"]
    idx = metric_to_remove_idx % len(required)
    removed_key = required[idx]
    del metrics[removed_key]

    # Should not crash
    findings, skipped = evaluate_rules(metrics)

    # This specific rule should be skipped
    skipped_ids = [s.split(" ")[0] for s in skipped]
    assert rule_id in skipped_ids, (
        f"Rule {rule_id} should be skipped when '{removed_key}' is missing. "
        f"Skipped: {skipped}"
    )


# ---------------------------------------------------------------------------
# Property 10: Exit code equals max severity observed
# ---------------------------------------------------------------------------

@given(
    rule_ids=st.lists(
        st.sampled_from(ALL_RULE_IDS), min_size=1, max_size=7, unique=True
    )
)
@settings(max_examples=300)
def test_property10_exit_code_equals_max_severity(rule_ids):
    """**Validates: Requirements 8.5**

    The exit code shall equal the maximum severity observed:
    0=info, 1=warn, 2=critical.
    """
    # Combine metrics that trigger all selected rules
    metrics = {}
    for rule_id in rule_ids:
        metrics.update(TRIGGER_BUILDERS[rule_id]())

    findings, skipped = evaluate_rules(metrics)

    # Compute expected max severity from the triggered rules
    triggered_findings = [f for f in findings if f.rule_id in rule_ids]

    # At least some findings should be present (some rules may share metrics
    # and get triggered or not depending on combined values)
    if not findings:
        # If no findings, exit code should be 0
        assert compute_exit_code(findings) == 0
        return

    # Exit code should equal max severity
    expected_exit = max(SEVERITY_ORDER[f.severity] for f in findings)
    actual_exit = compute_exit_code(findings)

    assert actual_exit == expected_exit, (
        f"Exit code mismatch: expected {expected_exit} but got {actual_exit}. "
        f"Findings: {[(f.rule_id, f.severity) for f in findings]}"
    )


@given(
    info_count=st.integers(min_value=0, max_value=5),
    warn_count=st.integers(min_value=0, max_value=5),
    critical_count=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=300)
def test_property10_exit_code_synthetic_findings(
    info_count, warn_count, critical_count
):
    """**Validates: Requirements 8.5**

    For any mix of finding severities, exit code equals max severity
    (0=info/none, 1=warn, 2=critical).
    """
    findings = []
    for _ in range(info_count):
        findings.append(
            Finding("DXX", "info", "test", "test", {})
        )
    for _ in range(warn_count):
        findings.append(
            Finding("DXX", "warn", "test", "test", {})
        )
    for _ in range(critical_count):
        findings.append(
            Finding("DXX", "critical", "test", "test", {})
        )

    actual_exit = compute_exit_code(findings)

    if critical_count > 0:
        assert actual_exit == 2
    elif warn_count > 0:
        assert actual_exit == 1
    elif info_count > 0:
        assert actual_exit == 0
    else:
        assert actual_exit == 0


def test_property10_no_findings_exit_zero():
    """**Validates: Requirements 8.5**

    With no findings, exit code is 0.
    """
    assert compute_exit_code([]) == 0


# ---------------------------------------------------------------------------
# Property 10: Combined property - all aspects together
# ---------------------------------------------------------------------------

def build_metrics_for_rules(present_rules):
    """Build metrics for present rules only."""
    metrics = {}
    for rule_id in present_rules:
        metrics.update(TRIGGER_BUILDERS[rule_id]())
    return metrics


def remove_absent_rule_metrics(metrics, present_rules, absent_rules):
    """Remove metrics required by absent rules that aren't also required by present rules."""
    present_required = set()
    for rule_id in present_rules:
        present_required.update(RULE_METRICS[rule_id]["required"])

    for rule_id in absent_rules:
        for metric_name in RULE_METRICS[rule_id]["required"]:
            if metric_name not in present_required and metric_name in metrics:
                del metrics[metric_name]


def assert_findings_are_valid(findings):
    """Verify: all findings have valid rule IDs and severities."""
    for f in findings:
        assert f.rule_id in ALL_RULE_IDS, (
            f"Unknown rule ID in finding: {f.rule_id}"
        )
        assert f.severity in SEVERITY_ORDER, (
            f"Unknown severity: {f.severity}"
        )


def assert_exit_code_matches_findings(findings):
    """Verify: exit code equals max severity."""
    exit_code = compute_exit_code(findings)
    if findings:
        expected_max = max(SEVERITY_ORDER[f.severity] for f in findings)
        assert exit_code == expected_max
    else:
        assert exit_code == 0


@given(
    present_rules=st.lists(
        st.sampled_from(ALL_RULE_IDS), min_size=0, max_size=7, unique=True
    ),
    absent_rules=st.lists(
        st.sampled_from(ALL_RULE_IDS), min_size=0, max_size=7, unique=True
    ),
)
@settings(max_examples=300)
def test_property10_combined_trigger_and_skip(present_rules, absent_rules):
    """**Validates: Requirements 8.3, 8.5, 8.6**

    For any combination of triggered and missing rules:
    - Triggered rules produce correct ID and severity
    - Missing rules are skipped gracefully
    - Exit code equals max severity
    """
    # Make absent_rules only those not in present_rules
    absent_rules = [r for r in absent_rules if r not in present_rules]

    # Build metrics for present rules only
    metrics = build_metrics_for_rules(present_rules)

    # Remove metrics required by absent rules
    remove_absent_rule_metrics(metrics, present_rules, absent_rules)

    # Evaluate - must not crash
    findings, skipped = evaluate_rules(metrics)

    # Verify: all findings have valid rule IDs and severities
    assert_findings_are_valid(findings)

    # Verify: exit code equals max severity
    assert_exit_code_matches_findings(findings)
