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
    if math.isclose(current, 0.0, rel_tol=0.0, abs_tol=0.0):
        assert math.isclose(deviation, 0.0, abs_tol=1e-12)
    else:
        assert math.isclose(deviation, 100.0, rel_tol=1e-9, abs_tol=1e-12)


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
    """
    Property test that verifies classification logic for "lower_is_better" metrics.
    
    Asserts that the computed deviation is classified as:
    - pass when deviation <= warning,
    - warn when warning < deviation <= blocking,
    - fail when deviation > blocking.
    The blocking threshold is computed as `warning_pct + blocking_pct_extra` to ensure blocking > warning.
    
    Parameters:
        baseline (float): Baseline value for deviation calculation.
        current (float): Current value to compare against the baseline.
        warning_pct (float): Warning threshold percentage.
        blocking_pct_extra (float): Extra percentage added to `warning_pct` to form the blocking threshold.
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
    """
    Classifies a deviation for metrics where higher values are better using negative thresholds and asserts the expected verdict.
    
    Constructs negative thresholds from `warning_abs` and `blocking_extra` (warning_pct = -warning_abs; blocking_pct = -(warning_abs + blocking_extra)) and checks that:
    - deviation < blocking_pct -> verdict is "fail"
    - blocking_pct <= deviation < warning_pct -> verdict is "warn"
    - deviation >= warning_pct -> verdict is "pass"
    
    Parameters:
        baseline (float): Reference baseline value for deviation calculation.
        current (float): Current measured value to compare against the baseline.
        warning_abs (float): Positive magnitude used to form the negative warning threshold.
        blocking_extra (float): Positive extra magnitude added to `warning_abs` to make the blocking threshold more negative.
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

    if math.isclose(expected, 0.0, abs_tol=1e-12):
        assert math.isclose(result, 0.0, abs_tol=1e-12)
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
    assert math.isclose(compute_deviation(baseline, baseline), 0.0, abs_tol=1e-12)


@given(
    baseline=st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False),
    current=st.floats(min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=300)
def test_property10_sign_correctness(baseline, current):
    """Positive deviation when current > baseline, negative when current < baseline."""
    assume(not math.isclose(current, baseline, rel_tol=0.0, abs_tol=0.0))
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
    """
    Assert that the median of a list containing only identical elements equals that element.
    
    Parameters:
        value (float): The value to repeat in the list.
        n (int): The number of times `value` is repeated (list length; expected to be a positive integer).
    """
    values = [value] * n
    assert math.isclose(median_value(values), value, rel_tol=1e-9, abs_tol=1e-12)
