#!/usr/bin/env python3
"""Validate the release-matrix auto-discovery support policy.

An NGINX version scraped from nginx.org is an observation, not evidence of
support.  When the updater adds a version, every generated row must remain
``best-effort`` and ``pending`` until a maintainer records verification.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import validate_read_path  # noqa: E402

DEFAULT_MATRIX = REPO_ROOT / "tools" / "release-matrix.json"
DEFAULT_DIFF = REPO_ROOT / "tools" / "matrix-diff.json"


def _load_json(path: pathlib.Path, purpose: str) -> Any:
    validated = validate_read_path(path, purpose=purpose)
    try:
        with validated.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {purpose}: {exc}") from exc


def validate_policy(matrix: dict[str, Any], diff: dict[str, Any]) -> list[str]:
    """Return policy violations for newly discovered versions."""
    added = diff.get("added_versions", [])
    if not isinstance(added, list) or not all(
        isinstance(version, str) and version for version in added
    ):
        return ["matrix diff added_versions must be a list of non-empty strings"]
    if not added:
        return []

    entries = matrix.get("entries")
    if not isinstance(entries, list):
        return ["release matrix entries must be a list"]

    violations: list[str] = []
    added_set = set(added)
    matched_versions: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            violations.append(f"entries[{index}] is not an object")
            continue
        version = entry.get("nginx_version", entry.get("nginx"))
        if version not in added_set:
            continue
        matched_versions.add(version)
        violations.extend(_check_added_version_entry(entry, index, version))
    for version in sorted(added_set - matched_versions):
        violations.append(f"added version {version} has no matrix entries")
    return violations


def _check_added_version_entry(
    entry: dict[str, Any], index: int, version: str
) -> list[str]:
    """Return policy violations for one auto-discovered version entry."""
    violations: list[str] = []
    support_tier = entry.get("support_tier")
    verification_state = entry.get("verification_state")
    support_stage = entry.get("support_stage")
    if support_tier != "best-effort":
        violations.append(
            f"entries[{index}] version {version} must have "
            f"support_tier=best-effort from auto-discovery "
            f"(got {support_tier!r})"
        )
    if verification_state != "pending":
        violations.append(
            f"entries[{index}] version {version} must have "
            f"verification_state=pending (got {verification_state!r})"
        )
    if support_stage not in {"best-effort", "experimental"}:
        violations.append(
            f"entries[{index}] version {version} must have a non-primary "
            f"support_stage (got {support_stage!r})"
        )
    return violations


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=pathlib.Path, default=DEFAULT_MATRIX)
    parser.add_argument("--diff", type=pathlib.Path, default=DEFAULT_DIFF)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        matrix = _load_json(args.matrix, "release matrix")
        if not isinstance(matrix, dict):
            raise ValueError("release matrix must be a JSON object")
        try:
            diff = _load_json(args.diff, "matrix diff")
        except (FileNotFoundError, ValueError) as exc:
            if isinstance(exc, FileNotFoundError) and args.diff == DEFAULT_DIFF:
                diff = {"added_versions": []}
            else:
                raise
        if not isinstance(diff, dict):
            raise ValueError("matrix diff must be a JSON object")
        violations = validate_policy(matrix, diff)
    except (OSError, ValueError) as exc:
        print(f"FAIL: auto-update policy could not be validated: {exc}", file=sys.stderr)
        return 1

    if violations:
        for violation in violations:
            print(f"FAIL: {violation}", file=sys.stderr)
        return 1
    print("PASS: auto-discovered matrix versions remain pending/best-effort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
