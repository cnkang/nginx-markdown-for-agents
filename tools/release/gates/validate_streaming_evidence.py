#!/usr/bin/env python3
"""Validate the streaming parity evidence summary and registry.

The summary is a release input, not a human-edited status note.  This gate
checks the frozen v1 shape, blocking pass semantics, and the registry-derived
counts so stale evidence cannot be accepted as current.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import validate_read_path  # noqa: E402


REQUIRED_FIELDS = {
    "schema_version",
    "verified_by",
    "verified_at",
    "total_comparisons",
    "identical_count",
    "known_difference_count",
    "known_difference_by_drift_type",
    "known_difference_by_severity",
    "known_difference_registry_by_drift_type",
    "known_difference_registry_by_severity",
    "unknown_difference_count",
    "error_parity_mismatch_count",
    "pass",
    "known_differences_registry",
    "known_differences_registry_total_entries",
    "corpus_root",
    "verification_command",
    "verification_result",
}


def _load_json(path: str | Path) -> dict[str, Any]:
    validated = validate_read_path(path, purpose="streaming evidence summary")
    try:
        value = json.loads(validated.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read evidence summary: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("evidence summary must be a JSON object")
    return value


def _load_registry(summary: dict[str, Any]):
    import tomllib

    registry_name = summary.get("known_differences_registry")
    if not isinstance(registry_name, str) or not registry_name:
        raise ValueError("known_differences_registry must be a non-empty path")
    registry_path = (REPO_ROOT / registry_name).resolve()
    try:
        registry_path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("known-differences registry escapes the repository") from exc
    validated = validate_read_path(registry_path, purpose="known-differences registry")
    try:
        return tomllib.loads(validated.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unable to read known-differences registry: {exc}") from exc


def _count_registry(registry: dict[str, Any]) -> tuple[Counter, Counter, int]:
    entries = registry.get("difference")
    if not isinstance(entries, list):
        raise ValueError("known-differences registry has no difference entries")
    drift = Counter()
    severity = Counter()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("known-differences registry contains a non-table entry")
        drift_type = entry.get("drift_type")
        level = entry.get("severity")
        if not isinstance(drift_type, str) or not isinstance(level, str):
            raise ValueError("every registry entry needs drift_type and severity")
        drift[drift_type] += 1
        severity[level] += 1
    return drift, severity, len(entries)


def validate(summary: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - summary.keys())
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if summary.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if summary.get("pass") is not True:
        errors.append("pass must be true")
    if summary.get("verification_result") != "PASS":
        errors.append("verification_result must be PASS")
    for field in (
        "unknown_difference_count",
        "error_parity_mismatch_count",
    ):
        if summary.get(field) != 0:
            errors.append(f"{field} must be 0")

    try:
        drift, severity, entry_count = _count_registry(registry)
    except ValueError as exc:
        errors.append(str(exc))
        return errors

    if summary.get("known_differences_registry_total_entries") != entry_count:
        errors.append("registry total does not match known-differences.toml")
    for field, actual in (
        ("known_difference_registry_by_drift_type", dict(drift)),
        ("known_difference_registry_by_severity", dict(severity)),
    ):
        if summary.get(field) != actual:
            errors.append(f"{field} does not match the registry")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", help="path to streaming evidence summary.json")
    args = parser.parse_args(argv)
    try:
        summary = _load_json(args.summary)
        registry = _load_registry(summary)
        errors = validate(summary, registry)
    except (ImportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: streaming evidence {args.summary} validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
