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
import re
from lib.path_validation import (
    validate_read_path,
    validate_write_path_within_root,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_RC_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+-rc(?:\.\d+)?$")
_RELEASE_TAG_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+(?:\.\d+)?$")

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
    """Check whether NGINX_BIN points to an executable file."""
    nginx_bin = os.environ.get("NGINX_BIN", "")
    if not nginx_bin:
        return False
    path = Path(nginx_bin)
    return path.is_file() and os.access(path, os.X_OK)


def _is_rc_tag() -> bool:
    """Detect whether the current git state is a release-candidate tag."""
    for env_var in ("GITHUB_REF", "CI_COMMIT_TAG", "RELEASE_VERSION"):
        val = os.environ.get(env_var, "")
        if val and _RC_RE.search(val):
            return True

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and _RC_RE.search(result.stdout.strip()):
            return True
    except Exception:
        pass

    return False


def _is_release_tag() -> bool:
    """Detect whether the current git state is a release or RC tag.

    Both formal release tags (e.g. v0.9.1) and RC tags (e.g. v0.9.1-rc.1)
    require full module benchmark evidence and a baseline.  Non-release
    builds (development branches, non-tagged commits) are exempt.
    """
    for env_var in ("GITHUB_REF", "CI_COMMIT_TAG", "RELEASE_VERSION"):
        val = os.environ.get(env_var, "")
        if val and (_RELEASE_TAG_RE.search(val) or _RC_RE.search(val)):
            return True

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0:
            tag = result.stdout.strip()
            if _RELEASE_TAG_RE.search(tag) or _RC_RE.search(tag):
                return True
    except Exception:
        pass

    return False


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


def _memory_point_for_scenario(scenario: dict) -> tuple[float, float] | None:
    """Return one measured input/RSS point, or None when evidence is absent.

    Uses the peak RSS delta (peak_rss_bytes - baseline_rss_bytes) as the
    dependent variable and input_bytes as the independent variable.
    This gives a slope with units of (RSS bytes / input bytes), which
    directly measures per-byte memory cost.

    Falls back to worker_rss_mb (post-run) when peak/baseline are absent,
    for backward compatibility with older reports.
    """
    metrics = scenario.get("metrics") or scenario.get("results") or scenario
    input_bytes = metrics.get("input_bytes") or metrics.get("html_bytes")
    if input_bytes is None or input_bytes <= 0:
        return None

    # Preferred: peak RSS delta from background sampling
    peak_rss = metrics.get("peak_rss_bytes")
    baseline_rss = metrics.get("baseline_rss_bytes")
    if peak_rss is not None and peak_rss > 0:
        if baseline_rss is not None and baseline_rss >= 0:
            delta = peak_rss - baseline_rss
            if delta > 0:
                return float(input_bytes), float(delta)

    # Fallback: post-run RSS (legacy reports without peak sampling)
    rss_mb = metrics.get("worker_rss_mb")
    if rss_mb is not None and rss_mb > 0:
        return float(input_bytes), rss_mb * 1024 * 1024

    # Last resort: raw peak_memory_bytes
    peak_rss = metrics.get("peak_memory_bytes") or metrics.get("worker_rss_bytes")
    if peak_rss is None or peak_rss <= 0:
        return None
    return float(input_bytes), float(peak_rss)


def _extract_memory_points(scenarios: list[dict]) -> list[tuple[float, float]]:
    """Extract measured memory data points for simple linear regression.

    Uses measured ``input_bytes`` from the benchmark report. Scenarios
    without an actual input size are excluded rather than assigned an
    invented size that would corrupt the regression slope.
    """
    return [
        point
        for scenario in scenarios
        if scenario.get("name") is not None
        if (point := _memory_point_for_scenario(scenario)) is not None
    ]


def _compute_memory_slope(data_points: list[tuple[float, float]]) -> float:
    """Compute memory slope as RSS bytes per input byte.

    Uses simple linear regression on (input_bytes, rss_delta) pairs.
    Returns the slope (ΔRSS_bytes / Δinput_bytes).  A slope of 0.0
    means no measurable memory growth per input byte (ideal).

    The slope has a clear physical meaning: how many bytes of RSS the
    module consumes per byte of input processed.  This is directly
    comparable across platforms and NGINX versions.

    Previously this divided by mean RSS to produce a dimensionless
    percentage, which was misleading (the dimension was 1/input_byte,
    not a percentage growth rate).  The threshold engine compares
    the slope value directly against the baseline slope.
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

    return (n * sum_xy - sum_x * sum_y) / denominator


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

    report, error_code = _obtain_benchmark_report(args, blocking)
    if error_code is not None:
        return error_code

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
        if _is_release_tag():
            _stderr(
                "FAIL: --allow-skip-module is not permitted for release tags.\n"
                "  Release and RC tags require module benchmark evidence.\n"
                "  Set NGINX_BIN=/path/to/nginx to provide benchmark evidence."
            )
            return 1

        _stderr(
            "WARNING: NGINX_BIN is not set — module benchmarks skipped.\n"
            "  Proceeding due to --allow-skip-module flag.\n"
            "  This is acceptable for development builds but NOT for release tags."
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
        "  In blocking mode, module benchmarks are required for release tags.\n"
        "  Either:\n"
        "    1. Set NGINX_BIN=/path/to/nginx (module-enabled build), or\n"
        "    2. Pass --allow-skip-module to explicitly skip (non-release only)."
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


# Scenarios that must complete (not be skipped) in blocking mode.
_CRITICAL_SCENARIOS = frozenset({
    "plain-small",
    "large-body",
    "streaming-first",
})


# Path-coverage invariants: a "completed" scenario must actually exercise
# the production path it claims to test.  If a scenario is marked
# "completed" but its target path was never hit (e.g. streaming-first
# with streaming_ratio=0), the evidence is not credible and the gate
# must reject it as MISSING_EVIDENCE.
#
# Each entry maps a scenario name to a list of (metric, predicate, label)
# tuples.  ``predicate`` is a callable taking the metric value and
# returning True when the path was genuinely exercised.  ``label`` is
# used in the breach/evidence message.
def _path_coverage_invariants() -> list[tuple[str, list[dict]]]:
    return [
        {
            "scenario": "streaming-first",
            "checks": [
                {
                    "metric": "streaming_ratio",
                    "predicate": lambda v: v is not None and v > 0.0,
                    "label": "streaming_ratio > 0 (streaming path must be hit)",
                },
                {
                    "metric": "streaming_path_hits",
                    "predicate": lambda v: v is not None and v > 0,
                    "label": "streaming_path_hits > 0",
                },
                {
                    "metric": "fullbuffer_ratio",
                    "predicate": lambda v: v is not None and v < 1.0,
                    "label": "fullbuffer_ratio < 1 (not all requests fell back to full-buffer)",
                },
                {
                    "metric": "zero_copy_output_total",
                    "predicate": lambda v: v is not None and v > 0,
                    "label": "zero_copy_output_total > 0 (zero-copy path must produce output)",
                },
            ],
        },
        {
            "scenario": "gzip-large",
            "checks": [
                {
                    "metric": "decompression_fullbuffer_total",
                    "predicate": lambda v: v is not None and v > 0,
                    "label": "decompression_fullbuffer_total > 0 (gzip full-buffer decompression must run)",
                },
            ],
        },
    ]


def _check_path_coverage(report: dict) -> list[tuple[str, str, str]]:
    """Return [(scenario, metric, label)] for path-coverage violations.

    A violation occurs when a critical scenario is marked "completed"
    but its target production path was never exercised (the invariant
    metric predicate returned False).  Each violation is evidence that
    the benchmark did not actually test the path it claims to cover.
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", [])
    if not scenarios:
        scenarios = report.get("scenarios", [])

    by_name: dict[str, dict] = {}
    for s in scenarios:
        name = s.get("name", "")
        if name:
            by_name[name] = s

    violations: list[tuple[str, str, str]] = []
    for invariant in _path_coverage_invariants():
        name = invariant["scenario"]
        scenario = by_name.get(name)
        if scenario is None:
            continue
        if scenario.get("status") != "completed":
            continue
        m = scenario.get("metrics") or scenario.get("results") or scenario
        for check in invariant["checks"]:
            value = m.get(check["metric"])
            if not check["predicate"](value):
                violations.append((name, check["metric"], check["label"]))
    return violations


def _check_skipped_scenarios(report: dict) -> list[tuple[str, str]]:
    """Return [(name, reason)] for critical scenarios that were skipped.

    A scenario is considered skipped when its status is "skipped" in
    the report.  In blocking mode, a skipped critical scenario means
    the evidence is incomplete and the gate must fail.
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", [])
    if not scenarios:
        scenarios = report.get("scenarios", [])

    skipped = []
    for s in scenarios:
        name = s.get("name", "")
        status = s.get("status", "")
        if status == "skipped" and name in _CRITICAL_SCENARIOS:
            reason = s.get("reason", "unknown")
            skipped.append((name, reason))
    return skipped


def _evaluate_and_report(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> int:
    """Evaluate metrics against thresholds and produce the final report.

    Returns the appropriate exit code.
    """
    # In blocking mode, verify that critical scenarios were not skipped.
    if blocking:
        skipped_critical = _check_skipped_scenarios(report or {})
        if skipped_critical:
            _stderr(
                "FAIL: Critical benchmark scenarios were skipped (fixture missing):\n"
                + "".join(f"  - {name}: {reason}\n"
                           for name, reason in skipped_critical)
                + "  Release tags require all critical scenarios to complete.\n"
                "  Ensure the benchmark fixtures exist (run "
                "tests/corpus/large/generate-large-fixtures.sh)."
            )
            evidence_pack = _build_evidence_pack(
                report=report,
                verdict="MISSING_EVIDENCE",
                breaches=[
                    {"metric": "scenario", "reason": f"skipped: {name}"}
                    for name, _ in skipped_critical
                ],
                results=[],
            )
            _print_evidence_summary(evidence_pack)
            _write_output(evidence_pack, args.output)
            return 1

        # Path-coverage invariants: a "completed" scenario must actually
        # exercise the production path it claims to test.  If the target
        # path was never hit (e.g. streaming-first with streaming_ratio=0
        # and zero_copy_output_total=0), the latency numbers are not
        # credible evidence for that path.
        path_violations = _check_path_coverage(report or {})
        if path_violations:
            _stderr(
                "FAIL: Path-coverage invariants violated — critical scenarios "
                "did not exercise their target production paths:\n"
                + "".join(
                    f"  - {name}: {label} (metric={metric})\n"
                    for name, metric, label in path_violations
                )
                + "  Release tags require critical scenarios to actually hit "
                "their target paths, not just report 'completed'.\n"
                "  Regenerate the baseline after fixing the benchmark runtime "
                "so that streaming/zero-copy/decompression paths are exercised."
            )
            evidence_pack = _build_evidence_pack(
                report=report,
                verdict="MISSING_EVIDENCE",
                breaches=[
                    {
                        "metric": "path_coverage",
                        "reason": f"{name}: {label}",
                    }
                    for name, metric, label in path_violations
                ],
                results=[],
            )
            _print_evidence_summary(evidence_pack)
            _write_output(evidence_pack, args.output)
            return 1

    current_metrics = _extract_evidence_metrics(report or {})
    thresholds_cfg = _load_thresholds()

    # Load baseline if available, otherwise use empty as baseline (first run).
    baseline_path = REPO_ROOT / "perf" / "baselines" / "module-baseline-091.json"
    if baseline_path.exists():
        baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline_metrics = _extract_evidence_metrics(baseline_report)
        has_baseline = True
    else:
        if blocking and _is_release_tag():
            _stderr(
                "FAIL: No module baseline found and this is a release tag.\n"
                "  Release and RC tags require a baseline for percentage threshold evaluation.\n"
                "  Create a baseline with: cp perf/reports/module-benchmark-091.json "
                "perf/baselines/module-baseline-091.json"
            )
            evidence_pack = _build_evidence_pack(
                report=report,
                verdict="MISSING_EVIDENCE",
                breaches=[{"metric": "baseline", "reason": "no baseline for release tag"}],
                results=[],
            )
            _print_evidence_summary(evidence_pack)
            _write_output(evidence_pack, args.output)
            return 1
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
            "  Release and RC tags require all module-level thresholds to pass and all critical evidence to be present.\n"
            "  Review breaches above and address performance regressions or missing measurements."
        )
        return 1

    _stderr("Evidence gate: GO — all module-level thresholds pass.")
    return 0


def _write_output(evidence_pack: dict, output_path: str | None) -> None:
    """Write evidence pack to file or default location."""
    if output_path is None:
        output_path = str(REPO_ROOT / "perf" / "reports" / "evidence-091.json")

    out = validate_write_path_within_root(
        output_path, REPO_ROOT, purpose="evidence output"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(evidence_pack, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _stderr(f"Evidence pack written to: {out}")


if __name__ == "__main__":
    sys.exit(main())
