#!/usr/bin/env python3
"""Finalize a module baseline with a verbatim_run baseline_policy block.

The module benchmark harness writes a report containing only
``module_benchmark`` and ``decompression_coverage``; it does not record
provenance.  Baseline validators require a ``baseline_policy`` object with
``type``, full 40-character ``source_git_commit``, ``source_run``,
``source_artifact``, and ``measurement_timestamp``.  This finalizer bridges
that gap by writing a ``verbatim_run`` policy block without modifying any
measured metric.

Usage:
    python3 tools/perf/finalize_module_baseline.py \\
        --input perf/baselines/module-baseline-091.json \\
        --source-git-commit <40-char SHA> \\
        --source-run <workflow run id or URL> \\
        --source-artifact perf/baselines/module-baseline-091-raw.json \\
        [--measurement-timestamp <UTC ISO-8601>]

The finalizer validates its own inputs (40-char SHA, non-empty run id and
artifact path) and writes the finalized baseline in place.  It never touches
metric data; path-coverage, memory, and scenario evidence remain verbatim
copies of the benchmark harness output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize a module baseline with verbatim_run provenance.",
    )
    parser.add_argument("--input", required=True, help="Path to raw baseline JSON.")
    parser.add_argument(
        "--source-git-commit",
        required=True,
        help="Full 40-character git SHA of the commit producing the run.",
    )
    parser.add_argument(
        "--source-run",
        required=True,
        help="Workflow run id, attempt, or run URL provenance for the benchmark.",
    )
    parser.add_argument(
        "--source-artifact",
        required=True,
        help="Relative path to the retained raw benchmark artifact.",
    )
    parser.add_argument(
        "--measurement-timestamp",
        default=None,
        help="UTC ISO-8601 measurement timestamp; defaults to current UTC time.",
    )
    return parser.parse_args(argv)


def _validate_inputs(
    sha: str,
    source_run: str,
    source_artifact: str,
) -> list[str]:
    errors: list[str] = []
    if not _FULL_SHA_RE.match(sha):
        errors.append(
            f"--source-git-commit must be a full 40-character lowercase git SHA "
            f"(got {sha!r})"
        )
    if not source_run:
        errors.append("--source-run must be non-empty")
    if not source_artifact:
        errors.append("--source-artifact must be non-empty")
    return errors


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if errors := _validate_inputs(
        args.source_git_commit,
        args.source_run,
        args.source_artifact,
    ):
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.exists():
        print(
            f"ERROR: baseline file not found: {input_path}",
            file=sys.stderr,
        )
        return 1

    try:
        baseline = json.loads(input_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: failed to read baseline: {exc}", file=sys.stderr)
        return 1

    timestamp = (
        args.measurement_timestamp
        if args.measurement_timestamp
        else _iso_utc_now()
    )

    baseline["baseline_policy"] = {
        "type": "verbatim_run",
        "source_git_commit": args.source_git_commit,
        "source_run": args.source_run,
        "source_artifact": args.source_artifact,
        "measurement_timestamp": timestamp,
        "normalization": "none",
    }

    try:
        input_path.write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"ERROR: failed to write finalized baseline: {exc}", file=sys.stderr)
        return 1

    print(f"Finalized baseline at {input_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
