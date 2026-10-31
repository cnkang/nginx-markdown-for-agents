#!/usr/bin/env python3
"""Stage the compatibility evidence records that one release run produced.

The release gate downloads every ``compat-evidence-*`` artifact of its own
run into one subdirectory per artifact.  This tool copies exactly one record
from each subdirectory into a flat, fresh directory that
``validate_compatibility_evidence.py --evidence-dir`` reads.

The tool checks the layout before it copies anything, so a failure leaves no
partial destination.  It rejects:

- a source that is missing, is a symlink, or holds no artifact
- a top-level entry that is not a ``compat-evidence-*`` directory
- an artifact directory that is a symlink, or that does not hold exactly one
  regular ``.json`` file with a safe name
- a record that is a symlink or that resolves outside its artifact directory
- the same record name in two artifacts (one producer would hide another)
- a destination that is a symlink or a non-empty directory

Usage:
    python3 tools/release/matrix/stage_compatibility_evidence.py \
        --source "$RUNNER_TEMP/compat-evidence-download" \
        --dest "$RUNNER_TEMP/compat-evidence"

Exit codes:
    0  Every record was staged
    1  The download layout or the destination is unsafe
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import (  # noqa: E402
    validate_filename_strict,
    validate_read_path,
)

ARTIFACT_NAME_RE = re.compile(r"^compat-evidence-[A-Za-z0-9._-]+$")


class StageError(RuntimeError):
    """Raised when the downloaded evidence layout is unsafe."""


def _resolve_source(raw: pathlib.Path) -> pathlib.Path:
    if os.path.islink(raw):
        raise StageError(f"evidence source {raw} is a symlink")
    source = validate_read_path(raw, purpose="compatibility evidence download")
    if not source.is_dir():
        raise StageError(f"evidence source {source} is not a directory")
    return source


def _record_in(artifact: os.DirEntry) -> pathlib.Path:
    """Return the single record that one artifact directory holds."""
    if artifact.is_symlink() or not artifact.is_dir(follow_symlinks=False):
        raise StageError(f"{artifact.name}: not a regular artifact directory")
    if not ARTIFACT_NAME_RE.match(artifact.name):
        raise StageError(f"{artifact.name}: not a compat-evidence-* artifact")
    with os.scandir(artifact.path) as handle:
        entries = sorted(handle, key=lambda entry: entry.name)
    if len(entries) != 1:
        raise StageError(
            f"{artifact.name}: expected exactly one record, found {len(entries)}"
        )
    entry = entries[0]
    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
        raise StageError(f"{artifact.name}/{entry.name}: not a regular file")
    try:
        validate_filename_strict(entry.name, purpose="evidence record name")
    except ValueError as exc:
        raise StageError(f"{artifact.name}: {exc}") from exc
    if not entry.name.endswith(".json"):
        raise StageError(f"{artifact.name}/{entry.name}: not a .json record")
    parent = pathlib.Path(os.path.realpath(artifact.path))
    record = pathlib.Path(os.path.realpath(entry.path))
    if record.parent != parent:
        raise StageError(
            f"{artifact.name}/{entry.name}: resolves outside its artifact"
        )
    return record


def collect(source: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Return ``(record name, path)`` pairs after every layout check."""
    with os.scandir(source) as handle:
        artifacts = sorted(handle, key=lambda entry: entry.name)
    if not artifacts:
        raise StageError(f"evidence source {source} holds no artifact")
    owners: dict[str, str] = {}
    staged: list[tuple[str, pathlib.Path]] = []
    for artifact in artifacts:
        record = _record_in(artifact)
        if record.name in owners:
            raise StageError(
                f"record {record.name} appears in both {owners[record.name]} "
                f"and {artifact.name}"
            )
        owners[record.name] = artifact.name
        staged.append((record.name, record))
    return staged


def _prepare_dest(raw: pathlib.Path) -> pathlib.Path:
    if os.path.islink(raw):
        raise StageError(f"destination {raw} is a symlink")
    dest = validate_read_path(
        raw, must_exist=False, purpose="compatibility evidence destination"
    )
    if dest.exists():
        if not dest.is_dir():
            raise StageError(f"destination {dest} is not a directory")
        if any(dest.iterdir()):
            raise StageError(f"destination {dest} is not empty")
    return dest


def stage(source_raw: pathlib.Path, dest_raw: pathlib.Path) -> int:
    """Copy every record into the destination and return the count."""
    source = _resolve_source(source_raw)
    records = collect(source)
    dest = _prepare_dest(dest_raw)
    dest.mkdir(parents=True, exist_ok=True)
    for name, record in records:
        # The name passed validate_filename_strict and the destination is a
        # fresh directory, so the copy target cannot leave it.
        shutil.copyfile(record, dest / name, follow_symlinks=False)
    return len(records)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, required=True)
    parser.add_argument("--dest", type=pathlib.Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        count = stage(args.source, args.dest)
    except (OSError, ValueError, StageError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"staged {count} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
