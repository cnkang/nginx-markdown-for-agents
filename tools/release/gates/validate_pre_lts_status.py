#!/usr/bin/env python3
"""Validate the six-status pre-LTS report and its status semantics."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

# A frozen candidate SHA is exactly 40 lowercase hexadecimal characters.
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from lib.path_validation import validate_read_path  # noqa: E402
from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)

SCHEMA_PATH = REPO_ROOT / "schemas" / "pre-lts-status.schema.json"
DEFAULT_REPORT = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "pre-lts-status.json"
)
ALLOWED_STATES = {
    "PASS",
    "REAL_FAIL",
    "ENV_BLOCKED",
    "NOT_APPLICABLE",
    "AWAITING_SOAK_OBSERVATION_EXTERNAL",
    "NON_BLOCKING_OBSERVATION",
}
STATUS_NAMES = (
    "SPEC_READY",
    "IMPLEMENTATION_COMPLETE",
    "SUPPORT_MATRIX_VERIFIED",
    "PRE_LTS_READY",
    "OBSERVATION_STATUS",
    "LTS_DECISION_PENDING",
)


def _load(path: pathlib.Path) -> dict[str, Any]:
    try:
        validated = validate_read_path(path, purpose="pre-LTS status input")
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot validate pre-LTS status input {path}: {exc}") from exc
    try:
        with validated.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {validated}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{validated} must contain a JSON object")
    return value


def _schema_errors(report: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError as exc:
        raise ValueError("jsonschema is required for pre-LTS status validation") from exc
    validator = jsonschema.Draft202012Validator(schema)
    return [error.message for error in sorted(validator.iter_errors(report), key=str)]


def _status_record_errors(name: str, record: dict[str, Any]) -> list[str]:
    """Validate one named status record against the shared vocabulary."""
    state = record.get("state")
    conditions = record.get("conditions")
    if state not in ALLOWED_STATES:
        return [f"{name}: state {state!r} is outside the shared vocabulary"]
    if not isinstance(conditions, list):
        return []
    if state == "PASS" and conditions:
        return [f"{name}: PASS cannot carry pending/blocked conditions"]
    if state != "PASS" and not conditions:
        return [f"{name}: non-PASS state must explain its conditions"]
    return []


def _observation_status_errors(statuses: dict[str, Any]) -> list[str]:
    """Keep external observation explicitly non-terminal."""
    observation = statuses.get("OBSERVATION_STATUS")
    if isinstance(observation, dict) and observation.get("state") == "PASS":
        return ["OBSERVATION_STATUS: external observation cannot be PASS"]
    return []


def git_head_sha() -> str:
    """Return the current git HEAD SHA or raise ValueError."""
    git = resolve_approved_executable("git")
    if git is None:
        raise ValueError(
            "stale-digest: cannot resolve git HEAD: approved git executable "
            "not found"
        )
    try:
        proc = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"stale-digest: cannot resolve git HEAD: {exc}") from exc
    if proc.returncode != 0:
        raise ValueError(
            f"stale-digest: cannot resolve git HEAD: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _candidate_identity_errors(
    report: dict[str, Any], git_head: bool
) -> list[str]:
    """Reject candidate identity drift when the report claims a frozen SHA."""
    candidate = report.get("candidate")
    if not isinstance(candidate, dict):
        return ["candidate must be an object"]
    source_sha = candidate.get("source_sha")
    if not isinstance(source_sha, str) or not SHA_PATTERN.fullmatch(source_sha):
        return ["candidate.source_sha must be 40 lowercase hexadecimal characters"]
    if not git_head:
        return []
    try:
        head = git_head_sha()
    except ValueError as exc:
        return [str(exc)]
    if source_sha != head:
        return [
            f"stale-digest: candidate.source_sha {source_sha} != git HEAD {head}"
        ]
    return []


def semantic_errors(
    report: dict[str, Any], git_head: bool = False
) -> list[str]:
    """Reject vocabulary drift and PASS records with unresolved conditions."""
    statuses = report.get("statuses")
    if not isinstance(statuses, dict):
        return ["statuses must be an object"]
    errors = [
        error
        for name in STATUS_NAMES
        for record in [statuses.get(name)]
        if isinstance(record, dict)
        for error in _status_record_errors(name, record)
    ]
    return (
        errors
        + _observation_status_errors(statuses)
        + _candidate_identity_errors(report, git_head)
    )


def validate_report(
    report: dict[str, Any], schema: dict[str, Any], git_head: bool = False
) -> list[str]:
    return _schema_errors(report, schema) + semantic_errors(report, git_head)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=pathlib.Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--git-head",
        action="store_true",
        help="fail closed when candidate.source_sha differs from "
        "`git rev-parse HEAD`",
    )
    args = parser.parse_args(argv)
    try:
        report = _load(args.report)
        schema = _load(SCHEMA_PATH)
        errors = validate_report(report, schema, git_head=args.git_head)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: six-status pre-LTS report and vocabulary are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
