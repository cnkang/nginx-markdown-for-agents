#!/usr/bin/env python3
"""Generate a PR benchmark summary from a Unified Report.

Produces a markdown summary suitable for inclusion in PR descriptions.

Usage:
    python3 tools/perf/format_pr_summary.py \
        --report perf/reports/corpus-report.json \
        --output perf/reports/pr-summary.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# pylint: disable=import-error,wrong-import-position
from lib.path_validation import validate_filename_strict, validate_read_path
from report_utils import load_json  # type: ignore[attr-defined]
# pylint: enable=import-error,wrong-import-position

REPO_ROOT: Path = Path(__file__).resolve().parents[2]  # type: ignore[assignment]
PR_SUMMARY_OUTPUT_DIR: Path = REPO_ROOT / "perf" / "reports"  # type: ignore[assignment]

TOKEN_DISCLAIMER = (
    "Token reduction values are estimates derived from byte-size ratios, "
    "not precise token counts or LLM billing data."
)


def _write_text_with_repo_guard(
    output_path: str | Path, content: str, *, purpose: str,
) -> Path:
    """Write UTF-8 text safely to a strictly validated output path.

    Accepts only bare filenames or ``perf/reports/<filename>`` paths.
    The filename component is validated against a strict allowlist
    (``validate_filename_strict``) to prevent path-traversal writes
    (CWE-22 / python:S2083). The output path is constructed entirely
    from the trusted ``PR_SUMMARY_OUTPUT_DIR`` constant and the validated
    filename, breaking the taint chain from user input to the I/O operation.

    Uses resolve(strict=False) to avoid following symlinks during
    resolution, preventing potential escape outside the repository root.
    """
    raw_output = Path(output_path)
    raw_name = raw_output.name

    if raw_name in {"", ".", ".."}:
        raise ValueError(
            f"Invalid output filename for {purpose}: {output_path!r}"
        )

    output_parts = raw_output.parts
    if not (len(output_parts) == 1
            or (len(output_parts) == 3
                and output_parts[:2] == ("perf", "reports"))):
        raise ValueError(
            "Output path must be '<filename>' or 'perf/reports/<filename>'"
        )

    safe_name = validate_filename_strict(raw_name, purpose=purpose)

    candidate_output = PR_SUMMARY_OUTPUT_DIR / safe_name

    resolved_candidate = candidate_output.resolve(strict=False)
    # pylint: disable=no-member
    resolved_root = REPO_ROOT.resolve(strict=False)
    # pylint: enable=no-member

    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(
            f"Refusing to write outside repository root: {resolved_candidate}"
        )

    resolved_candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved_candidate.write_text(
        content, encoding="utf-8",
    )
    # SONAR_NOTE(S2083): Filename validated via allowlist; path constructed
    # from trusted constant PR_SUMMARY_OUTPUT_DIR; no symlink traversal
    return resolved_candidate


def format_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _extract_report_values(report: dict[str, Any]) -> dict[str, Any]:
    """Extract and type-convert values from report dictionary."""
    meta: dict[str, Any] = report.get("metadata", {})
    summary_data: dict[str, Any] = report.get("summary", {})

    return {
        "corpus_version": str(meta.get("corpus-version", "unknown")),
        "git_commit": str(meta.get("git-commit", "unknown")),
        "platform": str(meta.get("platform", "unknown")),
        "total": int(summary_data.get("total-fixtures", 0)),
        "converted": int(summary_data.get("converted-count", 0)),
        "skipped": int(summary_data.get("skipped-count", 0)),
        "failed_open": int(summary_data.get("failed-open-count", 0)),
        "fallback_rate": float(summary_data.get("fallback-rate", 0.0)),
        "token_reduction": float(summary_data.get("token-reduction-percent", 0.0)),
        "p50": float(summary_data.get("p50-latency-ms", 0.0)),
        "p95": float(summary_data.get("p95-latency-ms", 0.0)),
        "p99": float(summary_data.get("p99-latency-ms", 0.0)),
        "input_total": int(summary_data.get("input-bytes-total", 0)),
        "output_total": int(summary_data.get("output-bytes-total", 0)),
        "fixtures": report.get("fixtures", []),
    }


def format_summary(report: dict[str, Any]) -> str:
    """Generate markdown summary from a Unified Report."""
    values = _extract_report_values(report)

    lines = [
        "## Benchmark Evidence",
        "",
        f"**Corpus**: v{values['corpus_version']} | "
        f"**Commit**: `{values['git_commit']}` | "
        f"**Platform**: {values['platform']}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Fixtures | {values['total']} total "
        f"({values['converted']} converted, {values['skipped']} skipped, "
        f"{values['failed_open']} failed-open) |",
        f"| Fallback rate | {values['fallback_rate']}% |",
        f"| Token reduction | ~{values['token_reduction']}% (estimate) |",
        f"| P50 / P95 / P99 latency | {values['p50']} / {values['p95']} "
        f"/ {values['p99']} ms |",
        f"| Input / Output bytes | "
        f"{format_bytes(values['input_total'])} / "
        f"{format_bytes(values['output_total'])} |",
        "",
        f"> {TOKEN_DISCLAIMER}",
        "",
        "<details>",
        "<summary>Per-fixture details</summary>",
        "",
        "| Fixture | Type | Result | Input | Output | Latency | Token Δ |",
        "|---------|------|--------|-------|--------|---------|---------|",
    ]

    for fixture in values["fixtures"]:
        lines.append(
            f"| {fixture['fixture-id']} "
            f"| {fixture['page-type']} "
            f"| {fixture['conversion-result']} "
            f"| {format_bytes(fixture['input-bytes'])} "
            f"| {format_bytes(fixture['output-bytes'])} "
            f"| {fixture['latency-ms']} ms "
            f"| {fixture['token-reduction-percent']}% |"
        )

    lines.extend(["", "</details>", ""])

    return "\n".join(lines)


def build_cli_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate PR benchmark summary from a Unified Report."
    )
    parser.add_argument(
        "--report", required=True, help="Path to Unified Report JSON."
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path. Prints to stdout if omitted.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for the PR summary formatter.

    Args:
        argv: Command line arguments. If None, uses sys.argv[1:].

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    args = build_cli_parser().parse_args(argv)
    report_path = validate_read_path(args.report, purpose="unified report")

    try:
        report = load_json(str(report_path))  # type: ignore[assignment]
    except (IOError, json.JSONDecodeError) as e:
        print(f"ERROR: failed to load report: {e}", file=sys.stderr)
        return 1

    md = format_summary(report)  # type: ignore[arg-type]

    if args.output:
        try:
            validated_output = _write_text_with_repo_guard(
                args.output, md, purpose="PR summary output",
            )
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"PR summary written to {validated_output}")
    else:
        print(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
