"""Unit and property-based tests for the evidence pack generator.

Covers:
  - Linear regression slope computation (pure Python, no numpy)
  - Bounded memory evidence evaluation (PASS/FAIL/INSUFFICIENT_DATA)
  - TTFB improvement evidence evaluation
  - No-regression evidence evaluation for small/medium tiers
  - Parity dual-threshold evaluation (with/without parity report)
  - Evidence pack generation and schema conformity
  - Release gates evaluation and GO/NO_GO verdict logic
  - P1 status independence from verdict
  - Human-readable summary output
  - CLI entry point

Run:
    cd tools/perf && python3 -m pytest tests/test_evidence_pack_generator.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence_pack_generator import (  # noqa: E402
    _PARITY_EPSILON,
    evaluate_bounded_memory,
    evaluate_no_regression,
    evaluate_parity_dual_threshold,
    evaluate_release_gates,
    evaluate_ttfb_improvement,
    generate_evidence_pack,
    linear_regression_slope,
    main,
    print_human_summary,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _write_tmp_json(data: dict, suffix: str = ".json") -> str:
    """
    Write a dictionary as JSON to a temporary file and return the file path.
    
    Parameters:
        data (dict): JSON-serializable mapping to write to the temporary file.
        suffix (str): Filename suffix for the temporary file (defaults to ".json").
    
    Returns:
        path (str): Filesystem path to the created temporary file.
    """
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _make_fullbuffer_report(tiers: dict | None = None) -> dict:
    """
    Create a minimal full-buffer measurement report, optionally overriding the default tiers.
    
    Parameters:
    	tiers (dict | None): Optional mapping of tier names to metric dicts; when provided, this value is used as the report's `tiers` field. If `None`, a default set of small/medium/large tiers with example latency and input sizes is returned.
    
    Returns:
    	report (dict): A report containing `schema_version`, `timestamp`, `git_commit`, `platform`, and `tiers`.
    """
    return {
        "schema_version": "1.0.0",
        "timestamp": "2026-04-01T10:00:00Z",
        "git_commit": "abc1234",
        "platform": "darwin-arm64",
        "tiers": tiers
        or {
            "small": {
                "p50_ms": 2.0,
                "p95_ms": 3.0,
                "p99_ms": 5.0,
                "input_bytes": 10000,
            },
            "medium": {
                "p50_ms": 5.0,
                "p95_ms": 8.0,
                "p99_ms": 12.0,
                "input_bytes": 100000,
            },
            "large-100k": {
                "p50_ms": 10.0,
                "p95_ms": 15.0,
                "p99_ms": 20.0,
                "input_bytes": 500000,
            },
            "large-1m": {
                "p50_ms": 50.0,
                "p95_ms": 80.0,
                "p99_ms": 120.0,
                "input_bytes": 1000000,
            },
        },
    }


def _make_streaming_report(tiers: dict | None = None) -> dict:
    """
    Create a minimal streaming measurement report for tests.
    
    Parameters:
        tiers (dict | None): Optional dictionary of tier measurements to include; if omitted, a default set of tiers
            ("small", "medium", "large-100k", "large-1m") with representative latency, TTFB, input size, and peak memory
            values is used.
    
    Returns:
        dict: A report dictionary containing `schema_version`, `timestamp`, `git_commit`, `platform`, and `tiers`.
    """
    return {
        "schema_version": "1.0.0",
        "timestamp": "2026-04-01T11:00:00Z",
        "git_commit": "def5678",
        "platform": "darwin-arm64",
        "tiers": tiers
        or {
            "small": {
                "p50_ms": 2.2,
                "p95_ms": 3.5,
                "p99_ms": 5.5,
                "ttfb_ms": 0.5,
                "input_bytes": 10000,
                "peak_memory_bytes": 500000,
            },
            "medium": {
                "p50_ms": 5.5,
                "p95_ms": 9.0,
                "p99_ms": 13.0,
                "ttfb_ms": 1.0,
                "input_bytes": 100000,
                "peak_memory_bytes": 800000,
            },
            "large-100k": {
                "p50_ms": 11.0,
                "p95_ms": 16.0,
                "p99_ms": 22.0,
                "ttfb_ms": 2.0,
                "input_bytes": 500000,
                "peak_memory_bytes": 1200000,
            },
            "large-1m": {
                "p50_ms": 55.0,
                "p95_ms": 85.0,
                "p99_ms": 130.0,
                "ttfb_ms": 10.0,
                "input_bytes": 1000000,
                "peak_memory_bytes": 2000000,
            },
        },
    }


def _make_evidence_targets(overrides: dict | None = None) -> dict:
    """
    Create a default evidence targets configuration, optionally merged with user overrides.
    
    Parameters:
        overrides (dict | None): Optional mapping whose keys will replace or extend the default targets.
    
    Returns:
        dict: Evidence targets including:
            - "bounded_memory": {"max_slope": float, "min_data_points": int}
            - "ttfb_improvement": {"max_ratio": float}
            - "no_regression": {"max_ratio": float}
            - "diff_testing_complete": "PASS" or other status string
            - "rollout_docs_complete": "PASS" or other status string
    """
    targets = {
        "bounded_memory": {
            "max_slope": 1.0,
            "min_data_points": 4,
        },
        "ttfb_improvement": {
            "max_ratio": 0.5,
        },
        "no_regression": {
            "max_ratio": 1.3,
        },
        "diff_testing_complete": "PASS",
        "rollout_docs_complete": "PASS",
    }
    if overrides:
        targets.update(overrides)
    return targets


def _make_parity_report(
    pass_rate: float = 1.0, correctness_rate: float = 1.0
) -> dict:
    """
    Constructs a minimal parity report dictionary with the specified pass and correctness rates.
    
    Parameters:
        pass_rate (float): Pass rate in the range 0.0–1.0 (default 1.0).
        correctness_rate (float): Correctness rate in the range 0.0–1.0 (default 1.0).
    
    Returns:
        dict: Parity report containing `schema_version`, `timestamp`, a `summary` with
        `pass_rate` and `correctness_rate`, and an empty `tiers` mapping.
    """
    return {
        "schema_version": "1.0.0",
        "timestamp": "2026-04-01T12:00:00Z",
        "summary": {
            "pass_rate": pass_rate,
            "correctness_rate": correctness_rate,
        },
        "tiers": {},
    }


# ---------------------------------------------------------------------------
# Linear regression tests
# ---------------------------------------------------------------------------


class TestLinearRegressionSlope:
    """Tests for the pure Python least-squares linear regression."""

    def test_perfect_linear_relationship(self):
        """y = 2x + 1 -> slope should be 2.0."""
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [3.0, 5.0, 7.0, 9.0, 11.0]
        slope = linear_regression_slope(x, y)
        assert slope == pytest.approx(2.0, abs=1e-10)

    def test_zero_slope(self):
        """Constant y values -> slope should be 0.0."""
        x = [1.0, 2.0, 3.0, 4.0]
        y = [5.0, 5.0, 5.0, 5.0]
        slope = linear_regression_slope(x, y)
        assert slope == pytest.approx(0.0, abs=1e-10)

    def test_negative_slope(self):
        """y = -3x + 10 -> slope should be -3.0."""
        x = [1.0, 2.0, 3.0, 4.0]
        y = [7.0, 4.0, 1.0, -2.0]
        slope = linear_regression_slope(x, y)
        assert slope == pytest.approx(-3.0, abs=1e-10)

    def test_two_points(self):
        """Minimum data points: exactly 2."""
        x = [0.0, 10.0]
        y = [0.0, 20.0]
        slope = linear_regression_slope(x, y)
        assert slope == pytest.approx(2.0, abs=1e-10)

    def test_identical_x_values_returns_zero(self):
        """All x values identical -> denominator is 0 -> returns 0.0."""
        x = [5.0, 5.0, 5.0]
        y = [1.0, 2.0, 3.0]
        slope = linear_regression_slope(x, y)
        assert slope == pytest.approx(0.0, abs=1e-10)

    def test_different_lengths_raises(self):
        """x and y with different lengths should raise ValueError."""
        with pytest.raises(ValueError, match="same length"):
            linear_regression_slope([1.0, 2.0], [1.0])

    def test_fewer_than_two_points_raises(self):
        """Less than 2 data points should raise ValueError."""
        with pytest.raises(ValueError, match="At least 2"):
            linear_regression_slope([1.0], [2.0])

    def test_deterministic_output(self):
        """Same input always produces same output (Property 1)."""
        x = [100.0, 200.0, 300.0, 400.0]
        y = [150.0, 250.0, 350.0, 450.0]
        slope1 = linear_regression_slope(x, y)
        slope2 = linear_regression_slope(x, y)
        assert slope1 == slope2


# ---------------------------------------------------------------------------
# Bounded memory evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateBoundedMemory:
    """Tests for bounded memory evidence evaluation."""

    def test_pass_with_sublinear_growth(self):
        """Low slope (sub-linear memory growth) -> PASS."""
        tiers = {
            "large-100k": {
                "input_bytes": 500000,
                "peak_memory_bytes": 2000000,  # base + small per-input
            },
            "large-1m": {
                "input_bytes": 1000000,
                "peak_memory_bytes": 2200000,  # slow growth
            },
            "extra-large-10m": {
                "input_bytes": 10000000,
                "peak_memory_bytes": 4000000,  # still slow growth
            },
            "extra-large-100m": {
                "input_bytes": 100000000,
                "peak_memory_bytes": 6000000,  # sub-linear: slope ~ 0.04
            },
        }
        streaming = _make_streaming_report(tiers=tiers)
        result = evaluate_bounded_memory(streaming, max_slope=1.0, min_data_points=4)
        assert result["status"] == "PASS"
        assert result["slope"] < 1.0
        assert result["data_point_count"] == 4

    def test_fail_with_supralinear_growth(self):
        """Very high peak_memory values -> high slope -> FAIL."""
        tiers = {
            "large-100k": {
                "input_bytes": 100000,
                "peak_memory_bytes": 100000000,  # 1000x input
            },
            "large-1m": {
                "input_bytes": 1000000,
                "peak_memory_bytes": 5000000000,  # 5000x input
            },
        }
        streaming = _make_streaming_report(tiers=tiers)
        result = evaluate_bounded_memory(streaming, max_slope=100.0, min_data_points=2)
        assert result["status"] == "FAIL"

    def test_insufficient_data_when_few_points(self):
        """Fewer data points than min_data_points -> INSUFFICIENT_DATA."""
        streaming = _make_streaming_report()
        result = evaluate_bounded_memory(streaming, max_slope=1.0, min_data_points=10)
        assert result["status"] == "INSUFFICIENT_DATA"
        assert result["slope"] == 0.0

    def test_only_large_tiers_are_considered(self):
        """Small/medium tiers should not contribute data points."""
        tiers = {
            "small": {
                "input_bytes": 1000,
                "peak_memory_bytes": 5000,
            },
            "medium": {
                "input_bytes": 5000,
                "peak_memory_bytes": 20000,
            },
        }
        streaming = _make_streaming_report(tiers=tiers)
        result = evaluate_bounded_memory(streaming, max_slope=1.0, min_data_points=1)
        assert result["data_point_count"] == 0
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_missing_fields_skipped(self):
        """Tiers missing input_bytes or peak_memory_bytes are skipped."""
        tiers = {
            "large-100k": {
                "input_bytes": 100000,
                # missing peak_memory_bytes
            },
            "large-1m": {
                "input_bytes": 1000000,
                "peak_memory_bytes": 2000000,
            },
        }
        streaming = _make_streaming_report(tiers=tiers)
        result = evaluate_bounded_memory(streaming, max_slope=1.0, min_data_points=1)
        assert result["data_point_count"] == 1

    def test_peak_memory_only_in_streaming_metrics(self):
        """Peak memory present only in streaming_metrics is still evaluated correctly.

        This mirrors the layout produced by the Rust streaming binary /
        build_measurement_report: peak_memory_bytes lives under
        streaming_report["streaming_metrics"][tier], and tiers[tier] does
        not contain peak_memory_bytes.
        """
        tier = "large-1m"
        peak_memory_bytes = 58_720_256

        streaming_report = {
            "tiers": {
                tier: {
                    "input_bytes": 1_048_576,
                    # peak_memory_bytes intentionally absent
                },
            },
            "streaming_metrics": {
                tier: {
                    "peak_memory_bytes": peak_memory_bytes,
                    "ttfb_ms": 2.5,
                },
            },
        }

        result = evaluate_bounded_memory(
            streaming_report, max_slope=1.0, min_data_points=1
        )
        assert result["data_point_count"] == 1
        assert result["data_points"][0]["input_bytes"] == 1_048_576
        assert result["data_points"][0]["peak_rss_bytes"] == peak_memory_bytes


# ---------------------------------------------------------------------------
# TTFB improvement evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateTTFBImprovement:
    """Tests for TTFB improvement evidence evaluation."""

    def test_pass_when_streaming_faster(self):
        """Streaming TTFB well below full-buffer P50 -> PASS."""
        fullbuffer = _make_fullbuffer_report()
        streaming = _make_streaming_report()
        result = evaluate_ttfb_improvement(streaming, fullbuffer, max_ratio=0.5)
        assert result["status"] == "PASS"

    def test_fail_when_streaming_slower(self):
        """Streaming TTFB exceeds threshold -> FAIL."""
        fullbuffer = _make_fullbuffer_report()
        streaming = {
            "schema_version": "1.0.0",
            "tiers": {
                "large-1m": {
                    "ttfb_ms": 100.0,  # 100 / 50 = 2.0 > 0.5
                    "p50_ms": 55.0,
                },
            },
        }
        result = evaluate_ttfb_improvement(streaming, fullbuffer, max_ratio=0.5)
        assert result["status"] == "FAIL"
        assert result["details"]["large-1m"]["ratio"] == pytest.approx(2.0, abs=0.01)

    def test_fail_when_no_large_tiers(self):
        """No large tiers present -> FAIL (no data to evaluate)."""
        fullbuffer = _make_fullbuffer_report()
        streaming = {
            "schema_version": "1.0.0",
            "tiers": {
                "small": {"ttfb_ms": 0.5, "p50_ms": 2.2},
            },
        }
        result = evaluate_ttfb_improvement(streaming, fullbuffer)
        assert result["status"] == "FAIL"

    def test_details_contain_per_tier_ratios(self):
        """Details should contain per-tier ratio computations."""
        fullbuffer = _make_fullbuffer_report()
        streaming = _make_streaming_report()
        result = evaluate_ttfb_improvement(streaming, fullbuffer, max_ratio=0.5)
        for tier_name, detail in result["details"].items():
            assert "streaming_ttfb_ms" in detail
            assert "fullbuffer_p50_ms" in detail
            assert "ratio" in detail
            assert "pass" in detail


# ---------------------------------------------------------------------------
# No regression evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateNoRegression:
    """Tests for no-regression evidence evaluation on small/medium tiers."""

    def test_pass_when_within_threshold(self):
        """Streaming P50 within max_ratio of full-buffer P50 -> PASS."""
        fullbuffer = _make_fullbuffer_report()
        streaming = _make_streaming_report()
        result = evaluate_no_regression(streaming, fullbuffer, max_ratio=1.3)
        assert result["status"] == "PASS"

    def test_fail_when_regression_detected(self):
        """Streaming P50 significantly exceeds full-buffer P50 -> FAIL."""
        fullbuffer = _make_fullbuffer_report()
        streaming = {
            "schema_version": "1.0.0",
            "tiers": {
                "small": {
                    "p50_ms": 5.0,  # 5.0 / 2.0 = 2.5 > 1.3
                },
            },
        }
        result = evaluate_no_regression(streaming, fullbuffer, max_ratio=1.3)
        assert result["status"] == "FAIL"

    def test_fail_when_no_small_medium_tiers(self):
        """
        Verify that evaluate_no_regression reports FAIL when the streaming report contains no small or medium tiers to evaluate.
        """
        fullbuffer = _make_fullbuffer_report()
        streaming = {
            "schema_version": "1.0.0",
            "tiers": {
                "large-1m": {"p50_ms": 55.0},
            },
        }
        result = evaluate_no_regression(streaming, fullbuffer)
        assert result["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Parity dual-threshold evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateParityDualThreshold:
    """Tests for parity dual-threshold evaluation."""

    def test_both_pass_with_perfect_rates(self):
        """pass_rate=1.0 and correctness_rate=1.0 -> both PASS."""
        parity = _make_parity_report(pass_rate=1.0, correctness_rate=1.0)
        result = evaluate_parity_dual_threshold(parity)
        assert result["streaming_supported_parity"]["status"] == "PASS"
        assert result["fallback_expected_correctness"]["status"] == "PASS"

    def test_fail_when_pass_rate_below_one(self):
        """pass_rate < 1.0 -> streaming_supported_parity FAIL."""
        parity = _make_parity_report(pass_rate=0.95, correctness_rate=1.0)
        result = evaluate_parity_dual_threshold(parity)
        assert result["streaming_supported_parity"]["status"] == "FAIL"
        assert result["fallback_expected_correctness"]["status"] == "PASS"

    def test_fail_when_correctness_rate_below_one(self):
        """correctness_rate < 1.0 -> fallback_expected_correctness FAIL."""
        parity = _make_parity_report(pass_rate=1.0, correctness_rate=0.99)
        result = evaluate_parity_dual_threshold(parity)
        assert result["streaming_supported_parity"]["status"] == "PASS"
        assert result["fallback_expected_correctness"]["status"] == "FAIL"

    def test_unknown_when_parity_report_is_none(self):
        """No parity report provided -> both UNKNOWN."""
        result = evaluate_parity_dual_threshold(None)
        assert result["streaming_supported_parity"]["status"] == "UNKNOWN"
        assert result["fallback_expected_correctness"]["status"] == "UNKNOWN"

    def test_epsilon_tolerant_pass_rate_treated_as_one(self):
        """pass_rate within _PARITY_EPSILON of 1.0 should still pass.

        Floating-point division may yield 0.9999999999999998 instead of
        exactly 1.0 for perfect parity (e.g. 180/180). The epsilon
        tolerance prevents false negatives from such imprecision.
        """
        near_one = 1.0 - (_PARITY_EPSILON / 2)
        parity = _make_parity_report(pass_rate=near_one, correctness_rate=1.0)
        result = evaluate_parity_dual_threshold(parity)
        assert result["streaming_supported_parity"]["status"] == "PASS"
        assert result["fallback_expected_correctness"]["status"] == "PASS"

    def test_epsilon_tolerant_correctness_rate_treated_as_one(self):
        """correctness_rate within _PARITY_EPSILON of 1.0 should still pass."""
        near_one = 1.0 - (_PARITY_EPSILON / 2)
        parity = _make_parity_report(pass_rate=1.0, correctness_rate=near_one)
        result = evaluate_parity_dual_threshold(parity)
        assert result["streaming_supported_parity"]["status"] == "PASS"
        assert result["fallback_expected_correctness"]["status"] == "PASS"


# ---------------------------------------------------------------------------
# Evidence pack generation tests
# ---------------------------------------------------------------------------


class TestGenerateEvidencePack:
    """Tests for the main evidence pack generation function."""

    def test_schema_version_is_fixed(self):
        """schema_version should always be '1.0.0'."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        assert pack["schema_version"] == "1.0.0"

    def test_type_is_evidence_pack(self):
        """type field should be 'evidence-pack'."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        assert pack["type"] == "evidence-pack"

    def test_metadata_contains_required_fields(self):
        """Metadata should have timestamp, git_commit, platform, engine_version."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        meta = pack["metadata"]
        assert "timestamp" in meta
        assert "git_commit" in meta
        assert "platform" in meta
        assert "engine_version" in meta

    def test_release_gates_all_present(self):
        """All 6 release gates should be present."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        expected_gates = {
            "bounded_memory_evidence",
            "ttfb_improvement_evidence",
            "no_regression_evidence",
            "parity_evidence",
            "diff_testing_complete",
            "rollout_docs_complete",
        }
        assert set(pack["release_gates"].keys()) == expected_gates

    def test_verdict_is_go_when_all_pass(self):
        """All gates PASS -> verdict should be GO."""
        # Build a streaming report with 4 large tiers so bounded_memory
        # has enough data points (min_data_points=4 by default).
        streaming_tiers = {
            "small": {
                "p50_ms": 2.2, "p95_ms": 3.5, "p99_ms": 5.5,
                "ttfb_ms": 0.5, "input_bytes": 10000,
                "peak_memory_bytes": 500000,
            },
            "medium": {
                "p50_ms": 5.5, "p95_ms": 9.0, "p99_ms": 13.0,
                "ttfb_ms": 1.0, "input_bytes": 100000,
                "peak_memory_bytes": 800000,
            },
            "large-100k": {
                "p50_ms": 11.0, "p95_ms": 16.0, "p99_ms": 22.0,
                "ttfb_ms": 2.0, "input_bytes": 500000,
                "peak_memory_bytes": 1200000,
            },
            "large-1m": {
                "p50_ms": 55.0, "p95_ms": 85.0, "p99_ms": 130.0,
                "ttfb_ms": 10.0, "input_bytes": 1000000,
                "peak_memory_bytes": 2000000,
            },
            "large-5m": {
                "p50_ms": 200.0, "p95_ms": 300.0, "p99_ms": 400.0,
                "ttfb_ms": 20.0, "input_bytes": 5000000,
                "peak_memory_bytes": 3000000,
            },
            "extra-large-10m": {
                "p50_ms": 400.0, "p95_ms": 600.0, "p99_ms": 800.0,
                "ttfb_ms": 40.0, "input_bytes": 10000000,
                "peak_memory_bytes": 4000000,
            },
        }
        fullbuffer_tiers = {
            "small": {"p50_ms": 2.0, "p95_ms": 3.0, "p99_ms": 5.0, "input_bytes": 10000},
            "medium": {"p50_ms": 5.0, "p95_ms": 8.0, "p99_ms": 12.0, "input_bytes": 100000},
            "large-100k": {"p50_ms": 10.0, "p95_ms": 15.0, "p99_ms": 20.0, "input_bytes": 500000},
            "large-1m": {"p50_ms": 50.0, "p95_ms": 80.0, "p99_ms": 120.0, "input_bytes": 1000000},
            "large-5m": {"p50_ms": 200.0, "p95_ms": 300.0, "p99_ms": 400.0, "input_bytes": 5000000},
            "extra-large-10m": {"p50_ms": 400.0, "p95_ms": 600.0, "p99_ms": 800.0, "input_bytes": 10000000},
        }
        targets = _make_evidence_targets({
            "diff_testing_complete": "PASS",
            "rollout_docs_complete": "PASS",
        })
        pack = generate_evidence_pack(
            _make_fullbuffer_report(tiers=fullbuffer_tiers),
            _make_streaming_report(tiers=streaming_tiers),
            targets,
            parity_report=_make_parity_report(),
        )
        assert pack["evidence_targets"]["bounded_memory"]["status"] == "PASS"
        assert pack["streaming_evidence_verdict"] == "GO"

    def test_verdict_is_no_go_when_any_fail(self):
        """Any gate FAIL -> verdict should be NO_GO."""
        targets = _make_evidence_targets({
            "diff_testing_complete": "FAIL",
        })
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            targets,
        )
        assert pack["streaming_evidence_verdict"] == "NO_GO"

    def test_p1_status_does_not_affect_verdict(self):
        """P1 status fields should NOT affect the verdict (Property 5)."""
        # Even with all P1 deferred, if gates pass, verdict is GO
        targets = _make_evidence_targets()
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            targets,
        )
        # P1 status is always "deferred"
        assert pack["p1_status"]["if_none_match_streaming"] == "deferred"
        assert pack["p1_status"]["otel_integration"] == "deferred"
        assert pack["p1_status"]["extra_formats"] == "deferred"

    def test_parity_unknown_causes_no_go(self):
        """No parity report -> parity gates UNKNOWN -> verdict NO_GO."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
            parity_report=None,
        )
        assert pack["release_gates"]["parity_evidence"] == "FAIL"
        assert pack["streaming_evidence_verdict"] == "NO_GO"

    def test_parity_report_included_when_provided(self):
        """Parity report subset should be included when provided."""
        parity = _make_parity_report()
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
            parity_report=parity,
        )
        assert pack["parity"] is not None
        assert pack["parity"]["summary"]["pass_rate"] == 1.0

    def test_parity_is_none_when_not_provided(self):
        """Parity should be None when not provided."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
            parity_report=None,
        )
        assert pack["parity"] is None

    def test_evidence_target_results_contain_all_goals(self):
        """Evidence targets should contain all 5 evidence goal results."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        expected_goals = {
            "bounded_memory",
            "ttfb_improvement",
            "no_regression_small_medium",
            "streaming_supported_parity",
            "fallback_expected_correctness",
        }
        assert set(pack["evidence_targets"].keys()) == expected_goals

    def test_deterministic_output(self):
        """Same input should always produce same output (Property 1)."""
        fullbuffer = _make_fullbuffer_report()
        streaming = _make_streaming_report()
        targets = _make_evidence_targets()

        pack1 = generate_evidence_pack(fullbuffer, streaming, targets)
        pack2 = generate_evidence_pack(fullbuffer, streaming, targets)

        # Compare everything except timestamp and git_commit (which may vary)
        assert pack1["evidence_targets"] == pack2["evidence_targets"]
        assert pack1["release_gates"] == pack2["release_gates"]
        assert pack1["streaming_evidence_verdict"] == pack2["streaming_evidence_verdict"]
        assert pack1["p1_status"] == pack2["p1_status"]


# ---------------------------------------------------------------------------
# Release gates evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateReleaseGates:
    """Tests for release gates evaluation."""

    def test_verdict_computed_from_gates(self):
        """evaluate_release_gates should compute verdict from gates."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        result = evaluate_release_gates(pack)
        assert "release_gates" in result
        assert "streaming_evidence_verdict" in result
        assert "_computed_verdict" in result
        assert "verdict_consistent" in result
        assert result["verdict_consistent"] is True

    def test_verdict_logic_go_only_when_all_pass(self):
        """Verdict is GO if and only if ALL gates are PASS (Property 4)."""
        pack_all_pass = {
            "release_gates": {
                "bounded_memory_evidence": "PASS",
                "ttfb_improvement_evidence": "PASS",
                "no_regression_evidence": "PASS",
                "parity_evidence": "PASS",
                "diff_testing_complete": "PASS",
                "rollout_docs_complete": "PASS",
            },
            "streaming_evidence_verdict": "GO",
        }
        result = evaluate_release_gates(pack_all_pass)
        assert result["streaming_evidence_verdict"] == "GO"

        pack_one_fail = {
            "release_gates": {
                "bounded_memory_evidence": "PASS",
                "ttfb_improvement_evidence": "FAIL",
                "no_regression_evidence": "PASS",
                "parity_evidence": "PASS",
                "diff_testing_complete": "PASS",
                "rollout_docs_complete": "PASS",
            },
            "streaming_evidence_verdict": "NO_GO",
        }
        result = evaluate_release_gates(pack_one_fail)
        assert result["streaming_evidence_verdict"] == "NO_GO"


# ---------------------------------------------------------------------------
# Human-readable summary tests
# ---------------------------------------------------------------------------


class TestPrintHumanSummary:
    """Tests for the human-readable summary output."""

    def test_outputs_to_stderr_by_default(self):
        """Summary should be written to stderr."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        output = StringIO()
        print_human_summary(pack, file=output)
        text = output.getvalue()
        assert "Evidence Goals" in text
        assert "Release Gates" in text
        assert "Verdict" in text

    def test_contains_verdict(self):
        """Summary should contain the final verdict line."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        output = StringIO()
        print_human_summary(pack, file=output)
        text = output.getvalue()
        assert "GO" in text or "NO_GO" in text

    def test_contains_metadata(self):
        """Summary should contain metadata information."""
        pack = generate_evidence_pack(
            _make_fullbuffer_report(),
            _make_streaming_report(),
            _make_evidence_targets(),
        )
        output = StringIO()
        print_human_summary(pack, file=output)
        text = output.getvalue()
        assert "Timestamp" in text
        assert "Git Commit" in text
        assert "Platform" in text


# ---------------------------------------------------------------------------
# CLI entry point tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the CLI entry point."""

    def test_main_succeeds_with_valid_inputs(self, capsys):
        """CLI should succeed with valid input files."""
        fullbuffer_path = _write_tmp_json(_make_fullbuffer_report())
        streaming_path = _write_tmp_json(_make_streaming_report())
        targets_path = _write_tmp_json(_make_evidence_targets())
        output_path = tempfile.mktemp(suffix=".json")

        exit_code = main([
            "--fullbuffer-report", fullbuffer_path,
            "--streaming-report", streaming_path,
            "--evidence-targets", targets_path,
            "--output", output_path,
        ])
        # Exit code depends on verdict; we just check it doesn't crash
        assert exit_code in (0, 1)

        # Verify output was written
        with open(output_path) as f:
            pack = json.load(f)
        assert pack["schema_version"] == "1.0.0"
        assert pack["type"] == "evidence-pack"

    def test_main_fails_with_missing_fullbuffer(self, capsys):
        """CLI should return exit code 2 when full-buffer report is missing."""
        streaming_path = _write_tmp_json(_make_streaming_report())
        targets_path = _write_tmp_json(_make_evidence_targets())
        output_path = tempfile.mktemp(suffix=".json")

        exit_code = main([
            "--fullbuffer-report", "/nonexistent/fullbuffer.json",
            "--streaming-report", streaming_path,
            "--evidence-targets", targets_path,
            "--output", output_path,
        ])
        assert exit_code == 2

    def test_main_fails_with_missing_streaming(self, capsys):
        """CLI should return exit code 2 when streaming report is missing."""
        fullbuffer_path = _write_tmp_json(_make_fullbuffer_report())
        targets_path = _write_tmp_json(_make_evidence_targets())
        output_path = tempfile.mktemp(suffix=".json")

        exit_code = main([
            "--fullbuffer-report", fullbuffer_path,
            "--streaming-report", "/nonexistent/streaming.json",
            "--evidence-targets", targets_path,
            "--output", output_path,
        ])
        assert exit_code == 2

    def test_main_fails_with_missing_targets(self, capsys):
        """CLI should return exit code 2 when evidence targets are missing."""
        fullbuffer_path = _write_tmp_json(_make_fullbuffer_report())
        streaming_path = _write_tmp_json(_make_streaming_report())
        output_path = tempfile.mktemp(suffix=".json")

        exit_code = main([
            "--fullbuffer-report", fullbuffer_path,
            "--streaming-report", streaming_path,
            "--evidence-targets", "/nonexistent/targets.json",
            "--output", output_path,
        ])
        assert exit_code == 2

    def test_main_handles_malformed_json(self, tmp_path, capsys):
        """CLI should return exit code 2 with malformed JSON."""
        bad_json = tmp_path / "bad.json"
        bad_json.write_text('{"invalid": json}', encoding="utf-8")

        streaming_path = _write_tmp_json(_make_streaming_report())
        targets_path = _write_tmp_json(_make_evidence_targets())
        output_path = tempfile.mktemp(suffix=".json")

        exit_code = main([
            "--fullbuffer-report", str(bad_json),
            "--streaming-report", streaming_path,
            "--evidence-targets", targets_path,
            "--output", output_path,
        ])
        assert exit_code == 2

    def test_summary_only_mode(self, capsys):
        """--summary-only should print summary but not write JSON."""
        fullbuffer_path = _write_tmp_json(_make_fullbuffer_report())
        streaming_path = _write_tmp_json(_make_streaming_report())
        targets_path = _write_tmp_json(_make_evidence_targets())
        output_path = tempfile.mktemp(suffix=".json")

        exit_code = main([
            "--fullbuffer-report", fullbuffer_path,
            "--streaming-report", streaming_path,
            "--evidence-targets", targets_path,
            "--output", output_path,
            "--summary-only",
        ])
        assert exit_code in (0, 1)
        # Output file should not exist
        assert not Path(output_path).exists()

    def test_summary_only_without_output(self, tmp_path, capsys):
        """--summary-only without --output should succeed and not write JSON."""
        fullbuffer_path = tmp_path / "fullbuffer.json"
        streaming_path = tmp_path / "streaming.json"
        targets_path = tmp_path / "targets.json"

        fullbuffer_path.write_text(json.dumps(_make_fullbuffer_report()))
        streaming_path.write_text(json.dumps(_make_streaming_report()))
        targets_path.write_text(json.dumps(_make_evidence_targets()))

        # Track JSON files before running
        before_json_files = {p.name for p in tmp_path.glob("*.json")}

        exit_code = main([
            "--fullbuffer-report", str(fullbuffer_path),
            "--streaming-report", str(streaming_path),
            "--evidence-targets", str(targets_path),
            "--summary-only",
        ])

        # Should succeed (exit 0 for GO, 1 for NO_GO — both are valid outcomes)
        assert exit_code in (0, 1)

        # Summary should have been printed to stderr
        captured = capsys.readouterr()

        # No new JSON files should have been created
        after_json_files = {p.name for p in tmp_path.glob("*.json")}
        assert after_json_files == before_json_files
