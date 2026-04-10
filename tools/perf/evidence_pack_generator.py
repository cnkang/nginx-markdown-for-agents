#!/usr/bin/env python3
"""Evidence pack generation and release gates evaluation for streaming performance benchmarks.

This module reads full-buffer and streaming Measurement Reports, evaluates multiple
evidence goals against configured targets, generates an Evidence Pack JSON conforming
to the schema, and evaluates release gates to produce a GO/NO_GO verdict.

Evidence goals evaluated:
    - bounded_memory: Linear regression on peak RSS for large/extra-large tiers
    - ttfb_improvement: Streaming TTFB vs full-buffer P50 for large tiers
    - no_regression_small_medium: Streaming P50 vs full-buffer P50 for small/medium tiers
    - streaming_supported_parity: Parity pass rate must be 100%
    - fallback_expected_correctness: Fallback correctness rate must be 100%

Release gates (all must PASS for GO verdict):
    - bounded_memory_evidence
    - ttfb_improvement_evidence
    - no_regression_evidence
    - parity_evidence
    - diff_testing_complete
    - rollout_docs_complete

P1 status fields (if_none_match_streaming, otel_integration, extra_formats) are
tracked but do NOT affect the release verdict.

Usage:
    python3 tools/perf/evidence_pack_generator.py \\
        --fullbuffer-report perf/reports/fullbuffer.json \\
        --streaming-report perf/reports/streaming.json \\
        --evidence-targets perf/config/evidence-targets.json \\
        --parity-report perf/reports/parity.json \\
        --output perf/reports/evidence-pack.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Tier classification constants
# ---------------------------------------------------------------------------

_LARGE_TIER_PREFIXES = ("large", "extra-large", "xlarge")
_SMALL_MEDIUM_TIER_PREFIXES = ("small", "medium")


def _is_large_tier(tier_name: str) -> bool:
    """
    Determine whether a tier name is classified as large (includes prefixes "large", "extra-large", or "xlarge").
    
    Returns:
        `True` if `tier_name` starts with one of the large-tier prefixes, `False` otherwise.
    """
    return any(tier_name.startswith(prefix) for prefix in _LARGE_TIER_PREFIXES)


def _is_small_medium_tier(tier_name: str) -> bool:
    """
    Determine whether a tier is classified as small or medium.
    
    Returns:
        `True` if the `tier_name` starts with "small" or "medium", `False` otherwise.
    """
    return any(tier_name.startswith(prefix) for prefix in _SMALL_MEDIUM_TIER_PREFIXES)


# ---------------------------------------------------------------------------
# Linear regression helper
# ---------------------------------------------------------------------------


def linear_regression_slope(x: list[float], y: list[float]) -> float:
    """
    Compute the least-squares slope of y with respect to x.
    
    Parameters:
        x (list[float]): Independent variable values; must contain at least 2 elements.
        y (list[float]): Dependent variable values; must be the same length as `x`.
    
    Returns:
        float: Slope of the best-fit line. Returns 0.0 when all `x` values are identical.
    
    Raises:
        ValueError: If `x` and `y` have different lengths or contain fewer than 2 points.
    """
    if len(x) != len(y):
        raise ValueError(f"x and y must have the same length, got {len(x)} and {len(y)}")
    if len(x) < 2:
        raise ValueError("At least 2 data points are required for linear regression")

    n = float(len(x))
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)

    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0.0:
        return 0.0

    return (n * sum_xy - sum_x * sum_y) / denominator


# ---------------------------------------------------------------------------
# Evidence evaluation functions
# ---------------------------------------------------------------------------


def evaluate_bounded_memory(
    streaming_report: dict,
    max_slope: float,
    min_data_points: int = 4,
) -> dict[str, Any]:
    """
    Evaluate whether peak RSS grows within a bounded slope relative to input size for large tiers.
    
    Parameters:
        streaming_report (dict): Streaming measurement report containing "tiers" and optional "streaming_metrics".
        max_slope (float): Threshold for allowed bytes-of-RSS growth per input byte; slope below this is considered passing.
        min_data_points (int): Minimum number of (input_bytes, peak_rss_bytes) pairs required to perform regression.
    
    Returns:
        dict: Result with keys:
            - "status": "PASS", "FAIL", or "INSUFFICIENT_DATA"
            - "slope": Computed regression slope (float; 0.0 when insufficient data)
            - "data_points": List of {"input_bytes": int, "peak_rss_bytes": int} used for regression
            - "data_point_count": Number of data points considered
            - "max_slope": Provided max_slope (present when regression ran) or
            - "required_data_points": min_data_points (present when status is "INSUFFICIENT_DATA")
    """
    tiers = streaming_report.get("tiers", {})
    streaming_metrics = streaming_report.get("streaming_metrics", {})
    data_points: list[dict[str, int]] = []

    for tier_name, tier_data in sorted(tiers.items()):
        if _is_large_tier(tier_name):
            # Rust binary emits 'html_bytes' but docs/schema use 'input_bytes'.
            # Accept both for compatibility, preferring 'html_bytes' (production key).
            input_bytes = tier_data.get("html_bytes") or tier_data.get("input_bytes")
            # Peak memory is in streaming_metrics sub-object for streaming runs,
            # but may also be in tiers for full-buffer or backward compatibility.
            peak_memory = None
            sm = streaming_metrics.get(tier_name, {})
            if isinstance(sm, dict):
                peak_memory = sm.get("peak_memory_bytes")
            if peak_memory is None:
                peak_memory = tier_data.get("peak_memory_bytes")
            if input_bytes is not None and peak_memory is not None:
                data_points.append({
                    "input_bytes": input_bytes,
                    "peak_rss_bytes": peak_memory,
                })

    if len(data_points) < min_data_points:
        return {
            "status": "INSUFFICIENT_DATA",
            "slope": 0.0,
            "data_points": data_points,
            "data_point_count": len(data_points),
            "required_data_points": min_data_points,
        }

    x = [float(dp["input_bytes"]) for dp in data_points]
    y = [float(dp["peak_rss_bytes"]) for dp in data_points]

    try:
        slope = linear_regression_slope(x, y)
    except ValueError:
        return {
            "status": "INSUFFICIENT_DATA",
            "slope": 0.0,
            "data_points": data_points,
            "data_point_count": len(data_points),
            "required_data_points": min_data_points,
        }

    status = "PASS" if slope < max_slope else "FAIL"
    return {
        "status": status,
        "slope": slope,
        "data_points": data_points,
        "data_point_count": len(data_points),
        "max_slope": max_slope,
    }


def evaluate_ttfb_improvement(
    streaming_report: dict,
    fullbuffer_report: dict,
    max_ratio: float = 0.5,
) -> dict[str, Any]:
    """Evaluate TTFB improvement evidence for large tiers.

    For each large tier, checks whether streaming TTFB is at most
    full-buffer P50 latency multiplied by max_ratio.

    Parameters:
        streaming_report: Streaming measurement report with a "tiers" mapping.
            Each tier should have a "ttfb_ms" key (time-to-first-byte).
        fullbuffer_report: Full-buffer measurement report with a "tiers" mapping.
            Each tier should have a "p50_ms" key.
        max_ratio: Maximum allowed ratio of streaming_ttfb / fullbuffer_p50.
            A value of 0.5 means streaming TTFB must be at most 50% of full-buffer P50.

    Returns:
        dict: Evaluation result with keys:
            - "status": "PASS" or "FAIL"
            - "details": Per-tier detail dict mapping tier name to:
                - "streaming_ttfb_ms": Streaming TTFB value
                - "fullbuffer_p50_ms": Full-buffer P50 value
                - "ratio": Computed ratio (streaming_ttfb / fullbuffer_p50)
                - "pass": Whether this tier passes the threshold
    """
    streaming_tiers = streaming_report.get("tiers", {})
    fullbuffer_tiers = fullbuffer_report.get("tiers", {})
    streaming_metrics = streaming_report.get("streaming_metrics", {})

    details: dict[str, dict[str, Any]] = {}
    all_pass = True

    for tier_name in sorted(streaming_tiers.keys()):
        if not _is_large_tier(tier_name):
            continue

        # TTFB is in streaming_metrics, not tiers
        streaming_ttfb = None
        if tier_name in streaming_metrics:
            streaming_ttfb = streaming_metrics[tier_name].get("ttfb_ms")
        # Fallback: check tiers for backward compatibility
        if streaming_ttfb is None:
            streaming_ttfb = streaming_tiers[tier_name].get("ttfb_ms")

        fullbuffer_p50 = fullbuffer_tiers.get(tier_name, {}).get("p50_ms")

        if streaming_ttfb is None or fullbuffer_p50 is None:
            details[tier_name] = {
                "streaming_ttfb_ms": streaming_ttfb,
                "fullbuffer_p50_ms": fullbuffer_p50,
                "ratio": None,
                "pass": False,
                "reason": "missing_data",
            }
            all_pass = False
            continue

        if fullbuffer_p50 == 0.0:
            ratio = float("inf") if streaming_ttfb > 0 else 0.0
        else:
            ratio = streaming_ttfb / fullbuffer_p50

        tier_pass = ratio <= max_ratio
        if not tier_pass:
            all_pass = False

        details[tier_name] = {
            "streaming_ttfb_ms": streaming_ttfb,
            "fullbuffer_p50_ms": fullbuffer_p50,
            "ratio": round(ratio, 4),
            "pass": tier_pass,
        }

    if not details:
        all_pass = False

    return {
        "status": "PASS" if all_pass else "FAIL",
        "details": details,
        "max_ratio": max_ratio,
    }


def evaluate_no_regression(
    streaming_report: dict,
    fullbuffer_report: dict,
    max_ratio: float = 1.3,
) -> dict[str, Any]:
    """
    Determines whether streaming P50 latencies for small and medium tiers stay within a configurable ratio of full-buffer P50 latencies.
    
    Parameters:
        streaming_report (dict): Streaming measurement report containing "tiers" and optionally "streaming_metrics".
        fullbuffer_report (dict): Full-buffer measurement report containing "tiers".
        max_ratio (float): Maximum allowed value of (streaming_p50_ms / fullbuffer_p50_ms). For example, 1.3 allows streaming P50 up to 130% of full-buffer P50.
    
    Returns:
        dict: Evaluation result with keys:
            - "status": `"PASS"` if all evaluated small/medium tiers meet the threshold, `"FAIL"` otherwise.
            - "details": Mapping from tier name to a dict with:
                - "streaming_p50_ms": Observed streaming P50 (or `None` if missing).
                - "fullbuffer_p50_ms": Observed full-buffer P50 (or `None` if missing).
                - "ratio": Computed ratio rounded to 4 decimals (or `None` if missing data).
                - "pass": `true` if the tier's ratio <= `max_ratio`, `false` otherwise.
                - "reason": Present when data is missing (value `"missing_data"`).
            - "max_ratio": The `max_ratio` value used for evaluation.
    """
    streaming_tiers = streaming_report.get("tiers", {})
    fullbuffer_tiers = fullbuffer_report.get("tiers", {})
    streaming_metrics = streaming_report.get("streaming_metrics", {})

    details: dict[str, dict[str, Any]] = {}
    all_pass = True

    for tier_name in sorted(streaming_tiers.keys()):
        if not _is_small_medium_tier(tier_name):
            continue

        # Streaming p50 is in streaming_metrics for --engine both runs,
        # but may be in tiers for standalone streaming runs.
        streaming_p50 = None
        if tier_name in streaming_metrics:
            streaming_p50 = streaming_metrics[tier_name].get("p50_ms")
        if streaming_p50 is None:
            streaming_p50 = streaming_tiers[tier_name].get("p50_ms")

        fullbuffer_p50 = fullbuffer_tiers.get(tier_name, {}).get("p50_ms")

        if streaming_p50 is None or fullbuffer_p50 is None:
            details[tier_name] = {
                "streaming_p50_ms": streaming_p50,
                "fullbuffer_p50_ms": fullbuffer_p50,
                "ratio": None,
                "pass": False,
                "reason": "missing_data",
            }
            all_pass = False
            continue

        if fullbuffer_p50 == 0.0:
            ratio = float("inf") if streaming_p50 > 0 else 0.0
        else:
            ratio = streaming_p50 / fullbuffer_p50

        tier_pass = ratio <= max_ratio
        if not tier_pass:
            all_pass = False

        details[tier_name] = {
            "streaming_p50_ms": streaming_p50,
            "fullbuffer_p50_ms": fullbuffer_p50,
            "ratio": round(ratio, 4),
            "pass": tier_pass,
        }

    if not details:
        all_pass = False

    return {
        "status": "PASS" if all_pass else "FAIL",
        "details": details,
        "max_ratio": max_ratio,
    }


# Tolerance for floating-point parity comparisons (e.g. 0.9999999999999998 ≈ 1.0).
_PARITY_EPSILON = 1e-9


def evaluate_parity_dual_threshold(
    parity_report: dict | None,
) -> dict[str, Any]:
    """Evaluate parity dual-threshold evidence.

    Checks two conditions against the parity report:
    - streaming_supported_parity: pass_rate must be 1.0 (100%)
    - fallback_expected_correctness: correctness_rate must be 1.0 (100%)

    Parameters:
        parity_report: Parity measurement report, or None if not available.
            Expected to have a "summary" key with "pass_rate" and "correctness_rate"
            fields, or equivalent per-tier breakdown.

    Returns:
        dict: Evaluation result with keys:
            - "streaming_supported_parity": {"status": ..., "pass_rate": ..., "threshold": 1.0}
            - "fallback_expected_correctness": {"status": ..., "correctness_rate": ..., "threshold": 1.0}
    """
    if parity_report is None:
        return {
            "streaming_supported_parity": {
                "status": "UNKNOWN",
                "pass_rate": None,
                "threshold": 1.0,
                "reason": "parity_report_not_provided",
            },
            "fallback_expected_correctness": {
                "status": "UNKNOWN",
                "correctness_rate": None,
                "threshold": 1.0,
                "reason": "parity_report_not_provided",
            },
        }

    summary = parity_report.get("summary", {})
    pass_rate = summary.get("pass_rate")
    correctness_rate = summary.get("correctness_rate")

    # Also check for per-tier breakdown if available
    tiers = parity_report.get("tiers", {})
    tier_details: dict[str, dict[str, Any]] = {}
    for tier_name, tier_data in sorted(tiers.items()):
        tier_details[tier_name] = {
            "pass_rate": tier_data.get("pass_rate"),
            "correctness_rate": tier_data.get("correctness_rate"),
        }

    # Use epsilon comparison to tolerate floating-point imprecision
    # (e.g. 180/180 may yield 0.9999999999999998 instead of exactly 1.0).
    streaming_parity_pass = pass_rate is not None and pass_rate >= (1.0 - _PARITY_EPSILON)
    fallback_correctness_pass = correctness_rate is not None and correctness_rate >= (1.0 - _PARITY_EPSILON)

    return {
        "streaming_supported_parity": {
            "status": "PASS" if streaming_parity_pass else "FAIL",
            "pass_rate": pass_rate,
            "threshold": 1.0,
        },
        "fallback_expected_correctness": {
            "status": "PASS" if fallback_correctness_pass else "FAIL",
            "correctness_rate": correctness_rate,
            "threshold": 1.0,
        },
        "tier_details": tier_details if tier_details else None,
    }


# ---------------------------------------------------------------------------
# Evidence pack assembly
# ---------------------------------------------------------------------------


def _get_git_commit() -> str:
    """Return the current short git commit hash, or 'unknown' if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _load_json(path: str | Path | None) -> dict | None:
    """Load and return JSON data from *path*, or None if path is None or file doesn't exist."""
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_evidence_pack(
    fullbuffer_report: dict,
    streaming_report: dict,
    evidence_targets: dict,
    parity_report: dict | None = None,
) -> dict:
    """Generate a complete Evidence Pack by evaluating all evidence goals.

    Merges full-buffer and streaming reports, evaluates all evidence goals
    against configured targets, assembles release gates, and produces a
    verdict.

    Parameters:
        fullbuffer_report: Full-buffer measurement report (dict with "tiers" mapping).
        streaming_report: Streaming measurement report (dict with "tiers" mapping).
        evidence_targets: Configuration dict specifying thresholds for each evidence goal.
            Expected keys:
            - "bounded_memory": {"max_slope": float, "min_data_points": int}
            - "ttfb_improvement": {"max_ratio": float}
            - "no_regression": {"max_ratio": float}
        parity_report: Optional parity measurement report. If None, parity
            evidence gates will have status "UNKNOWN".

    Returns:
        dict: Evidence Pack conforming to the schema with keys:
            - "schema_version": "1.0.0"
            - "type": "evidence-pack"
            - "metadata": {"timestamp", "git_commit", "platform", "engine_version"}
            - "fullbuffer_report": The input full-buffer report (subset)
            - "streaming_report": The input streaming report (subset)
            - "parity": Parity report subset or None
            - "evidence_targets": Evaluation results for each evidence goal
            - "release_gates": Gate statuses (PASS/FAIL)
            - "streaming_evidence_verdict": "GO" or "NO_GO"
            - "p1_status": P1 status fields (do NOT affect verdict)
    """
    # --- Evaluate evidence goals ---
    # Bounded memory: JSON uses 'max_slope_bytes_per_input_byte', code accepts both.
    bounded_memory_config = evidence_targets.get("bounded_memory", {})
    if "max_slope_bytes_per_input_byte" in bounded_memory_config:
        max_slope = bounded_memory_config["max_slope_bytes_per_input_byte"]
    elif "max_slope" in bounded_memory_config:
        max_slope = bounded_memory_config["max_slope"]
    else:
        max_slope = 0.5
    min_data_points = bounded_memory_config.get("min_data_points", 4)

    # TTFB improvement
    ttfb_config = evidence_targets.get("ttfb_improvement", {})
    ttfb_max_ratio = ttfb_config.get("max_ratio", 0.5)

    # No regression: JSON uses 'no_regression_small_medium', code accepts both
    no_regression_config = evidence_targets.get(
        "no_regression_small_medium"
    ) or evidence_targets.get("no_regression", {})
    no_regression_max_ratio = no_regression_config.get("max_ratio", 1.3)

    bounded_memory_result = evaluate_bounded_memory(
        streaming_report, max_slope, min_data_points
    )
    ttfb_result = evaluate_ttfb_improvement(
        streaming_report, fullbuffer_report, ttfb_max_ratio
    )
    no_regression_result = evaluate_no_regression(
        streaming_report, fullbuffer_report, no_regression_max_ratio
    )
    parity_result = evaluate_parity_dual_threshold(parity_report)

    # --- Assemble evidence targets ---
    evidence_target_results = {
        "bounded_memory": bounded_memory_result,
        "ttfb_improvement": ttfb_result,
        "no_regression_small_medium": no_regression_result,
        "streaming_supported_parity": parity_result["streaming_supported_parity"],
        "fallback_expected_correctness": parity_result["fallback_expected_correctness"],
    }

    # --- Evaluate release gates ---
    release_gates = {
        "bounded_memory_evidence": bounded_memory_result["status"],
        "ttfb_improvement_evidence": ttfb_result["status"],
        "no_regression_evidence": no_regression_result["status"],
        "parity_evidence": (
            "PASS"
            if (
                parity_result["streaming_supported_parity"]["status"] == "PASS"
                and parity_result["fallback_expected_correctness"]["status"] == "PASS"
            )
            else "FAIL"
        ),
        "diff_testing_complete": evidence_targets.get("diff_testing_complete", "PASS"),
        "rollout_docs_complete": evidence_targets.get("rollout_docs_complete", "PASS"),
    }

    # Normalize gate statuses: INSUFFICIENT_DATA and UNKNOWN become FAIL for gates
    for gate_name in list(release_gates.keys()):
        status = release_gates[gate_name]
        if status not in ("PASS", "FAIL"):
            release_gates[gate_name] = "FAIL"

    # --- Compute verdict ---
    verdict = "GO" if all(v == "PASS" for v in release_gates.values()) else "NO_GO"

    # --- Assemble evidence pack ---
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    evidence_pack: dict[str, Any] = {
        "schema_version": "1.0.0",
        "type": "evidence-pack",
        "metadata": {
            "timestamp": timestamp,
            "git_commit": _get_git_commit(),
            "platform": streaming_report.get("platform", "unknown"),
            "engine_version": "1.0.0",
        },
        "fullbuffer_report": {
            "schema_version": fullbuffer_report.get("schema_version", ""),
            "timestamp": fullbuffer_report.get("timestamp", ""),
            "git_commit": fullbuffer_report.get("git_commit", ""),
            "platform": fullbuffer_report.get("platform", ""),
            "tiers": {
                tier_name: {
                    "p50_ms": tier_data.get("p50_ms"),
                    "p95_ms": tier_data.get("p95_ms"),
                    "p99_ms": tier_data.get("p99_ms"),
                    # Rust emits 'html_bytes'; accept both for compatibility.
                    "input_bytes": tier_data.get("html_bytes") or tier_data.get("input_bytes"),
                }
                for tier_name, tier_data in fullbuffer_report.get("tiers", {}).items()
            },
        },
        "streaming_report": {
            "schema_version": streaming_report.get("schema_version", ""),
            "timestamp": streaming_report.get("timestamp", ""),
            "git_commit": streaming_report.get("git_commit", ""),
            "platform": streaming_report.get("platform", ""),
            "tiers": {
                tier_name: {
                    "p50_ms": tier_data.get("p50_ms"),
                    "p95_ms": tier_data.get("p95_ms"),
                    "p99_ms": tier_data.get("p99_ms"),
                    "ttfb_ms": tier_data.get("ttfb_ms"),
                    # Rust emits 'html_bytes'; accept both for compatibility.
                    "input_bytes": tier_data.get("html_bytes") or tier_data.get("input_bytes"),
                    # Peak memory is in streaming_metrics for streaming runs.
                    "peak_memory_bytes": tier_data.get("peak_memory_bytes"),
                }
                for tier_name, tier_data in streaming_report.get("tiers", {}).items()
            },
        },
        "parity": None,
        "evidence_targets": evidence_target_results,
        "release_gates": release_gates,
        "streaming_evidence_verdict": verdict,
        "p1_status": {
            "if_none_match_streaming": "deferred",
            "otel_integration": "deferred",
            "extra_formats": "deferred",
        },
    }

    # Include parity report subset if available
    if parity_report is not None:
        evidence_pack["parity"] = {
            "schema_version": parity_report.get("schema_version", ""),
            "timestamp": parity_report.get("timestamp", ""),
            "summary": parity_report.get("summary", {}),
        }

    return evidence_pack


# ---------------------------------------------------------------------------
# Release gates evaluation
# ---------------------------------------------------------------------------


def evaluate_release_gates(evidence_pack: dict) -> dict:
    """Evaluate release gates from an existing Evidence Pack.

    Checks all evidence target statuses and returns gate-level results
    along with the final streaming evidence verdict.

    Parameters:
        evidence_pack: A previously generated Evidence Pack dict.

    Returns:
        dict: Release gates evaluation with keys:
            - "release_gates": Dict of gate name to status (PASS/FAIL)
            - "streaming_evidence_verdict": "GO" or "NO_GO"
    """
    release_gates = evidence_pack.get("release_gates", {})
    verdict = evidence_pack.get("streaming_evidence_verdict", "NO_GO")

    # Re-compute verdict from gates for consistency check
    computed_verdict = (
        "GO" if all(v == "PASS" for v in release_gates.values()) else "NO_GO"
    )

    return {
        "release_gates": release_gates,
        "streaming_evidence_verdict": verdict,
        "_computed_verdict": computed_verdict,
        "verdict_consistent": verdict == computed_verdict,
    }


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def print_human_summary(evidence_pack: dict, file: Any = sys.stderr) -> None:
    """Output a human-readable evidence summary.

    Shows each evidence goal status (PASS/FAIL), release gates status,
    and the final GO/NO_GO verdict.

    Parameters:
        evidence_pack: Evidence Pack dict to summarize.
        file: Output file object (defaults to sys.stderr).
    """
    metadata = evidence_pack.get("metadata", {})
    evidence_targets = evidence_pack.get("evidence_targets", {})
    release_gates = evidence_pack.get("release_gates", {})
    verdict = evidence_pack.get("streaming_evidence_verdict", "NO_GO")
    p1_status = evidence_pack.get("p1_status", {})

    print("", file=file)
    print("=" * 60, file=file)
    print("  Streaming Performance Evidence Summary", file=file)
    print("=" * 60, file=file)
    print("", file=file)
    print(f"  Timestamp:    {metadata.get('timestamp', 'N/A')}", file=file)
    print(f"  Git Commit:   {metadata.get('git_commit', 'N/A')}", file=file)
    print(f"  Platform:     {metadata.get('platform', 'N/A')}", file=file)
    print("", file=file)

    # Evidence goals
    print("-" * 60, file=file)
    print("  Evidence Goals", file=file)
    print("-" * 60, file=file)

    for goal_name, result in evidence_targets.items():
        status = result.get("status", "UNKNOWN")
        status_marker = f"[{status}]"
        print(f"  {goal_name}: {status_marker}", file=file)

        # Print key details per goal type
        if goal_name == "bounded_memory":
            slope = result.get("slope", 0.0)
            data_points = result.get("data_point_count", 0)
            max_slope = result.get("max_slope", "N/A")
            print(
                f"    slope={slope:.6f}, data_points={data_points}, max_slope={max_slope}",
                file=file,
            )
        elif goal_name == "ttfb_improvement":
            max_ratio = result.get("max_ratio", "N/A")
            details = result.get("details", {})
            print(f"    max_ratio={max_ratio}, tiers_evaluated={len(details)}", file=file)
            for tier_name, tier_detail in details.items():
                ratio = tier_detail.get("ratio")
                ratio_str = f"{ratio:.4f}" if ratio is not None else "N/A"
                tier_pass = "PASS" if tier_detail.get("pass") else "FAIL"
                print(f"      {tier_name}: ratio={ratio_str} [{tier_pass}]", file=file)
        elif goal_name == "no_regression_small_medium":
            max_ratio = result.get("max_ratio", "N/A")
            details = result.get("details", {})
            print(f"    max_ratio={max_ratio}, tiers_evaluated={len(details)}", file=file)
            for tier_name, tier_detail in details.items():
                ratio = tier_detail.get("ratio")
                ratio_str = f"{ratio:.4f}" if ratio is not None else "N/A"
                tier_pass = "PASS" if tier_detail.get("pass") else "FAIL"
                print(f"      {tier_name}: ratio={ratio_str} [{tier_pass}]", file=file)
        elif goal_name in ("streaming_supported_parity", "fallback_expected_correctness"):
            rate_key = (
                "pass_rate" if goal_name == "streaming_supported_parity" else "correctness_rate"
            )
            rate = result.get(rate_key)
            rate_str = f"{rate:.4f}" if rate is not None else "N/A"
            print(f"    {rate_key}={rate_str}", file=file)

    print("", file=file)

    # Release gates
    print("-" * 60, file=file)
    print("  Release Gates", file=file)
    print("-" * 60, file=file)

    for gate_name, gate_status in release_gates.items():
        status_marker = f"[{gate_status}]"
        print(f"  {gate_name}: {status_marker}", file=file)

    print("", file=file)

    # P1 status (informational, does not affect verdict)
    if p1_status:
        print("-" * 60, file=file)
        print("  P1 Status (informational, does not affect verdict)", file=file)
        print("-" * 60, file=file)
        for p1_name, p1_value in p1_status.items():
            print(f"  {p1_name}: {p1_value}", file=file)
        print("", file=file)

    # Final verdict
    print("=" * 60, file=file)
    verdict_marker = f"[{verdict}]"
    print(f"  Streaming Evidence Verdict: {verdict_marker}", file=file)
    print("=" * 60, file=file)
    print("", file=file)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for evidence pack generation."""
    parser = argparse.ArgumentParser(
        description="Generate evidence pack and evaluate release gates for streaming performance benchmarks.",
    )
    parser.add_argument(
        "--fullbuffer-report",
        required=True,
        help="Path to the full-buffer measurement report JSON.",
    )
    parser.add_argument(
        "--streaming-report",
        required=True,
        help="Path to the streaming measurement report JSON.",
    )
    parser.add_argument(
        "--evidence-targets",
        required=True,
        help="Path to the evidence targets configuration JSON.",
    )
    parser.add_argument(
        "--parity-report",
        default=None,
        help="Path to the parity report JSON (optional). If omitted, parity gates will be UNKNOWN.",
    )
    parser.add_argument(
        "--output",
        required=False,
        default=None,
        help="Path to write the evidence pack JSON output. Required unless --summary-only is set.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        default=False,
        help="Only print the human-readable summary, do not write the JSON file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for evidence pack generation.

    Loads JSON input files, calls generate_evidence_pack(), writes the
    output JSON, and prints a human-readable summary to stderr.

    Parameters:
        argv: Optional list of CLI arguments (defaults to sys.argv[1:]).

    Returns:
        int: Exit code 0 on success, 1 on error.
    """
    args = build_parser().parse_args(argv)

    # Load input files
    try:
        fullbuffer_report = _load_json(args.fullbuffer_report)
        if fullbuffer_report is None:
            print(
                f"ERROR: failed to load full-buffer report: {args.fullbuffer_report}",
                file=sys.stderr,
            )
            return 1

        streaming_report = _load_json(args.streaming_report)
        if streaming_report is None:
            print(
                f"ERROR: failed to load streaming report: {args.streaming_report}",
                file=sys.stderr,
            )
            return 1

        evidence_targets = _load_json(args.evidence_targets)
        if evidence_targets is None:
            print(
                f"ERROR: failed to load evidence targets: {args.evidence_targets}",
                file=sys.stderr,
            )
            return 1

        parity_report = _load_json(args.parity_report)
        # parity_report being None is acceptable — handled gracefully
    except json.JSONDecodeError as exc:
        print(f"ERROR: JSON parse error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: file I/O error: {exc}", file=sys.stderr)
        return 1

    # Validate output path requirement
    if not args.summary_only and not args.output:
        print(
            "ERROR: --output is required unless --summary-only is set.",
            file=sys.stderr,
        )
        return 1

    # Generate evidence pack
    evidence_pack = generate_evidence_pack(
        fullbuffer_report=fullbuffer_report,
        streaming_report=streaming_report,
        evidence_targets=evidence_targets,
        parity_report=parity_report,
    )

    # Write output JSON (unless summary-only mode)
    if not args.summary_only:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(evidence_pack, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"Evidence pack written to {output_path}", file=sys.stderr)

    # Print human-readable summary
    print_human_summary(evidence_pack)

    # Exit code reflects verdict
    verdict = evidence_pack.get("streaming_evidence_verdict", "NO_GO")
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
