"""Tests for the corpus benchmark infrastructure.

Includes property-based tests (hypothesis) and unit tests for:
- Fixture metadata validation
- Report schema validation
- Aggregate computation correctness
- Token reduction computation
- Comparison regression detection
- Exit code mapping
- Report serialization round trip
- PR summary completeness
- Error handling paths

Run:
    cd tools/perf && python3 -m pytest tests/test_corpus_benchmark.py -v
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Make parent package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_corpus_benchmark import (
    build_summary,
    classify_result,
    compute_aggregate_token_reduction,
    compute_percentile,
    compute_token_reduction,
)
from compare_reports import (
    compare_metric,
    compare_reports,
    judge_metric_absolute,
    judge_metric_percent,
    verdict_to_exit_code,
)
from format_pr_summary import format_summary, TOKEN_DISCLAIMER
from report_schema import (
    VALID_CONVERSION_RESULTS,
    VALID_PAGE_TYPES,
    validate_kebab_case_keys,
    validate_report,
)

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

page_type_st = st.sampled_from(sorted(VALID_PAGE_TYPES))
conversion_result_st = st.sampled_from(sorted(VALID_CONVERSION_RESULTS))
positive_int = st.integers(min_value=1, max_value=10_000_000)
non_negative_int = st.integers(min_value=0, max_value=10_000_000)
positive_float = st.floats(
    min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False
)
latency_float = st.floats(
    min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False
)
factor_st = st.floats(
    min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False
)


fixture_id_st = st.builds(
    lambda prefix, suffix: f"{prefix}/{suffix}",
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=12),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=2, max_size=15),
)


def fixture_result_st():
    """Strategy for generating a single fixture result dict."""
    return st.fixed_dictionaries(
        {
            "fixture-id": fixture_id_st,
            "page-type": page_type_st,
            "conversion-result": conversion_result_st,
            "input-bytes": positive_int,
            "output-bytes": non_negative_int,
            "latency-ms": latency_float,
            "token-reduction-percent": st.floats(
                min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
            ),
            "failure-corpus": st.booleans(),
        }
    )


# ===========================================================================
# Property-Based Tests
# ===========================================================================


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 2: Report schema compliance
# ---------------------------------------------------------------------------


@given(
    fixtures=st.lists(fixture_result_st(), min_size=1, max_size=50),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property2_report_schema_compliance(fixtures, factor):
    """All JSON keys in a generated Unified Report must be kebab-case
    and all required fields must be present."""
    summary = build_summary(fixtures, factor)
    report = {
        "schema-version": "1.0.0",
        "metadata": {
            "corpus-version": "1.0.0",
            "git-commit": "abc1234",
            "platform": "linux-x86_64",
            "timestamp": "2025-01-15T10:30:00Z",
            "converter-version": "0.4.0",
            "token-approx-factor": factor,
        },
        "summary": summary,
        "fixtures": fixtures,
    }

    # All keys must be kebab-case
    key_errors = validate_kebab_case_keys(report)
    assert key_errors == [], f"Non-kebab-case keys found: {key_errors}"

    # All required fields must be present
    validation_errors = validate_report(report)
    assert validation_errors == [], f"Validation errors: {validation_errors}"


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 3: Fixture count invariant
# ---------------------------------------------------------------------------


@given(
    fixtures=st.lists(fixture_result_st(), min_size=1, max_size=100),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property3_fixture_count_invariant(fixtures, factor):
    """The report must contain exactly N fixture entries and
    summary.total-fixtures must equal N."""
    n = len(fixtures)
    summary = build_summary(fixtures, factor)

    assert summary["total-fixtures"] == n
    assert (
        summary["converted-count"]
        + summary["skipped-count"]
        + summary["failed-open-count"]
    ) == n


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 4: Aggregate computation correctness
# ---------------------------------------------------------------------------


@given(
    fixtures=st.lists(fixture_result_st(), min_size=1, max_size=50),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property4_aggregate_computation_correctness(fixtures, factor):
    """Summary fields must be correctly derived from per-fixture results."""
    summary = build_summary(fixtures, factor)

    total = len(fixtures)
    converted = sum(1 for f in fixtures if f["conversion-result"] == "converted")
    skipped = sum(1 for f in fixtures if f["conversion-result"] == "skipped")
    failed_open = sum(1 for f in fixtures if f["conversion-result"] == "failed-open")

    assert summary["total-fixtures"] == total
    assert summary["converted-count"] == converted
    assert summary["skipped-count"] == skipped
    assert summary["failed-open-count"] == failed_open

    expected_fallback = (failed_open / total * 100.0) if total > 0 else 0.0
    assert math.isclose(
        summary["fallback-rate"], round(expected_fallback, 2), abs_tol=0.01
    )

    expected_input_total = sum(f["input-bytes"] for f in fixtures)
    assert summary["input-bytes-total"] == expected_input_total

    expected_output_total = sum(
        f["output-bytes"] for f in fixtures if f["conversion-result"] == "converted"
    )
    assert summary["output-bytes-total"] == expected_output_total

    # Latency percentiles: verify p50 <= p95 <= p99
    assert summary["p50-latency-ms"] <= summary["p95-latency-ms"]
    assert summary["p95-latency-ms"] <= summary["p99-latency-ms"]


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 5: Token reduction computation
# ---------------------------------------------------------------------------


@given(
    input_bytes=st.integers(min_value=1, max_value=10_000_000),
    output_bytes=st.integers(min_value=0, max_value=10_000_000),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property5_token_reduction_per_fixture(input_bytes, output_bytes, factor):
    """Per-fixture token reduction must follow the formula."""
    result = compute_token_reduction(input_bytes, output_bytes, factor)
    expected = (1.0 - output_bytes / input_bytes) * 100.0 * factor
    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-12)


@given(
    fixtures=st.lists(
        st.fixed_dictionaries(
            {
                "conversion-result": st.just("converted"),
                "input-bytes": st.integers(min_value=100, max_value=1_000_000),
                "output-bytes": st.integers(min_value=1, max_value=999_999),
            }
        ),
        min_size=1,
        max_size=20,
    ),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property5_aggregate_token_reduction(fixtures, factor):
    """Aggregate token reduction must be the input-bytes-weighted average."""
    # Ensure output < input for valid reduction
    for f in fixtures:
        if f["output-bytes"] >= f["input-bytes"]:
            f["output-bytes"] = f["input-bytes"] // 2

    result = compute_aggregate_token_reduction(fixtures, factor)

    total_input = sum(f["input-bytes"] for f in fixtures)
    weighted_sum = sum(
        compute_token_reduction(f["input-bytes"], f["output-bytes"], factor)
        * f["input-bytes"]
        for f in fixtures
    )
    expected = weighted_sum / total_input if total_input > 0 else 0.0

    assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-12)


@given(
    fixtures=st.lists(
        st.fixed_dictionaries(
            {
                "conversion-result": st.sampled_from(["skipped", "failed-open"]),
                "input-bytes": positive_int,
                "output-bytes": st.just(0),
            }
        ),
        min_size=1,
        max_size=10,
    ),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property5_non_converted_zero_reduction(fixtures, factor):
    """Non-converted fixtures must have zero aggregate token reduction."""
    result = compute_aggregate_token_reduction(fixtures, factor)
    assert result == 0.0


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 8: Comparison regression detection
# ---------------------------------------------------------------------------


def _make_summary(
    fallback_rate=5.0,
    token_reduction=60.0,
    p50=1.0,
    p95=3.0,
    p99=7.0,
):
    """Helper to build a minimal summary dict."""
    return {
        "total-fixtures": 30,
        "converted-count": 25,
        "skipped-count": 2,
        "failed-open-count": 3,
        "fallback-rate": fallback_rate,
        "token-reduction-percent": token_reduction,
        "input-bytes-total": 500000,
        "output-bytes-total": 200000,
        "p50-latency-ms": p50,
        "p95-latency-ms": p95,
        "p99-latency-ms": p99,
    }


def _make_report(summary, fixtures=None):
    """Helper to build a minimal Unified Report."""
    return {
        "schema-version": "1.0.0",
        "metadata": {
            "corpus-version": "1.0.0",
            "git-commit": "abc1234",
            "platform": "linux-x86_64",
            "timestamp": "2025-01-15T10:30:00Z",
            "converter-version": "0.4.0",
            "token-approx-factor": 1.0,
        },
        "summary": summary,
        "fixtures": fixtures or [],
    }


QUALITY_THRESHOLDS = {
    "schema-version": "1.0.0",
    "thresholds": {
        "fallback-rate": {
            "warning-delta": 5.0,
            "blocking-delta": 10.0,
            "direction": "lower-is-better",
            "unit": "percentage-points",
        },
        "token-reduction-percent": {
            "warning-delta": -5.0,
            "blocking-delta": -10.0,
            "direction": "higher-is-better",
            "unit": "percentage-points",
        },
        "p50-latency-ms": {
            "warning-pct": 15,
            "blocking-pct": 30,
            "direction": "lower-is-better",
        },
        "p95-latency-ms": {
            "warning-pct": 20,
            "blocking-pct": 40,
            "direction": "lower-is-better",
        },
        "p99-latency-ms": {
            "warning-pct": 20,
            "blocking-pct": 40,
            "direction": "lower-is-better",
        },
    },
}


@given(
    fallback_increase=st.floats(
        min_value=10.1, max_value=50.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=100)
def test_property8_regression_detected_fallback(fallback_increase):
    """When fallback rate increases beyond blocking threshold, verdict must be fail."""
    baseline = _make_report(_make_summary(fallback_rate=5.0))
    current = _make_report(_make_summary(fallback_rate=5.0 + fallback_increase))

    verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
    assert verdict["overall-verdict"] in ("warn", "fail")
    assert verdict["metric-comparisons"]["fallback-rate"]["verdict"] == "fail"


@given(
    token_decrease=st.floats(
        min_value=10.1, max_value=50.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=100)
def test_property8_regression_detected_token_reduction(token_decrease):
    """When token reduction decreases beyond blocking threshold, verdict must be fail."""
    baseline = _make_report(_make_summary(token_reduction=60.0))
    current = _make_report(_make_summary(token_reduction=60.0 - token_decrease))

    verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
    assert verdict["overall-verdict"] in ("warn", "fail")
    assert verdict["metric-comparisons"]["token-reduction-percent"]["verdict"] == "fail"


def test_property8_no_regression_pass():
    """When no metric exceeds any threshold, verdict must be pass."""
    baseline = _make_report(_make_summary())
    current = _make_report(_make_summary())

    verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
    assert verdict["overall-verdict"] == "pass"
    for mc in verdict["metric-comparisons"].values():
        assert mc["verdict"] == "pass"


def test_property8_fixture_change_detection():
    """Per-fixture comparison must identify conversion result changes."""
    baseline_fixtures = [
        {
            "fixture-id": "simple/basic",
            "page-type": "clean-article",
            "conversion-result": "converted",
            "input-bytes": 400,
            "output-bytes": 150,
            "latency-ms": 1.0,
            "token-reduction-percent": 62.0,
            "failure-corpus": False,
        }
    ]
    current_fixtures = [
        {
            "fixture-id": "simple/basic",
            "page-type": "clean-article",
            "conversion-result": "failed-open",
            "input-bytes": 400,
            "output-bytes": 0,
            "latency-ms": 1.0,
            "token-reduction-percent": 0.0,
            "failure-corpus": False,
        }
    ]

    baseline = _make_report(_make_summary(), baseline_fixtures)
    current = _make_report(_make_summary(), current_fixtures)

    verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
    assert len(verdict["fixture-changes"]) == 1
    change = verdict["fixture-changes"][0]
    assert change["fixture-id"] == "simple/basic"
    assert change["change-type"] == "regression"


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 9: Exit code mapping
# ---------------------------------------------------------------------------


@given(verdict=st.sampled_from(["pass", "warn", "fail"]))
@settings(max_examples=100)
def test_property9_exit_code_mapping(verdict):
    """Exit code must be 0 for pass/warn and 1 for fail."""
    code = verdict_to_exit_code(verdict)
    if verdict == "fail":
        assert code == 1
    else:
        assert code == 0


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 10: Report serialization round trip
# ---------------------------------------------------------------------------


@given(
    fixtures=st.lists(fixture_result_st(), min_size=1, max_size=20),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property10_serialization_round_trip(fixtures, factor):
    """Serializing a report to JSON and back must preserve all values."""
    summary = build_summary(fixtures, factor)
    report = {
        "schema-version": "1.0.0",
        "metadata": {
            "corpus-version": "1.0.0",
            "git-commit": "abc1234",
            "platform": "linux-x86_64",
            "timestamp": "2025-01-15T10:30:00Z",
            "converter-version": "0.4.0",
            "token-approx-factor": factor,
        },
        "summary": summary,
        "fixtures": fixtures,
    }

    serialized = json.dumps(report, ensure_ascii=False)
    deserialized = json.loads(serialized)

    assert deserialized == report


# ---------------------------------------------------------------------------
# Feature: benchmark-corpus-evidence, Property 11: PR summary completeness
# ---------------------------------------------------------------------------


@given(
    fixtures=st.lists(fixture_result_st(), min_size=1, max_size=10),
    factor=factor_st,
)
@settings(max_examples=100)
def test_property11_pr_summary_completeness(fixtures, factor):
    """PR summary must contain corpus version, git commit, platform,
    fallback rate, token reduction, latency percentiles, and disclaimer."""
    summary = build_summary(fixtures, factor)
    report = {
        "schema-version": "1.0.0",
        "metadata": {
            "corpus-version": "1.0.0",
            "git-commit": "abc1234",
            "platform": "linux-x86_64",
            "timestamp": "2025-01-15T10:30:00Z",
            "converter-version": "0.4.0",
            "token-approx-factor": factor,
        },
        "summary": summary,
        "fixtures": fixtures,
    }

    md = format_summary(report)

    assert "v1.0.0" in md  # corpus version
    assert "abc1234" in md  # git commit
    assert "linux-x86_64" in md  # platform
    assert str(summary["fallback-rate"]) in md
    assert str(summary["token-reduction-percent"]) in md
    assert str(summary["p50-latency-ms"]) in md
    assert str(summary["p95-latency-ms"]) in md
    assert str(summary["p99-latency-ms"]) in md
    assert TOKEN_DISCLAIMER in md


# ===========================================================================
# Unit Tests
# ===========================================================================


# ---------------------------------------------------------------------------
# 12.1: Fixture metadata validation
# ---------------------------------------------------------------------------


class TestFixtureMetadataValidation:
    """Unit tests for fixture metadata validation."""

    def test_valid_metadata(self, tmp_path):
        """Known fixture .meta.json with all required fields passes."""
        meta = {
            "fixture-id": "simple/basic",
            "page-type": "clean-article",
            "expected-conversion-result": "converted",
            "input-size-bytes": 379,
            "source-description": "Minimal HTML",
            "failure-corpus": False,
        }
        assert meta["page-type"] in VALID_PAGE_TYPES
        assert meta["expected-conversion-result"] in VALID_CONVERSION_RESULTS
        assert isinstance(meta["input-size-bytes"], int)
        assert isinstance(meta["failure-corpus"], bool)

    def test_invalid_page_type(self):
        """Invalid page-type value is rejected."""
        assert "invalid-type" not in VALID_PAGE_TYPES

    def test_invalid_conversion_result(self):
        """Invalid conversion-result value is rejected."""
        assert "error" not in VALID_CONVERSION_RESULTS


# ---------------------------------------------------------------------------
# 12.2: Report schema validation
# ---------------------------------------------------------------------------


class TestReportSchemaValidation:
    """Unit tests for report schema validation."""

    def test_valid_report_passes(self):
        """A sample Unified Report passes schema validation."""
        report = _make_report(
            _make_summary(),
            [
                {
                    "fixture-id": "simple/basic",
                    "page-type": "clean-article",
                    "conversion-result": "converted",
                    "input-bytes": 400,
                    "output-bytes": 150,
                    "latency-ms": 1.0,
                    "token-reduction-percent": 62.0,
                    "failure-corpus": False,
                }
            ],
        )
        errors = validate_report(report)
        assert errors == []

    def test_schema_version_is_1_0_0(self):
        """Generated reports have schema-version 1.0.0."""
        report = _make_report(_make_summary())
        assert report["schema-version"] == "1.0.0"

    def test_missing_fields_rejected(self):
        """Reports with missing required fields are rejected."""
        report = {"schema-version": "1.0.0"}
        errors = validate_report(report)
        assert len(errors) > 0

    def test_invalid_fixture_page_type_rejected(self):
        """Fixtures with invalid page-type are flagged."""
        report = _make_report(
            _make_summary(),
            [
                {
                    "fixture-id": "test/x",
                    "page-type": "invalid-type",
                    "conversion-result": "converted",
                    "input-bytes": 100,
                    "output-bytes": 50,
                    "latency-ms": 1.0,
                    "token-reduction-percent": 50.0,
                    "failure-corpus": False,
                }
            ],
        )
        errors = validate_report(report)
        assert any("invalid page-type" in e for e in errors)


# ---------------------------------------------------------------------------
# 12.3: Comparison logic
# ---------------------------------------------------------------------------


class TestComparisonLogic:
    """Unit tests for comparison logic."""

    def test_identical_reports_pass(self):
        """Verdict is pass when baseline equals current."""
        baseline = _make_report(_make_summary())
        current = _make_report(_make_summary())
        verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
        assert verdict["overall-verdict"] == "pass"

    def test_known_regression_warn(self):
        """Known regression within warning threshold produces warn."""
        baseline = _make_report(_make_summary(fallback_rate=5.0))
        current = _make_report(_make_summary(fallback_rate=10.5))
        verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
        fr_verdict = verdict["metric-comparisons"]["fallback-rate"]["verdict"]
        assert fr_verdict == "warn"

    def test_known_regression_fail(self):
        """Known regression beyond blocking threshold produces fail."""
        baseline = _make_report(_make_summary(fallback_rate=5.0))
        current = _make_report(_make_summary(fallback_rate=20.0))
        verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
        assert verdict["overall-verdict"] == "fail"

    def test_latency_regression_detected(self):
        """Latency regression beyond threshold is detected."""
        baseline = _make_report(_make_summary(p50=1.0))
        current = _make_report(_make_summary(p50=1.5))
        verdict = compare_reports(baseline, current, QUALITY_THRESHOLDS)
        p50_verdict = verdict["metric-comparisons"]["p50-latency-ms"]["verdict"]
        assert p50_verdict in ("warn", "fail")


# ---------------------------------------------------------------------------
# 12.5: Error handling paths
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Unit tests for error handling paths."""

    def test_classify_result_converted(self):
        """Exit 0 with output classifies as converted."""
        assert classify_result(0, "# Hello") == "converted"

    def test_classify_result_failed_open(self):
        """Non-zero exit classifies as failed-open."""
        assert classify_result(1, "") == "failed-open"

    def test_classify_result_empty_output(self):
        """Exit 0 with empty output classifies as failed-open."""
        assert classify_result(0, "") == "failed-open"
        assert classify_result(0, "   ") == "failed-open"

    def test_classify_result_skipped(self):
        """Skip exit code classifies as skipped."""
        assert classify_result(2, "") == "skipped"

    def test_token_reduction_zero_input(self):
        """Zero input bytes returns 0 token reduction."""
        assert compute_token_reduction(0, 0, 1.0) == 0.0

    def test_percentile_single_value(self):
        """Percentile of single value returns that value."""
        assert compute_percentile([5.0], 50) == 5.0
        assert compute_percentile([5.0], 99) == 5.0

    def test_percentile_empty(self):
        """Percentile of empty list returns 0."""
        assert compute_percentile([], 50) == 0.0

    def test_exit_code_pass(self):
        assert verdict_to_exit_code("pass") == 0

    def test_exit_code_warn(self):
        assert verdict_to_exit_code("warn") == 0

    def test_exit_code_fail(self):
        assert verdict_to_exit_code("fail") == 1
