#!/usr/bin/env python3
"""Unified Report schema validation utility.

Validates a corpus benchmark Unified Report JSON against the schema
definition. Reusable by the benchmark script, comparison script, and tests.

Usage:
    python3 tools/perf/report_schema.py perf/reports/corpus-report.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

VALID_PAGE_TYPES = {
    "clean-article",
    "documentation",
    "nav-heavy",
    "boilerplate-heavy",
    "complex-common",
}

VALID_CONVERSION_RESULTS = {"converted", "skipped", "failed-open"}

REQUIRED_TOP_LEVEL = {"schema-version", "metadata", "summary", "fixtures"}

REQUIRED_METADATA = {
    "corpus-version",
    "git-commit",
    "platform",
    "timestamp",
    "converter-version",
    "token-approx-factor",
}

REQUIRED_SUMMARY = {
    "total-fixtures",
    "converted-count",
    "skipped-count",
    "failed-open-count",
    "fallback-rate",
    "token-reduction-percent",
    "input-bytes-total",
    "output-bytes-total",
    "p50-latency-ms",
    "p95-latency-ms",
    "p99-latency-ms",
}

REQUIRED_FIXTURE = {
    "fixture-id",
    "page-type",
    "conversion-result",
    "input-bytes",
    "output-bytes",
    "latency-ms",
    "token-reduction-percent",
    "failure-corpus",
}


def validate_kebab_case_keys(obj: object, path: str = "") -> list[str]:
    """Recursively check that all JSON keys are kebab-case."""
    errors = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            full_path = f"{path}.{key}" if path else key
            if not KEBAB_CASE_RE.match(key):
                errors.append(f"non-kebab-case key: {full_path}")
            errors.extend(validate_kebab_case_keys(value, full_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            errors.extend(validate_kebab_case_keys(item, f"{path}[{i}]"))
    return errors


def validate_required_fields(
    obj: dict, required: set[str], context: str
) -> list[str]:
    """Check that all required fields are present."""
    missing = required - set(obj.keys())
    return [f"missing {context} field: {f}" for f in sorted(missing)]


def _validate_fixture(fixture: object, index: int) -> list[str]:
    """Validate a single fixture entry and return error messages."""
    if not isinstance(fixture, dict):
        return [f"fixtures[{index}] must be a dict"]
    errors = validate_required_fields(fixture, REQUIRED_FIXTURE, f"fixtures[{index}]")
    pt = fixture.get("page-type")
    if pt and pt not in VALID_PAGE_TYPES:
        errors.append(f"fixtures[{index}] invalid page-type: {pt}")
    cr = fixture.get("conversion-result")
    if cr and cr not in VALID_CONVERSION_RESULTS:
        errors.append(f"fixtures[{index}] invalid conversion-result: {cr}")
    return errors


def validate_report(report: dict) -> list[str]:
    """Validate a Unified Report and return a list of error messages."""
    errors = []

    # Top-level fields
    errors.extend(validate_required_fields(report, REQUIRED_TOP_LEVEL, "top-level"))

    # Kebab-case keys
    errors.extend(validate_kebab_case_keys(report))

    # Metadata
    metadata = report.get("metadata", {})
    if isinstance(metadata, dict):
        errors.extend(validate_required_fields(metadata, REQUIRED_METADATA, "metadata"))

    # Summary
    summary = report.get("summary", {})
    if isinstance(summary, dict):
        errors.extend(validate_required_fields(summary, REQUIRED_SUMMARY, "summary"))

    # Fixtures
    fixtures = report.get("fixtures", [])
    if not isinstance(fixtures, list):
        errors.append("fixtures must be a list")
    else:
        for i, fixture in enumerate(fixtures):
            errors.extend(_validate_fixture(fixture, i))

    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for report validation."""
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: report_schema.py <report.json>", file=sys.stderr)
        return 1

    report_path = Path(argv[0])
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception as e:
        print(f"ERROR: failed to load report: {e}", file=sys.stderr)
        return 1

    errors = validate_report(report)
    if errors:
        print(f"Validation failed with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("Report is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
