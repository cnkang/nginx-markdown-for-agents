#!/usr/bin/env python3
"""Coverage gate enforcement for nginx-markdown-for-agents.

Parses lcov summary output to extract aggregate line and function coverage
percentages, then enforces configurable minimum thresholds. Zero external
dependencies — uses only the Python 3.10+ stdlib.

Usage:
    python3 tools/ci/coverage_gate.py \\
        --c-lcov coverage/c-coverage.lcov \\
        --rust-lcov coverage/rust-coverage.lcov \\
        --rust-streaming-lcov coverage/rust-streaming-coverage.lcov \\
        --c-min-line 80 --rust-min-line 80 \\
        --c-min-func 80 --rust-min-func 80

Exit codes:
    0  All thresholds met
    1  One or more thresholds violated
    2  Input file missing or parse error
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CoverageSummary:
    """Aggregate coverage metrics extracted from lcov summary."""

    lines_found: int
    lines_hit: int
    functions_found: int
    functions_hit: int

    @property
    def line_pct(self) -> float:
        """Line coverage as a percentage (0.0–100.0). Returns 0.0 when no lines are found."""
        if self.lines_found == 0:
            return 0.0
        return (self.lines_hit / self.lines_found) * 100.0

    @property
    def function_pct(self) -> float:
        """Function coverage as a percentage (0.0-100.0).

        Returns 0.0 when no functions are found.
        """
        if self.functions_found == 0:
            return 0.0
        return (self.functions_hit / self.functions_found) * 100.0


_LCOC_LINE_RE = re.compile(r"lines[.]\s*:\s*(\d+)\s+of\s+(\d+)")
_LCOC_FUNC_RE = re.compile(r"functions[.]\s*:\s*(\d+)\s+of\s+(\d+)")


def parse_lcov_summary(lcov_path: Path) -> CoverageSummary:
    """Parse ``lcov --summary`` output embedded in an lcov file header.

    Falls back to computing coverage from SF/DA/FN/FNDA records if no
    summary header is present.
    """
    if not lcov_path.exists():
        raise FileNotFoundError(f"lcov file not found: {lcov_path}")

    text = lcov_path.read_text(encoding="utf-8", errors="replace")

    lines_hit = _LCOC_LINE_RE.search(text)
    func_hit = _LCOC_FUNC_RE.search(text)

    if lines_hit and func_hit:
        return CoverageSummary(
            lines_found=int(lines_hit.group(2)),
            lines_hit=int(lines_hit.group(1)),
            functions_found=int(func_hit.group(2)),
            functions_hit=int(func_hit.group(1)),
        )

    return _compute_from_records(text)


def _parse_sf(line: str) -> str:
    """Extract file path from an SF: record."""
    return line[3:]


def _parse_da(
    line: str,
    current_file: str,
    lines_found: set[tuple[str, int]],
    lines_hit: set[tuple[str, int]],
) -> None:
    """Process a DA: line-data record."""
    parts = line[3:].split(",", 2)
    if len(parts) < 2:
        return
    lineno = int(parts[0])
    key = (current_file, lineno)
    lines_found.add(key)
    if int(parts[1]) > 0:
        lines_hit.add(key)


def _parse_fn(
    line: str,
    current_file: str,
    functions_found: set[tuple[str, str]],
) -> None:
    """Process an FN: function-definition record."""
    parts = line[3:].split(",", 1)
    if len(parts) == 2:
        functions_found.add((current_file, parts[1]))


def _parse_fna(
    line: str,
    current_file: str,
    functions_found: set[tuple[str, str]],
    functions_hit: set[tuple[str, str]],
) -> None:
    """Process an FNA: function-call record."""
    parts = line[4:].split(",", 2)
    if len(parts) != 3:
        return
    name = parts[2]
    functions_found.add((current_file, name))
    if int(parts[1]) > 0:
        functions_hit.add((current_file, name))


def _parse_fnda(
    line: str,
    current_file: str,
    functions_hit: set[tuple[str, str]],
) -> None:
    """Process an FNDA: function-call-data record."""
    parts = line[5:].split(",", 1)
    if len(parts) == 2 and int(parts[0]) > 0:
        functions_hit.add((current_file, parts[1]))


def _compute_from_records(text: str) -> CoverageSummary:
    """Compute coverage from raw SF/DA/FN/FNDA lcov records."""
    lines_found: set[tuple[str, int]] = set()
    lines_hit: set[tuple[str, int]] = set()
    functions_found: set[tuple[str, str]] = set()
    functions_hit: set[tuple[str, str]] = set()
    functions_found_fallback = 0
    functions_hit_fallback = 0
    current_file = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current_file = _parse_sf(line)
        elif line.startswith("DA:"):
            _parse_da(line, current_file, lines_found, lines_hit)
        elif line.startswith("FN:"):
            _parse_fn(line, current_file, functions_found)
        elif line.startswith("FNA:"):
            _parse_fna(line, current_file, functions_found, functions_hit)
        elif line.startswith("FNDA:"):
            _parse_fnda(line, current_file, functions_hit)
        elif line.startswith("FNF:"):
            functions_found_fallback += int(line[4:])
        elif line.startswith("FNH:"):
            functions_hit_fallback += int(line[4:])

    if functions_found:
        functions_found_total = len(functions_found)
        functions_hit_total = len(functions_hit)
    else:
        functions_found_total = functions_found_fallback
        functions_hit_total = functions_hit_fallback

    return CoverageSummary(
        lines_found=len(lines_found),
        lines_hit=len(lines_hit),
        functions_found=functions_found_total,
        functions_hit=functions_hit_total,
    )


@dataclass(frozen=True)
class GateResult:
    """Result of a single coverage threshold check."""

    label: str
    metric: str
    actual: float
    threshold: float
    passed: bool


def check_gate(
    label: str,
    summary: CoverageSummary,
    min_line: float,
    min_func: float,
) -> list[GateResult]:
    """Check line and function coverage against thresholds."""
    results: list[GateResult] = []
    results.append(
        GateResult(
            label=label,
            metric="line",
            actual=summary.line_pct,
            threshold=min_line,
            passed=summary.line_pct >= min_line,
        )
    )
    results.append(
        GateResult(
            label=label,
            metric="function",
            actual=summary.function_pct,
            threshold=min_func,
            passed=summary.function_pct >= min_func,
        )
    )
    return results


def format_results(results: list[GateResult]) -> str:
    """Format gate results as a human-readable table."""
    lines: list[str] = []
    lines.append(f"{'Component':<30} {'Metric':<10} {'Actual':>8} {'Threshold':>10} {'Status':<8}")
    lines.append("-" * 70)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(
            f"{r.label:<30} {r.metric:<10} {r.actual:>7.1f}% {r.threshold:>9.1f}% {status:<8}"
        )
    return "\n".join(lines)


def main() -> int:
    """CLI entry point: parse coverage data and enforce threshold gates.

    Returns 0 if all coverage thresholds are met, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Enforce coverage thresholds for nginx-markdown-for-agents",
    )
    parser.add_argument(
        "--c-lcov",
        type=Path,
        help="Path to C lcov report (coverage/c-coverage.lcov)",
    )
    parser.add_argument(
        "--rust-lcov",
        type=Path,
        help="Path to Rust lcov report (coverage/rust-coverage.lcov)",
    )
    parser.add_argument(
        "--rust-streaming-lcov",
        type=Path,
        help="Path to Rust streaming lcov report (coverage/rust-streaming-coverage.lcov)",
    )
    parser.add_argument(
        "--c-min-line",
        type=float,
        default=80.0,
        help="Minimum C line coverage percent (default: 80)",
    )
    parser.add_argument(
        "--c-min-func",
        type=float,
        default=80.0,
        help="Minimum C function coverage percent (default: 80)",
    )
    parser.add_argument(
        "--rust-min-line",
        type=float,
        default=80.0,
        help="Minimum Rust line coverage percent (default: 80)",
    )
    parser.add_argument(
        "--rust-min-func",
        type=float,
        default=80.0,
        help="Minimum Rust function coverage percent (default: 80)",
    )
    args = parser.parse_args()

    all_results: list[GateResult] = []
    errors: list[str] = []

    if args.c_lcov:
        try:
            c_summary = parse_lcov_summary(args.c_lcov)
            all_results.extend(
                check_gate("C module", c_summary, args.c_min_line, args.c_min_func)
            )
        except FileNotFoundError as exc:
            errors.append(str(exc))

    if args.rust_lcov:
        try:
            rust_summary = parse_lcov_summary(args.rust_lcov)
            all_results.extend(
                check_gate("Rust (default)", rust_summary, args.rust_min_line, args.rust_min_func)
            )
        except FileNotFoundError as exc:
            errors.append(str(exc))

    if args.rust_streaming_lcov:
        try:
            rust_stream_summary = parse_lcov_summary(args.rust_streaming_lcov)
            all_results.extend(
                check_gate(
                    "Rust (streaming)",
                    rust_stream_summary,
                    args.rust_min_line,
                    args.rust_min_func,
                )
            )
        except FileNotFoundError as exc:
            errors.append(str(exc))

    if not all_results and not errors:
        print("ERROR: no lcov files specified", file=sys.stderr)
        return 2

    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)

    print(format_results(all_results))

    if errors:
        return 2

    any_failed = any(not r.passed for r in all_results)
    if any_failed:
        print("\nCOVERAGE GATE: FAIL — one or more thresholds not met", file=sys.stderr)
        return 1

    print("\nCOVERAGE GATE: PASS — all thresholds met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
