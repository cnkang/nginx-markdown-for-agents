#!/usr/bin/env python3
"""Shared helpers for perf report workflows and local tooling."""

from __future__ import annotations

import argparse
import copy
import json
import platform as python_platform
import statistics
from pathlib import Path

CORE_METRICS = [
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "peak_memory_bytes",
    "req_per_s",
    "input_mb_per_s",
]

NUMERIC_TIER_KEYS = CORE_METRICS + [
    "html_bytes",
    "markdown_bytes_avg",
    "token_estimate_avg",
]

STAGE_KEYS = ["parse_pct", "convert_pct", "etag_pct", "token_pct"]


def load_json(path: str | Path) -> dict:
    """Load and return JSON data from *path*."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(data: dict, path: str | Path) -> None:
    """Write *data* as pretty-printed JSON with a trailing newline."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def normalize_platform(os_name: str, arch: str) -> str:
    """Return the shared lowercase ``os-arch`` platform identifier."""
    os_key = os_name.strip().lower()
    arch_key = arch.strip().lower()

    os_key = {
        "macos": "darwin",
    }.get(os_key, os_key)
    arch_key = {
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x64": "x86_64",
    }.get(arch_key, arch_key)

    return f"{os_key}-{arch_key}"


def detect_platform(os_name: str | None = None, arch: str | None = None) -> str:
    """Return the shared platform identifier used by perf tooling."""
    return normalize_platform(
        os_name or python_platform.system(),
        arch or python_platform.machine(),
    )


def median_value(values: list[float]) -> float:
    """Return the median value used by nightly aggregation."""
    return statistics.median(values)


def merge_measurement_reports(report_paths: list[str | Path]) -> dict | None:
    """Merge multiple Measurement Reports into a single report."""
    merged = None

    for report_path in sorted(Path(path) for path in report_paths):
        report = load_json(report_path)
        if merged is None:
            merged = copy.deepcopy(report)
            merged["tiers"] = dict(report.get("tiers", {}))
            continue

        merged.setdefault("tiers", {}).update(report.get("tiers", {}))

    return merged


def build_baseline_report(measurement_report: dict, platform: str | None = None) -> dict:
    """Extract the baseline subset from a Measurement Report."""
    baseline = {
        "schema_version": "1.0.0",
        "timestamp": measurement_report.get("timestamp", ""),
        "git_commit": measurement_report.get("git_commit", ""),
        "platform": platform or measurement_report.get("platform", "default"),
        "tiers": {},
    }

    for tier_name, tier_data in measurement_report.get("tiers", {}).items():
        baseline["tiers"][tier_name] = {
            metric: tier_data[metric]
            for metric in CORE_METRICS
            if metric in tier_data
        }

    return baseline


def _load_tier_reports(runs_root: Path, tier: str) -> tuple[dict | None, list[dict]]:
    """Load all run reports for *tier* and return ``(base_report, tier_reports)``."""
    base_report = None
    tier_reports = []

    for run_path in sorted(runs_root.glob(f"{tier}-run*.json")):
        report = load_json(run_path)
        if base_report is None:
            base_report = copy.deepcopy(report)
        tier_data = report.get("tiers", {}).get(tier)
        if tier_data is not None:
            tier_reports.append(tier_data)

    return base_report, tier_reports


def _median_for_keys(tier_reports: list[dict], keys: list[str]) -> dict:
    """Return medians for the numeric *keys* present in *tier_reports*."""
    medians = {}
    for key in keys:
        values = [tier_report[key] for tier_report in tier_reports if key in tier_report]
        if values:
            medians[key] = median_value(values)
    return medians


def _median_stage_breakdown(tier_reports: list[dict]) -> dict:
    """Return median stage breakdown values for *tier_reports*."""
    stage_breakdown = {}
    for stage_key in STAGE_KEYS:
        values = [
            tier_report.get("stage_breakdown", {}).get(stage_key)
            for tier_report in tier_reports
        ]
        values = [value for value in values if value is not None]
        if values:
            stage_breakdown[stage_key] = median_value(values)
    return stage_breakdown


def _build_aggregated_tier(tier_reports: list[dict]) -> dict:
    """
    Constructs an aggregated tier dictionary using median values computed from repeated tier reports.
    
    Parameters:
        tier_reports (list[dict]): List of per-run tier dictionaries to aggregate; must contain at least one report.
    
    Returns:
        dict: Aggregated tier dictionary containing median numeric metrics (for NUMERIC_TIER_KEYS), an optional
        "stage_breakdown" with median stage percentages if present, and "iterations" and "warmup" copied from the
        first report.
    
    Raises:
        ValueError: If `tier_reports` is empty.
    """
    if not tier_reports:
        raise ValueError("tier_reports cannot be empty")

    median_tier = _median_for_keys(tier_reports, NUMERIC_TIER_KEYS)
    stage_breakdown = _median_stage_breakdown(tier_reports)
    if stage_breakdown:
        median_tier["stage_breakdown"] = stage_breakdown

    first_report = tier_reports[0]
    median_tier["iterations"] = first_report.get("iterations", 0)
    median_tier["warmup"] = first_report.get("warmup", 0)
    return median_tier


def aggregate_measurement_runs(input_dir: str | Path, tiers: list[str], platform: str) -> dict | None:
    """Aggregate repeated per-tier runs into a median Measurement Report."""
    runs_root = Path(input_dir)
    merged_tiers = {}
    base_report = None

    for tier in tiers:
        tier_base_report, tier_reports = _load_tier_reports(runs_root, tier)
        if base_report is None and tier_base_report is not None:
            base_report = tier_base_report
        if not tier_reports:
            continue

        merged_tiers[tier] = _build_aggregated_tier(tier_reports)

    if base_report is None:
        return None

    base_report["platform"] = platform
    base_report["tiers"] = merged_tiers
    return base_report


def _cmd_detect_platform(_args: argparse.Namespace) -> int:
    print(detect_platform())
    return 0


def _cmd_extract_baseline(args: argparse.Namespace) -> int:
    measurement_report = load_json(args.measurement)
    baseline = build_baseline_report(measurement_report, args.platform)
    write_json(baseline, args.output)
    return 0


def _cmd_merge_measurements(args: argparse.Namespace) -> int:
    report_paths = sorted(Path().glob(args.glob_pattern))
    merged = merge_measurement_reports(report_paths)
    if merged is None:
        raise SystemExit(f"no measurement reports matched: {args.glob_pattern}")
    write_json(merged, args.output)
    return 0


def _cmd_aggregate_runs(args: argparse.Namespace) -> int:
    report = aggregate_measurement_runs(args.input_dir, args.tiers, args.platform)
    if report is None:
        raise SystemExit(f"no run reports found under: {args.input_dir}")
    write_json(report, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Shared helpers for perf report tooling.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser("detect-platform", help="Print the shared platform id.")
    detect_parser.set_defaults(func=_cmd_detect_platform)

    baseline_parser = subparsers.add_parser(
        "extract-baseline",
        help="Extract a baseline report from a Measurement Report.",
    )
    baseline_parser.add_argument("--measurement", required=True, help="Path to a measurement report.")
    baseline_parser.add_argument("--output", required=True, help="Path to the baseline report.")
    baseline_parser.add_argument(
        "--platform",
        help="Override the platform identifier written to the baseline report.",
    )
    baseline_parser.set_defaults(func=_cmd_extract_baseline)

    merge_parser = subparsers.add_parser(
        "merge-measurements",
        help="Merge multiple measurement reports into one report.",
    )
    merge_parser.add_argument("--glob-pattern", required=True, help="Glob pattern for input reports.")
    merge_parser.add_argument("--output", required=True, help="Path to the merged report.")
    merge_parser.set_defaults(func=_cmd_merge_measurements)

    aggregate_parser = subparsers.add_parser(
        "aggregate-runs",
        help="Aggregate repeated tier runs into a median measurement report.",
    )
    aggregate_parser.add_argument("--input-dir", required=True, help="Directory containing run JSON files.")
    aggregate_parser.add_argument("--output", required=True, help="Path to the aggregated report.")
    aggregate_parser.add_argument("--platform", required=True, help="Platform identifier for the report.")
    aggregate_parser.add_argument(
        "--tiers",
        nargs="+",
        required=True,
        help="Tier names to aggregate (for example: small medium large-1m).",
    )
    aggregate_parser.set_defaults(func=_cmd_aggregate_runs)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
