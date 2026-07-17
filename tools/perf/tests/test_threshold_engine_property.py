"""Property-based tests for module-level threshold engine verdicts.

**Validates: Requirements 9.1**

Property 12: Threshold Engine Verdict Correctness
    For any set of current vs baseline metrics, verify:
    - GO verdict iff all metrics are within thresholds
    - NO_GO verdict when any metric exceeds threshold
    - Breach list correctly identifies which metrics failed

Run:
    python3 -m pytest tools/perf/tests/test_threshold_engine_property.py -q
"""

import sys
from pathlib import Path

from hypothesis import given, assume, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Make the parent package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from threshold_engine import (
    evaluate_module_level,
    DEFAULT_MODULE_LEVEL_THRESHOLDS,
    _MODULE_LEVEL_DIRECTION,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Metric value strategy: positive floats representing latencies, rates, etc.
metric_value = st.floats(
    min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False
)

# Rate value strategy: values between 0 and 1 for fallback_rate_abs
rate_value = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)

# Threshold percentage strategy: positive percentages
threshold_pct = st.floats(
    min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False
)

# Absolute cap strategy: small positive cap values
absolute_cap = st.floats(
    min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
)

# Module-level metric names that use pct deviation
PCT_METRICS = [
    k for k, v in _MODULE_LEVEL_DIRECTION.items() if v == "lower_is_better"
]

# Module-level metric names that use absolute cap
ABS_METRICS = [
    k for k, v in _MODULE_LEVEL_DIRECTION.items() if v == "absolute_cap"
]

ALL_METRICS = list(DEFAULT_MODULE_LEVEL_THRESHOLDS.keys())


def _build_within_threshold_metrics(baseline_values, thresholds):
    """Build current metrics that are guaranteed within all thresholds.

    For pct-deviation metrics: current = baseline * (1 + threshold * safe_factor / 100)
        where safe_factor < 1.0 ensures we stay below threshold.
    For absolute cap metrics: current = threshold * safe_factor.
    """
    current = {}
    for metric_name in ALL_METRICS:
        direction = _MODULE_LEVEL_DIRECTION.get(metric_name, "lower_is_better")
        threshold_val = thresholds.get(metric_name, DEFAULT_MODULE_LEVEL_THRESHOLDS[metric_name])

        if direction == "absolute_cap":
            # Stay below the absolute cap
            current[metric_name] = threshold_val * 0.5
        else:
            base_val = baseline_values.get(metric_name, 100.0)
            # Apply deviation that is safely below the threshold
            safe_deviation = threshold_val * 0.5  # half the threshold
            current[metric_name] = base_val * (1 + safe_deviation / 100.0)
    return current


def _build_breach_metrics(baseline_values, thresholds, breach_metric):
    """Build current metrics where exactly one metric breaches its threshold.

    All other metrics are kept safely within thresholds.
    """
    current = _build_within_threshold_metrics(baseline_values, thresholds)

    direction = _MODULE_LEVEL_DIRECTION.get(breach_metric, "lower_is_better")
    threshold_val = thresholds.get(breach_metric, DEFAULT_MODULE_LEVEL_THRESHOLDS[breach_metric])

    if direction == "absolute_cap":
        # Exceed the absolute cap
        current[breach_metric] = threshold_val * 2.0
    else:
        base_val = baseline_values.get(breach_metric, 100.0)
        # Apply deviation that exceeds the threshold
        breach_deviation = threshold_val * 1.5  # 1.5x the threshold
        current[breach_metric] = base_val * (1 + breach_deviation / 100.0)

    return current


# ---------------------------------------------------------------------------
# Property 12: GO verdict when all metrics within thresholds
# ---------------------------------------------------------------------------

@given(
    base_val=metric_value,
)
@settings(max_examples=300)
def test_property12_go_verdict_all_within_thresholds(base_val):
    """**Validates: Requirements 9.1**

    When all current metrics are within their defined thresholds relative to
    baseline, evaluate_module_level MUST return verdict "GO" with empty breaches.
    """
    # Use default thresholds config (no module_level section -> defaults)
    thresholds_cfg = {}

    # Build baseline with uniform values
    baseline = {metric: base_val for metric in ALL_METRICS}

    # Build current values safely within all thresholds
    current = _build_within_threshold_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    assert result["verdict"] == "GO", (
        f"Expected GO but got {result['verdict']}. "
        f"Breaches: {result['breaches']}"
    )
    assert result["breaches"] == [], (
        f"Expected no breaches but got: {result['breaches']}"
    )


@given(
    base_values=st.fixed_dictionaries({
        m: metric_value for m in PCT_METRICS
    }),
    fallback_base=rate_value,
)
@settings(max_examples=300)
def test_property12_go_verdict_varied_baselines(base_values, fallback_base):
    """**Validates: Requirements 9.1**

    GO verdict holds even when baseline values vary independently across metrics.
    """
    thresholds_cfg = {}

    baseline = dict(base_values)
    # fallback_rate_abs uses absolute cap, baseline value is irrelevant for verdict
    # but we still set it for completeness
    baseline["fallback_rate_abs"] = fallback_base

    current = _build_within_threshold_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    assert result["verdict"] == "GO", (
        f"Expected GO but got {result['verdict']}. "
        f"Breaches: {result['breaches']}"
    )
    assert result["breaches"] == []


# ---------------------------------------------------------------------------
# Property 12: NO_GO verdict when any metric exceeds threshold
# ---------------------------------------------------------------------------

@given(
    base_val=metric_value,
    breach_metric=st.sampled_from(ALL_METRICS),
)
@settings(max_examples=500)
def test_property12_no_go_verdict_single_breach(base_val, breach_metric):
    """**Validates: Requirements 9.1**

    When any single metric exceeds its threshold, evaluate_module_level MUST
    return verdict "NO_GO" with at least one entry in breaches.
    """
    thresholds_cfg = {}
    baseline = {metric: base_val for metric in ALL_METRICS}

    current = _build_breach_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS, breach_metric)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    assert result["verdict"] == "NO_GO", (
        f"Expected NO_GO for breach in {breach_metric} but got {result['verdict']}. "
        f"Results: {result['results']}"
    )
    assert len(result["breaches"]) >= 1, (
        f"Expected at least one breach but got: {result['breaches']}"
    )


@given(
    base_val=metric_value,
    breach_metrics=st.lists(
        st.sampled_from(ALL_METRICS),
        min_size=2,
        max_size=len(ALL_METRICS),
        unique=True,
    ),
)
@settings(max_examples=300)
def test_property12_no_go_verdict_multiple_breaches(base_val, breach_metrics):
    """**Validates: Requirements 9.1**

    When multiple metrics exceed thresholds, verdict is NO_GO and all
    breaching metrics appear in the breaches list.
    """
    thresholds_cfg = {}
    baseline = {metric: base_val for metric in ALL_METRICS}

    # Start with all-within and then breach selected metrics
    current = _build_within_threshold_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS)
    for bm in breach_metrics:
        direction = _MODULE_LEVEL_DIRECTION.get(bm, "lower_is_better")
        threshold_val = DEFAULT_MODULE_LEVEL_THRESHOLDS[bm]
        if direction == "absolute_cap":
            current[bm] = threshold_val * 2.0
        else:
            current[bm] = base_val * (1 + threshold_val * 1.5 / 100.0)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    assert result["verdict"] == "NO_GO", (
        f"Expected NO_GO for breaches in {breach_metrics} but got {result['verdict']}"
    )

    breach_names = [b["metric"] for b in result["breaches"]]
    for bm in breach_metrics:
        assert bm in breach_names, (
            f"Expected {bm} in breaches but got: {breach_names}"
        )


# ---------------------------------------------------------------------------
# Property 12: Breach identification correctness
# ---------------------------------------------------------------------------

@given(
    base_val=metric_value,
    breach_metric=st.sampled_from(ALL_METRICS),
)
@settings(max_examples=300)
def test_property12_breach_identifies_correct_metric(base_val, breach_metric):
    """**Validates: Requirements 9.1**

    The breaches list must identify exactly which metric(s) exceeded their
    threshold, not others.
    """
    thresholds_cfg = {}
    baseline = {metric: base_val for metric in ALL_METRICS}

    current = _build_breach_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS, breach_metric)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    breach_names = [b["metric"] for b in result["breaches"]]

    # The intentionally breached metric must appear
    assert breach_metric in breach_names, (
        f"Breached metric {breach_metric} not in breaches: {breach_names}"
    )

    # Each breach entry must have the expected structure
    for breach in result["breaches"]:
        assert "metric" in breach
        assert "threshold" in breach
        assert "actual" in breach
        assert breach["status"] == "breach"


@given(
    base_val=metric_value,
    breach_metric=st.sampled_from(ALL_METRICS),
)
@settings(max_examples=300)
def test_property12_breach_actual_exceeds_threshold(base_val, breach_metric):
    """**Validates: Requirements 9.1**

    For every entry in the breaches list, the actual deviation or value must
    exceed the configured threshold.
    """
    thresholds_cfg = {}
    baseline = {metric: base_val for metric in ALL_METRICS}

    current = _build_breach_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS, breach_metric)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    for breach in result["breaches"]:
        assert breach["actual"] > breach["threshold"], (
            f"Breach {breach['metric']}: actual={breach['actual']} should "
            f"exceed threshold={breach['threshold']}"
        )


# ---------------------------------------------------------------------------
# Property 12: Custom thresholds_cfg respected
# ---------------------------------------------------------------------------

@given(
    base_val=metric_value,
    custom_threshold=st.floats(
        min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=200)
def test_property12_custom_thresholds_respected(base_val, custom_threshold):
    """**Validates: Requirements 9.1**

    When a custom thresholds_cfg provides module_level entries, those values
    are used instead of defaults. A metric within the custom threshold must
    produce GO.
    """
    # Use a custom threshold for p50_latency_small_pct
    custom_cfg = {
        "module_level": {
            "p50_latency_small_pct": custom_threshold,
            "p95_latency_small_pct": 15,
            "p50_latency_large_pct": 5,
            "ttfb_streaming_large_pct": 10,
            "fallback_rate_abs": 0.05,
            "memory_slope_pct": 20,
        }
    }

    baseline = {metric: base_val for metric in ALL_METRICS}
    current = _build_within_threshold_metrics(baseline, custom_cfg["module_level"])

    result = evaluate_module_level(current, baseline, custom_cfg)

    assert result["verdict"] == "GO", (
        f"Expected GO with custom thresholds but got {result['verdict']}. "
        f"Breaches: {result['breaches']}"
    )


# ---------------------------------------------------------------------------
# Property 12: Results list completeness
# ---------------------------------------------------------------------------

@given(
    base_val=metric_value,
)
@settings(max_examples=200)
def test_property12_results_list_covers_all_metrics(base_val):
    """**Validates: Requirements 9.1**

    The results list must contain an entry for every configured metric,
    regardless of verdict.
    """
    thresholds_cfg = {}
    baseline = {metric: base_val for metric in ALL_METRICS}
    current = _build_within_threshold_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    result_metrics = {r["metric"] for r in result["results"]}
    expected_metrics = set(ALL_METRICS)

    assert result_metrics == expected_metrics, (
        f"Results missing metrics. Expected: {expected_metrics}, "
        f"Got: {result_metrics}"
    )


@given(
    base_val=metric_value,
)
@settings(max_examples=200)
def test_property12_go_implies_all_pass_in_results(base_val):
    """**Validates: Requirements 9.1**

    When verdict is GO, every entry in results must have status "pass".
    """
    thresholds_cfg = {}
    baseline = {metric: base_val for metric in ALL_METRICS}
    current = _build_within_threshold_metrics(baseline, DEFAULT_MODULE_LEVEL_THRESHOLDS)

    result = evaluate_module_level(current, baseline, thresholds_cfg)

    assert result["verdict"] == "GO"
    for entry in result["results"]:
        assert entry["status"] == "pass", (
            f"GO verdict but metric {entry['metric']} has status={entry['status']}"
        )
