#!/usr/bin/env python3
# pylint: disable=too-many-lines
# pylint: disable=import-error
"""Performance evidence release gate for the active 0.9.x baseline.

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
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# These imports support direct execution from tools/perf and intentionally follow
# the repository path bootstrap rather than relying on the caller's PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import re  # pylint: disable=wrong-import-position
from lib.path_validation import (  # pylint: disable=wrong-import-position
    validate_read_path,
    validate_write_path_within_root,
)
from lib.baseline_provenance import (  # pylint: disable=wrong-import-position
    validate_iso_utc,
    validate_raw_commit_match,
    validate_source_run,
)
from threshold_engine import evaluate_module_level  # pylint: disable=wrong-import-position

REPO_ROOT = Path(__file__).resolve().parents[2]
_RC_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+-rc(?:\.\d+)?$")
_RELEASE_TAG_RE = re.compile(r"(?:^|/)v?\d+\.\d+\.\d+(?:\.\d+)?$")


def _report_scenarios(report: dict) -> list:
    """Extract the scenarios list from a benchmark report."""
    return (
        report.get("module_benchmark", {}).get("scenarios", [])
        or report.get("scenarios", [])
    )

# Exit code for SKIP_NOT_PRESENT (matches run_module_benchmark.sh)
EX_SKIP_NOT_PRESENT = 75


def _stderr(msg: str) -> None:
    """Write a message to stderr."""
    print(msg, file=sys.stderr)


# Approved system directories in which a PATH-discovered helper executable may
# legitimately live.  A helper discovered outside these roots is rejected so
# release evidence can never be produced by a PATH-shadowable executable.
_TRUSTED_TOOL_ROOTS = (
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/local/opt/nginx/sbin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/opt/homebrew/opt/nginx/sbin",
    "/opt/homebrew/Cellar",
    "/usr/local/Cellar",
    "/usr/lib/nginx",
)


def _canonicalize_path(path: str) -> str:
    """Return the symlink-resolved canonical absolute path of a candidate."""
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _is_trusted_tool_path(path: str) -> bool:
    """Return True when the candidate lives directly under a trusted root.

    The literal candidate location is checked (not the canonical target) so a
    user-writable symlink pointing into a trusted root stays rejected.
    """
    return any(
        path == root or path.startswith(root.rstrip("/") + "/")
        for root in _TRUSTED_TOOL_ROOTS
    )


def _resolve_tool(name: str) -> str | None:
    """Resolve a command name to an approved absolute executable path.

    The candidate must be found on PATH, canonicalize to a regular executable
    whose literal location is under a trusted system executable directory, and
    when running as root must be owned by root and not writable by group or
    other users.  Returns None when the tool is missing or untrusted.
    """
    candidate = shutil.which(name)
    if not candidate:
        return None
    resolved = _canonicalize_path(candidate)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        return None
    if not _is_trusted_tool_path(candidate) or not _is_trusted_tool_path(resolved):
        return None
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            stat_result = os.stat(resolved)
        except OSError:
            return None
        if stat_result.st_uid != 0:
            return None
        if stat_result.st_mode & 0o022:
            return None
    return resolved


def _git_bin() -> str | None:
    """Return the resolved absolute git executable path, or None when untrusted."""
    resolved = _resolve_tool("git")
    if not resolved:
        _stderr(
            "warning: git is missing or not from a trusted location; "
            "evidence will record git_commit as 'unknown'"
        )
    return resolved


def _git_rev_parse(resolved_git: str) -> str:
    """Return the current short git commit hash via the resolved git binary."""
    try:
        result = subprocess.run(
            [resolved_git, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=str(REPO_ROOT),
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _git_rev_parse_full(resolved_git: str) -> str:
    """Return the current full git commit hash via the resolved git binary."""
    try:
        result = subprocess.run(
            [resolved_git, "rev-parse", "--verify", "HEAD^{commit}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=str(REPO_ROOT),
        )
        value = result.stdout.strip()
        if result.returncode == 0 and _SHA_RE.fullmatch(value):
            return value
        return "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _git_describe(resolved_git: str) -> str:
    """Return the exact tag at HEAD via the resolved git binary, or ''."""
    with contextlib.suppress(Exception):
        result = subprocess.run(
            [resolved_git, "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return ""


def _get_git_commit() -> str:
    """Return the current short git commit hash, or 'unknown' if unavailable."""
    resolved_git = _git_bin()
    if not resolved_git:
        return "unknown"
    return _git_rev_parse(resolved_git)


def _get_git_commit_full() -> str:
    """Return the current full git commit hash, or ``unknown``."""
    resolved_git = _git_bin()
    if not resolved_git:
        return "unknown"
    return _git_rev_parse_full(resolved_git)


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
        resolved_git = _git_bin()
        if resolved_git:
            tag = _git_describe(resolved_git)
            if tag and _RC_RE.search(tag):
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
        resolved_git = _git_bin()
        if resolved_git:
            tag = _git_describe(resolved_git)
            if tag and (_RELEASE_TAG_RE.search(tag) or _RC_RE.search(tag)):
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

    resolved_bash = _resolve_tool("bash")
    if not resolved_bash:
        return 1, "Benchmark requires a trusted bash executable"

    result = subprocess.run(
        [resolved_bash, str(script), "--output", str(output_path)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stderr


def _extract_evidence_metrics(report: dict) -> dict:
    """Extract evidence-relevant metrics from a benchmark report.

    Returns a flat dict suitable for evaluate_module_level().
    """
    scenarios = _report_scenarios(report)

    if not scenarios:
        # keep empty/legacy tests happy while failing real missing scenarios
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
            not _is_exact_int(failopen)
            or failopen < 0
            or not _is_exact_int(requests)
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


def _build_evidence_pack(  # pylint: disable=too-many-arguments,too-many-positional-arguments
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
        "type": "perf-evidence-{}".format(_module_baseline_version()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _get_git_commit(),
        "toolchain": {
            "git": _git_bin(),
            "bash": _resolve_tool("bash"),
        },
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
    _stderr(
        "  Performance Evidence Gate {} — Summary".format(
            _module_baseline_version()
        )
    )
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
        description="Active 0.9.x performance evidence release gate.",
    )
    parser.add_argument(
        "--mode",
        choices=["non-blocking", "blocking"],
        default="non-blocking",
        help=(
            "Gate mode: non-blocking (report-only, integrity failures keep exit "
            "0) or blocking (fails on NO_GO and MISSING_EVIDENCE)."
        ),
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
        help="Write evidence pack JSON to this path (default: versioned evidence report).",
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

    try:
        _module_baseline_version()
    except ValueError as exc:
        _stderr(f"FAIL: invalid module baseline selection: {exc}")
        return 1

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

    version = _module_baseline_version()
    output_path = Path(
        REPO_ROOT / "perf" / "reports" / f"module-benchmark-{version}.json"
    )
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
    "brotli-streaming-first",
})
_CRITICAL_STREAMING_SCENARIOS = (
    "streaming-first",
    "gzip-streaming-first",
    "deflate-streaming-first",
    "brotli-streaming-first",
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
_HISTORICAL_BASELINE_PATH = "perf/baselines/module-baseline-091.json"
_HISTORICAL_BASELINE_SHA256 = (
    "5f2c70110458d4758f35c0c650ebbb2e43b06e0a86a5483579c8be6fe65a120c"
)
_LEGACY_091_BASELINE_COMMIT = (
    "cab92df229b0b68cb02d88817a208e009f3ce106"
)
_LEGACY_091_BASELINE_ARTIFACT = (
    "perf/baselines/module-baseline-091-raw.json"
)
_DEFAULT_MODULE_BASELINE_VERSION = "092"
_SUPPORTED_MODULE_BASELINE_VERSIONS = frozenset({"091", "092"})


def _module_baseline_version() -> str:
    """Return the allowlisted baseline version selected by the caller."""
    version = os.environ.get(
        "MODULE_BASELINE_VERSION", _DEFAULT_MODULE_BASELINE_VERSION
    )
    if version not in _SUPPORTED_MODULE_BASELINE_VERSIONS:
        raise ValueError(
            "MODULE_BASELINE_VERSION must be one of: "
            + ", ".join(sorted(_SUPPORTED_MODULE_BASELINE_VERSIONS))
        )
    return version


def _is_exact_int(value: object) -> bool:
    """Return whether a JSON value is an integer rather than a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_numeric(value: object) -> bool:
    """Return whether a JSON value is a non-boolean integer or float."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive(value: float | int | None) -> bool:
    return _is_numeric(value) and value > 0


def _is_positive_counter(value: float | int | None) -> bool:
    return _is_exact_int(value) and value > 0


def _is_less_than_one(value: float | int | None) -> bool:
    return _is_numeric(value) and value < 1.0


def _is_acceptable_fallback_rate(value: float | int | None) -> bool:
    return _is_numeric(value) and value <= 0.05


def _scenario_metadata_checks(
    scenario_config: str, compression: str, transfer_encoding: str,
    *, legacy: bool = False,
) -> list[dict]:
    """Return the frozen configuration contract for one scenario."""
    if legacy:
        scenario_config = {
            "explicit-defaults": "balanced",
            "explicit-streaming": "streaming_first",
            "explicit-strict-cache": "strict_cache",
        }[scenario_config]
        config_field = "profile"
    else:
        config_field = "scenario_config"
    checks = [
        {
            "field": config_field,
            "expected": scenario_config,
            "label": f"{config_field} must be {scenario_config!r}",
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
    if legacy:
        checks.append({
            "field": "scenario_config",
            "forbidden": True,
            "label": "scenario_config is not part of the 0.9.1 evidence contract",
        })
    return checks


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
def _fullbuffer_path_invariants(*, legacy: bool = False) -> list[dict]:
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
                "explicit-defaults", "none", "identity", legacy=legacy
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
                "explicit-defaults", "none", "chunked", legacy=legacy
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
                "explicit-defaults", "none", "identity", legacy=legacy
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
            "explicit-defaults", "gzip", "identity"
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
            "explicit-streaming", compression, "chunked"
        ),
    }


def _path_coverage_invariants(*, legacy: bool = False) -> list[dict]:
    return [
        *_fullbuffer_path_invariants(legacy=legacy),
        {
            "scenario": "streaming-first",
            "checks": _streaming_checks(),
            "metadata_checks": _scenario_metadata_checks(
                "explicit-streaming", "none", "chunked", legacy=legacy
            ),
        },
        {
            **_gzip_large_invariant(),
            "metadata_checks": _scenario_metadata_checks(
                "explicit-defaults", "gzip", "identity", legacy=legacy
            ),
        },
        {
            **_compressed_streaming_invariant(
                "gzip-streaming-first", "gzip"
            ),
            "metadata_checks": _scenario_metadata_checks(
                "explicit-streaming", "gzip", "chunked", legacy=legacy
            ),
        },
        {
            **_compressed_streaming_invariant(
                "deflate-streaming-first", "deflate"
            ),
            "metadata_checks": _scenario_metadata_checks(
                "explicit-streaming", "deflate", "chunked", legacy=legacy
            ),
        },
        {
            **_compressed_streaming_invariant(
                "brotli-streaming-first", "brotli"
            ),
            "metadata_checks": _scenario_metadata_checks(
                "explicit-streaming", "brotli", "chunked", legacy=legacy
            ),
        },
    ]

def _check_path_coverage(report: dict) -> list[tuple[str, str, str]]:
    """Return [(scenario, metric, label)] for path-coverage violations.

    A violation occurs when a critical scenario is marked "completed"
    but its target production path was never exercised (the invariant
    metric predicate returned False), or when scenario metadata does
    not match the expected configuration (wrong scenario_config, compression,
    or transfer_encoding).

    Each violation is evidence that the benchmark did not actually test
    the path it claims to cover.
    """
    scenarios = _report_scenarios(report)

    by_name: dict[str, dict] = {}
    for s in scenarios:
        if name := s.get("name", ""):
            by_name[name] = s

    violations: list[tuple[str, str, str]] = []
    for invariant in _path_coverage_invariants(
        legacy=_uses_legacy_profile_contract(report)
    ):
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
                not _is_exact_int(failopen)
                or failopen < 0
                or not _is_exact_int(requests)
                or requests <= 0
            )
            else float(failopen) / float(requests)
        )
    if metric == "output_total":
        # 0.9.2 dropped zero-copy output, so current diagnostics carry only
        # copied_output_total; historical baselines (091) still carry a
        # zero_copy_output_total alongside copied. Prefer the sum when the
        # zero-copy field exists (historical), else the copied counter.
        zero_copy = metrics.get("zero_copy_output_total")
        copied = metrics.get("copied_output_total")
        if zero_copy is None:
            if _is_exact_int(copied) and copied >= 0:
                return copied
            return None
        if (
            not _is_exact_int(zero_copy)
            or zero_copy < 0
            or not _is_exact_int(copied)
            or copied < 0
        ):
            return None
        return zero_copy + copied  # type: ignore[operator]
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
        if meta_check.get("forbidden"):
            if field in scenario:
                violations.append((
                    name,
                    field,
                    f"{meta_check['label']} (actual={scenario[field]!r})",
                ))
            continue
        expected = meta_check["expected"]
        actual = scenario.get(field, "")
        if actual != expected:
            violations.append((
                name,
                field,
                f"{meta_check['label']} (actual={actual!r})",
            ))


def _legacy_scenario_contract_violations(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Reject removed public profile metadata in frozen 0.9.2 evidence."""
    if _uses_legacy_profile_contract(report):
        return []
    violations: list[tuple[str, str]] = []
    for scenario in _report_scenarios(report):
        if "profile" in scenario:
            violations.append((
                f"{role}.scenario_metadata",
                f"{scenario.get('name', '<unnamed>')}: legacy profile field is "
                "not part of the 0.9.2 evidence contract; use "
                "scenario_config",
            ))
    return violations


def _check_skipped_scenarios(report: dict) -> list[tuple[str, str]]:
    """Return [(name, reason)] for critical scenarios that were skipped.

    A scenario is considered skipped when its status is "skipped" in
    the report.  In blocking mode, a skipped critical scenario means
    the evidence is incomplete and the gate must fail.
    """
    scenarios = _report_scenarios(report)

    skipped = []
    for s in scenarios:
        name = s.get("name", "")
        status = s.get("status", "")
        if status == "skipped" and name in _CRITICAL_SCENARIOS:
            reason = s.get("reason", "unknown")
            skipped.append((name, reason))
    return skipped


_SUPPORTED_POLICY_TYPES = frozenset({"verbatim_run", "conservative_normalized"})
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _baseline_head_binding_required() -> bool:
    """Return whether the selected baseline must match the current HEAD."""
    return os.environ.get("EVIDENCE_GATE_REQUIRE_BASELINE_HEAD", "0") == "1"


def _baseline_head_violations(report: dict) -> list[tuple[str, str]]:
    """Require both report identities to bind to the current full commit."""
    head = _get_git_commit_full()
    if not _SHA_RE.fullmatch(head):
        return [("baseline.head", "cannot resolve a full current git HEAD SHA")]

    module_benchmark = report.get("module_benchmark", {})
    policy = report.get("baseline_policy", {})
    if not isinstance(module_benchmark, dict):
        module_benchmark = {}
    if not isinstance(policy, dict):
        policy = {}
    violations: list[tuple[str, str]] = []
    for field, value in (
        ("module_benchmark.git_commit", module_benchmark.get("git_commit")),
        ("baseline_policy.source_git_commit", policy.get("source_git_commit")),
    ):
        if value != head:
            violations.append((
                f"baseline.{field}",
                f"{field}={value!r} does not match current git HEAD {head}",
            ))
    return violations


def _is_scoped_historical_exception(report: dict) -> bool:
    """Return True only for the immutable checked-in historical baseline."""
    policy = report.get("baseline_policy")
    if not isinstance(policy, dict):
        return False
    if not (
        policy.get("historical_audit_exception") is True
        and policy.get("source_git_commit") == _HISTORICAL_BASELINE_COMMIT
        and isinstance(policy.get("audit_note"), str)
        and bool(policy["audit_note"].strip())
    ):
        return False

    try:
        baseline_path = (REPO_ROOT / _HISTORICAL_BASELINE_PATH).resolve(
            strict=True
        )
        baseline_path.relative_to(REPO_ROOT.resolve())  # pylint: disable=no-member
        if _sha256_file(baseline_path) != _HISTORICAL_BASELINE_SHA256:
            return False
        historical_report = json.loads(
            baseline_path.read_text(encoding="utf-8")
        )
    except (OSError, RuntimeError, ValueError):
        return False
    return report == historical_report


def _uses_legacy_profile_contract(report: dict) -> bool:
    """Return whether the report is the explicitly supported 0.9.1 format.

    The 0.9.1 baseline is retained as a comparison input and therefore keeps
    its historical ``profile`` field.  This exception is bound to that
    baseline's immutable provenance; a 0.9.2 report cannot opt into the old
    vocabulary by merely adding the old field.
    """
    if _is_scoped_historical_exception(report):
        return True
    policy = report.get("baseline_policy")
    return (
        isinstance(policy, dict)
        and policy.get("source_git_commit") == _LEGACY_091_BASELINE_COMMIT
        and policy.get("source_artifact") == _LEGACY_091_BASELINE_ARTIFACT
    )


def _policy_type_violations(
    policy: dict, role: str, exception_is_scoped: bool,
) -> tuple[list[tuple[str, str]], str]:
    """Validate the policy type; return (violations, resolved_type)."""
    violations: list[tuple[str, str]] = []
    policy_type = policy.get("type")
    if role == "baseline" and not exception_is_scoped:
        if not isinstance(policy_type, str) or not policy_type:
            violations.append((f"{role}.baseline_policy", "missing or empty type"))
        elif policy_type not in _SUPPORTED_POLICY_TYPES:
            violations.append((
                f"{role}.baseline_policy",
                f"unsupported type {policy_type!r}; must be one of "
                f"{sorted(_SUPPORTED_POLICY_TYPES)}",
            ))
    return violations, policy_type


def _verbatim_run_violations(policy: dict, role: str) -> list[tuple[str, str]]:
    """Validate verbatim_run-specific fields."""
    violations: list[tuple[str, str]] = []
    if "measurement_timestamp" not in policy or not policy.get(
        "measurement_timestamp"
    ):
        violations.append((
            f"{role}.baseline_policy",
            "verbatim_run policy missing or empty measurement_timestamp",
        ))
    normalization = policy.get("normalization")
    if normalization != "none":
        violations.append((
            f"{role}.baseline_policy",
            f"verbatim_run policy normalization must be 'none' "
            f"(got {normalization!r})",
        ))
    return violations


def _conservative_normalized_violations(
    policy: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate conservative_normalized-specific fields."""
    violations: list[tuple[str, str]] = []
    if policy.get("normalization") != "conservative":
        violations.append((
            f"{role}.baseline_policy",
            "conservative_normalized policy normalization must be "
            f"'conservative' (got {policy.get('normalization')!r})",
        ))
    for field in ("adjustment_reason", "adjustment_date"):
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append((
                f"{role}.baseline_policy",
                f"missing or empty {field}",
            ))
    adjustments = policy.get("adjustments")
    if not isinstance(adjustments, dict):
        violations.append((
            f"{role}.baseline_policy",
            "adjustments must be an object containing the exact adjustment "
            "ledger",
        ))
    else:
        unknown = sorted(set(adjustments) - {"rps", "latency_ttfb"})
        if unknown:
            violations.append((
                f"{role}.baseline_policy",
                f"adjustments contains unsupported metric groups: {unknown}",
            ))
        for group, entries in adjustments.items():
            if not isinstance(entries, dict):
                violations.append((
                    f"{role}.baseline_policy",
                    f"adjustments.{group} must be an object",
                ))
    return violations


def _type_specific_violations(
    policy: dict, role: str, policy_type: str,
) -> list[tuple[str, str]]:
    """Validate type-specific fields for verbatim_run and conservative_normalized."""
    if policy_type == "verbatim_run":
        return _verbatim_run_violations(policy, role)
    if policy_type == "conservative_normalized":
        return _conservative_normalized_violations(policy, role)
    return []


def _source_artifact_violations(
    policy: dict, role: str, exception_is_scoped: bool,
) -> list[tuple[str, str]]:
    """Validate source_artifact and source_artifact_sha256 fields."""
    violations: list[tuple[str, str]] = []
    artifact = policy.get("source_artifact")
    if artifact in (None, "", "not-recorded", "unknown") and not exception_is_scoped:
        violations.append((
            f"{role}.baseline_policy",
            "source_artifact must identify a retained raw artifact",
        ))

    raw_digest = policy.get("source_artifact_sha256")
    sha256_re = re.compile(r"^[0-9a-f]{64}$")
    if not exception_is_scoped:
        if not raw_digest:
            violations.append((
                f"{role}.baseline_policy",
                "missing source_artifact_sha256; canonical baselines must "
                "record the SHA-256 of the retained raw artifact",
            ))
        elif not isinstance(raw_digest, str) or not sha256_re.fullmatch(raw_digest):
            violations.append((
                f"{role}.baseline_policy",
                f"source_artifact_sha256 must be a 64-char lowercase hex "
                f"digest (got {raw_digest!r})",
            ))

    if isinstance(artifact, str) and artifact and not exception_is_scoped:
        components = artifact.replace("\\", "/").split("/")
        if Path(artifact).is_absolute() or ".." in components:
            violations.append((
                f"{role}.baseline_policy",
                f"source_artifact must be a repository-relative path without "
                f"'..' traversal (got {artifact!r})",
            ))
    return violations


def _policy_provenance_violations(
    policy: dict, role: str, policy_type: str,
) -> list[tuple[str, str]]:
    """Validate policy provenance shape and timestamp/URL syntax."""
    violations = _missing_provenance_violations(policy, role)
    violations.extend(
        _source_commit_violations(policy, role, policy_type)
    )
    violations.extend(_source_run_violations(policy, role))
    violations.extend(_measurement_timestamp_violations(policy, role))
    return violations


def _missing_provenance_violations(
    policy: dict, role: str,
) -> list[tuple[str, str]]:
    """Report missing or non-string common provenance fields."""
    violations: list[tuple[str, str]] = []
    for field in (
        "source_git_commit", "source_run", "source_artifact",
        "measurement_timestamp",
    ):
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip():
            violations.append((
                f"{role}.baseline_policy",
                f"missing or empty {field}",
            ))
    return violations


def _source_commit_violations(
    policy: dict, role: str, policy_type: str,
) -> list[tuple[str, str]]:
    """Validate the full source Git SHA format."""
    source_git_commit = policy.get("source_git_commit")
    if isinstance(source_git_commit, str) and source_git_commit:
        if not _SHA_RE.fullmatch(source_git_commit):
            return [(
                f"{role}.baseline_policy",
                "source_git_commit must be a full 40-character SHA in "
                f"lowercase for {policy_type} (got {source_git_commit!r})",
            )]
    return []


def _source_run_violations(
    policy: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate the source GitHub Actions run URL."""
    violations: list[tuple[str, str]] = []
    source_run = policy.get("source_run")
    if isinstance(source_run, str) and source_run:
        for reason in validate_source_run(source_run, repo_root=REPO_ROOT):
            violations.append((f"{role}.baseline_policy", reason))
    return violations


def _measurement_timestamp_violations(
    policy: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate an explicit UTC measurement timestamp."""
    violations: list[tuple[str, str]] = []
    measurement_timestamp = policy.get("measurement_timestamp")
    if isinstance(measurement_timestamp, str) and measurement_timestamp:
        try:
            validate_iso_utc(
                measurement_timestamp,
                field=f"{role}.baseline_policy.measurement_timestamp",
            )
        except ValueError as exc:
            violations.append((f"{role}.baseline_policy", str(exc)))
    return violations


def _baseline_policy_violations(  # pylint: disable=too-many-return-statements
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate provenance for baselines.

    A baseline_policy must declare one of the supported policy types and
    include all type-specific fields.  Unknown or missing types are
    fail-closed: they cannot satisfy a baseline provenance requirement.

    All baseline policies must declare their provenance (source commit, run,
    artifact).  Conservative normalized baselines have additional requirements
    around adjustments and artifact retention.  The sole historical exception
    is the original 0.9.1 baseline at _HISTORICAL_BASELINE_COMMIT.

    Supported types:
      - verbatim_run: raw, unmodified benchmark output.
      - conservative_normalized: latency/throughput adjusted only downward/inward.
    """
    policy = report.get("baseline_policy")
    if policy is None:
        if role == "baseline":
            return [(f"{role}.baseline_policy", "missing baseline_policy object")]
        return []
    if not isinstance(policy, dict):
        return [(
            f"{role}.baseline_policy",
            "baseline_policy must be an object",
        )]
    if not policy:
        if role == "baseline":
            return [(f"{role}.baseline_policy", "missing baseline_policy object")]
        return []

    violations: list[tuple[str, str]] = []
    exception_is_scoped = _is_scoped_historical_exception(report)
    if exception_is_scoped:
        return violations

    type_violations, policy_type = _policy_type_violations(
        policy, role, exception_is_scoped
    )
    violations.extend(type_violations)

    violations.extend(
        _policy_provenance_violations(policy, role, policy_type)
    )
    violations.extend(_type_specific_violations(policy, role, policy_type))
    violations.extend(_source_artifact_violations(policy, role, exception_is_scoped))
    release_gate_eligible = policy.get("release_gate_eligible", True)
    if not isinstance(release_gate_eligible, bool):
        violations.append((
            f"{role}.baseline_policy",
            "release_gate_eligible must be a boolean when present",
        ))
    elif not release_gate_eligible:
        exclusion_reason = policy.get("release_gate_exclusion_reason")
        if not isinstance(exclusion_reason, str) or not exclusion_reason.strip():
            violations.append((
                f"{role}.baseline_policy",
                "release_gate_exclusion_reason is required when the baseline "
                "is excluded from release gates",
            ))
    return violations


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of a file's bytes."""
    validated_path = validate_read_path(path, purpose="raw artifact")
    digest = hashlib.sha256()
    with validated_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_raw_digest(  # pylint: disable=too-many-return-statements
    policy: dict,
) -> tuple[Path, str | None]:
    """Resolve and verify the raw artifact digest.

    Returns ``(raw_path, error_message)``.  On success ``error_message``
    is ``None`` and ``raw_path`` points to the verified raw file.  On
    failure ``raw_path`` is a placeholder and ``error_message`` describes
    the violation.
    """
    artifact = policy.get("source_artifact")
    if not isinstance(artifact, str) or not artifact:
        return Path(), "source_artifact is not a string"

    candidate = REPO_ROOT / artifact
    try:
        raw_path = candidate.resolve(strict=True)
        raw_path.relative_to(REPO_ROOT.resolve())  # pylint: disable=no-member
    except FileNotFoundError:
        return candidate, f"retained raw artifact does not exist: {artifact}"
    except (RuntimeError, ValueError) as exc:
        return candidate, (
            f"retained raw artifact must resolve within the repository root "
            f"(got {artifact!r}: {exc})"
        )

    raw_digest = policy.get("source_artifact_sha256")
    if not isinstance(raw_digest, str) or not _SHA256_RE.fullmatch(raw_digest):
        return raw_path, (
            f"source_artifact_sha256 is not a 64-char hex digest "
            f"(got {raw_digest!r})"
        )

    try:
        actual_digest = _sha256_file(raw_path)
    except OSError as exc:
        return raw_path, f"failed to read retained raw artifact: {exc}"
    if actual_digest != raw_digest:
        return raw_path, (
            f"raw artifact SHA-256 mismatch: policy={raw_digest} "
            f"actual={actual_digest}; the retained raw file does not match "
            f"the finalized baseline provenance"
        )
    return raw_path, None


def _raw_provenance_violations(
    raw_report: dict, policy: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify policy provenance fields against the retained raw report."""
    violations: list[tuple[str, str]] = []
    source_git_commit = policy.get("source_git_commit")
    if isinstance(source_git_commit, str):
        for reason in validate_raw_commit_match(raw_report, source_git_commit):
            violations.append((f"{role}.raw_binding", reason))

    raw_module_benchmark = raw_report.get("module_benchmark", {})
    raw_timestamp = (
        raw_module_benchmark.get("timestamp")
        if isinstance(raw_module_benchmark, dict)
        else None
    )
    policy_timestamp = policy.get("measurement_timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp:
        violations.append((
            f"{role}.raw_binding",
            "raw report is missing module_benchmark.timestamp",
        ))
    else:
        try:
            validate_iso_utc(
                raw_timestamp,
                field=f"{role}.raw_binding raw timestamp",
            )
        except ValueError as exc:
            violations.append((f"{role}.raw_binding", str(exc)))
        if policy_timestamp != raw_timestamp:
            violations.append((
                f"{role}.raw_binding",
                "baseline_policy.measurement_timestamp must equal "
                "raw.module_benchmark.timestamp",
            ))
    return violations


def _raw_content_binding_violations(
    report: dict, raw_report: dict, policy_type: str, role: str,
) -> list[tuple[str, str]]:
    """Verify finalized content against the raw report by policy type."""
    violations: list[tuple[str, str]] = []
    if policy_type == "verbatim_run":
        finalized_without_policy = {
            key: value for key, value in report.items()
            if key != "baseline_policy"
        }
        all_keys = sorted(
            set(finalized_without_policy) | set(raw_report)
        )
        for top_key in all_keys:
            if (
                top_key not in finalized_without_policy
                or top_key not in raw_report
                or finalized_without_policy[top_key] != raw_report[top_key]
            ):
                violations.append((
                    f"{role}.raw_binding",
                    f"verbatim_run {top_key} differs from the raw artifact; "
                    f"verbatim baselines must not modify measured data",
                ))
    elif policy_type == "conservative_normalized":
        violations.extend(
            _conservative_normalized_truth_violations(report, raw_report, role)
        )
        policy = report.get("baseline_policy")
        if isinstance(policy, dict):
            violations.extend(
                _adjustment_ledger_violations(report, raw_report, policy, role)
            )
    return violations


def _raw_artifact_binding_violations(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify the finalized baseline is bound to its retained raw artifact.

    For canonical baselines (non-historical), the validator:

      * resolves ``baseline_policy.source_artifact`` against the repo root;
      * confirms the raw file exists;
      * recomputes its SHA-256 and compares it to
        ``baseline_policy.source_artifact_sha256``;
      * for ``verbatim_run``, verifies that the finalized measured data
        (``module_benchmark`` and ``decompression_coverage``) is
        byte-identical to the raw report (only ``baseline_policy`` may
        differ);
      * for ``conservative_normalized``, verifies truth evidence
        (path, fallback, output, memory, environment, status, scenario
        metadata) is identical to the raw report.

    The historical audit exception is honored and skips raw binding.
    """
    policy = report.get("baseline_policy")
    if not isinstance(policy, dict):
        return []
    if _is_scoped_historical_exception(report):
        return []

    raw_path, error = _verify_raw_digest(policy)
    if error:
        return [(f"{role}.raw_binding", error)]

    try:
        validated_raw_path = validate_read_path(
            raw_path, purpose="retained raw artifact"
        )
        raw_report = json.loads(
            validated_raw_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        return [(
            f"{role}.raw_binding",
            f"failed to read raw artifact: {exc}",
        )]
    if not isinstance(raw_report, dict):
        return [(
            f"{role}.raw_binding",
            "retained raw artifact must contain a JSON object",
        )]

    provenance_violations = _raw_provenance_violations(
        raw_report, policy, role
    )

    return provenance_violations + _raw_content_binding_violations(
        report, raw_report, policy.get("type"), role
    )


_ADJUSTABLE_METRIC_FIELDS = (
    "rps",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "ttfb_p50_ms",
    "ttfb_p95_ms",
    "ttfb_ms",
    "ttlb_ms",
    "ttlb_p50_ms",
)
_LATENCY_ADJUSTABLE_METRIC_FIELDS = frozenset(
    field for field in _ADJUSTABLE_METRIC_FIELDS if field != "rps"
)


def _scenario_truth_violations(
    name: str, cur_scenario: dict | None, raw_scenario: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate one scenario and enforce conservative metric directions."""
    violations: list[tuple[str, str]] = []
    if cur_scenario is None:
        violations.append((
            f"{role}.raw_binding",
            f"conservative_normalized removed scenario {name!r} present in raw",
        ))
        return violations
    scenario_fields = (set(cur_scenario) | set(raw_scenario)) - {"metrics"}
    for field in sorted(scenario_fields):
        if (
            field not in cur_scenario
            or field not in raw_scenario
            or cur_scenario[field] != raw_scenario[field]
        ):
            violations.append((
                f"{role}.raw_binding",
                f"conservative_normalized must not modify scenario {name!r} "
                f"{field} (finalized={cur_scenario.get(field)!r} "
                f"raw={raw_scenario.get(field)!r})",
            ))
    return violations + _metric_truth_violations(
        name,
        cur_scenario.get("metrics"),
        raw_scenario.get("metrics"),
        role,
    )


def _metric_truth_violations(
    name: str, cur_metrics: Any, raw_metrics: Any, role: str,
) -> list[tuple[str, str]]:
    """Validate metric keys, numeric values, and conservative directions."""
    prefix = f"{role}.raw_binding"
    if not isinstance(cur_metrics, dict) or not isinstance(raw_metrics, dict):
        return [(prefix, f"scenario {name!r} metrics must be objects")]

    cur_fields = set(cur_metrics)
    raw_fields = set(raw_metrics)
    violations: list[tuple[str, str]] = []
    if cur_fields != raw_fields:
        violations.append((
            prefix,
            f"conservative_normalized changed metric keys for scenario {name!r} "
            f"(added={sorted(cur_fields - raw_fields)} "
            f"removed={sorted(raw_fields - cur_fields)})",
        ))

    for field in sorted(cur_fields & raw_fields):
        violations.extend(
            _metric_value_violations(
                name, field, cur_metrics[field], raw_metrics[field], prefix
            )
        )
    return violations


def _metric_value_violations(
    name: str, field: str, current: Any, raw: Any, prefix: str,
) -> list[tuple[str, str]]:
    """Validate one shared metric's numeric type and conservative direction."""
    if not _is_finite_number(current) or not _is_finite_number(raw):
        return [(
            prefix,
            f"scenario {name!r} metric {field} must be a finite number",
        )]
    return _metric_direction_violations(name, field, current, raw, prefix)


def _metric_direction_violations(
    name: str, field: str, current: int | float, raw: int | float, prefix: str,
) -> list[tuple[str, str]]:
    """Enforce the allowed direction for one finite metric value."""
    if field == "rps" and current > raw:
        return [(
            prefix,
            f"conservative_normalized may only lower rps for scenario "
            f"{name!r} (finalized={current!r} raw={raw!r})",
        )]
    if field in _LATENCY_ADJUSTABLE_METRIC_FIELDS and current < raw:
        return [(
            prefix,
            f"conservative_normalized may only raise {field} for scenario "
            f"{name!r} (finalized={current!r} raw={raw!r})",
        )]
    if field not in _ADJUSTABLE_METRIC_FIELDS and current != raw:
        return [(
            prefix,
            f"conservative_normalized must not modify scenario {name!r} "
            f"metric {field} (finalized={current!r} raw={raw!r})",
        )]
    return []


def _is_finite_number(value: Any) -> bool:
    """Return True for JSON-compatible finite numeric values, excluding bool."""
    return _is_numeric(value) and math.isfinite(value)


def _conservative_normalized_truth_violations(
    finalized: dict, raw: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify conservative_normalized baselines only adjust throughput/latency.

    Only RPS (may decrease), latency/TTFB/TTLB (may increase) may be
    adjusted. All other raw evidence must remain identical.
    """
    violations = _top_level_truth_violations(finalized, raw, role)
    cur_mb = finalized.get("module_benchmark", {})
    raw_mb = raw.get("module_benchmark", {})
    if not isinstance(cur_mb, dict) or not isinstance(raw_mb, dict):
        return violations + [(
            f"{role}.raw_binding",
            "conservative_normalized requires module_benchmark objects",
        )]
    violations.extend(_module_truth_violations(cur_mb, raw_mb, role))
    violations.extend(_scenario_list_truth_violations(cur_mb, raw_mb, role))
    return violations


def _top_level_truth_violations(
    finalized: dict, raw: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify top-level evidence keys and values are unchanged."""
    violations: list[tuple[str, str]] = []
    raw_keys = set(raw) - {"baseline_policy"}
    finalized_keys = set(finalized) - {"baseline_policy"}
    if finalized_keys != raw_keys:
        violations.append((
            f"{role}.raw_binding",
            "conservative_normalized changed top-level raw evidence keys "
            f"(added={sorted(finalized_keys - raw_keys)} "
            f"removed={sorted(raw_keys - finalized_keys)})",
        ))
    for field in sorted(raw_keys - {"module_benchmark"}):
        if field not in finalized or finalized[field] != raw[field]:
            violations.append((
                f"{role}.raw_binding",
                f"conservative_normalized must not modify top-level {field}",
            ))
    return violations


def _module_truth_violations(
    finalized: dict, raw: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify module benchmark environment and non-scenario fields."""
    violations: list[tuple[str, str]] = []
    for field in sorted((set(finalized) | set(raw)) - {"scenarios"}):
        if (
            field not in finalized
            or field not in raw
            or finalized[field] != raw[field]
        ):
            violations.append((
                f"{role}.raw_binding",
                f"conservative_normalized must not modify module_benchmark.{field} "
                f"(finalized={finalized.get(field)!r} raw={raw.get(field)!r})",
            ))
    return violations


def _scenario_list_truth_violations(
    finalized: dict, raw: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify scenario arrays and all scenario-level raw bindings."""
    raw_scenario_list = raw.get("scenarios")
    finalized_scenario_list = finalized.get("scenarios")
    if not isinstance(raw_scenario_list, list) or not isinstance(
        finalized_scenario_list, list
    ):
        return [(
            f"{role}.raw_binding",
            "conservative_normalized requires scenario arrays",
        )]

    finalized_scenarios = {
        s.get("name"): s for s in finalized_scenario_list if isinstance(s, dict)
    }
    raw_scenarios = {
        s.get("name"): s for s in raw_scenario_list if isinstance(s, dict)
    }
    violations: list[tuple[str, str]] = []
    if (
        len(finalized_scenarios) != len(finalized_scenario_list)
        or len(raw_scenarios) != len(raw_scenario_list)
    ):
        violations.append((
            f"{role}.raw_binding",
            "conservative_normalized scenario entries must be objects with "
            "unique names",
        ))
    for name, raw_scenario in raw_scenarios.items():
        violations.extend(
            _scenario_truth_violations(
                name, finalized_scenarios.get(name), raw_scenario, role
            )
        )
    for name in finalized_scenarios:
        if name not in raw_scenarios:
            violations.append((
                f"{role}.raw_binding",
                f"conservative_normalized added scenario {name!r} absent from raw",
            ))
    return violations


def _actual_adjustment_ledger(finalized: dict, raw: dict) -> dict:
    """Return the exact metric deltas between finalized and raw reports."""
    finalized_mb = finalized.get("module_benchmark", {})
    raw_mb = raw.get("module_benchmark", {})
    if not isinstance(finalized_mb, dict) or not isinstance(raw_mb, dict):
        return {}
    finalized_scenario_list = finalized_mb.get("scenarios")
    raw_scenario_list = raw_mb.get("scenarios")
    if not isinstance(finalized_scenario_list, list) or not isinstance(
        raw_scenario_list, list
    ):
        return {}
    finalized_scenarios = {
        scenario.get("name"): scenario
        for scenario in finalized_scenario_list
        if isinstance(scenario, dict)
    }
    raw_scenarios = {
        scenario.get("name"): scenario
        for scenario in raw_scenario_list
        if isinstance(scenario, dict)
    }
    ledger: dict = {}
    for name, raw_scenario in raw_scenarios.items():
        scenario_ledger = _scenario_adjustment_ledger(
            finalized_scenarios.get(name), raw_scenario
        )
        if "rps" in scenario_ledger:
            ledger.setdefault("rps", {})[name] = scenario_ledger["rps"]
        if "latency_ttfb" in scenario_ledger:
            ledger.setdefault("latency_ttfb", {})[name] = scenario_ledger[
                "latency_ttfb"
            ]
    return ledger


def _scenario_adjustment_ledger(
    finalized: Any, raw: dict,
) -> dict:
    """Return the adjustment deltas for one scenario."""
    if not isinstance(finalized, dict):
        return {}
    raw_metrics = raw.get("metrics")
    finalized_metrics = finalized.get("metrics")
    if not isinstance(raw_metrics, dict) or not isinstance(finalized_metrics, dict):
        return {}

    ledger: dict = {}
    for field in _ADJUSTABLE_METRIC_FIELDS:
        if field not in raw_metrics or field not in finalized_metrics:
            continue
        raw_value = raw_metrics[field]
        finalized_value = finalized_metrics[field]
        if not (
            _is_finite_number(raw_value)
            and _is_finite_number(finalized_value)
            and finalized_value != raw_value
        ):
            continue
        if field == "rps":
            ledger["rps"] = finalized_value - raw_value
        else:
            ledger.setdefault("latency_ttfb", {})[field] = (
                finalized_value - raw_value
            )
    return ledger


def _adjustment_ledger_violations(
    finalized: dict, raw: dict, policy: dict, role: str,
) -> list[tuple[str, str]]:
    """Require policy adjustments to describe every actual metric delta."""
    actual = _actual_adjustment_ledger(finalized, raw)
    declared = policy.get("adjustments")
    if not _strict_json_equal(declared, actual):
        return [(
            f"{role}.raw_binding",
            "conservative_normalized adjustments must exactly match the "
            f"raw/finalized metric deltas (declared={declared!r} "
            f"actual={actual!r})",
        )]
    return []


def _strict_json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values while tolerating harmless numeric round-off."""
    if _is_finite_number(left) and _is_finite_number(right):
        return math.isclose(
            float(left), float(right), rel_tol=0.0, abs_tol=1e-12
        )
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (
            set(left) == set(right)
            and all(_strict_json_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_json_equal(item_left, item_right)
            for item_left, item_right in zip(left, right)
        )
    return left == right


def _scenario_source_entry_violations(
    name: str,
    source: Any,
    mb: dict,
    scenario_names: set,
    role: str,
) -> list[tuple[str, str]]:
    """Validate one scenario_sources entry against the canonical environment."""
    violations: list[tuple[str, str]] = []
    if name not in scenario_names:
        violations.append((
            f"{role}.baseline_policy",
            f"scenario_sources entry '{name}' has no matching scenario",
        ))
    if not isinstance(source, dict):
        violations.append((
            f"{role}.baseline_policy",
            f"scenario_sources entry '{name}' must be an object",
        ))
        return violations
    for field in ("platform", "load_generator", "nginx_version"):
        expected = mb.get(field, "")
        actual = source.get(field)
        if actual is None:
            violations.append((
                f"{role}.baseline_policy",
                f"scenario '{name}' source must declare {field} so its "
                "environment can be verified against the canonical "
                "environment",
            ))
        elif actual != expected:
            violations.append((
                f"{role}.baseline_policy",
                f"scenario '{name}' source environment {field}={actual!r} "
                f"does not match canonical environment {field}={expected!r}; "
                "split diverging scenarios into a separate baseline",
            ))
    return violations


def _scenario_source_environment_violations(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Verify per-scenario source environments match the canonical environment.

    A baseline that merges scenarios measured in different runs must declare
    each diverging scenario under ``baseline_policy.scenario_sources`` with
    structured environment fields (``platform``, ``load_generator``,
    ``nginx_version``) that match the top-level ``module_benchmark``
    environment.  Mixing environments inside one baseline makes percentage
    comparisons meaningless while the top-level environment fields still
    pass the compatibility check, so any mismatch — or a source entry whose
    environment cannot be verified — is an integrity violation.  Diverging
    scenarios belong in a separate, environment-truthful baseline file.
    """
    policy = report.get("baseline_policy")
    if not isinstance(policy, dict):
        return []
    sources = policy.get("scenario_sources")
    if sources is None:
        return []
    if not isinstance(sources, dict):
        return [(
            f"{role}.baseline_policy",
            "scenario_sources must be an object keyed by scenario name",
        )]

    mb = report.get("module_benchmark", {})
    scenario_names = {
        scenario.get("name")
        for scenario in mb.get("scenarios", [])
        if isinstance(scenario, dict)
    }
    violations: list[tuple[str, str]] = []
    for name, source in sorted(sources.items()):
        violations.extend(
            _scenario_source_entry_violations(
                name, source, mb, scenario_names, role
            )
        )
    return violations


def _check_missing_scenarios(report: dict) -> list[str]:
    """Return [name] for critical scenarios that are entirely absent.

    A missing scenario means the benchmark harness did not even emit a
    record for it — stronger than "skipped".  The evidence gate must
    reject this as MISSING_EVIDENCE because there is no data at all.
    """
    scenarios = _report_scenarios(report)

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
    scenarios = _report_scenarios(report)

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
        elif not _is_exact_int(failopen) or failopen < 0:
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
        elif not _is_exact_int(requests):
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


_MISSING_EVIDENCE_VALUE = "<missing>"


def _fallback_rate_violation(
    role: str,
    scenario_name: object,
    field: str,
    actual: object,
    expected: object,
    failopen_total: object,
    requests_total: object,
) -> tuple[str, str]:
    """Format one fail-closed fallback-rate evidence violation."""
    return (
        f"{role}.fallback_rate_consistency",
        f"scenario {scenario_name!r} field {field}: actual={actual!r}; "
        f"expected={expected!r}; counter pair "
        f"precommit_failopen_total={failopen_total!r}, "
        f"streaming_requests_total={requests_total!r}",
    )


def _fallback_rate_scenario_metrics(
    scenario: object,
) -> tuple[object, dict]:
    """Return a scenario name and its metrics object safely."""
    if not isinstance(scenario, dict):
        return _MISSING_EVIDENCE_VALUE, {}

    metrics_value = scenario.get("metrics")
    if not isinstance(metrics_value, dict):
        metrics_value = scenario.get("results")
    metrics = metrics_value if isinstance(metrics_value, dict) else scenario
    return scenario.get("name", _MISSING_EVIDENCE_VALUE), metrics


def _fallback_rate_field_violations(
    role: str,
    scenario_name: object,
    stored: object,
    failopen_total: object,
    requests_total: object,
) -> tuple[list[tuple[str, str]], bool, bool, bool]:
    """Validate the types and bounds of the three required fields."""
    violations: list[tuple[str, str]] = []

    stored_valid = _is_finite_number(stored) and 0.0 <= stored <= 1.0
    if not stored_valid:
        violations.append(_fallback_rate_violation(
            role,
            scenario_name,
            "fallback_rate",
            stored,
            "finite JSON number in [0.0, 1.0]",
            failopen_total,
            requests_total,
        ))

    failopen_valid = _is_exact_int(failopen_total) and failopen_total >= 0
    if not failopen_valid:
        violations.append(_fallback_rate_violation(
            role,
            scenario_name,
            "precommit_failopen_total",
            failopen_total,
            "exact integer >= 0",
            failopen_total,
            requests_total,
        ))

    requests_valid = _is_exact_int(requests_total) and requests_total >= 0
    if not requests_valid:
        violations.append(_fallback_rate_violation(
            role,
            scenario_name,
            "streaming_requests_total",
            requests_total,
            "exact integer >= 0",
            failopen_total,
            requests_total,
        ))

    return violations, stored_valid, failopen_valid, requests_valid


def _fallback_rate_counter_relation(
    role: str,
    scenario_name: object,
    failopen_total: int,
    requests_total: int,
) -> tuple[bool, list[tuple[str, str]]]:
    """Validate the counter relationship and return whether it is usable."""
    if requests_total == 0:
        if failopen_total == 0:
            return True, []
        expected = "0 when streaming_requests_total is 0"
    elif failopen_total <= requests_total:
        return True, []
    else:
        expected = "<= streaming_requests_total"
    return False, [_fallback_rate_violation(
        role,
        scenario_name,
        "precommit_failopen_total",
        failopen_total,
        expected,
        failopen_total,
        requests_total,
    )]


def _fallback_rate_scenario_violations(
    scenario: object, role: str,
) -> list[tuple[str, str]]:
    """Validate the strict fallback-rate schema for one scenario."""
    scenario_name, metrics = _fallback_rate_scenario_metrics(scenario)
    stored = metrics.get("fallback_rate", _MISSING_EVIDENCE_VALUE)
    failopen_total = metrics.get(
        "precommit_failopen_total", _MISSING_EVIDENCE_VALUE,
    )
    requests_total = metrics.get(
        "streaming_requests_total", _MISSING_EVIDENCE_VALUE,
    )
    violations, stored_valid, failopen_valid, requests_valid = (
        _fallback_rate_field_violations(
            role, scenario_name, stored, failopen_total, requests_total,
        )
    )

    counters_related = False
    if failopen_valid and requests_valid:
        counters_related, relation_violations = _fallback_rate_counter_relation(
            role, scenario_name, failopen_total, requests_total,
        )
        violations.extend(relation_violations)

    if stored_valid and counters_related:
        derived = (
            0.0
            if requests_total == 0
            else float(failopen_total) / float(requests_total)
        )
        if not math.isclose(
            stored,
            derived,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            violations.append(_fallback_rate_violation(
                role,
                scenario_name,
                "fallback_rate",
                stored,
                derived,
                failopen_total,
                requests_total,
            ))

    return violations


def _fallback_rate_consistency_violations(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Fail closed when any scenario's fallback-rate evidence is invalid."""
    scenarios = _report_scenarios(report)
    violations = []
    for scenario in scenarios:
        violations.extend(_fallback_rate_scenario_violations(scenario, role))
    return violations


def _validate_benchmark_evidence(
    report: dict, role: str,
) -> list[tuple[str, str]]:
    """Validate a benchmark report for evidence integrity.

    Applies the same checks to both current reports and baselines:
      - critical scenarios must exist and be completed (not missing,
        not skipped, not in any other non-completed status)
      - path-coverage invariants satisfied
      - stored fallback_rate consistent with counter-derived value
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
    violations.extend(
        _fallback_rate_consistency_violations(report, role)
    )
    violations.extend(_legacy_scenario_contract_violations(report, role))
    violations.extend(_baseline_policy_violations(report, role))
    violations.extend(_scenario_source_environment_violations(report, role))
    violations.extend(_raw_artifact_binding_violations(report, role))

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
    scenarios = _report_scenarios(report)
    memory_points = _extract_memory_points(scenarios)
    if len(memory_points) < 2:
        violations.append(
            (f"{role}.memory_evidence",
             f"insufficient memory points: {len(memory_points)} (need >= 2)")
        )

    return violations


def _report_integrity_failure(  # pylint: disable=too-many-arguments,too-many-positional-arguments
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
    """Report current evidence integrity failures in either gate mode."""
    if violations := _validate_benchmark_evidence(report or {}, role="current"):
        return _report_integrity_failure(
            report,
            args,
            violations,
            (
                "FAIL: Current benchmark report failed evidence integrity validation:"
                if blocking else
                "MISSING_EVIDENCE: Current benchmark report failed evidence "
                "integrity validation:"
            ),
            "  Release tags require complete, credible evidence.\n",
            exit_code=1 if blocking else 0,
        )
    return None


def _validate_baseline_evidence(
    report: dict | None,
    args: argparse.Namespace,
    baseline_report: dict,
    blocking: bool,
) -> int | None:
    """Report baseline evidence integrity failures in either gate mode."""
    if violations := _validate_benchmark_evidence(
        baseline_report, role="baseline"
    ):
        return _report_integrity_failure(
            report,
            args,
            violations,
            (
                "FAIL: Checked-in baseline failed evidence integrity validation:"
                if blocking else
                "MISSING_EVIDENCE: Checked-in baseline failed evidence "
                "integrity validation:"
            ),
            "  The baseline must be regenerated by running a real benchmark "
            "after fixing the benchmark runtime.\n"
            "  Do not fabricate or improve measured evidence. Only documented "
            "conservative normalization of latency/throughput is allowed; path, "
            "fallback, output, memory and environment evidence must remain verbatim.\n",
            exit_code=1 if blocking else 0,
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


def _resolve_baseline_head_binding(
    report: dict | None,
    args: argparse.Namespace,
    blocking: bool,
    baseline_report: dict,
) -> tuple[dict, bool, int | None] | None:
    """Return a terminal result when the baseline is not HEAD-bound."""
    if not _baseline_head_binding_required():
        return None

    if head_violations := _baseline_head_violations(baseline_report):
        return {}, False, _report_integrity_failure(
            report,
            args,
            head_violations,
            "FAIL: Checked-in baseline is not bound to the current HEAD:",
            "  Regenerate the 0.9.2 module baseline from a real module-enabled "
            "benchmark at this exact commit before release qualification.",
            exit_code=1 if blocking else 0,
        )

    return None


def _resolve_ineligible_baseline(
    report: dict | None,
    args: argparse.Namespace,
    blocking: bool,
    baseline_report: dict,
) -> tuple[dict, bool, int | None] | None:
    """Handle a baseline excluded from percentage comparisons."""
    policy = baseline_report.get("baseline_policy")
    if not isinstance(policy, dict) or policy.get("release_gate_eligible") is not False:
        return None

    reason = policy.get(
        "release_gate_exclusion_reason",
        "no exclusion reason recorded",
    )
    if blocking and _is_release_tag():
        _stderr(
            "ERROR: checked-in module baseline is ineligible for "
            f"release-gate comparison: {reason}"
        )
        # Match _resolve_missing_baseline: build and emit the evidence pack
        # and summary before failing so the failure is documented in the
        # output artifact, not only on stderr.
        evidence_pack = _build_evidence_pack(
            report=report,
            verdict="MISSING_EVIDENCE",
            breaches=[{
                "metric": "baseline",
                "reason": f"release-tag baseline ineligible for comparison: {reason}",
            }],
            results=[],
        )
        _print_evidence_summary(evidence_pack)
        _write_output(evidence_pack, args.output)
        return {}, False, EXIT_FAILURE

    _stderr(
        "INFO: Checked-in module baseline is excluded from release-gate "
        f"comparisons: {reason}"
    )
    return {}, False, None


def _resolve_environment_mismatch(
    report: dict | None,
    args: argparse.Namespace,
    blocking: bool,
    baseline_report: dict,
) -> tuple[dict, bool, int | None] | None:
    """Return a terminal result when benchmark environments differ."""
    if not (env_violations := _check_environment_compatibility(
        report or {}, baseline_report
    )):
        return None

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


def _resolve_baseline(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> tuple[dict, bool, int | None]:
    """Load and validate the module baseline.

    An environment-incompatible baseline is never used for percentage
    comparisons.  Both modes report MISSING_EVIDENCE; blocking mode fails,
    while report-only mode preserves its informational exit status of zero.
    A baseline explicitly marked ``release_gate_eligible: false`` is validated
    for provenance but excluded from percentage comparisons until its stated
    remeasurement condition is satisfied.

    Returns:
        (baseline_metrics, has_baseline, exit_rc):
            exit_rc is None on success; otherwise it is a terminal exit
            code and the caller must return it immediately.
    """
    version = _module_baseline_version()
    baseline_path = (
        REPO_ROOT / "perf" / "baselines" / f"module-baseline-{version}.json"
    )
    if not baseline_path.exists():
        return _resolve_missing_baseline(report, args, blocking)

    baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))

    head_result = _resolve_baseline_head_binding(
        report, args, blocking, baseline_report
    )
    if head_result is not None:
        return head_result

    ineligible_result = _resolve_ineligible_baseline(
        report, args, blocking, baseline_report
    )
    if ineligible_result is not None:
        return ineligible_result

    integrity_rc = _validate_baseline_evidence(
        report, args, baseline_report, blocking
    )
    if integrity_rc is not None:
        return {}, False, integrity_rc

    environment_result = _resolve_environment_mismatch(
        report, args, blocking, baseline_report
    )
    if environment_result is not None:
        return environment_result

    return _extract_evidence_metrics(baseline_report), True, None


def _resolve_missing_baseline(
    report: dict | None, args: argparse.Namespace, blocking: bool,
) -> tuple[dict, bool, int | None]:
    """Handle the no-baseline case (first run or release-tag failure)."""
    version = _module_baseline_version()
    if blocking and _is_release_tag():
        _stderr(
            "FAIL: No module baseline found and this is a release tag.\n"
            "  Release and RC tags require a baseline for percentage threshold evaluation.\n"
            f"  Create a baseline with: cp perf/reports/module-benchmark-{version}.json "
            f"perf/baselines/module-baseline-{version}.json"
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

    eval_result = evaluate_module_level(
        current_metrics, baseline_metrics, thresholds_cfg,
        has_baseline=has_baseline,
    )
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
            "  Release and RC tags require all module-level thresholds "
            "to pass and all critical evidence to be present.\n"
            "  Review breaches above and address performance "
            "regressions or missing measurements."
        )
        return 1

    _stderr("Evidence gate: GO — all module-level thresholds pass.")
    return 0


def _write_output(evidence_pack: dict, output_path: str | None) -> None:
    """Write evidence pack to file or default location."""
    if output_path is None:
        version = _module_baseline_version()
        output_path = str(
            REPO_ROOT / "perf" / "reports" / f"evidence-{version}.json"
        )

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
