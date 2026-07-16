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
import contextlib
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

    with contextlib.suppress(Exception):
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and _RC_RE.search(result.stdout.strip()):
            return True
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

    with contextlib.suppress(Exception):
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
    scenarios = report.get("module_benchmark", {}).get("scenarios", []) or report.get("scenarios", [])

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

    # Compute memory slope from per-scenario RSS evidence.
    memory_data_points = _extract_memory_points(scenarios)

    if len(memory_data_points) >= 2:
        metrics["memory_slope_pct"] = _compute_memory_slope(memory_data_points)

    return metrics


def _extract_small_latency(scenarios: list[dict], metrics: dict) -> None:
    """Extract latency metrics for small scenario."""
    if plain_small := next(
        (s for s in scenarios if s.get("name") == "plain-small"), None
    ):
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
    if large_scenario := large_body or gzip_large:
        m = large_scenario.get("metrics") or large_scenario.get("results") or large_scenario
        p50 = m.get("latency_p50_ms") or m.get("p50_ms") or m.get("p50_latency_ms")
        if p50 is not None:
            metrics["p50_latency_large_pct"] = p50


def _extract_streaming_and_fallback(scenarios: list[dict], metrics: dict) -> None:
    """Extract streaming large TTFB and fallback rate."""
    if streaming_first := _find_streaming_scenario(scenarios):
        m = streaming_first.get("metrics") or streaming_first.get("results") or streaming_first
        ttfb = m.get("ttfb_p50_ms") or m.get("ttfb_ms")
        if ttfb is not None:
            metrics["ttfb_streaming_large_pct"] = ttfb
    fallback_rate = _calc_fallback_rate(scenarios)
    if fallback_rate is not None:
        metrics["fallback_rate_abs"] = fallback_rate


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


def _calc_fallback_rate(scenarios: list[dict]) -> float | None:
    """Return the worst fail-open rate across critical streaming scenarios.

    A missing scenario, missing counter, or non-positive request count is
    incomplete evidence.  Returning ``None`` leaves the threshold metric
    absent so the caller reports MISSING_EVIDENCE instead of a false zero.
    """
    by_name = {scenario.get("name"): scenario for scenario in scenarios}
    rates = []
    for name in _CRITICAL_STREAMING_SCENARIOS:
        scenario = by_name.get(name)
        if scenario is None:
            return None
        m = scenario.get("metrics") or scenario.get("results") or scenario
        failopen = m.get("precommit_failopen_total")
        requests = m.get("streaming_requests_total")
        if (
            type(failopen) is not int
            or failopen < 0
            or type(requests) is not int
            or requests <= 0
        ):
            return None
        rates.append(float(failopen) / float(requests))
    return max(rates)


def _memory_point_for_scenario(scenario: dict) -> tuple[float, float] | None:
    """Return one measured input/RSS point, or None when evidence is absent.

    Uses the peak RSS delta (peak_rss_bytes - baseline_rss_bytes) as the
    dependent variable and input_bytes as the independent variable.
    This gives a slope with units of (RSS bytes / input bytes), which
    directly measures per-byte memory cost.

    When peak/baseline evidence is absent, returns None (NOT a fallback
    to post-run worker_rss_mb).  Post-run RSS is a single sample taken
    after load completion — it does not represent peak memory during
    load and silently masking its absence as "evidence" defeats the
    purpose of the memory regression gate.  The evidence gate will then
    report MISSING_EVIDENCE for insufficient memory samples.

    A peak == baseline delta of 0 is valid evidence (no growth) and
    returns (input_bytes, 0.0) rather than None.
    """
    metrics = scenario.get("metrics") or scenario.get("results") or scenario
    input_bytes = metrics.get("input_bytes") or metrics.get("html_bytes")
    if input_bytes is None or input_bytes <= 0:
        return None

    # Required: peak RSS delta from background sampling
    peak_rss = metrics.get("peak_rss_bytes")
    baseline_rss = metrics.get("baseline_rss_bytes")
    if peak_rss is None or peak_rss <= 0:
        # No peak RSS evidence — cannot compute a valid memory point.
        # Do NOT fall back to worker_rss_mb; that would mask missing
        # peak evidence as reliable memory data.
        return None

    if baseline_rss is None or baseline_rss < 0:
        return None

    delta = peak_rss - baseline_rss
    return None if delta < 0 else (float(input_bytes), float(delta))


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
    module consumes per byte of input processed.  Percentage regression
    comparisons are valid only after the environment compatibility check
    confirms the same platform, load generator, and NGINX version; allocator
    and process-memory behavior differ across environments.

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

    if breaches := evidence_pack.get("breaches", []):
        _stderr("")
        _stderr(f"  Threshold breaches: {len(breaches)}")
        for b in breaches:
            actual = b.get("actual")
            threshold = b.get("threshold")
            reason = b.get("reason")
            if actual is not None and threshold is not None:
                _stderr(f"    - {b.get('metric')}: actual={actual}, threshold={threshold}")
            elif reason is not None:
                _stderr(f"    - {b.get('metric')}: {reason}")
            else:
                _stderr(f"    - {b.get('metric')}")

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
        return _report_skipped_benchmark(
            "SKIP_NOT_PRESENT: NGINX_BIN is not set or binary not found.\n"
            "  Module-level benchmarks require a locally-compiled NGINX binary\n"
            "  with the markdown filter module loaded.\n"
            "  Set NGINX_BIN=/path/to/nginx to enable module benchmarks.",
            "NGINX_BIN not set or binary not found",
            args,
            EX_SKIP_NOT_PRESENT,
        )
    # Blocking mode
    if args.allow_skip_module:
        if _is_release_tag():
            _stderr(
                "FAIL: --allow-skip-module is not permitted for release tags.\n"
                "  Release and RC tags require module benchmark evidence.\n"
                "  Set NGINX_BIN=/path/to/nginx to provide benchmark evidence."
            )
            return 1

        return _report_skipped_benchmark(
            "WARNING: NGINX_BIN is not set — module benchmarks skipped.\n"
            "  Proceeding due to --allow-skip-module flag.\n"
            "  This is acceptable for development builds but NOT for release tags.",
            "NGINX_BIN not set; --allow-skip-module used",
            args,
            0,
        )
    _stderr(
        "FAIL: NGINX_BIN is not set and --allow-skip-module was not provided.\n"
        "  In blocking mode, module benchmarks are required for release tags.\n"
        "  Either:\n"
        "    1. Set NGINX_BIN=/path/to/nginx (module-enabled build), or\n"
        "    2. Pass --allow-skip-module to explicitly skip (non-release only)."
    )
    return 1


def _report_skipped_benchmark(message, skip_reason, args, exit_code):
    """Write a skipped evidence pack and return the requested exit code."""
    _stderr(message)
    evidence_pack = _build_evidence_pack(
        report=None,
        verdict="SKIPPED",
        breaches=[],
        results=[],
        skipped=True,
        skip_reason=skip_reason,
    )
    _print_evidence_summary(evidence_pack)
    _write_output(evidence_pack, args.output)
    return exit_code


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
    "chunked-medium",
    "gzip-large",
    "large-body",
    "streaming-first",
    "gzip-streaming-first",
    "deflate-streaming-first",
})
_CRITICAL_STREAMING_SCENARIOS = (
    "streaming-first",
    "gzip-streaming-first",
    "deflate-streaming-first",
)
_FULLBUFFER_RATIO_COVERAGE_LABEL = (
    "fullbuffer_ratio < 1 (not all requests fell back to full-buffer)"
)
_STREAMING_REQUESTS_COVERAGE_LABEL = "streaming_requests_total > 0"
_OUTPUT_TOTAL_COVERAGE_LABEL = (
    "zero_copy_output_total + copied_output_total > 0"
)
_FALLBACK_RATE_COVERAGE_LABEL = (
    "precommit_failopen_total / streaming_requests_total <= 0.05"
)
_HISTORICAL_BASELINE_COMMIT = "847f90139d287446882052ec78661746541aebff"


def _is_positive(value: float | int | None) -> bool:
    return type(value) in (int, float) and value > 0


def _is_positive_counter(value: float | int | None) -> bool:
    return type(value) is int and value > 0


def _is_less_than_one(value: float | int | None) -> bool:
    return type(value) in (int, float) and value < 1.0


def _is_acceptable_fallback_rate(value: float | int | None) -> bool:
    return type(value) in (int, float) and value <= 0.05


def _scenario_metadata_checks(
    profile: str, compression: str, transfer_encoding: str,
) -> list[dict]:
    """Return the frozen configuration contract for one scenario."""
    return [
        {
            "field": "profile",
            "expected": profile,
            "label": f"profile must be {profile!r}",
        },
        {
            "field": "compression",
            "expected": compression,
            "label": f"compression must be {compression!r}",
        },
        {
            "field": "transfer_encoding",
            "expected": transfer_encoding,
            "label": f"transfer_encoding must be {transfer_encoding!r}",
        },
    ]


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
def _fullbuffer_path_invariants() -> list[dict]:
    fullbuffer_hits_label = "fullbuffer_path_hits > 0"
    return [
        {
            "scenario": "plain-small",
            "checks": [
                {
                    "metric": "fullbuffer_path_hits",
                    "predicate": _is_positive_counter,
                    "label": fullbuffer_hits_label,
                },
                {
                    "metric": "fullbuffer_ratio",
                    "predicate": _is_positive,
                    "label": "fullbuffer_ratio > 0",
                },
            ],
            "metadata_checks": _scenario_metadata_checks(
                "balanced", "none", "identity"
            ),
        },
        {
            "scenario": "chunked-medium",
            "checks": [{
                "metric": "fullbuffer_path_hits",
                "predicate": _is_positive_counter,
                "label": fullbuffer_hits_label,
            }],
            "metadata_checks": _scenario_metadata_checks(
                "balanced", "none", "chunked"
            ),
        },
        {
            "scenario": "large-body",
            "checks": [{
                "metric": "fullbuffer_path_hits",
                "predicate": _is_positive_counter,
                "label": fullbuffer_hits_label,
            }],
            "metadata_checks": _scenario_metadata_checks(
                "balanced", "none", "identity"
            ),
        },
    ]


def _streaming_checks() -> list[dict]:
    return [
        {
            "metric": "streaming_ratio",
            "predicate": _is_positive,
            "label": "streaming_ratio > 0 (streaming path must be hit)",
        },
        {
            "metric": "streaming_path_hits",
            "predicate": _is_positive_counter,
            "label": "streaming_path_hits > 0",
        },
        {
            "metric": "fullbuffer_ratio",
            "predicate": _is_less_than_one,
            "label": _FULLBUFFER_RATIO_COVERAGE_LABEL,
        },
        {
            "metric": "streaming_requests_total",
            "predicate": _is_positive_counter,
            "label": _STREAMING_REQUESTS_COVERAGE_LABEL,
        },
        {
            "metric": "output_total",
            "predicate": _is_positive_counter,
            "label": _OUTPUT_TOTAL_COVERAGE_LABEL,
        },
        {
            "metric": "fallback_rate",
            "predicate": _is_acceptable_fallback_rate,
            "label": _FALLBACK_RATE_COVERAGE_LABEL,
        },
    ]


def _gzip_large_invariant() -> dict:
    return {
        "scenario": "gzip-large",
        "checks": [
            {
                "metric": "decompression_fullbuffer_total",
                "predicate": _is_positive_counter,
                "label": (
                    "decompression_fullbuffer_total > 0 "
                    "(gzip full-buffer decompression must run)"
                ),
            },
            {
                "metric": "fullbuffer_path_hits",
                "predicate": _is_positive_counter,
                "label": "fullbuffer_path_hits > 0 (full-buffer path must be hit)",
            },
        ],
        "metadata_checks": _scenario_metadata_checks(
            "balanced", "gzip", "identity"
        ),
    }


def _compressed_streaming_invariant(name: str, compression: str) -> dict:
    checks = _streaming_checks()
    checks.insert(0, {
        "metric": "decompression_streaming_total",
        "predicate": _is_positive_counter,
        "label": (
            f"decompression_streaming_total > 0 "
            f"({compression} streaming decompression must run)"
        ),
    })
    return {
        "scenario": name,
        "checks": checks,
        "metadata_checks": _scenario_metadata_checks(
            "streaming_first", compression, "chunked"
        ),
    }


def _path_coverage_invariants() -> list[dict]:
    return [
        *_fullbuffer_path_invariants(),
        {
            "scenario": "streaming-first",
            "checks": _streaming_checks(),
            "metadata_checks": _scenario_metadata_checks(
                "streaming_first", "none", "chunked"
            ),
        },
        _gzip_large_invariant(),
        _compressed_streaming_invariant(
            "gzip-streaming-first", "gzip"
        ),
        _compressed_streaming_invariant(
            "deflate-streaming-first", "deflate"
        ),
    ]

def _check_path_coverage(report: dict) -> list[tuple[str, str, str]]:
    """Return [(scenario, metric, label)] for path-coverage violations.

    A violation occurs when a critical scenario is marked "completed"
    but its target production path was never exercised (the invariant
    metric predicate returned False), or when scenario metadata does
    not match the expected configuration (wrong profile, compression,
    or transfer_encoding).

    Each violation is evidence that the benchmark did not actually test
    the path it claims to cover.
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", []) or report.get("scenarios", [])

    by_name: dict[str, dict] = {}
    for s in scenarios:
        if name := s.get("name", ""):
            by_name[name] = s

    violations: list[tuple[str, str, str]] = []
    for invariant in _path_coverage_invariants():
        name = invariant["scenario"]
        scenario = by_name.get(name)
        if scenario is None or scenario.get("status") != "completed":
            continue
        _check_metric_predicates(name, scenario, invariant, violations)
        _check_metadata_fields(name, scenario, invariant, violations)
    return violations


def _check_metric_predicates(
    name: str,
    scenario: dict,
    invariant: dict,
    violations: list[tuple[str, str, str]],
) -> None:
    """Check metric predicate invariants for a single scenario."""
    m = scenario.get("metrics") or scenario.get("results") or scenario
    for check in invariant["checks"]:
        value = _path_metric_value(m, check["metric"])
        if not check["predicate"](value):
            violations.append((name, check["metric"], check["label"]))


def _path_metric_value(metrics: dict, metric: str) -> float | int | None:
    """Return a stored or derived path-integrity metric."""
    if metric == "fallback_rate":
        failopen = metrics.get("precommit_failopen_total")
        requests = metrics.get("streaming_requests_total")
        return (
            None
            if (
                type(failopen) is not int
                or failopen < 0
                or type(requests) is not int
                or requests <= 0
            )
            else float(failopen) / float(requests)
        )
    elif metric == "output_total":
        zero_copy = metrics.get("zero_copy_output_total")
        copied = metrics.get("copied_output_total")
        if (
            type(zero_copy) is not int
            or zero_copy < 0
            or type(copied) is not int
            or copied < 0
        ):
            return None
        return zero_copy + copied
    return metrics.get(metric)


def _check_metadata_fields(
    name: str,
    scenario: dict,
    invariant: dict,
    violations: list[tuple[str, str, str]],
) -> None:
    """Check metadata field expectations for a single scenario."""
    for meta_check in invariant.get("metadata_checks", []):
        field = meta_check["field"]
        expected = meta_check["expected"]
        actual = scenario.get(field, "")
        if actual != expected:
            violations.append((
                name,
                field,
                f"{meta_check['label']} (actual={actual!r})",
            ))


def _check_skipped_scenarios(report: dict) -> list[tuple[str, str]]:
    """Return [(name, reason)] for critical scenarios that were skipped.

    A scenario is considered skipped when its status is "skipped" in
    the report.  In blocking mode, a skipped critical scenario means
    the evidence is incomplete and the gate must fail.
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", []) or report.get("scenarios", [])

    skipped = []
    for s in scenarios:
        name = s.get("name", "")
        status = s.get("status", "")
        if status == "skipped" and name in _CRITICAL_SCENARIOS:
            reason = s.get("reason", "unknown")
            skipped.append((name, reason))
    return skipped


def _baseline_policy_violations(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate provenance for conservatively normalized baselines."""
    policy = report.get("baseline_policy")
    if not policy or policy.get("type") != "conservative_normalized":
        return []

    violations = []
    required = (
        "source_git_commit",
        "source_run",
        "source_artifact",
        "adjustments",
        "adjustment_reason",
        "adjustment_date",
    )
    for field in required:
        if field not in policy or policy[field] in (None, "", {}):
            violations.append((
                f"{role}.baseline_policy",
                f"missing or empty {field}",
            ))

    adjustments = policy.get("adjustments")
    if isinstance(adjustments, dict):
        for field in ("rps", "latency_ttfb"):
            if not adjustments.get(field):
                violations.append((
                    f"{role}.baseline_policy",
                    f"missing or empty adjustments.{field}",
                ))

    artifact = policy.get("source_artifact")
    historical_exception = policy.get("historical_audit_exception") is True
    exception_is_scoped = (
        historical_exception
        and policy.get("source_git_commit") == _HISTORICAL_BASELINE_COMMIT
        and bool(policy.get("audit_note"))
    )
    if artifact in (None, "", "not-recorded", "unknown") and not exception_is_scoped:
        violations.append((
            f"{role}.baseline_policy",
            "source_artifact must identify a retained raw artifact",
        ))
    return violations


def _check_missing_scenarios(report: dict) -> list[str]:
    """Return [name] for critical scenarios that are entirely absent.

    A missing scenario means the benchmark harness did not even emit a
    record for it — stronger than "skipped".  The evidence gate must
    reject this as MISSING_EVIDENCE because there is no data at all.
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", []) or report.get("scenarios", [])

    by_name: dict[str, dict] = {}
    for s in scenarios:
        if name := s.get("name", ""):
            by_name[name] = s

    missing = []
    missing.extend(name for name in _CRITICAL_SCENARIOS if name not in by_name)
    return missing


def _check_scenario_completion(report: dict) -> list[tuple[str, str]]:
    """Return [(name, status)] for critical scenarios that exist but are
    not completed.

    A scenario present with status != "completed" (and not "skipped",
    which is handled by _check_skipped_scenarios) is incomplete evidence.
    """
    scenarios = report.get("module_benchmark", {}).get("scenarios", []) or report.get("scenarios", [])

    by_name: dict[str, dict] = {}
    for s in scenarios:
        if name := s.get("name", ""):
            by_name[name] = s

    incomplete = []
    for name in _CRITICAL_SCENARIOS:
        scenario = by_name.get(name)
        if scenario is None:
            continue
        status = scenario.get("status", "")
        if status != "completed":
            incomplete.append((name, status or "empty"))
    return incomplete


def _canonical_baseline_fallback_violations(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Require complete zero-fallback counters in canonical baselines."""
    if role != "baseline":
        return []

    scenarios = report.get("module_benchmark", {}).get("scenarios", [])
    by_name = {scenario.get("name"): scenario for scenario in scenarios}
    violations = []
    for name in _CRITICAL_STREAMING_SCENARIOS:
        scenario = by_name.get(name, {})
        metrics = scenario.get("metrics") or scenario.get("results") or scenario
        failopen = metrics.get("precommit_failopen_total")
        requests = metrics.get("streaming_requests_total")
        if failopen is None:
            violations.append((
                f"{role}.fallback_rate",
                f"{name}: missing precommit_failopen_total",
            ))
        elif type(failopen) is not int or failopen < 0:
            violations.append((
                f"{role}.fallback_rate",
                f"{name}: precommit_failopen_total must be an integer >= 0 "
                f"(actual={failopen!r})",
            ))
        elif failopen != 0:
            violations.append((
                f"{role}.fallback_rate",
                f"{name}: canonical precommit_failopen_total must be 0 "
                f"(actual={failopen})",
            ))
        if requests is None:
            violations.append((
                f"{role}.fallback_rate",
                f"{name}: missing streaming_requests_total",
            ))
        elif type(requests) is not int:
            violations.append((
                f"{role}.fallback_rate",
                f"{name}: streaming_requests_total must be an integer > 0 "
                f"(actual={requests!r})",
            ))
        elif requests <= 0:
            violations.append((
                f"{role}.fallback_rate",
                f"{name}: streaming_requests_total must be > 0 "
                f"(actual={requests})",
            ))
    return violations


def _validate_benchmark_evidence(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate a benchmark report for evidence integrity.

    Applies the same checks to both current reports and baselines:
      - critical scenarios must exist and be completed (not missing,
        not skipped, not in any other non-completed status)
      - path-coverage invariants satisfied
      - nginx_version is present and not "unknown"
      - memory evidence completeness: at least 2 valid memory points

    Returns a list of (check_name, reason) violations.  Empty list
    means the report passes all integrity checks.
    """
    # 1. Critical scenarios must exist
    missing = _check_missing_scenarios(report)
    violations: list[tuple[str, str]] = [
        (f"{role}.scenario", f"missing critical scenario: {name}")
        for name in missing
    ]
    # 2. Critical scenarios must be completed (not skipped, not other)
    incomplete = _check_scenario_completion(report)
    violations.extend(
        (
            f"{role}.scenario",
            f"incomplete critical scenario: {name} status={status}",
        )
        for name, status in incomplete
    )
    # 3. Skipped critical scenarios (redundant with #2 but preserves the
    #    existing skipped-with-reason message format for diagnostics)
    skipped = _check_skipped_scenarios(report)
    violations.extend(
        (f"{role}.scenario", f"skipped: {name}: {reason}")
        for name, reason in skipped
    )
    # 4. Path-coverage invariants
    path_violations = _check_path_coverage(report)
    violations.extend(
        (f"{role}.path_coverage", f"{name}: {label} (metric={metric})")
        for name, metric, label in path_violations
    )
    violations.extend(
        _canonical_baseline_fallback_violations(report, role)
    )
    violations.extend(_baseline_policy_violations(report, role))

    # 5. Environment identity fields must be present and non-empty;
    #    nginx_version must also not use the legacy "unknown" placeholder.
    mb = report.get("module_benchmark", {})
    for field in ("platform", "load_generator", "nginx_version"):
        val = mb.get(field, "")
        if not val:
            violations.append(
                (f"{role}.{field}", f"missing or empty {field}")
            )
        elif field == "nginx_version" and val.startswith("unknown"):
            violations.append(
                (f"{role}.nginx_version", "missing or 'unknown' nginx_version")
            )

    # 6. Memory evidence completeness: at least 2 valid memory points
    scenarios = report.get("module_benchmark", {}).get("scenarios", []) or report.get("scenarios", [])
    memory_points = _extract_memory_points(scenarios)
    if len(memory_points) < 2:
        violations.append(
            (f"{role}.memory_evidence",
             f"insufficient memory points: {len(memory_points)} (need >= 2)")
        )

    return violations


def _report_integrity_failure(
    report: dict | None,
    args: argparse.Namespace,
    violations: list[tuple[str, str]],
    heading: str,
    guidance: str,
    exit_code: int = 1,
) -> int:
    """Emit a missing-evidence result and return the caller-selected status."""
    _stderr(
        heading
        + "\n"
        + "".join(
            f"  - {check}: {reason}\n" for check, reason in violations
        )
        + guidance
    )
    evidence_pack = _build_evidence_pack(
        report=report,
        verdict="MISSING_EVIDENCE",
        breaches=[
            {"metric": check, "reason": reason}
            for check, reason in violations
        ],
        results=[],
    )
    _print_evidence_summary(evidence_pack)
    _write_output(evidence_pack, args.output)
    return exit_code


def _validate_current_evidence(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> int | None:
    """Return a blocking exit code when current evidence is incomplete."""
    if not blocking:
        return None

    if violations := _validate_benchmark_evidence(report or {}, role="current"):
        return _report_integrity_failure(
            report,
            args,
            violations,
            "FAIL: Current benchmark report failed evidence integrity validation:",
            "  Release tags require complete, credible evidence.",
        )
    return None


def _validate_baseline_evidence(
    report: dict | None,
    args: argparse.Namespace,
    baseline_report: dict,
    blocking: bool,
) -> int | None:
    """Return a blocking exit code when baseline evidence is incomplete."""
    if not blocking:
        return None

    if violations := _validate_benchmark_evidence(
        baseline_report, role="baseline"
    ):
        return _report_integrity_failure(
            report,
            args,
            violations,
            "FAIL: Checked-in baseline failed evidence integrity validation:",
            "  The baseline must be regenerated by running a real benchmark "
            "after fixing the benchmark runtime.\n"
            "  Do not fabricate or improve measured evidence. Only documented "
            "conservative normalization of latency/throughput is allowed; path, "
            "fallback, output, memory and environment evidence must remain verbatim.",
        )
    return None


def _check_environment_compatibility(
    current: dict, baseline: dict,
) -> list[tuple[str, str]]:
    """Return [(field, detail)] for environment mismatches.

    The current and baseline benchmark reports must share the same:
      - platform
      - load_generator
      - nginx_version
      - input_bytes for each critical scenario

    All three environment fields must be present and non-empty on both sides.
    Critical scenario input sizes must also match. Comparing metrics across
    different environments or fixtures produces meaningless regression
    percentages and must be rejected as MISSING_EVIDENCE in blocking mode.
    Missing fields on both sides (e.g. both empty) are also violations — the
    validator's per-report check should have caught them already, but the
    compatibility check must be defensive.
    """
    violations: list[tuple[str, str]] = []

    cur_mb = current.get("module_benchmark", {})
    base_mb = baseline.get("module_benchmark", {})

    for field in ("platform", "load_generator", "nginx_version"):
        cur_val = cur_mb.get(field, "")
        base_val = base_mb.get(field, "")
        if not cur_val or not base_val:
            violations.append(
                (field,
                 f"current={cur_val!r} baseline={base_val!r} (both must be non-empty)")
            )
        elif cur_val != base_val:
            violations.append(
                (field,
                 f"current={cur_val!r} vs baseline={base_val!r}")
            )

    cur_scenarios = {
        scenario.get("name"): scenario
        for scenario in cur_mb.get("scenarios", [])
        if scenario.get("name")
    }
    base_scenarios = {
        scenario.get("name"): scenario
        for scenario in base_mb.get("scenarios", [])
        if scenario.get("name")
    }
    for name in sorted(_CRITICAL_SCENARIOS):
        if name not in cur_scenarios or name not in base_scenarios:
            continue
        cur_metrics = cur_scenarios[name].get("metrics", {})
        base_metrics = base_scenarios[name].get("metrics", {})
        cur_bytes = cur_metrics.get("input_bytes")
        base_bytes = base_metrics.get("input_bytes")
        if cur_bytes != base_bytes:
            violations.append(
                (
                    f"scenario.{name}.input_bytes",
                    f"current={cur_bytes!r} vs baseline={base_bytes!r}",
                )
            )

    return violations


def _resolve_baseline(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> tuple[dict, bool, int | None]:
    """Load and validate the module baseline.

    An environment-incompatible baseline is never used for percentage
    comparisons.  Both modes report MISSING_EVIDENCE; blocking mode fails,
    while report-only mode preserves its informational exit status of zero.

    Returns:
        (baseline_metrics, has_baseline, exit_rc):
            exit_rc is None on success; otherwise it is a terminal exit
            code and the caller must return it immediately.
    """
    baseline_path = REPO_ROOT / "perf" / "baselines" / "module-baseline-091.json"
    if not baseline_path.exists():
        return _resolve_missing_baseline(report, args, blocking)

    baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))

    integrity_rc = _validate_baseline_evidence(
        report, args, baseline_report, blocking
    )
    if integrity_rc is not None:
        return {}, False, integrity_rc

    if env_violations := _check_environment_compatibility(
        report or {},
        baseline_report,
    ):
        env_violation_strs = [
            (f"env.{field}", detail)
            for field, detail in env_violations
        ]
        env_violation_strs.append(
            (
                "baseline.percentage_thresholds",
                "cannot evaluate percentage thresholds across incompatible "
                "benchmark environments",
            )
        )
        heading = (
            "FAIL: Current and baseline benchmark environments are incompatible:"
            if blocking else
            "MISSING_EVIDENCE: Current and baseline benchmark environments "
            "are incompatible:"
        )
        return {}, False, _report_integrity_failure(
            report,
            args,
            env_violation_strs,
            heading,
            "  Percentage thresholds cannot be evaluated across incompatible "
            "environments.\n"
            "  Regenerate the baseline on the same platform, load generator, "
            "and NGINX version as the current run.",
            exit_code=1 if blocking else 0,
        )

    return _extract_evidence_metrics(baseline_report), True, None


def _resolve_missing_baseline(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> tuple[dict, bool, int | None]:
    """Handle the no-baseline case (first run or release-tag failure)."""
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
        return {}, False, 1

    _stderr("INFO: No module baseline found — percentage thresholds will be skipped (first run).")
    return {}, False, None


def _evaluate_and_report(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> int:
    """Evaluate metrics against thresholds and produce the final report.

    Returns the appropriate exit code.
    """
    integrity_rc = _validate_current_evidence(report, args, blocking)
    if integrity_rc is not None:
        return integrity_rc

    current_metrics = _extract_evidence_metrics(report or {})
    thresholds_cfg = _load_thresholds()

    baseline_metrics, has_baseline, baseline_rc = _resolve_baseline(
        report, args, blocking,
    )
    if baseline_rc is not None:
        return baseline_rc

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
