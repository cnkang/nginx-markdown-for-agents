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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path, validate_write_path_within_root

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_utils import load_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PR_SUMMARY_OUTPUT_DIR = REPO_ROOT / "perf" / "reports"

TOKEN_DISCLAIMER = (
    "Token reduction values are estimates derived from byte-size ratios, "
    "not precise token counts or LLM billing data."
)


def _write_text_with_repo_guard(
    output_path: str | Path, content: str, *, purpose: str,
) -> Path:
    """Resolve output path under REPO_ROOT and write UTF-8 text safely."""
    raw_output = Path(output_path)
    output_name = raw_output.name
    if output_name in {"", ".", ".."}:
        raise ValueError(
            f"Invalid output filename for {purpose}: {output_path!r}"
        )
    output_parts = raw_output.parts
    if len(output_parts) == 1:
        pass
    elif len(output_parts) == 3 and output_parts[:2] == ("perf", "reports"):
        pass
    else:
        raise ValueError(
            "Output path must be '<filename>' or 'perf/reports/<filename>'"
        )

    candidate_output = PR_SUMMARY_OUTPUT_DIR / output_name
    validated_output = validate_write_path_within_root(
        candidate_output, REPO_ROOT, purpose=purpose,
    ).resolve()
    if not validated_output.is_relative_to(REPO_ROOT.resolve()):
        raise ValueError(
            f"Refusing to write outside repository root: {validated_output}"
        )
    validated_output.parent.mkdir(parents=True, exist_ok=True)
    validated_output.write_text(content, encoding="utf-8")
    return validated_output


def format_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def format_summary(report: dict) -> str:
    """Generate markdown summary from a Unified Report."""
    meta = report.get("metadata", {})
    summary = report.get("summary", {})
    fixtures = report.get("fixtures", [])

    corpus_version = meta.get("corpus-version", "unknown")
    git_commit = meta.get("git-commit", "unknown")
    platform = meta.get("platform", "unknown")

    total = summary.get("total-fixtures", 0)
    converted = summary.get("converted-count", 0)
    skipped = summary.get("skipped-count", 0)
    failed_open = summary.get("failed-open-count", 0)
    fallback_rate = summary.get("fallback-rate", 0.0)
    token_reduction = summary.get("token-reduction-percent", 0.0)
    p50 = summary.get("p50-latency-ms", 0.0)
    p95 = summary.get("p95-latency-ms", 0.0)
    p99 = summary.get("p99-latency-ms", 0.0)
    input_total = summary.get("input-bytes-total", 0)
    output_total = summary.get("output-bytes-total", 0)

    lines = [
        "## Benchmark Evidence",
        "",
        f"**Corpus**: v{corpus_version} | "
        f"**Commit**: `{git_commit}` | "
        f"**Platform**: {platform}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Fixtures | {total} total "
        f"({converted} converted, {skipped} skipped, "
        f"{failed_open} failed-open) |",
        f"| Fallback rate | {fallback_rate}% |",
        f"| Token reduction | ~{token_reduction}% (estimate) |",
        f"| P50 / P95 / P99 latency | {p50} / {p95} / {p99} ms |",
        f"| Input / Output bytes | "
        f"{format_bytes(input_total)} / {format_bytes(output_total)} |",
        "",
        f"> {TOKEN_DISCLAIMER}",
        "",
        "<details>",
        "<summary>Per-fixture details</summary>",
        "",
        "| Fixture | Type | Result | Input | Output | Latency | Token Δ |",
        "|---------|------|--------|-------|--------|---------|---------|",
    ]

    for f in fixtures:
        lines.append(
            f"| {f['fixture-id']} "
            f"| {f['page-type']} "
            f"| {f['conversion-result']} "
            f"| {format_bytes(f['input-bytes'])} "
            f"| {format_bytes(f['output-bytes'])} "
            f"| {f['latency-ms']} ms "
            f"| {f['token-reduction-percent']}% |"
        )

    lines.extend(["", "</details>", ""])

    return "\n".join(lines)


def build_cli_parser() -> argparse.ArgumentParser:
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


def main(argv: list[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    report_path = validate_read_path(args.report, purpose="unified report")

    try:
        report = load_json(str(report_path))
    except Exception as e:
        print(f"ERROR: failed to load report: {e}", file=sys.stderr)
        return 1

    md = format_summary(report)

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
