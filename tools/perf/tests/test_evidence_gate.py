"""Unit tests for the 0.9.1 performance evidence release gate.

Covers:
  - GO verdict when all metrics within thresholds
  - NO_GO verdict with specific threshold breach identification
  - Graceful degradation when NGINX_BIN unavailable (non-blocking exit 75)
  - --allow-skip-module flag in blocking mode

Run:
    python3 -m pytest tools/perf/tests/test_evidence_gate.py -q

Requirements: 9.1, 9.3, 9.4
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evidence_gate
from evidence_gate import (
    EX_SKIP_NOT_PRESENT,
    _RC_RE,
    _RELEASE_TAG_RE,
    _check_skipped_scenarios,
    _check_missing_scenarios,
    _check_scenario_completion,
    _check_path_coverage,
    _check_environment_compatibility,
    _compute_memory_slope,
    _extract_evidence_metrics,
    _extract_memory_points,
    _nginx_bin_available,
    _validate_benchmark_evidence,
    _write_output,
    main,
    parse_args,
)
from threshold_engine import evaluate_module_level


def test_memory_points_require_measured_input_bytes():
    """Scenarios without peak RSS evidence produce no memory points.

    Previously, worker_rss_mb was used as a fallback.  Now peak_rss_bytes
    and baseline_rss_bytes are required — without them, the point is
    None so the gate reports MISSING_EVIDENCE rather than masking
    missing evidence.
    """
    scenarios = [
        {"name": "plain-small", "metrics": {"worker_rss_mb": 12.0}},
        {
            "name": "large-body",
            "metrics": {"worker_rss_mb": 24.0, "input_bytes": 1_048_516},
        },
    ]

    # Neither scenario has peak_rss_bytes/baseline_rss_bytes, so both
    # produce no memory point.
    assert _extract_memory_points(scenarios) == []


def test_memory_points_with_peak_evidence():
    """Scenarios with peak/baseline RSS produce valid memory points."""
    scenarios = [
        {
            "name": "large-body",
            "metrics": {
                "input_bytes": 1_048_516,
                "baseline_rss_bytes": 10 * 1024 * 1024,
                "peak_rss_bytes": 24 * 1024 * 1024,
            },
        },
    ]

    assert _extract_memory_points(scenarios) == [
        (1_048_516.0, 14 * 1024 * 1024),
    ]


def test_memory_points_prefer_peak_rss_delta():
    """Peak RSS delta is preferred over post-run RSS.

    The point should be (input_bytes, peak - baseline), not
    (input_bytes, worker_rss_mb * 1024 * 1024).
    """
    scenarios = [
        {
            "name": "large-body",
            "metrics": {
                "input_bytes": 1_048_516,
                "baseline_rss_bytes": 10 * 1024 * 1024,
                "peak_rss_bytes": 15 * 1024 * 1024,
                "worker_rss_mb": 24.0,
            },
        },
    ]

    points = _extract_memory_points(scenarios)
    assert len(points) == 1
    assert points[0] == (1_048_516.0, 5 * 1024 * 1024)


def test_memory_points_zero_delta_is_valid_evidence():
    """When peak == baseline (delta=0), the point is (input_bytes, 0.0).

    A delta of 0 is valid evidence (no memory growth), not missing
    evidence.  It must NOT fall back to post-run RSS.
    """
    scenarios = [
        {
            "name": "large-body",
            "metrics": {
                "input_bytes": 1_048_516,
                "baseline_rss_bytes": 10 * 1024 * 1024,
                "peak_rss_bytes": 10 * 1024 * 1024,
                "worker_rss_mb": 10.0,
            },
        },
    ]

    points = _extract_memory_points(scenarios)
    assert len(points) == 1
    assert points[0] == (1_048_516.0, 0.0)


def test_memory_points_no_peak_evidence_returns_none():
    """When peak RSS evidence is absent, returns None (NOT post-run RSS).

    Missing peak evidence must be treated as MISSING_EVIDENCE by the
    gate, not silently masked by falling back to worker_rss_mb.
    """
    scenarios = [
        {
            "name": "large-body",
            "metrics": {
                "input_bytes": 1_048_516,
                "worker_rss_mb": 24.0,
            },
        },
    ]

    points = _extract_memory_points(scenarios)
    assert points == []


def test_memory_points_peak_below_baseline_returns_none():
    """When peak < baseline (sampler error), returns None."""
    scenarios = [
        {
            "name": "large-body",
            "metrics": {
                "input_bytes": 1_048_516,
                "baseline_rss_bytes": 15 * 1024 * 1024,
                "peak_rss_bytes": 10 * 1024 * 1024,
            },
        },
    ]

    points = _extract_memory_points(scenarios)
    assert points == []


def test_memory_slope_is_rss_per_input_byte():
    """Slope is ΔRSS_bytes / Δinput_bytes, not a dimensionless percentage.

    For two points:
      (1 MB input, 1 MB RSS) and (2 MB input, 1.5 MB RSS)
    slope = (1.5 - 1.0) / (2.0 - 1.0) = 0.5 bytes RSS per byte input
    """
    points = [
        (1_048_576.0, 1_048_576.0),
        (2_097_152.0, 1_572_864.0),
    ]
    slope = _compute_memory_slope(points)
    # 0.5 bytes RSS per input byte
    assert abs(slope - 0.5) < 0.001


def test_memory_slope_single_point_returns_zero():
    """With fewer than 2 points, slope is 0.0 (insufficient data)."""
    assert _compute_memory_slope([(1.0, 1.0)]) == 0.0
    assert _compute_memory_slope([]) == 0.0


def test_tag_release_job_supplies_module_enabled_nginx():
    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github"
        / "workflows"
        / "release-packages.yml"
    ).read_text(encoding="utf-8")

    # The public top-level `build` target produces both artifacts across the
    # supported matrix. `binary` is only an objs/Makefile target in 1.24.0.
    assert "make -j\"$(nproc)\" build" in workflow, (
        "Build step must run the portable top-level `make build` target"
    )
    assert "make -j\"$(nproc)\" binary modules" not in workflow
    # Must verify artifacts exist before copying (prevents silent failures
    # when only one target is built).
    assert "Verify build artifacts exist" in workflow, (
        "Build job must verify both nginx binary and module .so exist "
        "before copying"
    )
    assert "test -x \"${NGINX_SRC}/objs/nginx\"" in workflow, (
        "Build job must verify objs/nginx is executable"
    )
    assert 'cp "${NGINX_SRC}/objs/nginx" build/nginx' in workflow
    assert "apache2-utils" in workflow
    # The release gate must download a single canonical NGINX version's
    # artifact by exact name, NOT use a wildcard pattern with merge-multiple
    # (which causes filename collisions when multiple NGINX versions produce
    # the same build/nginx and build/ngx_http_markdown_filter_module.so names).
    assert "Determine canonical benchmark NGINX version" in workflow, (
        "Release gate must explicitly select a canonical benchmark NGINX "
        "version to avoid artifact filename collisions"
    )
    assert "module-so-${{ steps.bench-nginx.outputs.bench_nginx_version }}-x86_64" in workflow, (
        "Release gate must download the canonical benchmark NGINX artifact "
        "by exact name, not a wildcard pattern with merge-multiple"
    )
    assert "NGINX_BIN: ${{ github.workspace }}/module-runtime/nginx" in workflow
    assert "MODULE_SO: ${{ github.workspace }}/module-runtime/ngx_http_markdown_filter_module.so" in workflow
    assert "BENCHMARK_NGINX_VERSION" in workflow, (
        "Release gate must record the benchmark NGINX version for evidence"
    )
    assert "python3 tools/perf/evidence_gate.py --mode blocking" in workflow
    assert "evidence_gate.py --blocking" not in workflow


def test_manual_module_baseline_workflow_uses_canonical_native_runtime():
    """Manual baseline bootstrap produces auditable native module evidence."""
    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github"
        / "workflows"
        / "nightly-perf.yml"
    ).read_text(encoding="utf-8")

    assert "bootstrap_module_baseline" in workflow
    assert "if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.bootstrap_module_baseline) }}" in workflow
    assert "name: Canonical Module Baseline 0.9.1" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert '[[ "$(uname -m)" == "x86_64" ]]' in workflow
    assert "Determine canonical benchmark NGINX version" in workflow
    assert "tools/release-matrix.json" in workflow
    assert "apache2-utils" in workflow
    assert "tools/perf/run_module_benchmark.sh" in workflow
    assert "perf/baselines/module-baseline-091.json" in workflow
    assert "_validate_benchmark_evidence" in workflow
    assert "make perf-evidence-check" in workflow
    assert "make release-gates-check-091" in workflow
    assert "module-baseline-091-${{ github.sha }}" in workflow


def test_module_baseline_contains_measured_critical_scenarios():
    """The checked-in baseline contains real critical-path evidence."""
    baseline_path = (
        Path(__file__).resolve().parents[3]
        / "perf"
        / "baselines"
        / "module-baseline-091.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    scenarios = baseline["module_benchmark"]["scenarios"]
    by_name = {scenario["name"]: scenario for scenario in scenarios}

    # Original critical scenarios — verified from existing baseline
    for name in ("plain-small", "large-body", "streaming-first"):
        assert name in by_name, f"baseline missing scenario: {name}"
        assert by_name[name]["status"] == "completed"
        assert by_name[name]["metrics"]["input_bytes"] > 0
        assert by_name[name]["metrics"]["baseline_rss_bytes"] > 0
        assert by_name[name]["metrics"]["peak_rss_bytes"] > 0

    assert baseline["module_benchmark"]["nginx_version"].startswith(
        "nginx version: nginx/"
    )
    streaming = by_name["streaming-first"]["metrics"]
    assert streaming["input_bytes"] == 1_048_516
    assert streaming["streaming_path_hits"] > 0
    assert streaming["streaming_ratio"] == 1.0
    assert streaming["streaming_fallback_total"] == 0
    assert streaming["zero_copy_output_total"] > 0

    # 0.9.1 compressed-streaming critical scenarios.
    for name in ("gzip-large", "gzip-streaming-first", "deflate-streaming-first"):
        assert name in by_name, (
            f"baseline missing critical scenario: {name}; "
            f"regenerate baseline with module-enabled NGINX"
        )
        assert by_name[name]["status"] == "completed"
        assert by_name[name]["metrics"]["input_bytes"] > 0
        assert by_name[name]["metrics"]["baseline_rss_bytes"] > 0
        assert by_name[name]["metrics"]["peak_rss_bytes"] > 0

    # Verify decompression path evidence in compressed streaming scenarios
    if "gzip-streaming-first" in by_name:
        gzip_s = by_name["gzip-streaming-first"]["metrics"]
        assert gzip_s["decompression_streaming_total"] > 0, (
            "gzip-streaming-first must prove gzip streaming decompression ran"
        )
        assert gzip_s["streaming_path_hits"] > 0

    if "deflate-streaming-first" in by_name:
        deflate_s = by_name["deflate-streaming-first"]["metrics"]
        assert deflate_s["decompression_streaming_total"] > 0, (
            "deflate-streaming-first must prove deflate streaming decompression ran"
        )
        assert deflate_s["streaming_path_hits"] > 0


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
                "status": "completed",
                "metrics": {
                    "latency_p50_ms": 1.0,
                    "latency_p95_ms": 1.5,
                    "ttfb_p50_ms": 0.8,
                    "fallback_rate": 0.01,
                    "worker_rss_mb": 47.68,
                },
            },
            {
                "name": "gzip-large",
                "status": "completed",
                "metrics": {
                    "latency_p50_ms": 5.0,
                    "latency_p95_ms": 7.0,
                    "ttfb_p50_ms": 3.0,
                    "fallback_rate": 0.01,
                    "worker_rss_mb": 76.29,
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
        monkeypatch.setattr(evidence_gate, "REPO_ROOT", tmp_path)
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
        monkeypatch.setattr(evidence_gate, "REPO_ROOT", tmp_path)
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

    def test_nginx_bin_available_returns_false_for_non_executable_file(
        self, monkeypatch, tmp_path
    ):
        """_nginx_bin_available rejects present files without execute permission."""
        nginx_bin = tmp_path / "nginx"
        nginx_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        nginx_bin.chmod(0o600)
        monkeypatch.setenv("NGINX_BIN", str(nginx_bin))

        assert _nginx_bin_available() is False


# ---------------------------------------------------------------------------
# Test: --allow-skip-module flag in blocking mode
# ---------------------------------------------------------------------------

class TestAllowSkipModule:
    """Validates: Requirements 9.4 — --allow-skip-module flag in blocking mode."""

    def test_blocking_with_allow_skip_exits_0(self, monkeypatch, tmp_path):
        """Blocking mode with --allow-skip-module exits 0 when NGINX_BIN unset."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        monkeypatch.setattr(evidence_gate, "REPO_ROOT", tmp_path)
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
        monkeypatch.setattr(evidence_gate, "REPO_ROOT", tmp_path)
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

    @pytest.mark.parametrize(
        "tag_ref",
        ["refs/tags/v0.9.1-rc.1", "refs/tags/v0.9.1"],
    )
    def test_release_tags_reject_allow_skip_module(
        self, monkeypatch, tag_ref
    ):
        """Development skip must never satisfy an RC or release-tag gate."""
        monkeypatch.delenv("NGINX_BIN", raising=False)
        monkeypatch.delenv("CI_COMMIT_TAG", raising=False)
        monkeypatch.delenv("RELEASE_VERSION", raising=False)
        monkeypatch.setenv("GITHUB_REF", tag_ref)

        exit_code = main([
            "--mode", "blocking",
            "--allow-skip-module",
        ])

        assert exit_code == 1

    def test_parse_args_allow_skip_module_default_false(self):
        """--allow-skip-module defaults to False when not provided."""
        args = parse_args(["--mode", "blocking"])
        assert args.allow_skip_module is False

    def test_parse_args_allow_skip_module_true(self):
        """--allow-skip-module is True when explicitly provided."""
        args = parse_args(["--mode", "blocking", "--allow-skip-module"])
        assert args.allow_skip_module is True


# ---------------------------------------------------------------------------
# Test: Evidence pack output validation
# ---------------------------------------------------------------------------


class TestEvidenceOutputValidation:
    """Output paths must stay within the repository tree."""

    def test_write_output_rejects_path_outside_repo(self, tmp_path):
        """Caller-controlled output path cannot escape the repo root."""
        outside_path = tmp_path / "evidence.json"
        output_path = str(outside_path)
        evidence = {"verdict": "GO"}

        with pytest.raises(ValueError):
            _write_output(evidence, output_path)


# ---------------------------------------------------------------------------
# Test: Release-candidate tag detection
# ---------------------------------------------------------------------------


class TestReleaseCandidateTagPattern:
    """Release-candidate tag matching stays bounded and explicit."""

    @pytest.mark.parametrize(
        "value",
        [
            "0.9.1-rc",
            "v0.9.1-rc",
            "0.9.1-rc.1",
            "refs/tags/v0.9.1-rc.1",
        ],
    )
    def test_rc_tag_pattern_accepts_supported_forms(self, value):
        """Supported release-candidate tag forms match."""
        assert _RC_RE.search(value)

    @pytest.mark.parametrize(
        "value",
        [
            "0.9.1",
            "v0.9.1",
            "refs/heads/dev/wip-0.9.1-rc.1",
            "refs/tags/v0.9.1-rc.1-extra",
        ],
    )
    def test_rc_tag_pattern_rejects_non_tags(self, value):
        """Non-RC and non-tag-like strings do not match."""
        assert _RC_RE.search(value) is None


class TestReleaseTagPattern:
    """Formal release tag matching for evidence gate enforcement."""

    @pytest.mark.parametrize(
        "value",
        [
            "0.9.1",
            "v0.9.1",
            "v0.8.3",
            "refs/tags/v0.9.1",
        ],
    )
    def test_release_tag_pattern_accepts_formal_tags(self, value):
        """Supported formal release tag forms match."""
        assert _RELEASE_TAG_RE.search(value)

    @pytest.mark.parametrize(
        "value",
        [
            "0.9.1-rc",
            "v0.9.1-rc.1",
            "refs/heads/dev/wip-0.9.1",
            "refs/tags/v0.9.1-rc.1",
            "0.9.1-rc.1-extra",
        ],
    )
    def test_release_tag_pattern_rejects_non_release(self, value):
        """RC tags and non-release strings do not match formal release pattern."""
        assert _RELEASE_TAG_RE.search(value) is None


class TestRCAndReleaseTagEnforcement:
    """Both RC and formal release tags must enforce evidence gate."""

    @pytest.mark.parametrize(
        "value",
        [
            "0.9.1-rc",
            "v0.9.1-rc",
            "0.9.1-rc.1",
            "refs/tags/v0.9.1-rc.1",
        ],
    )
    def test_rc_tag_rejected_by_release_tag_re(self, value):
        """RC tags are NOT matched by _RELEASE_TAG_RE."""
        assert _RELEASE_TAG_RE.search(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "0.9.1-rc",
            "v0.9.1-rc.1",
            "refs/tags/v0.9.1-rc.1",
            "0.9.1",
            "v0.9.1",
            "refs/tags/v0.9.1",
        ],
    )
    def test_rc_and_release_tags_match_rc_re(self, value):
        """RC tags must match _RC_RE, formal release tags must match _RELEASE_TAG_RE."""
        is_rc = _RC_RE.search(value) is not None
        is_release = _RELEASE_TAG_RE.search(value) is not None
        assert is_rc or is_release, f"tag {value} matched neither RC nor release pattern"


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
        """Computes fallback rate from the worst critical streaming scenario."""
        report = _make_benchmark_report(scenarios=[
            {
                "name": name,
                "metrics": {
                    "streaming_requests_total": requests,
                    "precommit_failopen_total": failopen,
                },
            }
            for name, failopen, requests in (
                ("streaming-first", 10, 1000),
                ("gzip-streaming-first", 5, 500),
                ("deflate-streaming-first", 1, 50),
            )
        ])
        metrics = _extract_evidence_metrics(report)

        expected_rate = 1 / 50
        assert metrics["fallback_rate_abs"] == pytest.approx(expected_rate, abs=1e-6)

    def test_empty_report_returns_zero_fallback(self):
        """Empty report produces zero fallback rate."""
        metrics = _extract_evidence_metrics({})
        assert metrics["fallback_rate_abs"] == 0.0

    @pytest.mark.parametrize(
        "degraded_scenario",
        ["gzip-streaming-first", "deflate-streaming-first"],
    )
    def test_codec_fallback_controls_absolute_rate(self, degraded_scenario):
        """Any critical streaming codec regression controls the gate rate."""
        scenarios = []
        for name in (
            "streaming-first",
            "gzip-streaming-first",
            "deflate-streaming-first",
        ):
            failopen = 100 if name == degraded_scenario else 0
            scenarios.append({
                "name": name,
                "metrics": {
                    "streaming_requests_total": 100,
                    "precommit_failopen_total": failopen,
                },
            })

        metrics = _extract_evidence_metrics({"scenarios": scenarios})

        assert metrics["fallback_rate_abs"] == 1.0
        result = evaluate_module_level(
            {**_make_passing_metrics(), **metrics},
            _make_baseline_metrics(),
            _make_module_thresholds_cfg(),
        )
        assert result["verdict"] == "NO_GO"

    def test_critical_streaming_fallback_uses_maximum_codec_rate(self):
        """Healthy critical scenarios expose their maximum real fail-open rate."""
        scenarios = [
            {
                "name": name,
                "metrics": {
                    "streaming_requests_total": 100,
                    "precommit_failopen_total": failopen,
                },
            }
            for name, failopen in (
                ("streaming-first", 0),
                ("gzip-streaming-first", 2),
                ("deflate-streaming-first", 4),
            )
        ]

        metrics = _extract_evidence_metrics({"scenarios": scenarios})

        assert metrics["fallback_rate_abs"] == pytest.approx(0.04)

    def test_missing_codec_fallback_counts_remain_missing_evidence(self):
        """Missing real fallback counters must not be interpreted as zero."""
        scenarios = [
            {
                "name": name,
                "metrics": {
                    "streaming_requests_total": 100,
                    **({} if name == "gzip-streaming-first" else {
                        "precommit_failopen_total": 0,
                    }),
                },
            }
            for name in (
                "streaming-first",
                "gzip-streaming-first",
                "deflate-streaming-first",
            )
        ]

        metrics = _extract_evidence_metrics({"scenarios": scenarios})

        assert "fallback_rate_abs" not in metrics

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


class TestSkippedCriticalScenarios:
    """Blocking mode must fail when critical scenarios are skipped."""

    def test_skipped_large_body_detected(self):
        """Skipped large-body scenario is detected."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "pass"},
                {"name": "large-body", "status": "skipped", "reason": "fixture_not_found"},
                {"name": "streaming-first", "status": "pass"},
            ]
        }
        skipped = _check_skipped_scenarios(report)
        assert len(skipped) == 1
        assert skipped[0][0] == "large-body"
        assert skipped[0][1] == "fixture_not_found"

    def test_all_pass_no_skipped(self):
        """No skipped critical scenarios returns empty list."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "pass"},
                {"name": "large-body", "status": "pass"},
                {"name": "streaming-first", "status": "pass"},
            ]
        }
        assert _check_skipped_scenarios(report) == []

    def test_non_critical_skipped_ignored(self):
        """Skipped non-critical scenarios are not reported."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "pass"},
                {"name": "chunked-medium", "status": "skipped", "reason": "fixture_not_found"},
                {"name": "large-body", "status": "pass"},
                {"name": "streaming-first", "status": "pass"},
            ]
        }
        assert _check_skipped_scenarios(report) == []

    def test_empty_report_no_skipped(self):
        """Empty report has no skipped scenarios."""
        assert _check_skipped_scenarios({}) == []


# ---------------------------------------------------------------------------
# Path-coverage invariants (Requirement: critical scenarios must actually
# exercise their target production paths, not just report "completed")
# ---------------------------------------------------------------------------

class TestPathCoverageInvariants:
    """Blocking mode must reject evidence when target paths were not hit."""

    def test_streaming_first_with_zero_streaming_ratio_is_violation(self):
        """streaming-first with streaming_ratio=0 and zero_copy=0 is rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                            "streaming_path_hits": 0,
                            "zero_copy_output_total": 0,
                            "copied_output_total": 0,
                        },
                    },
                ]
            }
        }
        violations = _check_path_coverage(report)
        assert len(violations) == 5
        names = {v[0] for v in violations}
        assert names == {"streaming-first"}

    def test_streaming_first_with_valid_path_is_not_violation(self):
        """streaming-first with streaming_ratio>0 and zero_copy>0 passes."""
        report = {
            "module_benchmark": {
                "scenarios": [
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "streaming_requests_total": 1000,
                            "zero_copy_output_total": 500,
                            "copied_output_total": 0,
                        },
                    },
                ]
            }
        }
        assert _check_path_coverage(report) == []

    def test_gzip_large_without_decompression_is_violation(self):
        """gzip-large must show decompression_fullbuffer_total > 0."""
        report = {
            "module_benchmark": {
                "scenarios": [
                    {
                        "name": "gzip-large",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "gzip",
                        "metrics": {
                            "decompression_fullbuffer_total": 0,
                            "fullbuffer_path_hits": 0,
                        },
                    },
                ]
            }
        }
        violations = _check_path_coverage(report)
        assert len(violations) == 2
        assert all(v[0] == "gzip-large" for v in violations)

    def test_gzip_large_with_decompression_is_not_violation(self):
        """gzip-large with decompression_fullbuffer_total > 0 passes."""
        report = {
            "module_benchmark": {
                "scenarios": [
                    {
                        "name": "gzip-large",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "gzip",
                        "metrics": {
                            "decompression_fullbuffer_total": 1030,
                            "fullbuffer_path_hits": 1030,
                        },
                    },
                ]
            }
        }
        assert _check_path_coverage(report) == []

    def test_skipped_scenario_not_checked_for_path_coverage(self):
        """Skipped scenarios are not subject to path-coverage checks."""
        report = {
            "module_benchmark": {
                "scenarios": [
                    {
                        "name": "streaming-first",
                        "status": "skipped",
                        "metrics": {},
                    },
                ]
            }
        }
        assert _check_path_coverage(report) == []

    def test_current_baseline_passes_compressed_path_coverage(self):
        """The regenerated baseline exercises every required module path."""
        baseline_path = (
            Path(__file__).resolve().parents[3]
            / "perf"
            / "baselines"
            / "module-baseline-091.json"
        )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert _check_path_coverage(baseline) == []


# ---------------------------------------------------------------------------
# Baseline evidence integrity validation (Requirement: baseline must pass
# the same integrity checks as the current report)
# ---------------------------------------------------------------------------

class TestBaselineEvidenceIntegrity:
    """Baselines must pass the same integrity checks as current evidence."""

    def test_current_baseline_passes_full_validation(self):
        """The regenerated baseline passes the blocking integrity contract."""
        baseline_path = (
            Path(__file__).resolve().parents[3]
            / "perf"
            / "baselines"
            / "module-baseline-091.json"
        )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert _validate_benchmark_evidence(
            baseline, role="baseline"
        ) == []

    def test_valid_baseline_requires_zero_canonical_failopen(self):
        """Canonical evidence passes at zero fail-open and rejects even 1%."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx/1.28.2",
                "platform": "linux-x86_64",
                "load_generator": "ab",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "none",
                        "transfer_encoding": "identity",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "none",
                        "transfer_encoding": "identity",
                        "metrics": {
                            "input_bytes": 200,
                            "baseline_rss_bytes": 2000,
                            "peak_rss_bytes": 2200,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "none",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 500,
                            "copied_output_total": 0,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3300,
                        },
                    },
                    {
                        "name": "gzip-large",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "gzip",
                        "transfer_encoding": "identity",
                        "metrics": {
                            "decompression_fullbuffer_total": 1030,
                            "fullbuffer_path_hits": 1030,
                            "input_bytes": 150,
                            "baseline_rss_bytes": 1500,
                            "peak_rss_bytes": 1700,
                        },
                    },
                    {
                        "name": "gzip-streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "gzip",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "decompression_streaming_total": 800,
                            "streaming_path_hits": 1000,
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 500,
                            "copied_output_total": 0,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3500,
                        },
                    },
                    {
                        "name": "deflate-streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "deflate",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "decompression_streaming_total": 800,
                            "streaming_path_hits": 1000,
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 500,
                            "copied_output_total": 0,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3500,
                        },
                    },
                ],
            }
        }
        assert _validate_benchmark_evidence(report, role="baseline") == []

        gzip_streaming = next(
            scenario
            for scenario in report["module_benchmark"]["scenarios"]
            if scenario["name"] == "gzip-streaming-first"
        )
        gzip_streaming["metrics"]["precommit_failopen_total"] = 10
        violations = _validate_benchmark_evidence(report, role="baseline")
        assert any(
            check == "baseline.fallback_rate"
            and "gzip-streaming-first" in reason
            for check, reason in violations
        )

    def test_unknown_nginx_version_is_rejected(self):
        """An 'unknown' nginx_version fails evidence validation."""
        report = {
            "module_benchmark": {
                "nginx_version": "unknown (placeholder)",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 200,
                            "baseline_rss_bytes": 2000,
                            "peak_rss_bytes": 2200,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "zero_copy_output_total": 500,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3300,
                        },
                    },
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        assert any("nginx_version" in v[0] for v in violations)


# ---------------------------------------------------------------------------
# Critical scenario completeness (P1-2): missing and incomplete scenarios
# ---------------------------------------------------------------------------

class TestCriticalScenarioCompleteness:
    """Critical scenarios must exist AND be completed — not just 'not skipped'."""

    def test_missing_large_body_detected(self):
        """A completely absent critical scenario is a violation."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
            ]
        }
        missing = _check_missing_scenarios(report)
        assert "large-body" in missing

    def test_present_but_not_completed_detected(self):
        """A critical scenario with status != completed is incomplete."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "error"},
                {"name": "streaming-first", "status": "completed"},
            ]
        }
        incomplete = _check_scenario_completion(report)
        assert any(name == "large-body" for name, _ in incomplete)

    def test_all_completed_no_incomplete(self):
        """All critical scenarios completed returns empty."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        assert _check_missing_scenarios(report) == []
        assert _check_scenario_completion(report) == []

    def test_validate_rejects_missing_scenario(self):
        """_validate_benchmark_evidence flags a missing critical scenario."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx/1.28.2",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "zero_copy_output_total": 500,
                            "input_bytes": 200,
                            "baseline_rss_bytes": 2000,
                            "peak_rss_bytes": 2200,
                        },
                    },
                    # large-body is MISSING
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        missing_violations = [
            v for v in violations if "missing critical" in v[1]
        ]
        assert missing_violations


# ---------------------------------------------------------------------------
# Memory evidence completeness (P2-1): >= 2 memory points required
# ---------------------------------------------------------------------------

class TestMemoryEvidenceCompleteness:
    """Blocking mode must reject reports with < 2 valid memory points."""

    def test_insufficient_memory_points_flagged(self):
        """A report with only 1 memory point fails validation."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx/1.28.2",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 200,
                            # No peak_rss_bytes → no memory point
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "zero_copy_output_total": 500,
                            "input_bytes": 300,
                            # No peak_rss_bytes → no memory point
                        },
                    },
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        memory_violations = [
            v for v in violations if "memory_evidence" in v[0]
        ]
        assert memory_violations

    def test_sufficient_memory_points_pass(self):
        """A report with >= 2 memory points passes the memory check."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx/1.28.2",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 200,
                            "baseline_rss_bytes": 2000,
                            "peak_rss_bytes": 2200,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "zero_copy_output_total": 500,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3300,
                        },
                    },
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        memory_violations = [
            v for v in violations if "memory_evidence" in v[0]
        ]
        assert not memory_violations


# ---------------------------------------------------------------------------
# Environment compatibility (P2-2): platform/load_generator/nginx_version match
# ---------------------------------------------------------------------------

class TestEnvironmentCompatibility:
    """Current and baseline environments must match for regression comparison."""

    def test_non_blocking_incompatible_baseline_is_not_compared(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Report-only runs expose incompatible percentage evidence as missing."""
        baseline_path = (
            tmp_path / "perf" / "baselines" / "module-baseline-091.json"
        )
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text(json.dumps({
            "module_benchmark": {
                "platform": "linux-x86_64",
                "load_generator": "ab",
                "nginx_version": "nginx version: nginx/1.24.0",
                "scenarios": [],
            },
        }), encoding="utf-8")
        current = {
            "module_benchmark": {
                "platform": "darwin-arm64",
                "load_generator": "hey",
                "nginx_version": "nginx version: nginx/1.28.2",
                "scenarios": [],
            },
        }

        monkeypatch.setattr(evidence_gate, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(
            evidence_gate, "_validate_baseline_evidence",
            lambda *_args: None,
        )

        output_path = tmp_path / "perf" / "reports" / "evidence-091.json"
        metrics, has_baseline, exit_rc = evidence_gate._resolve_baseline(
            current,
            parse_args(["--output", str(output_path)]),
            blocking=False,
        )

        assert metrics == {}
        assert has_baseline is False
        assert exit_rc == 0
        evidence = json.loads(output_path.read_text(encoding="utf-8"))
        assert evidence["verdict"] == "MISSING_EVIDENCE"
        assert any(
            breach["metric"] == "baseline.percentage_thresholds"
            and "cannot evaluate percentage thresholds" in breach["reason"]
            for breach in evidence["breaches"]
        )
        stderr = capsys.readouterr().err.lower()
        assert "incompatible" in stderr
        assert "percentage thresholds cannot be evaluated" in stderr

    def test_matching_environments_no_violations(self):
        """Same platform, load_generator, nginx_version → no violations."""
        current = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        baseline = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        assert _check_environment_compatibility(current, baseline) == []

    def test_changed_critical_fixture_size_is_rejected(self):
        """A same-name scenario cannot compare a different fixture payload."""
        current = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.24.0",
            "scenarios": [{
                "name": "streaming-first",
                "metrics": {"input_bytes": 1_048_516},
            }],
        }}
        baseline = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.24.0",
            "scenarios": [{
                "name": "streaming-first",
                "metrics": {"input_bytes": 5_390},
            }],
        }}

        violations = _check_environment_compatibility(current, baseline)

        assert violations == [(
            "scenario.streaming-first.input_bytes",
            "current=1048516 vs baseline=5390",
        )]

    def test_mismatched_platform_detected(self):
        """Different platform → violation."""
        current = {"module_benchmark": {
            "platform": "darwin-arm64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        baseline = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        violations = _check_environment_compatibility(current, baseline)
        assert any("platform" in v[0] for v in violations)

    def test_mismatched_load_generator_detected(self):
        """Different load_generator → violation."""
        current = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "hey",
            "nginx_version": "nginx/1.28.2",
        }}
        baseline = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        violations = _check_environment_compatibility(current, baseline)
        assert any("load_generator" in v[0] for v in violations)

    def test_mismatched_nginx_version_detected(self):
        """Different nginx_version → violation."""
        current = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.3",
        }}
        baseline = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        violations = _check_environment_compatibility(current, baseline)
        assert any("nginx_version" in v[0] for v in violations)

    def test_both_missing_platform_detected(self):
        """Both sides missing platform → violation (not silently passing)."""
        current = {"module_benchmark": {
            "platform": "",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        baseline = {"module_benchmark": {
            "platform": "",
            "load_generator": "ab",
            "nginx_version": "nginx/1.28.2",
        }}
        violations = _check_environment_compatibility(current, baseline)
        assert any("platform" in v[0] for v in violations)

    def test_both_missing_load_generator_detected(self):
        """Both sides missing load_generator → violation."""
        current = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "",
            "nginx_version": "nginx/1.28.2",
        }}
        baseline = {"module_benchmark": {
            "platform": "linux-x86_64",
            "load_generator": "",
            "nginx_version": "nginx/1.28.2",
        }}
        violations = _check_environment_compatibility(current, baseline)
        assert any("load_generator" in v[0] for v in violations)

    def test_validate_requires_platform_non_empty(self):
        """_validate_benchmark_evidence flags missing platform."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx/1.28.2",
                "platform": "",
                "load_generator": "ab",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 200,
                            "baseline_rss_bytes": 2000,
                            "peak_rss_bytes": 2200,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "zero_copy_output_total": 500,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3300,
                        },
                    },
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        platform_violations = [
            v for v in violations if "platform" in v[0]
        ]
        assert platform_violations

    def test_validate_requires_load_generator_non_empty(self):
        """_validate_benchmark_evidence flags missing load_generator."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx/1.28.2",
                "platform": "linux-x86_64",
                "load_generator": "",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 100,
                            "baseline_rss_bytes": 1000,
                            "peak_rss_bytes": 1100,
                            "streaming_ratio": 0.0,
                            "fullbuffer_ratio": 1.0,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "metrics": {
                            "input_bytes": 200,
                            "baseline_rss_bytes": 2000,
                            "peak_rss_bytes": 2200,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 0.8,
                            "fullbuffer_ratio": 0.2,
                            "streaming_path_hits": 820,
                            "zero_copy_output_total": 500,
                            "input_bytes": 300,
                            "baseline_rss_bytes": 3000,
                            "peak_rss_bytes": 3300,
                        },
                    },
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        load_generator_violations = [
            v for v in violations if "load_generator" in v[0]
        ]
        assert load_generator_violations


# ---------------------------------------------------------------------------
# Compressed streaming decompression evidence completeness (Finding 2)
# ---------------------------------------------------------------------------

class TestCompressedStreamingEvidenceCompleteness:
    """Critical compressed streaming scenarios must exist and be completed."""

    def test_missing_gzip_streaming_first_is_violation(self):
        """gzip-streaming-first absent from report → missing critical scenario."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        missing = _check_missing_scenarios(report)
        assert "gzip-streaming-first" in missing

    def test_missing_deflate_streaming_first_is_violation(self):
        """deflate-streaming-first absent from report → missing critical scenario."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "completed"},
            ]
        }
        missing = _check_missing_scenarios(report)
        assert "deflate-streaming-first" in missing

    def test_missing_gzip_large_is_violation(self):
        """gzip-large absent from report → missing critical scenario."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        missing = _check_missing_scenarios(report)
        assert "gzip-large" in missing

    def test_skipped_gzip_streaming_first_is_violation(self):
        """gzip-streaming-first skipped → blocking mode must reject."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "skipped", "reason": "fixture_not_found"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        skipped = _check_skipped_scenarios(report)
        assert any(name == "gzip-streaming-first" for name, _ in skipped)

    def test_skipped_deflate_streaming_first_is_violation(self):
        """deflate-streaming-first skipped → blocking mode must reject."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "skipped", "reason": "timeout"},
            ]
        }
        skipped = _check_skipped_scenarios(report)
        assert any(name == "deflate-streaming-first" for name, _ in skipped)

    def test_skipped_gzip_large_is_violation(self):
        """gzip-large skipped → blocking mode must reject."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "skipped", "reason": "fixture_not_found"},
                {"name": "gzip-streaming-first", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        skipped = _check_skipped_scenarios(report)
        assert any(name == "gzip-large" for name, _ in skipped)

    def test_incomplete_gzip_streaming_first_is_violation(self):
        """gzip-streaming-first with non-completed status → incomplete."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "error"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        incomplete = _check_scenario_completion(report)
        assert any(name == "gzip-streaming-first" for name, _ in incomplete)

    def test_incomplete_deflate_streaming_first_is_violation(self):
        """deflate-streaming-first with non-completed status → incomplete."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "error"},
            ]
        }
        incomplete = _check_scenario_completion(report)
        assert any(name == "deflate-streaming-first" for name, _ in incomplete)

    def test_all_critical_scenarios_completed_passes(self):
        """All six critical scenarios completed → no missing/incomplete."""
        report = {
            "scenarios": [
                {"name": "plain-small", "status": "completed"},
                {"name": "large-body", "status": "completed"},
                {"name": "streaming-first", "status": "completed"},
                {"name": "gzip-large", "status": "completed"},
                {"name": "gzip-streaming-first", "status": "completed"},
                {"name": "deflate-streaming-first", "status": "completed"},
            ]
        }
        assert _check_missing_scenarios(report) == []
        assert _check_scenario_completion(report) == []
        assert _check_skipped_scenarios(report) == []


# ---------------------------------------------------------------------------
# Compressed streaming path truthfulness (Finding 2)
# ---------------------------------------------------------------------------

class TestCompressedStreamingPathTruthfulness:
    """Compressed streaming scenarios must genuinely hit their production paths."""

    def test_gzip_streaming_first_zero_decompression_is_violation(self):
        """gzip-streaming-first with decompression_streaming_total == 0 → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "gzip-streaming-first",
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 0,
                        "streaming_path_hits": 500,
                        "streaming_ratio": 1.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "gzip-streaming-first" and "decompression_streaming_total" in v[1]
            for v in violations
        )

    def test_gzip_streaming_first_zero_streaming_hits_is_violation(self):
        """gzip-streaming-first with streaming_path_hits == 0 → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "gzip-streaming-first",
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 500,
                        "streaming_path_hits": 0,
                        "streaming_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "gzip-streaming-first" and "streaming_path_hits" in v[1]
            for v in violations
        )

    def test_gzip_streaming_first_zero_streaming_ratio_is_violation(self):
        """gzip-streaming-first with streaming_ratio == 0 → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "gzip-streaming-first",
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 500,
                        "streaming_path_hits": 500,
                        "streaming_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "gzip-streaming-first" and "streaming_ratio" in v[1]
            for v in violations
        )

    def test_gzip_streaming_first_fullbuffer_ratio_1_is_violation(self):
        """gzip-streaming-first: fullbuffer_ratio == 1 is a violation (all fell back)."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "gzip-streaming-first",
                    "status": "completed",
                    "profile": "streaming_first",
                    "compression": "gzip",
                    "transfer_encoding": "chunked",
                    "metrics": {
                        "decompression_streaming_total": 500,
                        "streaming_path_hits": 500,
                        "streaming_ratio": 0.8,
                        "fullbuffer_ratio": 1.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "gzip-streaming-first" and "fullbuffer_ratio" in v[1]
            for v in violations
        )

    def test_deflate_streaming_first_zero_decompression_is_violation(self):
        """deflate-streaming-first with decompression_streaming_total == 0 → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "deflate-streaming-first",
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 0,
                        "streaming_path_hits": 500,
                        "streaming_ratio": 1.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "deflate-streaming-first" and "decompression_streaming_total" in v[1]
            for v in violations
        )

    def test_deflate_streaming_first_zero_streaming_hits_is_violation(self):
        """deflate-streaming-first with streaming_path_hits == 0 → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "deflate-streaming-first",
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 500,
                        "streaming_path_hits": 0,
                        "streaming_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "deflate-streaming-first" and "streaming_path_hits" in v[1]
            for v in violations
        )

    def test_deflate_streaming_first_zero_streaming_ratio_is_violation(self):
        """deflate-streaming-first with streaming_ratio == 0 → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "deflate-streaming-first",
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 500,
                        "streaming_path_hits": 500,
                        "streaming_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "deflate-streaming-first" and "streaming_ratio" in v[1]
            for v in violations
        )

    @pytest.mark.parametrize(
        "scenario_name", ["gzip-streaming-first", "deflate-streaming-first"]
    )
    def test_compressed_streaming_zero_output_is_violation(self, scenario_name):
        """Codec path hits without delivered output are not credible evidence."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": scenario_name,
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 100,
                        "streaming_path_hits": 100,
                        "streaming_requests_total": 100,
                        "precommit_failopen_total": 0,
                        "streaming_ratio": 1.0,
                        "fullbuffer_ratio": 0.0,
                        "zero_copy_output_total": 0,
                        "copied_output_total": 0,
                    },
                }]
            }
        }

        violations = _check_path_coverage(report)

        assert any(
            v[0] == scenario_name and v[1] == "output_total"
            for v in violations
        )

    @pytest.mark.parametrize(
        "scenario_name", ["gzip-streaming-first", "deflate-streaming-first"]
    )
    def test_compressed_streaming_all_failopen_is_violation(self, scenario_name):
        """A codec scenario with 100 percent precommit fail-open is invalid."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": scenario_name,
                    "status": "completed",
                    "metrics": {
                        "decompression_streaming_total": 100,
                        "streaming_path_hits": 100,
                        "streaming_requests_total": 100,
                        "precommit_failopen_total": 100,
                        "streaming_ratio": 1.0,
                        "fullbuffer_ratio": 0.0,
                        "zero_copy_output_total": 1,
                        "copied_output_total": 0,
                    },
                }]
            }
        }

        violations = _check_path_coverage(report)

        assert any(
            v[0] == scenario_name and v[1] == "fallback_rate"
            for v in violations
        )

    def test_all_compressed_streaming_paths_genuinely_hit_passes(self):
        """Valid report with genuine path hits for all compressed scenarios passes."""
        report = {
            "module_benchmark": {
                "scenarios": [
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "metrics": {
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_path_hits": 1000,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 5000,
                            "copied_output_total": 0,
                        },
                    },
                    {
                        "name": "gzip-large",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "gzip",
                        "metrics": {
                            "decompression_fullbuffer_total": 1030,
                            "fullbuffer_path_hits": 1030,
                        },
                    },
                    {
                        "name": "gzip-streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "gzip",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "decompression_streaming_total": 800,
                            "streaming_path_hits": 1000,
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 5000,
                            "copied_output_total": 0,
                        },
                    },
                    {
                        "name": "deflate-streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "deflate",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "decompression_streaming_total": 800,
                            "streaming_path_hits": 1000,
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 5000,
                            "copied_output_total": 0,
                        },
                    },
                ]
            }
        }
        violations = _check_path_coverage(report)
        assert violations == []

    def test_gzip_streaming_first_wrong_profile_is_violation(self):
        """gzip-streaming-first with wrong profile metadata → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "gzip-streaming-first",
                    "status": "completed",
                    "profile": "balanced",
                    "compression": "gzip",
                    "transfer_encoding": "chunked",
                    "metrics": {
                        "decompression_streaming_total": 800,
                        "streaming_path_hits": 1000,
                        "streaming_ratio": 1.0,
                        "fullbuffer_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "gzip-streaming-first" and v[1] == "profile"
            for v in violations
        )

    def test_gzip_streaming_first_wrong_compression_is_violation(self):
        """gzip-streaming-first with wrong compression metadata → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "gzip-streaming-first",
                    "status": "completed",
                    "profile": "streaming_first",
                    "compression": "deflate",
                    "transfer_encoding": "chunked",
                    "metrics": {
                        "decompression_streaming_total": 800,
                        "streaming_path_hits": 1000,
                        "streaming_ratio": 1.0,
                        "fullbuffer_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "gzip-streaming-first" and v[1] == "compression"
            for v in violations
        )

    def test_deflate_streaming_first_wrong_transfer_encoding_is_violation(self):
        """deflate-streaming-first with wrong transfer_encoding → rejected."""
        report = {
            "module_benchmark": {
                "scenarios": [{
                    "name": "deflate-streaming-first",
                    "status": "completed",
                    "profile": "streaming_first",
                    "compression": "deflate",
                    "transfer_encoding": "identity",
                    "metrics": {
                        "decompression_streaming_total": 800,
                        "streaming_path_hits": 1000,
                        "streaming_ratio": 1.0,
                        "fullbuffer_ratio": 0.0,
                    },
                }]
            }
        }
        violations = _check_path_coverage(report)
        assert any(
            v[0] == "deflate-streaming-first" and v[1] == "transfer_encoding"
            for v in violations
        )

    def test_validate_benchmark_evidence_with_full_report_passes(self):
        """A complete report with all critical scenarios and genuine paths passes."""
        report = {
            "module_benchmark": {
                "nginx_version": "nginx version: nginx/1.24.0",
                "platform": "linux-x86_64",
                "load_generator": "hey",
                "scenarios": [
                    {
                        "name": "plain-small",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "none",
                        "transfer_encoding": "identity",
                        "metrics": {
                            "input_bytes": 5390,
                            "baseline_rss_bytes": 10_000_000,
                            "peak_rss_bytes": 11_000_000,
                        },
                    },
                    {
                        "name": "large-body",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "none",
                        "transfer_encoding": "identity",
                        "metrics": {
                            "input_bytes": 1_048_516,
                            "baseline_rss_bytes": 20_000_000,
                            "peak_rss_bytes": 25_000_000,
                        },
                    },
                    {
                        "name": "streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "none",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_path_hits": 1000,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 5000,
                            "copied_output_total": 0,
                            "input_bytes": 1_048_516,
                            "baseline_rss_bytes": 22_000_000,
                            "peak_rss_bytes": 28_000_000,
                        },
                    },
                    {
                        "name": "gzip-large",
                        "status": "completed",
                        "profile": "balanced",
                        "compression": "gzip",
                        "transfer_encoding": "identity",
                        "metrics": {
                            "decompression_fullbuffer_total": 1030,
                            "fullbuffer_path_hits": 1030,
                            "input_bytes": 30_000,
                            "baseline_rss_bytes": 10_000_000,
                            "peak_rss_bytes": 12_000_000,
                        },
                    },
                    {
                        "name": "gzip-streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "gzip",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "decompression_streaming_total": 800,
                            "streaming_path_hits": 1000,
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 5000,
                            "copied_output_total": 0,
                            "input_bytes": 1_048_516,
                            "baseline_rss_bytes": 22_000_000,
                            "peak_rss_bytes": 29_000_000,
                        },
                    },
                    {
                        "name": "deflate-streaming-first",
                        "status": "completed",
                        "profile": "streaming_first",
                        "compression": "deflate",
                        "transfer_encoding": "chunked",
                        "metrics": {
                            "decompression_streaming_total": 800,
                            "streaming_path_hits": 1000,
                            "streaming_ratio": 1.0,
                            "fullbuffer_ratio": 0.0,
                            "streaming_requests_total": 1000,
                            "precommit_failopen_total": 0,
                            "zero_copy_output_total": 5000,
                            "copied_output_total": 0,
                            "input_bytes": 1_048_516,
                            "baseline_rss_bytes": 22_000_000,
                            "peak_rss_bytes": 29_000_000,
                        },
                    },
                ],
            }
        }
        violations = _validate_benchmark_evidence(report, role="current")
        assert violations == []
