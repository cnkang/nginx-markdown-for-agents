#!/usr/bin/env python3
"""Compare complexity tool output against baseline.json.

Reads lizard warnings (from -w flag) and complexipy --failed output,
compares each violation against the baseline, and reports:
  - NEW: violation not in baseline -> ERROR (exit 1)
  - WORSENED: baseline violation got worse -> WARNING (stderr)
  - BASELINED: in baseline, same or better -> silent

Usage:
    python3 tools/complexity/_compare_baseline.py \
        --baseline tools/complexity/baseline.json \
        --lizard-c target/complexity/c-lizard-warnings.txt \
        --lizard-rust target/complexity/rust-lizard-warnings.txt \
        --lizard-py target/complexity/py-lizard-warnings.txt \
        --complexipy target/complexity/python-complexipy.txt \
        --output target/complexity/baseline-report.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_LIZARD_WARNING_RE = re.compile(
    r"^(.+?):(\d+): warning: (.+?) has (\d+) NLOC, (\d+) CCN,"
    r" (\d+) token, (\d+) PARAM, (\d+) length"
)


def _relpath(p: str) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return p


def parse_lizard_warnings(path: str | None) -> list[dict]:
    """Parse lizard -w output into list of violation dicts."""
    if not path or not Path(path).exists():
        return []
    violations: list[dict] = []
    for line in Path(path).read_text().splitlines():
        m = _LIZARD_WARNING_RE.match(line.strip())
        if m:
            violations.append({
                "file": _relpath(m.group(1)),
                "line": int(m.group(2)),
                "function": m.group(3),
                "ccn": int(m.group(5)),
                "length": int(m.group(8)),
                "params": int(m.group(7)),
            })
    return violations


def parse_complexipy_output(path: str | None) -> list[dict]:
    """Parse complexipy --failed output into list of violation dicts.

    complexipy output format:
        tools/ci/check_third_party_notices.py
            main 16  ❌ FAILED
    """
    if not path or not Path(path).exists():
        return []
    violations: list[dict] = []
    current_file: str | None = None
    for line in Path(path).read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("─") or stripped.startswith("Failed"):
            continue
        # File line: no leading spaces, ends with .py
        if not line.startswith(" ") and stripped.endswith(".py"):
            current_file = _relpath(stripped)
            continue
        # Function line: indented, contains " N " and "FAILED"
        if current_file and "FAILED" in stripped:
            parts = stripped.split()
            if len(parts) >= 3:
                func_name = parts[0]
                try:
                    score = int(parts[1])
                except ValueError:
                    continue
                violations.append({
                    "file": current_file,
                    "function": func_name,
                    "cognitive_complexity": score,
                })
    return violations


def _make_key(entry: dict) -> tuple:
    """Stable key for matching violations to baseline entries."""
    return (entry["file"], entry["function"])


def compare(
    baseline: dict,
    lizard_entries: list[dict],
    complexipy_entries: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """Compare violations against baseline.

    Returns (new_errors, worsened_warnings, ok_messages).
    """
    new_errors: list[str] = []
    worsened: list[str] = []
    ok_msgs: list[str] = []

    baseline_entries = baseline.get("entries", [])
    baseline_map: dict[tuple, dict] = {}
    for entry in baseline_entries:
        key = (entry["file"], entry["function"])
        baseline_map[key] = entry

    # Check lizard violations
    for v in lizard_entries:
        key = _make_key(v)
        if key not in baseline_map:
            new_errors.append(
                f"NEW: {v['file']}:{v['line']} {v['function']} "
                f"CCN={v['ccn']} length={v['length']} params={v['params']}"
            )
            continue
        b = baseline_map[key]
        # Only compare lizard fields if the baseline entry has them
        if "ccn" not in b:
            # Baseline entry is for complexipy, not lizard — treat as new
            new_errors.append(
                f"NEW: {v['file']}:{v['line']} {v['function']} "
                f"CCN={v['ccn']} length={v['length']} params={v['params']}"
            )
            continue
        worsened_fields = []
        if v.get("ccn", 0) > b.get("ccn", 0):
            worsened_fields.append(f"CCN {b['ccn']}->{v['ccn']}")
        if v.get("length", 0) > b.get("length", 0):
            worsened_fields.append(f"length {b['length']}->{v['length']}")
        if v.get("params", 0) > b.get("params", 0):
            worsened_fields.append(f"params {b['params']}->{v['params']}")
        if worsened_fields:
            worsened.append(
                f"WORSENED: {v['file']}:{v['line']} {v['function']} "
                f"({', '.join(worsened_fields)})"
            )
        else:
            ok_msgs.append(
                f"BASELINED: {v['file']} {v['function']} "
                f"(CCN={v.get('ccn','?')} length={v.get('length','?')})"
            )

    # Check complexipy violations
    for v in complexipy_entries:
        key = _make_key(v)
        if key not in baseline_map:
            new_errors.append(
                f"NEW: {v['file']} {v['function']} "
                f"cognitive_complexity={v['cognitive_complexity']}"
            )
            continue
        b = baseline_map[key]
        # Only compare cognitive if the baseline entry has it
        if "cognitive_complexity" not in b:
            new_errors.append(
                f"NEW: {v['file']} {v['function']} "
                f"cognitive_complexity={v['cognitive_complexity']}"
            )
            continue
        if v.get("cognitive_complexity", 0) > b.get("cognitive_complexity", 0):
            worsened.append(
                f"WORSENED: {v['file']} {v['function']} "
                f"cognitive {b['cognitive_complexity']}->{v['cognitive_complexity']}"
            )
        else:
            ok_msgs.append(
                f"BASELINED: {v['file']} {v['function']} "
                f"(cognitive={v.get('cognitive_complexity','?')})"
            )

    return new_errors, worsened, ok_msgs


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare complexity output against baseline")
    parser.add_argument("--baseline", required=True, help="Path to baseline.json")
    parser.add_argument("--lizard-c", dest="lizard_c", help="C lizard warnings file")
    parser.add_argument("--lizard-rust", dest="lizard_rust", help="Rust lizard warnings file")
    parser.add_argument("--lizard-py", dest="lizard_py", help="Python lizard warnings file")
    parser.add_argument("--complexipy", help="complexipy --failed output file")
    parser.add_argument("--output", default="-", help="Report output file (default: stdout)")
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"ERROR: baseline file not found: {args.baseline}", file=sys.stderr)
        return 1

    baseline = json.loads(baseline_path.read_text())

    lizard_entries = []
    lizard_entries.extend(parse_lizard_warnings(args.lizard_c))
    lizard_entries.extend(parse_lizard_warnings(args.lizard_rust))
    lizard_entries.extend(parse_lizard_warnings(args.lizard_py))

    complexipy_entries = parse_complexipy_output(args.complexipy)

    new_errors, worsened, ok_msgs = compare(baseline, lizard_entries, complexipy_entries)

    lines: list[str] = []
    lines.append(f"=== Complexity Baseline Report ===")
    lines.append(f"Baseline: {args.baseline}")
    lines.append(f"Total baseline entries: {len(baseline.get('entries', []))}")
    lines.append(f"Current lizard violations: {len(lizard_entries)}")
    lines.append(f"Current complexipy violations: {len(complexipy_entries)}")
    lines.append("")

    if new_errors:
        lines.append(f"--- NEW VIOLATIONS ({len(new_errors)}) ---")
        for e in new_errors:
            lines.append(f"  ERROR: {e}")
        lines.append("")

    if worsened:
        lines.append(f"--- WORSENED BASELINE ({len(worsened)}) ---")
        for w in worsened:
            lines.append(f"  WARNING: {w}")
        lines.append("")

    if ok_msgs:
        lines.append(f"--- BASELINED (existing, not worsened) ({len(ok_msgs)}) ---")
        for ok in ok_msgs:
            lines.append(f"  OK: {ok}")
        lines.append("")

    if not new_errors and not worsened and not ok_msgs:
        lines.append("No complexity violations detected. All clear.")

    report = "\n".join(lines)

    if args.output == "-":
        print(report)
    else:
        Path(args.output).write_text(report)
        print(report)

    if new_errors:
        print(f"\nFAIL: {len(new_errors)} new violation(s) not in baseline", file=sys.stderr)
        return 1

    if worsened:
        print(f"WARNING: {len(worsened)} baseline violation(s) worsened", file=sys.stderr)

    print("PASS: no new complexity violations", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
