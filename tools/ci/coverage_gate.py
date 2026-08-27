#!/usr/bin/env python3
"""Coverage gate enforcement for nginx-markdown-for-agents.

Parses lcov summary output to extract aggregate line and function coverage
percentages, then enforces configurable minimum thresholds. Critical-path
line coverage is measured from the source records and is enforced separately.
Zero external dependencies — uses only the Python 3.10+ stdlib.

Usage:
    python3 tools/ci/coverage_gate.py \\
        --c-lcov coverage/c-coverage.lcov \\
        --rust-lcov coverage/rust-coverage.lcov \\
        --rust-streaming-lcov coverage/rust-streaming-coverage.lcov \\
        --c-min-line 80 --rust-min-line 80 \\
        --c-min-func 80 --rust-min-func 80 --critical-path-min 90

Exit codes:
    0  All thresholds met
    1  One or more thresholds violated
    2  Input file missing or parse error
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path  # noqa: E402


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


_LCOV_LINE_RE = re.compile(r"lines[.]+\s*:\s*(\d+)\s+of\s+(\d+)")
_LCOV_FUNC_RE = re.compile(r"functions[.]+\s*:\s*(\d+)\s+of\s+(\d+)")

_SOURCE_SUFFIXES = (".c", ".h", ".rs")


def _source_file_matches(path: str, predicate: Callable[[str], bool]) -> bool:
    """Return whether a direct child of a ``src`` component matches."""
    parts = path.replace("\\", "/").casefold().split("/")
    return any(
        index > 0 and part == "src" and index + 1 < len(parts)
        and predicate(parts[index + 1])
        for index, part in enumerate(parts)
    )


def _is_source_file(filename: str) -> bool:
    """Return whether a path component has a tracked source suffix."""
    return filename.endswith(_SOURCE_SUFFIXES)


def _matches_auth_path(path: str) -> bool:
    return _source_file_matches(
        path,
        lambda filename: "auth" in filename and _is_source_file(filename),
    )


def _matches_error_path(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    if "/src/error/" in normalized:
        return _is_source_file(normalized.split("/src/error/", 1)[1])
    return normalized.endswith("/src/ngx_http_markdown_error.c")


def _matches_ffi_path(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    if "/src/ffi/" in normalized:
        return _is_source_file(normalized.split("/src/ffi/", 1)[1])
    if normalized.endswith("/src/dynconf/ffi.rs"):
        return True
    return _source_file_matches(
        normalized,
        lambda filename: "_ffi" in filename and _is_source_file(filename),
    )


def _matches_conditional_path(path: str) -> bool:
    parts = path.replace("\\", "/").casefold().split("/")
    return (
        "/src/" in path.replace("\\", "/").casefold()
        and "conditional" in parts[-1]
        and _is_source_file(parts[-1])
    )


_CRITICAL_PATH_PATTERNS: dict[str, Callable[[str], bool]] = {
    "auth": _matches_auth_path,
    "error handling": _matches_error_path,
    "FFI boundary": _matches_ffi_path,
    "conditional requests": _matches_conditional_path,
}


def parse_lcov_summary(lcov_path: Path) -> CoverageSummary:
    """Parse ``lcov --summary`` output embedded in an lcov file header.

    Falls back to computing coverage from SF/DA/FN/FNDA records if no
    summary header is present.
    """
    if not lcov_path.exists():
        raise FileNotFoundError(f"lcov file not found: {lcov_path}")

    resolved = validate_read_path(lcov_path, purpose="lcov input")
    text = resolved.read_text(encoding="utf-8", errors="replace")

    lines_hit = _LCOV_LINE_RE.search(text)
    func_hit = _LCOV_FUNC_RE.search(text)

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


def _parse_line_records(text: str) -> dict[str, tuple[set[int], set[int]]]:
    """Return measured and hit line numbers grouped by source file."""
    records: dict[str, tuple[set[int], set[int]]] = {}
    current_file = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current_file = _parse_sf(line)
            records.setdefault(current_file, (set(), set()))
            continue
        if not line.startswith("DA:") or not current_file:
            continue

        parts = line[3:].split(",", 2)
        if len(parts) < 2:
            continue
        try:
            line_number = int(parts[0])
            hit_count = int(parts[1])
        except ValueError:
            continue
        found, hit = records[current_file]
        found.add(line_number)
        if hit_count > 0:
            hit.add(line_number)

    return records


def _merge_critical_path_records(
    lcov_paths: list[Path],
) -> dict[str, tuple[set[int], set[int]]]:
    """Merge measured and hit lines from the supplied component reports."""
    merged: dict[str, tuple[set[int], set[int]]] = {}
    for lcov_path in lcov_paths:
        if not lcov_path.exists():
            raise FileNotFoundError(f"lcov file not found: {lcov_path}")
        resolved = validate_read_path(lcov_path, purpose="lcov input")
        for source_file, (found, hit) in _parse_line_records(
            resolved.read_text(encoding="utf-8", errors="replace")
        ).items():
            normalized = source_file.replace("\\", "/")
            if "/components/" not in normalized or "/src/" not in normalized:
                continue
            destination = merged.setdefault(normalized, (set(), set()))
            destination[0].update(found)
            destination[1].update(hit)
    return merged


def _critical_path_summary(
    pattern: Callable[[str], bool],
    records: dict[str, tuple[set[int], set[int]]],
) -> CoverageSummary:
    """Summarize the records selected by one critical-path pattern."""
    lines_found: set[tuple[str, int]] = set()
    lines_hit: set[tuple[str, int]] = set()
    for source_file, (found, hit) in records.items():
        if not pattern(source_file):
            continue
        lines_found.update((source_file, line) for line in found)
        lines_hit.update((source_file, line) for line in hit)
    return CoverageSummary(
        lines_found=len(lines_found),
        lines_hit=len(lines_hit),
        functions_found=0,
        functions_hit=0,
    )


def parse_lcov_critical_paths(
    lcov_paths: list[Path],
) -> dict[str, CoverageSummary]:
    """Measure critical-path line coverage across the supplied lcov reports.

    Reports generated for default and streaming Rust builds contain the same
    source files.  Merging by ``(source file, line)`` avoids double-counting
    those records while preserving the union of exercised lines.
    """
    records = _merge_critical_path_records(lcov_paths)

    return {
        label: _critical_path_summary(pattern, records)
        for label, pattern in _CRITICAL_PATH_PATTERNS.items()
    }


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
    results: list[GateResult] = [
        GateResult(
            label=label,
            metric="line",
            actual=summary.line_pct,
            threshold=min_line,
            passed=summary.line_pct >= min_line,
        )
    ]
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
    lines: list[str] = [
        f"{'Component':<30} {'Metric':<10} {'Actual':>8} {'Threshold':>10} {'Status':<8}",
        "-" * 70,
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(
            f"{r.label:<30} {r.metric:<10} {r.actual:>7.1f}% {r.threshold:>9.1f}% {status:<8}"
        )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the coverage gate."""
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
    parser.add_argument(
        "--critical-path-min",
        type=float,
        default=90.0,
        help="Minimum critical-path line coverage percent (default: 90)",
    )
    return parser


def _append_lcov_results(
    results: list[GateResult],
    errors: list[str],
    label: str,
    lcov_path: Path | None,
    min_line: float,
    min_func: float,
) -> None:
    """Append threshold results for one optional lcov report."""
    if lcov_path is None:
        return
    try:
        summary = parse_lcov_summary(lcov_path)
    except FileNotFoundError as exc:
        errors.append(str(exc))
        return
    results.extend(check_gate(label, summary, min_line, min_func))


def _append_critical_results(
    results: list[GateResult],
    errors: list[str],
    lcov_paths: list[Path],
    threshold: float,
) -> None:
    """Append critical-path results for the selected lcov reports."""
    if not lcov_paths:
        return
    try:
        records = _merge_critical_path_records(lcov_paths)
    except FileNotFoundError as exc:
        errors.append(str(exc))
        return
    summaries = {
        label: _critical_path_summary(pattern, records)
        for label, pattern in _CRITICAL_PATH_PATTERNS.items()
    }
    for label, summary in summaries.items():
        if summary.lines_found == 0:
            pattern = _CRITICAL_PATH_PATTERNS[label]
            if any(pattern(source_file) for source_file in records):
                errors.append(
                    f"critical-path category has no measured lines: {label}"
                )
            continue
        results.append(
            GateResult(
                label=f"Critical: {label}",
                metric="line",
                actual=summary.line_pct,
                threshold=threshold,
                passed=summary.line_pct >= threshold,
            )
        )


def _finish_gate(results: list[GateResult], errors: list[str]) -> int:
    """Print the coverage report and return its process status."""
    if not results and not errors:
        print("ERROR: no lcov files specified", file=sys.stderr)
        return 2

    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)

    print(format_results(results))
    print("\n  Policy source: AGENTS.md Rule 25 — 80% aggregate; 90% critical paths (blocking)")
    print("  Critical paths: auth, error handling, FFI boundary, conditional requests")

    if errors:
        return 2

    any_failed = any(not r.passed for r in results)
    if any_failed:
        print("\nCOVERAGE GATE: FAIL — one or more thresholds not met", file=sys.stderr)
        return 1

    print("\nCOVERAGE GATE: PASS — all thresholds met")
    return 0


def main() -> int:
    """CLI entry point: parse coverage data and enforce threshold gates."""
    args = _build_parser().parse_args()
    results: list[GateResult] = []
    errors: list[str] = []
    reports = (
        ("C module", args.c_lcov, args.c_min_line, args.c_min_func),
        ("Rust (default)", args.rust_lcov, args.rust_min_line, args.rust_min_func),
        (
            "Rust (streaming)",
            args.rust_streaming_lcov,
            args.rust_min_line,
            args.rust_min_func,
        ),
    )
    for label, path, min_line, min_func in reports:
        _append_lcov_results(results, errors, label, path, min_line, min_func)
    _append_critical_results(
        results,
        errors,
        [path for _, path, _, _ in reports if path is not None],
        args.critical_path_min,
    )
    return _finish_gate(results, errors)


if __name__ == "__main__":
    sys.exit(main())
