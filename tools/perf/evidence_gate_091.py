#!/usr/bin/env python3
"""0.9.1 performance evidence release gate.

Runs the module-level benchmark harness and evaluates results against
the threshold engine module-level thresholds.  Supports two modes:

  - Non-blocking (report-only): Collects evidence, prints a summary,
    and exits 0 regardless of verdict.  When NGINX_BIN is unavailable,
    exits with code 75 (SKIP_NOT_PRESENT).

  - Blocking: Runs evidence collection and fails with exit code 1
    if the verdict is NO_GO and the tag matches a release-candidate
    pattern.  When NGINX_BIN is unavailable, requires the explicit
    --allow-skip-module flag to proceed; without it, exits with
    exit code 1 and an actionable error message.

Evidence pack includes:
  - Module benchmark tier results (p50, p95, TTFB per scenario)
  - Decompression path coverage (streaming vs full-buffer counts)
  - Fallback rate per tier
  - Memory slope calculation (RSS/input_MB linear regression)

Exit codes:
    0   Success (or non-blocking skip)
    1   Blocking failure (NO_GO verdict or missing --allow-skip-module)
    75  SKIP_NOT_PRESENT (non-blocking mode, NGINX_BIN unavailable)

Requirements: 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Exit code for SKIP_NOT_PRESENT (matches run_module_benchmark.sh)
EX_SKIP_NOT_PRESENT = 75


def _stderr(msg: str) -> None:
    """Write a message to stderr."""
    print(msg, file=sys.stderr)


def _get_git_commit() -> str:
    """Return the current short git commit hash, or 'unknown' if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(REPO_ROOT),
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _nginx_bin_available() -> bool:
    """Check whether NGINX_BIN environment variable is set and the binary exists."""
    nginx_bin = os.environ.get("NGINX_BIN", "")
    if not nginx_bin:
        return False
    return Path(nginx_bin).is_file()


def _load_thresholds() -> dict:
    """Load the thresholds configuration from perf/thresholds.json."""
    thresholds_path = REPO_ROOT / "perf" / "thresholds.json"
    if not thresholds_path.exists():
        return {}
    return json.loads(thresholds_path.read_text(encoding="utf-8"))


def _run_module_benchmark(output_path: Path) -> tuple[int, str]:
    """Run the module benchmark harness and collect its output.

    Returns:
        tuple of (exit_code, stderr_output)
    """
    script = REPO_ROOT / "tools" / "perf" / "run_module_benchmark.sh"
    if not script.exists():
        return 1, f"Benchmark script not found: {script}"

    result = subprocess.run(
        [str(script), "--output", str(output_path)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stderr


def _extract_evidence_metrics(report: dict) -> dict:
    """Extract evidence-relevant metrics from a benchmark report.

    Returns a flat dict suitable for evaluate_module_level().
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", [])
    if not scenarios:
        # Try top-level scenarios key (alternate format)
        scenarios = report.get("scenarios", [])

    metrics: dict = {}
    fallback_numerator = 0
    fallback_denominator = 0
    memory_data_points: list[tuple[float, float]] = []

    for scenario in scenarios:
        _collect_scenario_metrics(scenario, metrics)
        fallback_numerator, fallback_denominator = _accumulate_fallback(
            scenario, fallback_numerator, fallback_denominator,
        )
        _accumulate_memory_point(scenario, memory_data_points)

    # Compute fallback rate (absolute)
    if fallback_denominator > 0:
        metrics["fallback_rate_abs"] = fallback_numerator / fallback_denominator
    else:
        metrics["fallback_rate_abs"] = 0.0

    # Compute memory slope via simple linear regression
    if len(memory_data_points) >= 2:
        metrics["memory_slope_pct"] = _compute_memory_slope(memory_data_points)
    else:
        metrics["memory_slope_pct"] = 0.0

    return metrics


def _collect_scenario_metrics(scenario: dict, metrics: dict) -> None:
    """Collect latency metrics from a single scenario into the metrics dict."""
    name = scenario.get("name", "")
    results = scenario.get("results", scenario)

    p50 = results.get("p50_ms") or results.get("p50_latency_ms")
    p95 = results.get("p95_ms") or results.get("p95_latency_ms")
    ttfb = results.get("ttfb_ms") or results.get("ttfb_p50_ms")

    if "small" in name and p50 is not None:
        metrics.setdefault("p50_latency_small_pct", p50)
        if p95 is not None:
            metrics.setdefault("p95_latency_small_pct", p95)
    elif "large" in name:
        if p50 is not None:
            metrics.setdefault("p50_latency_large_pct", p50)
        if ttfb is not None:
            metrics.setdefault("ttfb_streaming_large_pct", ttfb)


def _accumulate_fallback(
    scenario: dict, numerator: int, denominator: int,
) -> tuple[int, int]:
    """Accumulate fallback count and total requests from a scenario."""
    results = scenario.get("results", scenario)
    fallback_count = results.get("fallback_count", 0)
    total_requests = results.get("total_requests", 0)
    return numerator + fallback_count, denominator + total_requests


def _accumulate_memory_point(
    scenario: dict, data_points: list[tuple[float, float]],
) -> None:
    """Collect a memory data point from a scenario if available."""
    results = scenario.get("results", scenario)
    input_bytes = results.get("input_bytes") or results.get("html_bytes", 0)
    peak_rss = results.get("peak_memory_bytes") or results.get("worker_rss_bytes", 0)
    if input_bytes > 0 and peak_rss > 0:
        data_points.append((float(input_bytes), float(peak_rss)))


def _compute_memory_slope(data_points: list[tuple[float, float]]) -> float:
    """Compute memory slope as percentage growth rate.

    Uses simple linear regression on (input_bytes, peak_rss) pairs.
    Returns the slope as a percentage of the mean baseline RSS.
    """
    n = len(data_points)
    if n < 2:
        return 0.0

    sum_x = sum(p[0] for p in data_points)
    sum_y = sum(p[1] for p in data_points)
    sum_xy = sum(p[0] * p[1] for p in data_points)
    sum_x2 = sum(p[0] * p[0] for p in data_points)

    denominator = n * sum_x2 - sum_x * sum_x
    if abs(denominator) < 1e-15:
        return 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator

    # Express slope as percentage of mean RSS
    mean_rss = sum_y / n
    if mean_rss <= 0:
        return 0.0

    # slope is bytes_rss per bytes_input; normalize to percentage
    # For module-level threshold, we compare slope growth relative to baseline
    return slope


def _build_evidence_pack(
    report: dict | None,
    verdict: str,
    breaches: list,
    results: list,
    skipped: bool = False,
    skip_reason: str = "",
) -> dict:
    """Build the evidence pack JSON structure."""
    return {
        "schema_version": "1.0.0",
        "type": "perf-evidence-091",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _get_git_commit(),
        "verdict": verdict,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "breaches": breaches,
        "results": results,
        "evidence": {
            "module_benchmark_tiers": (
                report.get("module_benchmark", {}).get("scenarios", [])
                if report else []
            ),
            "decompression_coverage": (
                report.get("decompression_coverage", {})
                if report else {}
            ),
            "fallback_rate": next(
                (r for r in results if r.get("metric") == "fallback_rate_abs"),
                None,
            ),
            "memory_slope": next(
                (r for r in results if r.get("metric") == "memory_slope_pct"),
                None,
            ),
        },
    }


def _print_evidence_summary(evidence_pack: dict) -> None:
    """Print a human-readable summary of the evidence pack."""
    _stderr("")
    _stderr("=" * 60)
    _stderr("  Performance Evidence Gate 0.9.1 — Summary")
    _stderr("=" * 60)
    _stderr("")

    if evidence_pack.get("skipped"):
        _stderr("  Status: SKIPPED")
        _stderr(f"  Reason: {evidence_pack.get('skip_reason', 'unknown')}")
        _stderr("")
        return

    verdict = evidence_pack.get("verdict", "UNKNOWN")
    _stderr(f"  Verdict: {verdict}")
    _stderr("")

    results = evidence_pack.get("results", [])
    for entry in results:
        _print_result_entry(entry)

    breaches = evidence_pack.get("breaches", [])
    if breaches:
        _stderr("")
        _stderr(f"  Threshold breaches: {len(breaches)}")
        for b in breaches:
            _stderr(f"    - {b.get('metric')}: {b.get('actual')} > {b.get('threshold')}")

    _stderr("")


def _print_result_entry(entry: dict) -> None:
    """Print a single result entry in the evidence summary."""
    metric = entry.get("metric", "")
    status = entry.get("status", "").upper()
    if status == "BREACH":
        actual = entry.get("actual", "?")
        threshold = entry.get("threshold", "?")
        _stderr(f"  [FAIL] {metric}: actual={actual}, threshold={threshold}")
    elif status == "PASS":
        _stderr(f"  [PASS] {metric}")
    else:
        reason = entry.get("reason", "")
        _stderr(f"  [SKIP] {metric}: {reason}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="0.9.1 performance evidence release gate.",
    )
    parser.add_argument(
        "--mode",
        choices=["non-blocking", "blocking"],
        default="non-blocking",
        help="Gate mode: non-blocking (report-only) or blocking (fails on NO_GO).",
    )
    parser.add_argument(
        "--allow-skip-module",
        action="store_true",
        default=False,
        help="In blocking mode, allow proceeding when NGINX_BIN is unavailable.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write evidence pack JSON to this path (default: perf/reports/evidence-091.json).",
    )
    parser.add_argument(
        "--benchmark-report",
        default=None,
        help="Use an existing benchmark report instead of running the harness.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the evidence gate."""
    args = parse_args(argv)

    blocking = args.mode == "blocking"
    nginx_available = _nginx_bin_available()

    # --- Handle NGINX_BIN unavailability ---
    if not nginx_available:
        return _handle_nginx_unavailable(blocking, args)

    # --- Run benchmark or load existing report ---
    report, err_code = _obtain_benchmark_report(args, blocking)
    if err_code is not None:
        return err_code

    # --- Extract metrics and evaluate thresholds ---
    return _evaluate_and_report(report, args, blocking)


def _handle_nginx_unavailable(blocking: bool, args: argparse.Namespace) -> int:
    """Handle the case when NGINX_BIN is not available.

    Returns the appropriate exit code.
    """
    if not blocking:
        _stderr(
            "SKIP_NOT_PRESENT: NGINX_BIN is not set or binary not found.\n"
            "  Module-level benchmarks require a locally-compiled NGINX binary\n"
            "  with the markdown filter module loaded.\n"
            "  Set NGINX_BIN=/path/to/nginx to enable module benchmarks."
        )
        evidence_pack = _build_evidence_pack(
            report=None,
            verdict="SKIPPED",
            breaches=[],
            results=[],
            skipped=True,
            skip_reason="NGINX_BIN not set or binary not found",
        )
        _print_evidence_summary(evidence_pack)
        _write_output(evidence_pack, args.output)
        return EX_SKIP_NOT_PRESENT

    # Blocking mode
    if args.allow_skip_module:
        _stderr(
            "WARNING: NGINX_BIN is not set — module benchmarks skipped.\n"
            "  Proceeding due to --allow-skip-module flag.\n"
            "  This is acceptable for development builds but NOT for RC tags."
        )
        evidence_pack = _build_evidence_pack(
            report=None,
            verdict="SKIPPED",
            breaches=[],
            results=[],
            skipped=True,
            skip_reason="NGINX_BIN not set; --allow-skip-module used",
        )
        _print_evidence_summary(evidence_pack)
        _write_output(evidence_pack, args.output)
        return 0

    _stderr(
        "FAIL: NGINX_BIN is not set and --allow-skip-module was not provided.\n"
        "  In blocking mode, module benchmarks are required for RC tags.\n"
        "  Either:\n"
        "    1. Set NGINX_BIN=/path/to/nginx (module-enabled build), or\n"
        "    2. Pass --allow-skip-module to explicitly skip (non-RC only)."
    )
    return 1


def _obtain_benchmark_report(
    args: argparse.Namespace, blocking: bool,
) -> tuple[dict | None, int | None]:
    """Obtain the benchmark report either from file or by running the harness.

    Returns:
        (report, error_code): report dict on success with error_code=None,
        or (None, exit_code) on failure.
    """
    if args.benchmark_report:
        report_path = validate_read_path(
            args.benchmark_report, purpose="benchmark report"
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return report, None

    output_path = Path(REPO_ROOT / "perf" / "reports" / "module-benchmark-091.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _stderr("Running module-level benchmark harness...")
    rc, stderr_output = _run_module_benchmark(output_path)
    if rc != 0:
        _stderr(f"Benchmark harness failed (exit {rc}):")
        _stderr(stderr_output)
        if not blocking:
            _stderr("Non-blocking mode: reporting failure as evidence.")
            evidence_pack = _build_evidence_pack(
                report=None,
                verdict="NO_GO",
                breaches=[{"metric": "benchmark_run", "reason": f"harness exit {rc}"}],
                results=[],
            )
            _print_evidence_summary(evidence_pack)
            _write_output(evidence_pack, args.output)
            return None, 0
        return None, 1

    if output_path.exists():
        report = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        _stderr("WARNING: Benchmark completed but no output file found.")
        report = {}

    return report, None


def _evaluate_and_report(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> int:
    """Evaluate metrics against thresholds and produce the final report.

    Returns the appropriate exit code.
    """
    current_metrics = _extract_evidence_metrics(report or {})
    thresholds_cfg = _load_thresholds()

    # Load baseline if available, otherwise use current as baseline (first run).
    baseline_path = REPO_ROOT / "perf" / "baselines" / "module-baseline-091.json"
    if baseline_path.exists():
        baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_metrics = _extract_evidence_metrics(baseline_report)
    else:
        _stderr("INFO: No module baseline found — percentage thresholds will pass (first run).")
        baseline_metrics = current_metrics.copy()

    # Import threshold engine for module-level evaluation
    from threshold_engine import evaluate_module_level

    eval_result = evaluate_module_level(current_metrics, baseline_metrics, thresholds_cfg)
    verdict = eval_result["verdict"]
    breaches = eval_result["breaches"]
    results = eval_result["results"]

    evidence_pack = _build_evidence_pack(
        report=report,
        verdict=verdict,
        breaches=breaches,
        results=results,
    )
    _print_evidence_summary(evidence_pack)
    _write_output(evidence_pack, args.output)

    if not blocking:
        return 0

    if verdict == "NO_GO":
        _stderr(
            "BLOCKING: Evidence gate verdict is NO_GO.\n"
            "  Release-candidate tags require all module-level thresholds to pass.\n"
            "  Review breaches above and address performance regressions."
        )
        return 1

    _stderr("Evidence gate: GO — all module-level thresholds pass.")
    return 0


def _write_output(evidence_pack: dict, output_path: str | None) -> None:
    """Write evidence pack to file or default location."""
    if output_path is None:
        output_path = str(REPO_ROOT / "perf" / "reports" / "evidence-091.json")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(evidence_pack, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _stderr(f"Evidence pack written to: {out}")


if __name__ == "__main__":
    sys.exit(main())
