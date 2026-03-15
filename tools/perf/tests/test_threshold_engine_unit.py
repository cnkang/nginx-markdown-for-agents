"""Unit tests for the threshold engine.

Covers:
  - Specific threshold verdict examples (known input → known output)
  - Missing baseline graceful degradation (skip, exit 0, overall_verdict: skipped)
  - Missing thresholds config falls back to defaults
  - PERF_GATE_SKIP=1 skip behaviour

Run:
    cd tools/perf && python3 -m pytest tests/test_threshold_engine_unit.py -v
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from threshold_engine import (
    build_direction_map,
    build_verdict_report,
    compute_deviation,
    DEFAULT_THRESHOLDS,
    get_threshold,
    judge_metric,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

METRICS_SCHEMA = {
    "schema_version": "1.0.0",
    "metrics": [
        {"name": "p50_ms", "direction": "lower_is_better"},
        {"name": "p95_ms", "direction": "lower_is_better"},
        {"name": "p99_ms", "direction": "lower_is_better"},
        {"name": "peak_memory_bytes", "direction": "lower_is_better"},
        {"name": "req_per_s", "direction": "higher_is_better"},
        {"name": "input_mb_per_s", "direction": "higher_is_better"},
    ],
}


DIRECTION_MAP = build_direction_map(METRICS_SCHEMA)


def _write_tmp_json(data, suffix=".json"):
    """Write *data* to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _temp_output_path(suffix=".json"):
    """Reserve a temp output path without using insecure mktemp."""
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = f.name
    f.close()
    return path


def _make_baseline(tiers=None):
    return {
        "schema_version": "1.0.0",
        "timestamp": "2026-03-15T10:00:00Z",
        "git_commit": "abc1234",
        "platform": "ubuntu-latest",
        "tiers": tiers or {
            "small": {
                "p50_ms": 1.0,
                "p95_ms": 1.5,
                "p99_ms": 2.0,
                "peak_memory_bytes": 1000000,
                "req_per_s": 1000.0,
                "input_mb_per_s": 50.0,
            },
        },
    }


def _make_current(tiers=None):
    return {
        "schema_version": "1.0.0",
        "report_type": "measurement",
        "timestamp": "2026-03-15T11:00:00Z",
        "git_commit": "def5678",
        "platform": "ubuntu-latest",
        "tiers": tiers or {
            "small": {
                "p50_ms": 1.0,
                "p95_ms": 1.5,
                "p99_ms": 2.0,
                "peak_memory_bytes": 1000000,
                "req_per_s": 1000.0,
                "input_mb_per_s": 50.0,
            },
        },
    }


THRESHOLDS_CFG = {
    "schema_version": "1.0.0",
    "platforms": {
        "ubuntu-latest": {
            "small": {
                "p50_ms": {"warning_pct": 15, "blocking_pct": 30},
                "p95_ms": {"warning_pct": 20, "blocking_pct": 40},
                "p99_ms": {"warning_pct": 20, "blocking_pct": 40},
                "peak_memory_bytes": {"warning_pct": 10, "blocking_pct": 25},
                "req_per_s": {"warning_pct": -15, "blocking_pct": -30},
                "input_mb_per_s": {"warning_pct": -15, "blocking_pct": -30},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Specific threshold verdict examples
# ---------------------------------------------------------------------------

class TestSpecificVerdicts:
    """Known-input → known-output threshold judgement tests."""

    def test_all_pass_when_identical(self):
        """Identical baseline and current → all pass."""
        out_path = _temp_output_path()
        report, has_failure = build_verdict_report(
            _make_baseline(), _make_current(), THRESHOLDS_CFG,
            DIRECTION_MAP, "ubuntu-latest", out_path,
        )
        assert report["overall_verdict"] == "pass"
        assert not has_failure
        for tier in report["comparison"]["tiers"].values():
            for metric_info in tier.values():
                assert metric_info["verdict"] == "pass"
                assert metric_info["deviation_pct"] == pytest.approx(0.0, abs=1e-12)

    def test_warn_on_moderate_regression(self):
        """20% latency increase → warn (within 15-30 range for p50)."""
        current = _make_current({"small": {
            "p50_ms": 1.2,  # +20% → warn
            "p95_ms": 1.5, "p99_ms": 2.0,
            "peak_memory_bytes": 1000000,
            "req_per_s": 1000.0, "input_mb_per_s": 50.0,
        }})
        out_path = _temp_output_path()
        report, has_failure = build_verdict_report(
            _make_baseline(), current, THRESHOLDS_CFG,
            DIRECTION_MAP, "ubuntu-latest", out_path,
        )
        assert report["overall_verdict"] == "warn"
        assert not has_failure
        p50 = report["comparison"]["tiers"]["small"]["p50_ms"]
        assert p50["verdict"] == "warn"
        assert p50["deviation_pct"] == pytest.approx(20.0, abs=0.01)

    def test_fail_on_severe_regression(self):
        """50% latency increase → fail (exceeds 30% blocking)."""
        current = _make_current({"small": {
            "p50_ms": 1.5,  # +50% → fail
            "p95_ms": 1.5, "p99_ms": 2.0,
            "peak_memory_bytes": 1000000,
            "req_per_s": 1000.0, "input_mb_per_s": 50.0,
        }})
        out_path = _temp_output_path()
        report, has_failure = build_verdict_report(
            _make_baseline(), current, THRESHOLDS_CFG,
            DIRECTION_MAP, "ubuntu-latest", out_path,
        )
        assert report["overall_verdict"] == "fail"
        assert has_failure
        assert report["comparison"]["tiers"]["small"]["p50_ms"]["verdict"] == "fail"

    def test_throughput_drop_triggers_fail(self):
        """40% throughput drop → fail (exceeds -30% blocking)."""
        current = _make_current({"small": {
            "p50_ms": 1.0, "p95_ms": 1.5, "p99_ms": 2.0,
            "peak_memory_bytes": 1000000,
            "req_per_s": 600.0,  # -40% → fail
            "input_mb_per_s": 50.0,
        }})
        out_path = _temp_output_path()
        report, has_failure = build_verdict_report(
            _make_baseline(), current, THRESHOLDS_CFG,
            DIRECTION_MAP, "ubuntu-latest", out_path,
        )
        assert has_failure
        assert report["comparison"]["tiers"]["small"]["req_per_s"]["verdict"] == "fail"


# ---------------------------------------------------------------------------
# Deviation edge cases
# ---------------------------------------------------------------------------

class TestDeviationEdgeCases:
    """Edge cases around zero baselines stay explicit and testable."""

    def test_zero_baseline_zero_current_stays_zero(self):
        assert compute_deviation(0.0, 0.0) == pytest.approx(0.0, abs=1e-12)

    def test_zero_baseline_positive_current_flags_lower_is_better_regression(self):
        deviation = compute_deviation(5.0, 0.0)
        assert deviation == pytest.approx(100.0, abs=1e-12)
        assert judge_metric(deviation, "lower_is_better", 15, 30) == "fail"

    def test_zero_baseline_positive_current_is_improvement_for_higher_is_better(self):
        deviation = compute_deviation(5.0, 0.0)
        assert deviation == pytest.approx(100.0, abs=1e-12)
        assert judge_metric(deviation, "higher_is_better", -15, -30) == "pass"


# ---------------------------------------------------------------------------
# Missing baseline → graceful degradation
# ---------------------------------------------------------------------------

class TestMissingBaseline:
    """When baseline file is absent, engine skips comparison with exit 0."""

    def test_missing_baseline_returns_zero(self):
        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", "/nonexistent/baseline.json",
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])
        assert exit_code == 0

    def test_missing_baseline_verdict_is_skipped(self):
        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        main([
            "--baseline", "/nonexistent/baseline.json",
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])
        with open(out_path) as f:
            verdict = json.load(f)
        assert verdict["overall_verdict"] == "skipped"
        assert verdict["comparison"]["tiers"] == {}
        assert verdict["comparison"]["baseline_commit"] is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Corrupt inputs and schema drift should fail loudly."""

    def test_malformed_baseline_json_returns_nonzero(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text('{"schema_version": "1.0.0", "tiers": [}', encoding="utf-8")

        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", str(baseline_path),
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "baseline" in captured.err.lower()
        assert "json" in captured.err.lower()

    def test_malformed_thresholds_json_returns_nonzero(self, tmp_path, capsys):
        thresholds_path = tmp_path / "thresholds.json"
        thresholds_path.write_text(
            '{"schema_version": "1.0.0", "platforms": {"ubuntu-latest": {"small": {"p50_ms": }}',
            encoding="utf-8",
        )

        baseline_path = _write_tmp_json(_make_baseline())
        current_path = _write_tmp_json(_make_current())
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", baseline_path,
            "--current", current_path,
            "--thresholds", str(thresholds_path),
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "thresholds" in captured.err.lower()
        assert "expecting value" in captured.err.lower()

    def test_schema_version_mismatch_returns_nonzero(self):
        baseline_path = _write_tmp_json({
            **_make_baseline(),
            "schema_version": "2.0.0",
        })
        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", baseline_path,
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])

        assert exit_code == 1


# ---------------------------------------------------------------------------
# Missing thresholds config → use defaults
# ---------------------------------------------------------------------------

class TestMissingThresholds:
    """When thresholds config is absent, engine uses built-in defaults."""

    def test_missing_thresholds_uses_defaults(self):
        baseline_path = _write_tmp_json(_make_baseline())
        current_path = _write_tmp_json(_make_current())
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", baseline_path,
            "--current", current_path,
            "--thresholds", "/nonexistent/thresholds.json",
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])
        assert exit_code == 0
        with open(out_path) as f:
            verdict = json.load(f)
        assert verdict["overall_verdict"] == "pass"

    def test_get_threshold_fallback_to_defaults(self):
        """get_threshold returns DEFAULT_THRESHOLDS when config has no match."""
        empty_cfg = {"platforms": {}}
        result = get_threshold(empty_cfg, "ubuntu-latest", "small", "p50_ms")
        assert result == DEFAULT_THRESHOLDS["p50_ms"]


# ---------------------------------------------------------------------------
# PERF_GATE_SKIP=1 skip behaviour
# ---------------------------------------------------------------------------

class TestPerfGateSkip:
    """PERF_GATE_SKIP=1 causes engine to skip all checks."""

    def test_skip_returns_zero(self, monkeypatch):
        monkeypatch.setenv("PERF_GATE_SKIP", "1")
        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", "/nonexistent/baseline.json",
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])
        assert exit_code == 0

    def test_skip_verdict_is_skipped(self, monkeypatch):
        monkeypatch.setenv("PERF_GATE_SKIP", "1")
        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        main([
            "--baseline", "/nonexistent/baseline.json",
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])
        with open(out_path) as f:
            verdict = json.load(f)
        assert verdict["overall_verdict"] == "skipped"

    def test_skip_not_triggered_when_unset(self, monkeypatch):
        """Without PERF_GATE_SKIP, engine runs normally."""
        monkeypatch.delenv("PERF_GATE_SKIP", raising=False)
        baseline_path = _write_tmp_json(_make_baseline())
        current_path = _write_tmp_json(_make_current())
        thresholds_path = _write_tmp_json(THRESHOLDS_CFG)
        schema_path = _write_tmp_json(METRICS_SCHEMA)
        out_path = _temp_output_path()

        exit_code = main([
            "--baseline", baseline_path,
            "--current", current_path,
            "--thresholds", thresholds_path,
            "--metrics-schema", schema_path,
            "--output-json", out_path,
            "--platform", "ubuntu-latest",
        ])
        assert exit_code == 0
        with open(out_path) as f:
            verdict = json.load(f)
        assert verdict["overall_verdict"] == "pass"
