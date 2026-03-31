#!/usr/bin/env python3
"""Corpus benchmark runner for the nginx-markdown-for-agents project.

Discovers HTML fixtures with .meta.json sidecars under a corpus directory,
runs the converter binary on each fixture, collects per-fixture metrics,
and produces a Unified Report JSON.

Usage:
    python3 tools/perf/run_corpus_benchmark.py \
        --corpus-dir tests/corpus \
        --converter-bin <path-to-test-corpus-conversion> \
        --output perf/reports/corpus-report.json

    # With before/after examples:
    python3 tools/perf/run_corpus_benchmark.py \
        --corpus-dir tests/corpus \
        --converter-bin <path> \
        --output <path> \
        --examples-dir perf/reports/examples
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

# ---------------------------------------------------------------------------
# Make sibling modules importable when invoked directly
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_schema import (  # noqa: E402
    VALID_CONVERSION_RESULTS,
    VALID_PAGE_TYPES,
    validate_report,
)
from report_utils import detect_platform, write_json  # noqa: E402

# Exit code used by the converter to signal "skipped" (ineligible input).
# The test-corpus-conversion binary currently only exits with 0 (success)
# or 1 (error).  Exit code 2 is reserved for a future converter version
# that can signal "input is ineligible for conversion".  The benchmark
# script, report schema, and comparison logic handle all three
# Conversion_Result values (converted, skipped, failed-open) so the
# pipeline is ready when the converter gains eligibility detection.
CONVERTER_SKIP_EXIT_CODE = 2

# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------


def discover_fixtures(corpus_dir: Path) -> list[dict]:
    """Find all .meta.json sidecars and return parsed metadata with HTML paths."""
    fixtures = []
    for meta_path in sorted(corpus_dir.rglob("*.meta.json")):
        html_path = meta_path.with_suffix("").with_suffix(".html")
        if not html_path.exists():
            print(f"WARNING: no HTML for {meta_path}, skipping", file=sys.stderr)
            continue
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["_html_path"] = str(html_path)
        meta["_meta_path"] = str(meta_path)
        fixtures.append(meta)
    return fixtures


def load_corpus_version(corpus_dir: Path) -> str:
    """Read corpus-version.json and return the version string."""
    version_file = corpus_dir / "corpus-version.json"
    with open(version_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["version"]


def get_git_commit() -> str:
    """Return the current short git commit hash."""
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


# ---------------------------------------------------------------------------
# Per-fixture conversion
# ---------------------------------------------------------------------------


def run_converter(converter_bin: str, html_path: str) -> tuple[str, int, float]:
    """Run the converter binary on a single HTML file.

    Both arguments must be filesystem paths validated by the caller.
    The command is executed as a list to avoid shell injection.

    Returns:
        (stdout_output, exit_code, latency_ms)
    """
    if not os.path.isfile(converter_bin):
        return "", 1, 0.0
    if not os.access(converter_bin, os.X_OK):
        return "", 1, 0.0
    if not os.path.isfile(html_path):
        return "", 1, 0.0

    start = time.perf_counter()
    try:
        # Security audit (S603): list-form invocation (shell=False, the default)
        # passes args directly to execvp — no shell expansion, no injection
        # vector.  Both paths are validated above with os.path.isfile() and
        # os.access().  shlex.escape() is NOT appropriate here; it is designed
        # for shell=True string concatenation and would corrupt paths
        # containing spaces or special characters.
        result = subprocess.run(  # noqa: S603
            [converter_bin, html_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return result.stdout, result.returncode, elapsed_ms
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - start) * 1000.0  # NOSONAR — parens required: without them `start * 1000` binds first
        return "", 1, elapsed_ms
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000.0  # NOSONAR — parens required: without them `start * 1000` binds first
        return "", 1, elapsed_ms


def classify_result(exit_code: int, output: str) -> str:
    """Classify conversion result based on runtime execution."""
    if exit_code == CONVERTER_SKIP_EXIT_CODE:
        return "skipped"
    return "converted" if exit_code == 0 and output.strip() else "failed-open"


# ---------------------------------------------------------------------------
# Token reduction estimation
# ---------------------------------------------------------------------------


def compute_token_reduction(
    input_bytes: int, output_bytes: int, factor: float
) -> float:
    """Compute per-fixture token reduction estimate.

    Formula: (1 - output_bytes / input_bytes) * 100 * factor
    Returns 0 if input_bytes is 0.
    """
    if input_bytes <= 0:
        return 0.0
    return (1.0 - output_bytes / input_bytes) * 100.0 * factor


def compute_aggregate_token_reduction(
    fixture_results: list[dict], factor: float
) -> float:
    """Compute input-bytes-weighted average token reduction across converted fixtures."""
    total_input = 0
    weighted_sum = 0.0
    for fr in fixture_results:
        if fr["conversion-result"] == "converted" and fr["input-bytes"] > 0:
            reduction = compute_token_reduction(
                fr["input-bytes"], fr["output-bytes"], factor
            )
            weighted_sum += reduction * fr["input-bytes"]
            total_input += fr["input-bytes"]
    return 0.0 if total_input == 0 else weighted_sum / total_input


# ---------------------------------------------------------------------------
# Aggregate summary computation
# ---------------------------------------------------------------------------


def compute_percentile(values: list[float], pct: float) -> float:
    """Compute a percentile using linear interpolation.

    The input list does not need to be pre-sorted; it is sorted internally.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    # Use linear interpolation for percentile
    rank = (pct / 100.0) * (n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])


def build_summary(fixture_results: list[dict], factor: float) -> dict:
    """Build the summary section of the Unified Report."""
    total = len(fixture_results)
    converted = sum(f["conversion-result"] == "converted" for f in fixture_results)
    skipped = sum(f["conversion-result"] == "skipped" for f in fixture_results)
    failed_open = sum(
        f["conversion-result"] == "failed-open" for f in fixture_results
    )

    fallback_rate = (failed_open / total * 100.0) if total > 0 else 0.0

    input_total = sum(f["input-bytes"] for f in fixture_results)
    output_total = sum(
        f["output-bytes"]
        for f in fixture_results
        if f["conversion-result"] == "converted"
    )

    latencies = [f["latency-ms"] for f in fixture_results]

    return {
        "total-fixtures": total,
        "converted-count": converted,
        "skipped-count": skipped,
        "failed-open-count": failed_open,
        "fallback-rate": round(fallback_rate, 2),
        "token-reduction-percent": round(
            compute_aggregate_token_reduction(fixture_results, factor), 2
        ),
        "input-bytes-total": input_total,
        "output-bytes-total": output_total,
        "p50-latency-ms": round(compute_percentile(latencies, 50), 2),
        "p95-latency-ms": round(compute_percentile(latencies, 95), 2),
        "p99-latency-ms": round(compute_percentile(latencies, 99), 2),
    }


# ---------------------------------------------------------------------------
# Unified Report generation
# ---------------------------------------------------------------------------


def build_report(
    fixture_results: list[dict],
    corpus_version: str,
    git_commit: str,
    platform: str,
    converter_version: str,
    factor: float,
) -> dict:
    """Build the complete Unified Report."""
    return {
        "schema-version": "1.0.0",
        "metadata": {
            "corpus-version": corpus_version,
            "git-commit": git_commit,
            "platform": platform,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "converter-version": converter_version,
            "token-approx-factor": factor,
        },
        "summary": build_summary(fixture_results, factor),
        "fixtures": fixture_results,
    }


# ---------------------------------------------------------------------------
# Before/after example generation
# ---------------------------------------------------------------------------


def select_examples(
    fixture_results: list[dict], fixtures_meta: list[dict]
) -> list[dict]:
    """Select one fixture per page type (median input size) + one failure corpus."""
    # Build lookup from fixture-id to meta
    meta_lookup = {m["fixture-id"]: m for m in fixtures_meta}

    # Group by page type
    by_page_type: dict[str, list[dict]] = {}
    for fr in fixture_results:
        pt = fr["page-type"]
        by_page_type.setdefault(pt, []).append(fr)

    selected_ids = set()
    examples = []

    for pt, frs in sorted(by_page_type.items()):
        # Sort by input-bytes and pick median
        sorted_frs = sorted(frs, key=lambda x: x["input-bytes"])
        mid = len(sorted_frs) // 2
        chosen = sorted_frs[mid]
        selected_ids.add(chosen["fixture-id"])
        examples.append(chosen)

    # Ensure at least one failure corpus fixture
    has_failure = any(
        meta_lookup.get(e["fixture-id"], {}).get("failure-corpus", False)
        for e in examples
    )
    if not has_failure:
        for fr in fixture_results:
            fid = fr["fixture-id"]
            if fid not in selected_ids and meta_lookup.get(fid, {}).get(
                "failure-corpus", False
            ):
                examples.append(fr)
                break

    return examples


def _sanitize_path_component(name: str) -> str:
    """Sanitize a string for safe use as a path component.

    Strips directory separators and path traversal sequences to prevent
    path-traversal attacks when constructing file paths from metadata.
    """
    # Remove any directory separators and path traversal components
    name = name.replace("/", "-").replace("\\", "-")
    # Collapse any ".." sequences
    name = name.replace("..", "_")
    # Strip leading/trailing dots and whitespace
    name = name.strip(". \t")
    return name or "unknown"


def write_examples(
    examples: list[dict],
    fixtures_meta: list[dict],
    converter_bin: str,
    examples_dir: Path,
) -> None:
    """Write before/after example pairs to the examples directory."""
    meta_lookup = {m["fixture-id"]: m for m in fixtures_meta}
    examples_dir.mkdir(parents=True, exist_ok=True)
    resolved_examples_dir = examples_dir.resolve()

    for ex in examples:
        fid = ex["fixture-id"]
        meta = meta_lookup.get(fid, {})
        pt = ex.get("page-type", "unknown")
        is_failure = meta.get("failure-corpus", False)

        prefix = "failure" if is_failure else _sanitize_path_component(pt)
        safe_id = _sanitize_path_component(fid)
        base_name = f"{prefix}--{safe_id}"

        html_path = meta.get("_html_path", "")
        if not html_path or not Path(html_path).exists():
            continue

        # Validate output paths stay within examples_dir
        html_dest = (examples_dir / f"{base_name}.html").resolve()
        md_dest = (examples_dir / f"{base_name}.md").resolve()
        if not (
            str(html_dest).startswith(str(resolved_examples_dir))
            and str(md_dest).startswith(str(resolved_examples_dir))
        ):
            continue

        # Copy HTML input
        shutil.copy2(html_path, html_dest)

        # Run converter for the .md output
        output, _, _ = run_converter(converter_bin, html_path)
        with open(md_dest, "w", encoding="utf-8") as f:
            f.write(output)


# ---------------------------------------------------------------------------
# CLI and main
# ---------------------------------------------------------------------------


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Run corpus benchmark and produce a Unified Report."
    )
    parser.add_argument(
        "--corpus-dir",
        required=True,
        help="Path to the corpus directory (e.g., tests/corpus).",
    )
    parser.add_argument(
        "--converter-bin",
        required=True,
        help="Path to the test-corpus-conversion binary.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path for the output Unified Report JSON.",
    )
    parser.add_argument(
        "--token-approx-factor",
        type=float,
        default=1.0,
        help="Approximation factor for token reduction estimation (default: 1.0).",
    )
    parser.add_argument(
        "--examples-dir",
        default=None,
        help="Directory to write before/after example pairs.",
    )
    parser.add_argument(
        "--converter-version",
        default="0.4.0",
        help="Converter version string for report metadata (default: 0.4.0).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the corpus benchmark runner."""
    args = build_cli_parser().parse_args(argv)

    corpus_dir = Path(args.corpus_dir)
    converter_bin = args.converter_bin
    output_path = args.output
    factor = args.token_approx_factor

    # Validate converter binary
    if not os.path.isfile(converter_bin) or not os.access(converter_bin, os.X_OK):
        print(
            f"ERROR: converter binary not found or not executable: {converter_bin}",
            file=sys.stderr,
        )
        return 1

    # Discover fixtures
    fixtures_meta = discover_fixtures(corpus_dir)
    if not fixtures_meta:
        print("ERROR: no fixtures with valid metadata found", file=sys.stderr)
        return 1

    # Load corpus version
    try:
        corpus_version = load_corpus_version(corpus_dir)
    except Exception as e:
        print(f"ERROR: failed to read corpus-version.json: {e}", file=sys.stderr)
        return 1

    git_commit = get_git_commit()
    platform = detect_platform()

    # Run conversions
    fixture_results = []
    for meta in fixtures_meta:
        html_path = meta["_html_path"]
        input_bytes = os.path.getsize(html_path)

        output, exit_code, latency_ms = run_converter(converter_bin, html_path)
        result = classify_result(exit_code, output)

        output_bytes = len(output.encode("utf-8")) if result == "converted" else 0
        token_reduction = (
            compute_token_reduction(input_bytes, output_bytes, factor)
            if result == "converted"
            else 0.0
        )

        fixture_results.append(
            {
                "fixture-id": meta["fixture-id"],
                "page-type": meta["page-type"],
                "conversion-result": result,
                "input-bytes": input_bytes,
                "output-bytes": output_bytes,
                "latency-ms": round(latency_ms, 2),
                "token-reduction-percent": round(token_reduction, 2),
                "failure-corpus": meta.get("failure-corpus", False),
            }
        )

    # Build and write report
    report = build_report(
        fixture_results,
        corpus_version,
        git_commit,
        platform,
        args.converter_version,
        factor,
    )
    if schema_errors := validate_report(report):
        print("ERROR: generated report fails schema validation:", file=sys.stderr)
        for err in schema_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    write_json(report, output_path)
    print(f"Unified Report written to {output_path}")

    # Generate examples if requested
    if args.examples_dir:
        examples = select_examples(fixture_results, fixtures_meta)
        write_examples(
            examples, fixtures_meta, converter_bin, Path(args.examples_dir)
        )
        print(f"Examples written to {args.examples_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
