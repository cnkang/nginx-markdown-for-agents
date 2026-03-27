#!/usr/bin/env python3
"""Compare two Unified Reports and produce a verdict.

Compares baseline vs current corpus benchmark reports on quality metrics
(fallback rate, token reduction) and latency percentiles. Produces a
corpus-verdict JSON with per-metric and overall verdicts.

Usage:
    python3 tools/perf/compare_reports.py \
        --baseline perf/reports/baseline.json \
        --current perf/reports/current.json \
        --thresholds perf/quality-thresholds.json \
        --output perf/reports/corpus-verdict.json
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_schema import validate_report  # noqa: E402
from report_utils import load_json, write_json  # noqa: E402


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------


def compute_delta_absolute(current: float, baseline: float) -> float:
    """Compute absolute delta (current - baseline)."""
    return current - baseline


def compute_delta_percent(current: float, baseline: float) -> float:
    """Compute percentage change: (current - baseline) / baseline * 100."""
    if math.isclose(baseline, 0.0):
        return 0.0 if math.isclose(current, 0.0) else 100.0
    return (current - baseline) / baseline * 100.0


def judge_metric_absolute(
    delta: float, direction: str, warning_delta: float, blocking_delta: float
) -> str:
    """Judge a metric using absolute delta thresholds.

    For lower-is-better: positive delta = regression.
    For higher-is-better: negative delta = regression.
    """
    if direction == "lower-is-better":
        if delta >= blocking_delta:
            return "fail"
        if delta >= warning_delta:
            return "warn"
    else:  # higher-is-better
        if delta <= blocking_delta:
            return "fail"
        if delta <= warning_delta:
            return "warn"

    return "pass"


def judge_metric_percent(
    pct_change: float, direction: str, warning_pct: float, blocking_pct: float
) -> str:
    """Judge a metric using percentage-based thresholds.

    For lower-is-better: positive pct_change = regression.
    """
    if direction == "lower-is-better":
        if pct_change >= blocking_pct:
            return "fail"
        if pct_change >= warning_pct:
            return "warn"
    else:  # higher-is-better
        if pct_change <= -blocking_pct:
            return "fail"
        if pct_change <= -warning_pct:
            return "warn"

    return "pass"


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def compare_metric(
    baseline_val: float,
    current_val: float,
    threshold_cfg: dict,
) -> dict:
    """Compare a single metric and return the comparison result."""
    direction = threshold_cfg.get("direction", "lower-is-better")

    if "warning-delta" in threshold_cfg:
        delta = compute_delta_absolute(current_val, baseline_val)
        verdict = judge_metric_absolute(
            delta,
            direction,
            threshold_cfg["warning-delta"],
            threshold_cfg["blocking-delta"],
        )
    else:
        delta = compute_delta_percent(current_val, baseline_val)
        verdict = judge_metric_percent(
            delta,
            direction,
            threshold_cfg["warning-pct"],
            threshold_cfg["blocking-pct"],
        )

    return {
        "baseline": baseline_val,
        "current": current_val,
        "delta": round(delta, 4),
        "verdict": verdict,
    }


def compare_fixtures(
    baseline_fixtures: list[dict], current_fixtures: list[dict]
) -> list[dict]:
    """Compare per-fixture conversion results between baseline and current.

    Fixtures present in only one report are flagged as 'added' or 'removed'.
    Removed fixtures are treated as regressions to prevent silent corpus
    shrinkage from masking quality degradation.
    """
    baseline_map = {f["fixture-id"]: f for f in baseline_fixtures}
    current_map = {f["fixture-id"]: f for f in current_fixtures}

    changes = []
    all_ids = sorted(set(baseline_map.keys()) | set(current_map.keys()))

    for fid in all_ids:
        b = baseline_map.get(fid)
        c = current_map.get(fid)

        if b is None:
            changes.append(
                {
                    "fixture-id": fid,
                    "baseline-result": None,
                    "current-result": c["conversion-result"],
                    "change-type": "added",
                }
            )
            continue

        if c is None:
            changes.append(
                {
                    "fixture-id": fid,
                    "baseline-result": b["conversion-result"],
                    "current-result": None,
                    "change-type": "removed",
                }
            )
            continue

        b_result = b["conversion-result"]
        c_result = c["conversion-result"]

        if b_result == c_result:
            change_type = "unchanged"
        elif _is_regression(b_result, c_result):
            change_type = "regression"
        else:
            change_type = "improvement"

        if change_type != "unchanged":
            changes.append(
                {
                    "fixture-id": fid,
                    "baseline-result": b_result,
                    "current-result": c_result,
                    "change-type": change_type,
                }
            )

    return changes


def _is_regression(baseline_result: str, current_result: str) -> bool:
    """Determine if a conversion result change is a regression."""
    rank = {"converted": 0, "skipped": 1, "failed-open": 2}
    return rank.get(current_result, 2) > rank.get(baseline_result, 2)


LATENCY_METRICS = {"p50-latency-ms", "p95-latency-ms", "p99-latency-ms"}


def compare_reports(
    baseline: dict,
    current: dict,
    thresholds: dict,
    skip_metrics: set[str] | None = None,
) -> dict:
    """Compare two Unified Reports and produce a verdict.

    When skip_metrics is provided, those metrics are excluded from comparison
    and verdict computation (useful for skipping latency when platforms differ).
    """
    b_summary = baseline["summary"]
    c_summary = current["summary"]
    threshold_defs = thresholds.get("thresholds", {})
    skip = skip_metrics or set()

    metric_comparisons = {}
    for metric_name, threshold_cfg in threshold_defs.items():
        if metric_name in skip:
            continue
        b_val = b_summary.get(metric_name, 0.0)
        c_val = c_summary.get(metric_name, 0.0)
        metric_comparisons[metric_name] = compare_metric(
            b_val, c_val, threshold_cfg
        )

    fixture_changes = compare_fixtures(
        baseline.get("fixtures", []), current.get("fixtures", [])
    )

    verdicts = [mc["verdict"] for mc in metric_comparisons.values()]
    if "fail" in verdicts:
        overall = "fail"
    elif "warn" in verdicts:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "schema-version": "1.0.0",
        "report-type": "corpus-verdict",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git-commit": current.get("metadata", {}).get("git-commit", "unknown"),
        "platform": current.get("metadata", {}).get("platform", "unknown"),
        "overall-verdict": overall,
        "baseline-corpus-version": baseline.get("metadata", {}).get(
            "corpus-version", "unknown"
        ),
        "current-corpus-version": current.get("metadata", {}).get(
            "corpus-version", "unknown"
        ),
        "metric-comparisons": metric_comparisons,
        "fixture-changes": fixture_changes,
    }


def verdict_to_exit_code(verdict: str) -> int:
    """Map overall verdict to process exit code."""
    return 1 if verdict == "fail" else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two corpus benchmark Unified Reports."
    )
    parser.add_argument(
        "--baseline", required=True, help="Path to baseline Unified Report JSON."
    )
    parser.add_argument(
        "--current", required=True, help="Path to current Unified Report JSON."
    )
    parser.add_argument(
        "--thresholds",
        required=True,
        help="Path to quality-thresholds.json.",
    )
    parser.add_argument(
        "--output", required=True, help="Path for the verdict JSON output."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)

    try:
        baseline = load_json(args.baseline)
        current = load_json(args.current)
        thresholds = load_json(args.thresholds)
    except Exception as e:
        print(f"ERROR: failed to load input files: {e}", file=sys.stderr)
        return 1

    # Validate both reports against schema
    for label, report, path in [
        ("baseline", baseline, args.baseline),
        ("current", current, args.current),
    ]:
        if schema_errors := validate_report(report):
            print(
                f"ERROR: {label} report ({path}) fails schema validation:",
                file=sys.stderr,
            )
            for err in schema_errors:
                print(f"  - {err}", file=sys.stderr)
            return 1

    # Warn on corpus version mismatch
    b_cv = baseline.get("metadata", {}).get("corpus-version", "")
    c_cv = current.get("metadata", {}).get("corpus-version", "")
    if b_cv and c_cv and b_cv != c_cv:
        print(
            f"WARNING: corpus version mismatch: baseline={b_cv}, current={c_cv}",
            file=sys.stderr,
        )

    # Warn on platform mismatch (latency comparisons may be noisy)
    b_plat = baseline.get("metadata", {}).get("platform", "")
    c_plat = current.get("metadata", {}).get("platform", "")
    skip_metrics: set[str] = set()
    if b_plat and c_plat and b_plat != c_plat:
        print(
            f"WARNING: platform mismatch: baseline={b_plat}, current={c_plat}"
            " — skipping latency comparisons",
            file=sys.stderr,
        )
        skip_metrics = LATENCY_METRICS

    verdict_report = compare_reports(
        baseline, current, thresholds, skip_metrics=skip_metrics
    )
    write_json(verdict_report, args.output)

    overall = verdict_report["overall-verdict"]
    print(f"Verdict: {overall}")

    if overall == "fail":
        failed = [
            name
            for name, mc in verdict_report["metric-comparisons"].items()
            if mc["verdict"] == "fail"
        ]
        print(f"Failed metrics: {', '.join(failed)}", file=sys.stderr)

    return verdict_to_exit_code(overall)


if __name__ == "__main__":
    raise SystemExit(main())
