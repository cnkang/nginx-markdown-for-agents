#!/usr/bin/env python3
"""Promote release-matrix rows with recorded compatibility evidence.

Auto-discovery may add a newly published NGINX version, but it must not grant
support.  This tool is the other half of that rule: a maintainer runs it with
evidence records, and it promotes only the rows those records cover.

What it does:

1. Reads one or more evidence records and validates them against the shared
   contract in ``evidence_contract.py``.
2. Promotes every non-manual row the records cover to ``supported``,
   ``primary``, ``verified``, and release blocking.
3. Optionally demotes superseded patches of the same minor track, so the
   previous patch stops blocking a release without losing its row.
4. Refuses to touch a row with ``managed_by: manual``.

The tool is idempotent.  ``--check`` reports whether the matrix already
reflects the evidence, and ``--dry-run`` prints the change set.

Usage:
    python3 tools/release/matrix/promote_matrix.py \
        --evidence docs/evidence/0.9.3/compat/nginx.org-1.30.6-amd64.json \
        [--evidence ...] [--demote-superseded] [--dry-run | --check]

Exit codes:
    0  Promotion applied, or the matrix already matches the evidence
    1  Invalid evidence, uncovered record, or a refused manual row
    2  ``--check`` found a matrix that does not reflect the evidence
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(SCRIPT_DIR))
from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)
from evidence_contract import (  # noqa: E402
    EvidenceError,
    is_promotable,
    iter_records,
    load_record,
    record_covers_entry,
    validate_record,
)

DEFAULT_MATRIX = REPO_ROOT / "tools" / "release-matrix.json"

PROMOTABLE_ARTIFACT_TYPES = frozenset({"dynamic-module", "deb-package"})


class PromotionError(RuntimeError):
    """Raised when a promotion request cannot be applied safely."""


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _track(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _load_matrix(path: pathlib.Path) -> dict[str, Any]:
    validated = validate_read_path(path, purpose="release matrix promotion")
    with validated.open(encoding="utf-8") as handle:
        matrix = json.load(handle)
    if not isinstance(matrix, dict) or not isinstance(matrix.get("entries"), list):
        raise PromotionError("release matrix must be an object with an entries list")
    return matrix


def _is_promotable_row(entry: dict[str, Any]) -> bool:
    """Return whether evidence may govern this row."""
    if entry.get("managed_by") == "manual":
        return False
    if entry.get("artifact_type") not in PROMOTABLE_ARTIFACT_TYPES:
        return False
    return True


def _promote_entry(entry: dict[str, Any], record: dict[str, Any]) -> None:
    """Apply one evidence record to one matrix row."""
    entry["support_tier"] = "supported"
    entry["support_stage"] = "primary"
    entry["verification_state"] = "verified"
    entry["release_blocking"] = True
    entry["date"] = record["date"]
    entry["source"] = record["source_kind"]
    entry["provenance"] = {
        "kind": record["source_kind"],
        "reference": record["source_reference"],
    }


def _covered_rows(
    entries: list[dict[str, Any]], record: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return the rows one record verifies."""
    return [
        entry
        for entry in entries
        if _is_promotable_row(entry) and record_covers_entry(record, entry)
    ]


def _refused_manual_rows(
    entries: list[dict[str, Any]], record: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return manual rows a record would have claimed.

    Evidence that covers a manual row means the operator pointed at the wrong
    row family, or the matrix has two rows for one target.  Both need a human.
    """
    return [
        entry
        for entry in entries
        if entry.get("managed_by") == "manual" and record_covers_entry(record, entry)
    ]


def _demote_superseded(
    entries: list[dict[str, Any]], promoted_versions: set[str]
) -> list[dict[str, Any]]:
    """Demote supported patches that a newer patch in the same track replaces."""
    newest_by_track: dict[str, str] = {}
    for version in promoted_versions:
        track = _track(version)
        current = newest_by_track.get(track)
        if current is None or _version_tuple(version) > _version_tuple(current):
            newest_by_track[track] = version

    changed: list[dict[str, Any]] = []
    for entry in entries:
        version = entry.get("nginx_version")
        if not isinstance(version, str) or version in promoted_versions:
            continue
        newest = newest_by_track.get(_track(version))
        if newest is None:
            continue
        if _version_tuple(version) >= _version_tuple(newest):
            continue
        if entry.get("support_tier") != "supported":
            continue
        entry["support_tier"] = "best-effort"
        entry["support_stage"] = "best-effort"
        entry["verification_state"] = "verified"
        entry["release_blocking"] = False
        changed.append(entry)
    return changed


def build_promoted_matrix(
    matrix: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    demote_superseded: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Return the promoted matrix and a human-readable change summary."""
    entries = matrix["entries"]
    summary: list[str] = []
    promoted_versions: set[str] = set()

    for record in records:
        if not is_promotable(record):
            raise PromotionError(
                f"record {record.get('target_identity')} is not promotable"
            )
        refused = _refused_manual_rows(entries, record)
        if refused:
            raise PromotionError(
                "record "
                f"{record.get('target_identity')} matches managed_by=manual "
                f"rows: {[row.get('os') for row in refused]}"
            )
        covered = _covered_rows(entries, record)
        if not covered:
            raise PromotionError(
                "record "
                f"{record.get('target_identity')} covers no matrix row; "
                "add the row before promoting it"
            )
        for entry in covered:
            _promote_entry(entry, record)
        promoted_versions.add(record["nginx_version"])
        summary.append(
            f"promote {record['nginx_version']} "
            f"{record['distro']}/{record['libc']}/{record['arch']}: "
            f"{len(covered)} row(s)"
        )

    if demote_superseded:
        demoted = _demote_superseded(entries, promoted_versions)
        demoted_versions = sorted(
            {entry["nginx_version"] for entry in demoted}, key=_version_tuple
        )
        for version in demoted_versions:
            summary.append(f"demote superseded patch {version} to best-effort")
    return matrix, summary


def _write_matrix(
    path: pathlib.Path, matrix: dict[str, Any], *, root: pathlib.Path = REPO_ROOT
) -> None:
    """Write the matrix atomically inside ``root``."""
    target = validate_write_path_within_root(path, root=root)
    tmp = target.with_suffix(f"{target.suffix}.tmp")
    tmp_path = validate_write_path_within_root(tmp, root=root)
    try:
        tmp_path.write_text(
            json.dumps(matrix, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp_path, target)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise PromotionError(f"cannot write {target}: {exc}") from exc


def _collect_records(
    evidence: list[pathlib.Path], evidence_dir: pathlib.Path | None
) -> list[dict[str, Any]]:
    """Load and validate every requested evidence record."""
    paths = list(evidence)
    if evidence_dir is not None:
        directory = validate_read_path(
            evidence_dir, purpose="compatibility evidence directory"
        )
        paths.extend(path for path, _ in iter_records(directory))
    if not paths:
        raise PromotionError("no evidence records given")
    records: list[dict[str, Any]] = []
    for path in paths:
        validated = validate_read_path(path, purpose="compatibility evidence")
        record = load_record(validated)
        errors = validate_record(record)
        if errors:
            raise PromotionError(
                f"{validated.name}: " + "; ".join(errors)
            )
        records.append(record)
    return records


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=pathlib.Path, default=DEFAULT_MATRIX)
    parser.add_argument("--evidence", type=pathlib.Path, action="append", default=[])
    parser.add_argument("--evidence-dir", type=pathlib.Path, default=None)
    parser.add_argument(
        "--demote-superseded",
        action="store_true",
        help="demote supported patches replaced by a promoted patch",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    return parser


def main(
    argv: list[str] | None = None, *, repo_root: pathlib.Path = REPO_ROOT
) -> int:
    args = _parser().parse_args(argv)
    try:
        matrix = _load_matrix(args.matrix)
        records = _collect_records(args.evidence, args.evidence_dir)
        original = json.dumps(matrix, indent=2)
        promoted, summary = build_promoted_matrix(
            matrix,
            records,
            demote_superseded=args.demote_superseded,
        )
    except (OSError, ValueError, EvidenceError, PromotionError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    changed = json.dumps(promoted, indent=2) != original
    for line in summary:
        print(f"  {line}")

    if args.check:
        if changed:
            print(
                "FAIL: the matrix does not reflect the supplied evidence; "
                "run without --check",
                file=sys.stderr,
            )
            return 2
        print("PASS: the matrix already reflects the supplied evidence")
        return 0

    if args.dry_run:
        print("dry run: matrix unchanged")
        return 0

    if not changed:
        print("PASS: the matrix already reflects the supplied evidence")
        return 0

    try:
        _write_matrix(args.matrix, promoted, root=repo_root)
    except PromotionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.matrix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
