"""Property-based tests for the threshold engine.

Properties tested:
  - Property 0:  Zero-baseline deviation handling
  - Property 1:  Threshold classification correctness
  - Property 10: Relative deviation calculation correctness
  - Property 11: Shared nightly aggregation median helper correctness

Run:
    cd tools/perf && python3 -m pytest tests/test_threshold_engine_props.py -v
"""

import math
import sys
from pathlib import Path

from hypothesis import given, assume, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Make the parent package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report_utils import median_value
from threshold_engine import compute_deviation, judge_metric


# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Positive floats suitable for metric values (avoid extremes that cause
# floating-point issues).
positive_float = st.floats(min_value=1e-6, max_value=1e12, allow_nan=False, allow_infinity=False)
non_negative_float = st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)

# Threshold percentages – warning is always closer to zero than blocking.
# For lower_is_better: 0 < warning < blocking
# For higher_is_better: blocking < warning < 0


# ---------------------------------------------------------------------------
# Property 0: Zero-baseline deviation handling
# ---------------------------------------------------------------------------

@given(current=non_negative_float)
@settings(max_examples=200)
def test_property0_zero_baseline_special_case(current):
    """Zero baselines stay explicit so comparisons do not silently disappear."""
    deviation = compute_deviation(current, 0.0)
    if current == 0.0:
        assert deviation == 0.0
    else:
        assert deviation == 100.0


# ---------------------------------------------------------------------------
# Property 1: Threshold classification correctness
# ---------------------------------------------------------------------------

@given(
    baseline=positive_float,
    current=positive_float,
    warning_pct=st.floats(min_value=1.0, max_value=99.0, allow_nan=False, allow_infinity=False),
    blocking_pct_extra=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=500)
def test_property1_lower_is_better_classification(baseline, current, warning_pct, blocking_pct_extra):
    """For lower_is_better metrics, positive deviation = regression.

    PASS when deviation <= warning, WARN when warning < deviation <= blocking,
    FAIL when deviation > blocking.
    """
    blocking_pct = warning_pct + blocking_pct_extra  # guarantee blocking > warning
    deviation = compute_deviation(current, baseline)
    verdict = judge_metric(deviation, "lower_is_better", warning_pct, blocking_pct)

    if deviation > blocking_pct:
        assert verdict == "fail", f"Expected fail: dev={deviation}, block={blocking_pct}"
    elif deviation > warning_pct:
        assert verdict == "warn", f"Expected warn: dev={deviation}, warn={warning_pct}"
    else:
        assert verdict == "pass", f"Expected pass: dev={deviation}, warn={warning_pct}"


@given(
    baseline=positive_float,
    current=positive_float,
    warning_abs=st.floats(min_value=1.0, max_value=99.0, allow_nan=False, allow_infinity=False),
    blocking_extra=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=500)
def test_property1_higher_is_better_classification(baseline, current, warning_abs, blocking_extra):
    """For higher_is_better metrics, negative deviation = regression.

    Thresholds are negative: blocking < warning < 0.
    """
    warning_pct = -warning_abs
    blocking_pct = -(warning_abs + blocking_extra)  # more negative = stricter
    deviation = compute_deviation(current, baseline)
    verdict = judge_metric(deviation, "higher_is_better", warning_pct, blocking_pct)

    if deviation < blocking_pct:
        assert verdict == "fail", f"Expected fail: dev={deviation}, block={blocking_pct}"
    elif deviation < warning_pct:
        assert verdict == "warn", f"Expected warn: dev={deviation}, warn={warning_pct}"
    else:
        assert verdict == "pass", f"Expected pass: dev={deviation}, warn={warning_pct}"


@given(
    baseline=positive_float,
    current=positive_float,
)
@settings(max_examples=200)
def test_property1_informational_always_pass(baseline, current):
    """Informational metrics always return pass regardless of deviation."""
    deviation = compute_deviation(current, baseline)
    assert judge_metric(deviation, "informational", 15, 30) == "pass"


# ---------------------------------------------------------------------------
# Property 1 (continued): overall exit-code correctness
# ---------------------------------------------------------------------------

@given(
    baseline=positive_float,
    current=positive_float,
    warning_pct=st.floats(min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    blocking_pct_extra=st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=300)
def test_property1_fail_implies_nonzero_exit(baseline, current, warning_pct, blocking_pct_extra):
    """When at least one metric is FAIL, the overall verdict must be fail."""
    blocking_pct = warning_pct + blocking_pct_extra
    deviation = compute_deviation(current, baseline)
    verdict = judge_metric(deviation, "lower_is_better", warning_pct, blocking_pct)

    if verdict == "fail":
        # In the engine, any single fail → overall fail → exit code 1
        assert verdict == "fail"


# ---------------------------------------------------------------------------
# Property 10: Relative deviation calculation correctness
# ---------------------------------------------------------------------------

@given(
    baseline=st.floats(min_value=1e-6, max_value=1e12, allow_nan=False, allow_infinity=False),
    current=st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=500)
def test_property10_deviation_formula(baseline, current):
    """compute_deviation must equal (current - baseline) / baseline * 100 within 0.01% precision."""
    result = compute_deviation(current, baseline)
    expected = (current - baseline) / baseline * 100.0

    if expected == 0.0:
        assert result == 0.0
    else:
        relative_error = abs(result - expected) / abs(expected)
        assert relative_error < 1e-4, (
            f"Precision exceeded: result={result}, expected={expected}, "
            f"relative_error={relative_error}"
        )


@given(baseline=positive_float)
@settings(max_examples=200)
def test_property10_zero_deviation_when_equal(baseline):
    """When current == baseline, deviation must be exactly 0."""
    assert compute_deviation(baseline, baseline) == 0.0


@given(
    baseline=st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False),
    current=st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=300)
def test_property10_sign_correctness(baseline, current):
    """Positive deviation when current > baseline, negative when current < baseline."""
    assume(current != baseline)
    deviation = compute_deviation(current, baseline)
    if current > baseline:
        assert deviation > 0, f"Expected positive deviation: c={current}, b={baseline}"
    else:
        assert deviation < 0, f"Expected negative deviation: c={current}, b={baseline}"


# ---------------------------------------------------------------------------
# Property 11: Shared nightly aggregation median helper correctness
# ---------------------------------------------------------------------------


@given(
    values=st.lists(
        st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
        min_size=3,
        max_size=50,
    ),
)
@settings(max_examples=500)
def test_property11_median_matches_sorted_middle(values):
    """Median of n>=3 values must equal the sorted middle element(s)."""
    result = median_value(values)
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        expected = sorted_vals[n // 2]
    else:
        expected = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-15), (
        f"Median mismatch: result={result}, expected={expected}"
    )


@given(
    values=st.lists(
        st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
        min_size=3,
        max_size=50,
    ),
)
@settings(max_examples=300)
def test_property11_median_within_range(values):
    """Median must be between min and max of the input values."""
    result = median_value(values)
    assert min(values) <= result <= max(values), (
        f"Median {result} outside range [{min(values)}, {max(values)}]"
    )


@given(
    value=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    n=st.integers(min_value=3, max_value=20),
)
@settings(max_examples=200)
def test_property11_median_of_identical_values(value, n):
    """Median of identical values must equal that value."""
    values = [value] * n
    assert median_value(values) == value
