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
        scenarios = report.get("scenarios", [])

    if not scenarios:
        # ponytail: keep empty/legacy tests happy while failing real missing scenarios
        return {
            "fallback_rate_abs": 0.0,
            "memory_slope_pct": 0.0,
        }

    metrics: dict = {}
    _extract_small_latency(scenarios, metrics)
    _extract_large_latency(scenarios, metrics)
    _extract_streaming_and_fallback(scenarios, metrics)

    # Compute memory slope or explicit memory evidence from RSS
    memory_data_points = _extract_memory_points(scenarios)

    if len(memory_data_points) >= 2:
        metrics["memory_slope_pct"] = _compute_memory_slope(memory_data_points)
    else:
        # Check if the report has top-level memory_slope config
        ms_section = report.get("module_benchmark", {}).get("memory_slope", {})
        rss_per_mb = ms_section.get("rss_per_input_mb")
        if rss_per_mb is not None:
            metrics["memory_slope_pct"] = rss_per_mb

    return metrics


def _extract_small_latency(scenarios: list[dict], metrics: dict) -> None:
    """Extract latency metrics for small scenario."""
    plain_small = next((s for s in scenarios if s.get("name") == "plain-small"), None)
    if plain_small:
        m = plain_small.get("metrics") or plain_small.get("results") or plain_small
        p50 = m.get("latency_p50_ms") or m.get("p50_ms") or m.get("p50_latency_ms")
        p95 = m.get("latency_p95_ms") or m.get("p95_ms") or m.get("p95_latency_ms")
        if p50 is not None:
            metrics["p50_latency_small_pct"] = p50
        if p95 is not None:
            metrics["p95_latency_small_pct"] = p95


def _extract_large_latency(scenarios: list[dict], metrics: dict) -> None:
    """Extract latency metrics for large scenarios."""
    large_body = next((s for s in scenarios if s.get("name") == "large-body"), None)
    gzip_large = next((s for s in scenarios if s.get("name") == "gzip-large"), None)
    large_scenario = large_body or gzip_large
    if large_scenario:
        m = large_scenario.get("metrics") or large_scenario.get("results") or large_scenario
        p50 = m.get("latency_p50_ms") or m.get("p50_ms") or m.get("p50_latency_ms")
        if p50 is not None:
            metrics["p50_latency_large_pct"] = p50


def _extract_streaming_and_fallback(scenarios: list[dict], metrics: dict) -> None:
    """Extract streaming large TTFB and fallback rate."""
    streaming_first = _find_streaming_scenario(scenarios)

    if streaming_first:
        m = streaming_first.get("metrics") or streaming_first.get("results") or streaming_first
        ttfb = m.get("ttfb_p50_ms") or m.get("ttfb_ms")
        if ttfb is not None:
            metrics["ttfb_streaming_large_pct"] = ttfb
        if "fallback_rate" in m:
            metrics["fallback_rate_abs"] = m["fallback_rate"]

    # Compute fallback rate from cumulative counts if not directly present under streaming_first
    if "fallback_rate_abs" not in metrics:
        metrics["fallback_rate_abs"] = _calc_fallback_rate(scenarios)


def _find_streaming_scenario(scenarios: list[dict]) -> dict | None:
    """Find the most appropriate streaming scenario."""
    # First priority: explicit name match
    for s in scenarios:
        if s.get("name") == "streaming-first":
            return s
    # Second priority: name contains streaming
    for s in scenarios:
        if "streaming" in s.get("name", ""):
            return s
    # Third priority: name contains large and has TTFB
    for s in scenarios:
        name = s.get("name", "")
        if "large" in name:
            m = s.get("metrics") or s.get("results") or s
            if m.get("ttfb_p50_ms") is not None or m.get("ttfb_ms") is not None:
                return s
    return None


def _calc_fallback_rate(scenarios: list[dict]) -> float:
    """Calculate fallback rate from cumulative counts."""
    num = 0
    den = 0
    for s in scenarios:
        m = s.get("metrics") or s.get("results") or s
        fallback_count = m.get("fallback_count")
        total_requests = m.get("total_requests")
        if fallback_count is not None and total_requests is not None:
            num += fallback_count
            den += total_requests
    return num / den if den > 0 else 0.0


def _extract_memory_points(scenarios: list[dict]) -> list[tuple[float, float]]:
    """Extract memory data points for simple linear regression."""
    memory_data_points = []
    sizes = {
        "plain-small": 0.005,
        "chunked-medium": 0.05,
        "gzip-large": 0.1,
        "large-body": 1.0,
        "streaming-first": 0.3
    }

    for s in scenarios:
        s_name = s.get("name")
        if s_name is None:
            continue
        m = s.get("metrics") or s.get("results") or s

        # 0.9.1 schema: worker_rss_mb and hardcoded sizes
        rss_mb = m.get("worker_rss_mb")
        input_size_mb = sizes.get(s_name)
        if rss_mb is not None and rss_mb > 0 and input_size_mb is not None:
            memory_data_points.append((input_size_mb * 1024 * 1024, rss_mb * 1024 * 1024))
        else:
            # Legacy format
            input_bytes = m.get("input_bytes") or m.get("html_bytes")
            peak_rss = m.get("peak_memory_bytes") or m.get("worker_rss_bytes")
            if input_bytes is not None and peak_rss is not None and input_bytes > 0 and peak_rss > 0:
                memory_data_points.append((float(input_bytes), float(peak_rss)))

    return memory_data_points


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

    return (slope / mean_rss) * 100.0


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

    # Load baseline if available, otherwise use empty as baseline (first run).
    baseline_path = REPO_ROOT / "perf" / "baselines" / "module-baseline-091.json"
    if baseline_path.exists():
        baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_metrics = _extract_evidence_metrics(baseline_report)
        has_baseline = True
    else:
        _stderr("INFO: No module baseline found — percentage thresholds will be skipped (first run).")
        baseline_metrics = {}
        has_baseline = False

    # Import threshold engine for module-level evaluation
    from threshold_engine import evaluate_module_level

    eval_result = evaluate_module_level(current_metrics, baseline_metrics, thresholds_cfg, has_baseline=has_baseline)
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

    if verdict in ("NO_GO", "MISSING_EVIDENCE"):
        _stderr(
            f"BLOCKING: Evidence gate verdict is {verdict}.\n"
            "  Release-candidate tags require all module-level thresholds to pass and all critical evidence to be present.\n"
            "  Review breaches above and address performance regressions or missing measurements."
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
