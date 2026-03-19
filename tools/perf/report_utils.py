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
    """
    Write a dictionary to disk as pretty-printed UTF-8 JSON and ensure the target directory exists.
    
    The JSON is written with 2-space indentation, non-ASCII characters preserved, and a trailing newline is appended.
    
    Parameters:
        data (dict): Mapping to serialize to JSON.
        path (str | Path): Destination file path; parent directories will be created if missing.
    """
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def normalize_platform(os_name: str, arch: str) -> str:
    """
    Produce a normalized platform identifier in the form "os-arch".
    
    Parameters:
        os_name (str): Operating system name; whitespace is stripped and value is lowercased. "macos" is mapped to "darwin".
        arch (str): CPU architecture name; whitespace is stripped and value is lowercased. The function maps "aarch64" -> "arm64", and "amd64" or "x64" -> "x86_64".
    
    Returns:
        platform (str): Normalized platform string formatted as "os-arch".
    """
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
    """
    Determine the normalized OS-architecture platform identifier used by perf tooling.
    
    Parameters:
        os_name (str | None): Optional OS name to normalize (e.g., "macOS", "Linux"). If None, the system OS is used.
        arch (str | None): Optional architecture name to normalize (e.g., "aarch64", "x86_64"). If None, the system architecture is used.
    
    Returns:
        platform (str): Normalized, lowercased platform string in the form "os-arch" (for example, "darwin-arm64" or "linux-x86_64").
    """
    return normalize_platform(
        os_name or python_platform.system(),
        arch or python_platform.machine(),
    )


def median_value(values: list[float]) -> float:
    """
    Compute the median of the given numeric values.
    
    Returns:
        float: The median of the provided values.
    """
    return statistics.median(values)


def merge_measurement_reports(report_paths: list[str | Path]) -> dict | None:
    """
    Combine multiple measurement report JSON files into a single merged report.
    
    The first report (lexically first path) is used as the base; its top-level fields are preserved.
    Subsequent reports contribute or overwrite entries in the top-level "tiers" mapping (later reports override earlier tiers with the same keys).
    
    Parameters:
        report_paths (list[str | Path]): Paths to measurement report JSON files; paths are sorted lexically before merging.
    
    Returns:
        dict | None: A merged measurement report dictionary, or `None` if `report_paths` is empty.
    """
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
    """
    Builds a baseline report containing core metrics and top-level metadata from a Measurement Report.
    
    Parameters:
        measurement_report (dict): Source measurement report containing top-level metadata and a "tiers" mapping.
        platform (str | None): Optional platform identifier to set on the baseline; if omitted the source report's platform is used (or "default" if absent).
    
    Returns:
        dict: Baseline report with keys "schema_version", "timestamp", "git_commit", "platform", and "tiers". Each tier maps to only the metrics from CORE_METRICS that were present in the source report.
    """
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
    """
    Load per-run measurement files for the given tier and return a base report plus the tier-specific data extracted from each run.
    
    Scans runs_root for files named "{tier}-run*.json" in sorted order.
    
    Parameters:
        runs_root (Path): Directory to search for per-run JSON files.
        tier (str): Tier name whose per-run data will be collected.
    
    Returns:
        tuple[dict | None, list[dict]]: A pair where the first element is a deep copy of the first full report found (or `None` if no reports exist), and the second element is a list of the `tiers[tier]` dictionaries extracted from each run (empty if none found).
    """
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
    """
    Compute medians for specified numeric metric keys across a collection of tier reports.
    
    For each key present in at least one report, the median of that key's values is included in the result.
    
    Parameters:
        tier_reports (list[dict]): List of per-run tier dictionaries containing metric values.
        keys (list[str]): Metric keys to consider.
    
    Returns:
        dict: Mapping of each key (from `keys`) that appeared in `tier_reports` to its median value.
    """
    medians = {}
    for key in keys:
        values = [tier_report[key] for tier_report in tier_reports if key in tier_report]
        if values:
            medians[key] = median_value(values)
    return medians


def _median_stage_breakdown(tier_reports: list[dict]) -> dict:
    """
    Compute median stage breakdown values for the provided tier reports.
    
    Parameters:
        tier_reports (list[dict]): List of per-run tier report dictionaries which may contain a "stage_breakdown" mapping.
    
    Returns:
        stage_breakdown (dict): Mapping from each stage key to its median value across reports; only includes keys that have at least one non-None value.
    """
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
    Builds an aggregated tier dictionary by taking medians across repeated per-run tier reports.
    
    Parameters:
        tier_reports (list[dict]): List of per-run tier dictionaries to aggregate; must contain at least one report.
    
    Returns:
        dict: Aggregated tier containing median numeric metrics for the module's numeric keys, an optional
        "stage_breakdown" of median stage percentages (when present in inputs), and "iterations" and "warmup"
        copied from the first report.
    
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
    """
    Aggregate per-tier run JSON files into a median-based Measurement Report.
    
    Parameters:
        input_dir (str | Path): Directory containing per-run JSON files for tiers (files named like "<tier>-run*.json").
        tiers (list[str]): Tier names to aggregate.
        platform (str): Platform identifier to set on the resulting report.
    
    Returns:
        dict | None: A Measurement Report dictionary with medians computed for each requested tier and the `platform` field set, or `None` if no base report was found in the input directory.
    """
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
    """
    Prints the normalized platform identifier (os-arch) to stdout.
    
    Parameters:
        _args (argparse.Namespace): Parsed CLI arguments (ignored).
    
    Returns:
        int: Exit code 0 on success.
    """
    print(detect_platform())
    return 0


def _cmd_extract_baseline(args: argparse.Namespace) -> int:
    """
    Create a baseline report from a measurement report and write it to the specified output path.
    
    Parameters:
        args (argparse.Namespace): Must have attributes:
            - measurement (str | Path): Path to the input measurement JSON file.
            - platform (str | None): Optional platform override for the baseline; if None, the baseline's platform is unchanged.
            - output (str | Path): Path where the baseline JSON will be written.
    
    Returns:
        int: Exit code `0` on success.
    """
    measurement_report = load_json(args.measurement)
    baseline = build_baseline_report(measurement_report, args.platform)
    write_json(baseline, args.output)
    return 0


def _cmd_merge_measurements(args: argparse.Namespace) -> int:
    """
    Merge multiple measurement report JSON files matched by a glob pattern and write the merged report to the provided output path.
    
    Parameters:
        args (argparse.Namespace): Must provide `glob_pattern` (a glob string to locate measurement report files) and `output` (path to write the merged JSON report).
    
    Returns:
        int: 0 on success.
    
    Raises:
        SystemExit: If no measurement reports match the provided glob pattern.
    """
    report_paths = sorted(Path().glob(args.glob_pattern))
    merged = merge_measurement_reports(report_paths)
    if merged is None:
        raise SystemExit(f"no measurement reports matched: {args.glob_pattern}")
    write_json(merged, args.output)
    return 0


def _cmd_aggregate_runs(args: argparse.Namespace) -> int:
    """
    Aggregate per-tier run reports from a directory and write the consolidated measurement report to a file.
    
    Parameters:
        args (argparse.Namespace): Namespace with attributes:
            - input_dir: path to the directory containing per-run JSON reports.
            - tiers: list of tier names to aggregate.
            - platform: platform identifier to set in the aggregated report.
            - output: path where the aggregated JSON report will be written.
    
    Returns:
        int: Exit code 0 on success.
    
    Raises:
        SystemExit: If no run reports are found under `args.input_dir`.
    """
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
    """
    Parse command-line arguments and invoke the selected subcommand handler.
    
    Parameters:
        argv (list[str] | None): Optional list of command-line arguments to parse; if None, uses system argv.
    
    Returns:
        int: Exit code returned by the invoked subcommand.
    """
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
