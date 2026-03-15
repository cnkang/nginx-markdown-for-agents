#!/usr/bin/env python3
"""Performance threshold engine for regression detection.

Compares current measurement data against a stored baseline using
configurable dual thresholds (warning / blocking) and produces a
Verdict Report in JSON format.

Exit codes:
    0 - All metrics pass or only warnings present (also used for skip).
    1 - At least one metric exceeds its blocking threshold.

Environment variables:
    PERF_GATE_SKIP=1  Skip all checks and emit a "skipped" verdict.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Default thresholds used when the thresholds config file is missing or a
# metric/tier/platform entry is absent.
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS = {
    "p50_ms": {"warning_pct": 15, "blocking_pct": 30},
    "p95_ms": {"warning_pct": 20, "blocking_pct": 40},
    "p99_ms": {"warning_pct": 20, "blocking_pct": 40},
    "peak_memory_bytes": {"warning_pct": 10, "blocking_pct": 25},
    "req_per_s": {"warning_pct": -15, "blocking_pct": -30},
    "input_mb_per_s": {"warning_pct": -15, "blocking_pct": -30},
}

# Metrics that are compared (non-informational).
COMPARABLE_METRICS = [
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "peak_memory_bytes",
    "req_per_s",
    "input_mb_per_s",
]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def load_json(path):
    """Load and return parsed JSON from *path*."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_direction_map(metrics_schema):
    """Return ``{metric_name: direction}`` from the metrics schema."""
    return {m["name"]: m["direction"] for m in metrics_schema.get("metrics", [])}


def get_threshold(thresholds_cfg, platform, tier, metric):
    """Look up the threshold for *metric* in *tier* on *platform*.

    Falls back to the ``"default"`` platform entry, then to the built-in
    ``DEFAULT_THRESHOLDS``.
    """
    platforms = thresholds_cfg.get("platforms", {})
    for plat_key in (platform, "default"):
        plat = platforms.get(plat_key, {})
        tier_cfg = plat.get(tier, {})
        metric_cfg = tier_cfg.get(metric)
        if metric_cfg is not None:
            return metric_cfg
    return DEFAULT_THRESHOLDS.get(metric, {"warning_pct": 15, "blocking_pct": 30})


def compute_deviation(current, baseline):
    """Return a percentage deviation suitable for threshold comparisons.

    The standard formula is ``(current - baseline) / baseline * 100``.
    When the baseline is zero we use explicit sentinels instead of infinity so
    the verdict report stays valid JSON:

    - baseline == 0 and current == 0 -> 0.0
    - baseline == 0 and current > 0 -> 100.0
    - baseline == 0 and current < 0 -> -100.0
    """
    if baseline == 0:
        if current == 0:
            return 0.0
        return 100.0 if current > 0 else -100.0
    return (current - baseline) / baseline * 100.0


def judge_metric(deviation_pct, direction, warning_pct, blocking_pct):
    """Return ``"pass"``, ``"warn"``, or ``"fail"`` for a single metric.

    For ``lower_is_better`` metrics a *positive* deviation is a regression.
    For ``higher_is_better`` metrics a *negative* deviation is a regression
    (thresholds are expressed as negative numbers, e.g. -15 / -30).
    """
    if direction == "lower_is_better":
        if deviation_pct > blocking_pct:
            return "fail"
        if deviation_pct > warning_pct:
            return "warn"
        return "pass"

    if direction == "higher_is_better":
        # blocking_pct and warning_pct are negative (e.g. -30, -15).
        if deviation_pct < blocking_pct:
            return "fail"
        if deviation_pct < warning_pct:
            return "warn"
        return "pass"

    # informational – never triggers a verdict.
    return "pass"


# ---------------------------------------------------------------------------
# Verdict report builders
# ---------------------------------------------------------------------------

def build_skipped_verdict(reason, platform, output_path):
    """Build and write a ``"skipped"`` Verdict Report."""
    report = {
        "schema_version": "1.0.0",
        "report_type": "verdict",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": None,
        "platform": platform,
        "overall_verdict": "skipped",
        "comparison": {
            "baseline_commit": None,
            "baseline_timestamp": None,
            "tiers": {},
        },
    }
    _write_json(report, output_path)
    _stderr(reason)
    return report


def _metric_comparison(cur_tier, base_tier, metric, direction_map, thresholds_cfg, platform, tier_name):
    """Return the comparison payload and verdict for one metric, or ``None``."""
    direction = direction_map.get(metric, "lower_is_better")
    if direction == "informational":
        return None

    cur_val = cur_tier.get(metric)
    base_val = base_tier.get(metric)
    if cur_val is None or base_val is None:
        return None

    deviation = compute_deviation(cur_val, base_val)
    thresh = get_threshold(thresholds_cfg, platform, tier_name, metric)
    verdict = judge_metric(
        deviation,
        direction,
        thresh.get("warning_pct", 15),
        thresh.get("blocking_pct", 30),
    )
    return {
        "name": metric,
        "payload": {
            "baseline": base_val,
            "current": cur_val,
            "deviation_pct": round(deviation, 4),
            "verdict": verdict,
        },
        "verdict": verdict,
    }


def _compare_tier_metrics(base_tier, cur_tier, direction_map, thresholds_cfg, platform, tier_name):
    """Return ``(tier_comparison, has_warning, has_failure)`` for one tier."""
    tier_comparison = {}
    has_warning = False
    has_failure = False

    for metric in COMPARABLE_METRICS:
        comparison = _metric_comparison(
            cur_tier,
            base_tier,
            metric,
            direction_map,
            thresholds_cfg,
            platform,
            tier_name,
        )
        if comparison is None:
            continue

        tier_comparison[comparison["name"]] = comparison["payload"]
        if comparison["verdict"] == "fail":
            has_failure = True
        elif comparison["verdict"] == "warn":
            has_warning = True

    return tier_comparison, has_warning, has_failure


def _overall_verdict(has_warning, has_failure):
    """Return the report-level verdict label."""
    if has_failure:
        return "fail"
    if has_warning:
        return "warn"
    return "pass"


def build_verdict_report(baseline, current, thresholds_cfg, direction_map, platform, output_path):
    """Compare *current* against *baseline* and produce a Verdict Report.

    Returns ``(report_dict, has_failure)``.
    """
    comparison_tiers = {}
    has_failure = False
    has_warning = False

    baseline_tiers = baseline.get("tiers", {})
    current_tiers = current.get("tiers", {})

    for tier_name, cur_tier in sorted(current_tiers.items()):
        base_tier = baseline_tiers.get(tier_name)
        if base_tier is None:
            continue

        tier_comparison, tier_has_warning, tier_has_failure = _compare_tier_metrics(
            base_tier,
            cur_tier,
            direction_map,
            thresholds_cfg,
            platform,
            tier_name,
        )
        has_warning = has_warning or tier_has_warning
        has_failure = has_failure or tier_has_failure

        if tier_comparison:
            comparison_tiers[tier_name] = tier_comparison

    overall = _overall_verdict(has_warning, has_failure)

    report = {
        "schema_version": "1.0.0",
        "report_type": "verdict",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": current.get("git_commit"),
        "platform": platform,
        "overall_verdict": overall,
        "comparison": {
            "baseline_commit": baseline.get("git_commit"),
            "baseline_timestamp": baseline.get("timestamp"),
            "tiers": comparison_tiers,
        },
    }

    _write_json(report, output_path)
    _print_text_summary(comparison_tiers, overall)
    return report, has_failure


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_json(data, path):
    """Serialise *data* as pretty-printed JSON to *path*."""
    if path is None:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _stderr(msg):
    """Print *msg* to stderr."""
    print(msg, file=sys.stderr)


def _print_text_summary(comparison_tiers, overall):
    """Print a human-readable summary to stderr."""
    _stderr("")
    _stderr("=== Performance Verdict Summary ===")
    _stderr("")
    for tier_name in sorted(comparison_tiers):
        metrics = comparison_tiers[tier_name]
        _stderr(f"  Tier: {tier_name}")
        for metric_name in sorted(metrics):
            entry = metrics[metric_name]
            v = entry["verdict"].upper()
            dev = entry["deviation_pct"]
            sign = "+" if dev >= 0 else ""
            tag = ""
            if v == "WARN":
                tag = " [WARN]"
            elif v == "FAIL":
                tag = " [FAIL]"
            _stderr(
                f"    {metric_name}: {entry['baseline']:.4g} -> {entry['current']:.4g}"
                f"  ({sign}{dev:.2f}%){tag}"
            )
        _stderr("")
    _stderr(f"  Overall: {overall.upper()}")
    _stderr("")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Performance threshold engine – compares current measurements "
        "against a baseline and emits a verdict report.",
    )
    parser.add_argument(
        "--baseline",
        required=True,
        help="Path to the baseline JSON file.",
    )
    parser.add_argument(
        "--current",
        required=True,
        help="Path to the current Measurement Report JSON.",
    )
    parser.add_argument(
        "--thresholds",
        required=True,
        help="Path to the thresholds configuration JSON.",
    )
    parser.add_argument(
        "--metrics-schema",
        required=True,
        help="Path to the metrics schema JSON.",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to write the Verdict Report JSON.",
    )
    parser.add_argument(
        "--platform",
        default="default",
        help="Platform name for threshold lookup (default: 'default').",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Entry point for the threshold engine."""
    args = parse_args(argv)

    # ---- PERF_GATE_SKIP ----
    if os.environ.get("PERF_GATE_SKIP") == "1":
        build_skipped_verdict(
            "Performance gate skipped by PERF_GATE_SKIP=1",
            args.platform,
            args.output_json,
        )
        return 0

    # ---- Load metrics schema for direction map ----
    try:
        metrics_schema = load_json(args.metrics_schema)
    except (OSError, json.JSONDecodeError) as exc:
        _stderr(f"Error loading metrics schema: {exc}")
        return 1

    direction_map = build_direction_map(metrics_schema)

    # ---- Load baseline (missing → skipped) ----
    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        platform_label = args.platform
        build_skipped_verdict(
            f"No baseline found for platform {platform_label}, skipping comparison",
            args.platform,
            args.output_json,
        )
        return 0

    try:
        baseline = load_json(args.baseline)
    except json.JSONDecodeError as exc:
        _stderr(f"Error parsing baseline JSON: {exc}")
        return 1

    # ---- Load current measurement report ----
    try:
        current = load_json(args.current)
    except (OSError, json.JSONDecodeError) as exc:
        _stderr(f"Error loading current measurement report: {exc}")
        return 1

    # ---- Load thresholds config (missing → use defaults with warning) ----
    thresholds_cfg = {"platforms": {}}
    thresholds_path = Path(args.thresholds)
    if not thresholds_path.exists():
        _stderr("Using default thresholds")
    else:
        try:
            thresholds_cfg = load_json(args.thresholds)
        except json.JSONDecodeError as exc:
            _stderr(f"Error parsing thresholds config: {exc}")
            return 1

    # ---- Schema version check ----
    baseline_version = baseline.get("schema_version", "")
    if baseline_version and baseline_version != "1.0.0":
        _stderr(f"Baseline schema version mismatch: expected 1.0.0, got {baseline_version}")
        return 1

    # ---- Run comparison ----
    _report, has_failure = build_verdict_report(
        baseline,
        current,
        thresholds_cfg,
        direction_map,
        args.platform,
        args.output_json,
    )

    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
