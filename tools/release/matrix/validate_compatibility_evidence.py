#!/usr/bin/env python3
"""Validate compatibility evidence and bind it to the release matrix.

The gate answers two questions:

1. Is every evidence record well formed and internally consistent?
2. Does every matrix row that claims ``verified`` have a record that covers
   it, with zero failures and zero skips?

``--require-blocking`` adds a third question for the release stage: does every
``release_blocking`` row have a record?  The candidate stage answers it against
the evidence bundle produced for the frozen SHA, so the default run stays
usable while a branch is still collecting evidence.

Usage:
    python3 tools/release/matrix/validate_compatibility_evidence.py [options]

Exit codes:
    0  Every checked record and binding passed
    1  A record or binding failed
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(SCRIPT_DIR))
from lib.path_validation import validate_read_path  # noqa: E402
from normalize_matrix import RELEASE_VERSION  # noqa: E402
from evidence_contract import (  # noqa: E402
    EvidenceError,
    is_promotable,
    iter_records,
    record_covers_entry,
    validate_record,
)

DEFAULT_MATRIX = REPO_ROOT / "tools" / "release-matrix.json"
# The evidence directory follows the active release line, so a version bump
# moves it without another edit here.
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence" / RELEASE_VERSION / "compat"


def _load_matrix(path: pathlib.Path) -> dict[str, Any]:
    validated = validate_read_path(path, purpose="compatibility evidence matrix")
    import json

    with validated.open(encoding="utf-8") as handle:
        matrix = json.load(handle)
    if not isinstance(matrix, dict) or not isinstance(matrix.get("entries"), list):
        raise ValueError("release matrix must be an object with an entries list")
    return matrix


def _record_problems(
    records: list[tuple[pathlib.Path, dict[str, Any]]],
    *,
    require_ci: bool = False,
    candidate_sha: str = "",
) -> list[str]:
    """Return contract violations for every record."""
    problems: list[str] = []
    for path, record in records:
        if candidate_sha and record.get("commit_sha") != candidate_sha:
            problems.append(
                f"{path.name}: record was produced for commit "
                f"{record.get('commit_sha')}, not the candidate {candidate_sha}"
            )
        for error in validate_record(record):
            problems.append(f"{path.name}: {error}")
        if not is_promotable(record):
            problems.append(
                f"{path.name}: record cannot support a release promise "
                f"(fail={record.get('fail')}, skip={record.get('skip')})"
            )
        if require_ci and not str(record.get("verified_by", "")).startswith("ci:"):
            problems.append(
                f"{path.name}: the release stage requires a CI record, found "
                f"verified_by={record.get('verified_by')!r}"
            )
    return problems


def _verified_without_record(
    entries: list[dict[str, Any]],
    records: list[tuple[pathlib.Path, dict[str, Any]]],
) -> list[str]:
    """Return verified rows that no valid record covers."""
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if entry.get("verification_state") != "verified":
            continue
        if not any(
            is_promotable(record) and record_covers_entry(record, entry)
            for _, record in records
        ):
            problems.append(
                f"entries[{index}] {entry.get('nginx_version')} "
                f"{entry.get('os')}/{entry.get('libc')}/{entry.get('arch')} "
                "is verified without a matching evidence record"
            )
    return problems


def _blocking_without_record(
    entries: list[dict[str, Any]],
    records: list[tuple[pathlib.Path, dict[str, Any]]],
) -> list[str]:
    """Return release-blocking rows that no valid record covers."""
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if entry.get("release_blocking") is not True:
            continue
        if not any(
            is_promotable(record) and record_covers_entry(record, entry)
            for _, record in records
        ):
            problems.append(
                f"entries[{index}] {entry.get('nginx_version')} "
                f"{entry.get('os')}/{entry.get('libc')}/{entry.get('arch')} "
                f"({entry.get('artifact_type')}) is release-blocking "
                "without a matching evidence record"
            )
    return problems


def validate(
    matrix: dict[str, Any],
    records: list[tuple[pathlib.Path, dict[str, Any]]],
    *,
    require_blocking: bool = False,
    require_ci: bool = False,
    candidate_sha: str = "",
) -> list[str]:
    """Return every evidence-contract and matrix-binding violation."""
    problems = _record_problems(
        records, require_ci=require_ci, candidate_sha=candidate_sha
    )
    entries = matrix["entries"]
    problems.extend(_verified_without_record(entries, records))
    if require_blocking:
        problems.extend(_blocking_without_record(entries, records))
    return problems


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=pathlib.Path, default=DEFAULT_MATRIX)
    parser.add_argument(
        "--evidence-dir", type=pathlib.Path, default=DEFAULT_EVIDENCE_DIR
    )
    parser.add_argument(
        "--require-blocking",
        action="store_true",
        help="require a record for every release-blocking row",
    )
    parser.add_argument(
        "--require-ci",
        action="store_true",
        help="require a CI-produced record (used at the release stage)",
    )
    parser.add_argument(
        "--candidate-sha",
        default="",
        help="require every record to name this commit (release stage)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        matrix = _load_matrix(args.matrix)
        evidence_dir = validate_read_path(
            args.evidence_dir, purpose="compatibility evidence directory"
        )
        records = list(iter_records(evidence_dir))
    except (OSError, ValueError, EvidenceError) as exc:
        print(f"FAIL: compatibility evidence unavailable: {exc}", file=sys.stderr)
        return 1

    problems = validate(
        matrix,
        records,
        require_blocking=args.require_blocking,
        require_ci=args.require_ci,
        candidate_sha=args.candidate_sha,
    )
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    verified = sum(
        1 for entry in matrix["entries"] if entry.get("verification_state") == "verified"
    )
    print(
        f"PASS: {len(records)} compatibility evidence records validate and "
        f"cover {verified} verified matrix rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
