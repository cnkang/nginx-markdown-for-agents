"""Unit tests for the 0.9.1 performance evidence release gate.

Covers:
  - GO verdict when all metrics within thresholds
  - NO_GO verdict with specific threshold breach identification
  - Graceful degradation when NGINX_BIN unavailable (non-blocking exit 75)
  - --allow-skip-module flag in blocking mode

Run:
    python3 -m pytest tools/perf/tests/test_evidence_gate_091.py -q

Requirements: 9.1, 9.3, 9.4
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence_gate_091 import (
    EX_SKIP_NOT_PRESENT,
    _extract_evidence_metrics,
    _nginx_bin_available,
    main,
    parse_args,
)
from threshold_engine import evaluate_module_level


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_module_thresholds_cfg():
    """Return a thresholds config with module_level section."""
    return {
        "module_level": {
            "p50_latency_small_pct": 10,
            "p95_latency_small_pct": 15,
            "p50_latency_large_pct": 5,
            "ttfb_streaming_large_pct": 10,
            "fallback_rate_abs": 0.05,
            "memory_slope_pct": 20,
        },
    }


def _make_passing_metrics():
    """Return current metrics that are within all thresholds (no regression)."""
    return {
        "p50_latency_small_pct": 1.0,
        "p95_latency_small_pct": 1.5,
        "p50_latency_large_pct": 5.0,
        "ttfb_streaming_large_pct": 3.0,
        "fallback_rate_abs": 0.02,
        "memory_slope_pct": 0.5,
    }


def _make_baseline_metrics():
    """Return baseline metrics matching the passing current metrics."""
    return {
        "p50_latency_small_pct": 1.0,
        "p95_latency_small_pct": 1.5,
        "p50_latency_large_pct": 5.0,
        "ttfb_streaming_large_pct": 3.0,
        "fallback_rate_abs": 0.01,
        "memory_slope_pct": 0.5,
    }


def _make_breaching_metrics():
    """Return current metrics that breach thresholds."""
    return {
        "p50_latency_small_pct": 1.5,   # +50% vs baseline (threshold: 10%)
        "p95_latency_small_pct": 2.0,   # +33% vs baseline (threshold: 15%)
        "p50_latency_large_pct": 5.0,
        "ttfb_streaming_large_pct": 3.0,
        "fallback_rate_abs": 0.10,      # 10% > 5% threshold
        "memory_slope_pct": 0.5,
    }


def _make_benchmark_report(scenarios=None):
    """Build a minimal benchmark report for testing."""
    if scenarios is None:
        scenarios = [
            {
                "name": "plain-small",
                "results": {
                    "p50_ms": 1.0,
                    "p95_ms": 1.5,
                    "ttfb_ms": 0.8,
                    "total_requests": 1000,
                    "fallback_count": 10,
                    "input_bytes": 500,
                    "peak_memory_bytes": 50000000,
                },
            },
            {
                "name": "gzip-large",
                "results": {
                    "p50_ms": 5.0,
                    "p95_ms": 7.0,
                    "ttfb_ms": 3.0,
                    "total_requests": 500,
                    "fallback_count": 5,
                    "input_bytes": 1000000,
                    "peak_memory_bytes": 80000000,
                },
            },
        ]
    return {
        "module_benchmark": {
            "version": "1.0.0",
            "scenarios": scenarios,
        },
    }


# ---------------------------------------------------------------------------
# Test: GO verdict when all metrics within thresholds
# ---------------------------------------------------------------------------

class TestGoVerdict:
    """Validates: Requirements 9.1 — GO verdict when all metrics pass."""

    def test_evaluate_module_level_returns_go_when_all_pass(self):
        """evaluate_module_level returns GO when all metrics within thresholds."""
        current = _make_passing_metrics()
        baseline = _make_baseline_metrics()
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "GO"
        assert result["breaches"] == []
        assert len(result["results"]) == 6
        for entry in result["results"]:
            assert entry["status"] in ("pass", "skipped")

    def test_go_when_metrics_identical_to_baseline(self):
        """Zero deviation (identical metrics) produces GO verdict."""
        metrics = _make_passing_metrics()
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(metrics, metrics.copy(), cfg)

        assert result["verdict"] == "GO"
        assert result["breaches"] == []

    def test_go_with_slight_improvement(self):
        """Slight improvement (lower latency) still produces GO."""
        baseline = _make_baseline_metrics()
        current = {**baseline, "p50_latency_small_pct": 0.9}  # 10% improvement
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "GO"

    def test_go_with_fallback_at_threshold_boundary(self):
        """Fallback rate exactly at threshold (5%) still passes."""
        baseline = _make_baseline_metrics()
        current = {**_make_passing_metrics(), "fallback_rate_abs": 0.05}
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "GO"


# ---------------------------------------------------------------------------
# Test: NO_GO with specific threshold breach identification
# ---------------------------------------------------------------------------

class TestNoGoVerdict:
    """Validates: Requirements 9.1 — NO_GO with breach identification."""

    def test_no_go_on_latency_regression(self):
        """Large latency regression triggers NO_GO with breach details."""
        baseline = _make_baseline_metrics()
        current = _make_breaching_metrics()
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "NO_GO"
        assert len(result["breaches"]) > 0
        breach_metrics = [b["metric"] for b in result["breaches"]]
        assert "p50_latency_small_pct" in breach_metrics

    def test_no_go_identifies_fallback_rate_breach(self):
        """Fallback rate exceeding 5% absolute cap triggers NO_GO."""
        baseline = _make_baseline_metrics()
        current = {**_make_passing_metrics(), "fallback_rate_abs": 0.08}
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "NO_GO"
        breach_metrics = [b["metric"] for b in result["breaches"]]
        assert "fallback_rate_abs" in breach_metrics
        # Verify breach includes threshold and actual value
        fallback_breach = next(
            b for b in result["breaches"] if b["metric"] == "fallback_rate_abs"
        )
        assert fallback_breach["threshold"] == 0.05
        assert fallback_breach["actual"] == 0.08

    def test_no_go_identifies_memory_slope_breach(self):
        """Memory slope exceeding +20% triggers NO_GO."""
        baseline = _make_baseline_metrics()
        # Memory slope: current=0.7, baseline=0.5, deviation=40% > 20% threshold
        current = {**_make_passing_metrics(), "memory_slope_pct": 0.7}
        baseline_with_slope = {**baseline, "memory_slope_pct": 0.5}
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline_with_slope, cfg)

        assert result["verdict"] == "NO_GO"
        breach_metrics = [b["metric"] for b in result["breaches"]]
        assert "memory_slope_pct" in breach_metrics

    def test_no_go_breach_contains_threshold_and_actual(self):
        """Each breach entry includes metric, threshold, and actual deviation."""
        baseline = _make_baseline_metrics()
        # p95: 2.0 vs 1.5 = +33% deviation, threshold is 15%
        current = {**_make_passing_metrics(), "p95_latency_small_pct": 2.0}
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "NO_GO"
        p95_breach = next(
            b for b in result["breaches"] if b["metric"] == "p95_latency_small_pct"
        )
        assert "threshold" in p95_breach
        assert "actual" in p95_breach
        assert p95_breach["threshold"] == 15
        assert p95_breach["actual"] > 15  # deviation exceeds threshold

    def test_multiple_breaches_all_identified(self):
        """When multiple thresholds are breached, all are reported."""
        baseline = _make_baseline_metrics()
        current = _make_breaching_metrics()
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "NO_GO"
        # Should have at least 2 breaches (p50 +50%, fallback >5%)
        assert len(result["breaches"]) >= 2


# ---------------------------------------------------------------------------
# Test: Graceful degradation when NGINX_BIN unavailable
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Validates: Requirements 9.3 — exit 75 when NGINX_BIN unset in non-blocking."""

    def test_non_blocking_exits_75_when_nginx_bin_unset(self, monkeypatch, tmp_path):
        """Non-blocking mode exits with code 75 (SKIP_NOT_PRESENT) when NGINX_BIN unset."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        output_path = tmp_path / "evidence.json"

        exit_code = main([
            "--mode", "non-blocking",
            "--output", str(output_path),
        ])

        assert exit_code == EX_SKIP_NOT_PRESENT
        assert exit_code == 75

    def test_non_blocking_skip_produces_evidence_pack(self, monkeypatch, tmp_path):
        """Skip produces an evidence pack JSON with skipped=true."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        output_path = tmp_path / "evidence.json"

        main(["--mode", "non-blocking", "--output", str(output_path)])

        assert output_path.exists()
        pack = json.loads(output_path.read_text(encoding="utf-8"))
        assert pack["skipped"] is True
        assert pack["verdict"] == "SKIPPED"
        assert "NGINX_BIN" in pack["skip_reason"]

    def test_blocking_exits_1_without_allow_skip(self, monkeypatch):
        """Blocking mode exits 1 when NGINX_BIN unset and --allow-skip-module not provided."""
        monkeypatch.delenv("NGINX_BIN", raising=False)

        exit_code = main(["--mode", "blocking"])

        assert exit_code == 1

    def test_nginx_bin_available_returns_false_when_unset(self, monkeypatch):
        """_nginx_bin_available returns False when NGINX_BIN env is not set."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        assert _nginx_bin_available() is False

    def test_nginx_bin_available_returns_false_for_nonexistent_path(self, monkeypatch):
        """_nginx_bin_available returns False when NGINX_BIN points to nonexistent file."""
        monkeypatch.setenv("NGINX_BIN", "/nonexistent/nginx")
        assert _nginx_bin_available() is False


# ---------------------------------------------------------------------------
# Test: --allow-skip-module flag in blocking mode
# ---------------------------------------------------------------------------

class TestAllowSkipModule:
    """Validates: Requirements 9.4 — --allow-skip-module flag in blocking mode."""

    def test_blocking_with_allow_skip_exits_0(self, monkeypatch, tmp_path):
        """Blocking mode with --allow-skip-module exits 0 when NGINX_BIN unset."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        output_path = tmp_path / "evidence.json"

        exit_code = main([
            "--mode", "blocking",
            "--allow-skip-module",
            "--output", str(output_path),
        ])

        assert exit_code == 0

    def test_blocking_with_allow_skip_produces_skipped_evidence(self, monkeypatch, tmp_path):
        """Blocking + --allow-skip-module produces evidence pack with skipped=true."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        output_path = tmp_path / "evidence.json"

        main([
            "--mode", "blocking",
            "--allow-skip-module",
            "--output", str(output_path),
        ])

        assert output_path.exists()
        pack = json.loads(output_path.read_text(encoding="utf-8"))
        assert pack["skipped"] is True
        assert pack["verdict"] == "SKIPPED"
        assert "--allow-skip-module" in pack["skip_reason"]

    def test_parse_args_allow_skip_module_default_false(self):
        """--allow-skip-module defaults to False when not provided."""
        args = parse_args(["--mode", "blocking"])
        assert args.allow_skip_module is False

    def test_parse_args_allow_skip_module_true(self):
        """--allow-skip-module is True when explicitly provided."""
        args = parse_args(["--mode", "blocking", "--allow-skip-module"])
        assert args.allow_skip_module is True


# ---------------------------------------------------------------------------
# Test: Evidence metric extraction from benchmark reports
# ---------------------------------------------------------------------------

class TestEvidenceMetricExtraction:
    """Test _extract_evidence_metrics correctly parses benchmark reports."""

    def test_extracts_small_scenario_latency(self):
        """Extracts p50 and p95 from small scenario."""
        report = _make_benchmark_report()
        metrics = _extract_evidence_metrics(report)

        assert "p50_latency_small_pct" in metrics
        assert metrics["p50_latency_small_pct"] == 1.0
        assert "p95_latency_small_pct" in metrics
        assert metrics["p95_latency_small_pct"] == 1.5

    def test_extracts_large_scenario_metrics(self):
        """Extracts p50 and TTFB from large scenario."""
        report = _make_benchmark_report()
        metrics = _extract_evidence_metrics(report)

        assert "p50_latency_large_pct" in metrics
        assert metrics["p50_latency_large_pct"] == 5.0
        assert "ttfb_streaming_large_pct" in metrics
        assert metrics["ttfb_streaming_large_pct"] == 3.0

    def test_computes_fallback_rate(self):
        """Computes fallback rate from aggregated scenario data."""
        report = _make_benchmark_report()
        metrics = _extract_evidence_metrics(report)

        # Total fallback: 10+5=15, total requests: 1000+500=1500
        expected_rate = 15 / 1500
        assert metrics["fallback_rate_abs"] == pytest.approx(expected_rate, abs=1e-6)

    def test_empty_report_returns_zero_fallback(self):
        """Empty report produces zero fallback rate."""
        metrics = _extract_evidence_metrics({})
        assert metrics["fallback_rate_abs"] == 0.0

    def test_missing_critical_metric_causes_missing_evidence_verdict(self):
        """When a critical metric is missing, the verdict must be MISSING_EVIDENCE."""
        current = _make_passing_metrics()
        current.pop("p50_latency_small_pct")  # Remove critical metric
        baseline = _make_baseline_metrics()
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg)

        assert result["verdict"] == "MISSING_EVIDENCE"
        missing_entries = [r for r in result["results"] if r["status"] == "missing_evidence"]
        assert len(missing_entries) == 1
        assert missing_entries[0]["metric"] == "p50_latency_small_pct"

    def test_first_run_without_baseline_explains_non_comparable_thresholds(self):
        """When no baseline is available, percentage thresholds are clearly marked and explained."""
        current = _make_passing_metrics()
        baseline = {}  # Empty/missing baseline
        cfg = _make_module_thresholds_cfg()

        result = evaluate_module_level(current, baseline, cfg, has_baseline=False)

        # Verdict should still be GO because there are no breaches, just skipped percentage thresholds
        assert result["verdict"] == "GO"
        
        # Absolute caps like fallback_rate_abs should still be evaluated and pass
        fallback_entry = next(r for r in result["results"] if r["metric"] == "fallback_rate_abs")
        assert fallback_entry["status"] == "pass"

        # Percentage deviation metrics like p50_latency_small_pct should be skipped with explanations
        p50_entry = next(r for r in result["results"] if r["metric"] == "p50_latency_small_pct")
        assert p50_entry["status"] == "skipped"
        assert "missing baseline" in p50_entry["reason"]
