#!/usr/bin/env python3
"""Release candidate evidence gate validator.

Validates a release-candidate evidence record against the v1 schema.
The record binds a candidate SHA to a set of gate entries, each with a
status and blocking flag. All blocking entries must have status=pass;
no blocking entry may be pending.

Real mode validates the candidate-bound release-candidate-sha-manifest
(`artifacts/release/0.9.2/release-candidate-sha-manifest.json`) produced
by release tooling: candidate SHA format, branch, source-tree digest, required
input existence, and per-input sha256 digests. When run inside the
repository worktree at the freeze point, the candidate SHA is verified
against `git rev-parse HEAD`.

Fixture mode validates a pre-made evidence record against the schema,
rejecting it with an identifiable reason:

  - malformed             record is not JSON or lacks required structure
  - stale-digest          record candidate_sha differs from expected
  - blocking-pending      a blocking entry has status != pass
  - below-threshold       missing required gate entries
  - missing-observation   entry missing required fields

Exit codes:
  0 = validation passed
  1 = validation failed or could not be established
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import validate_read_path  # noqa: E402
from lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)

SCHEMA_VERSION = "release.candidate-evidence.v1"
MANIFEST_SCHEMA_VERSION = "release.candidate-sha-manifest.v1"

CANDIDATE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")

REQUIRED_TOP_FIELDS = ("schema_version", "candidate_sha", "created_at", "entries")

REQUIRED_ENTRY_FIELDS = (
    "id", "gate", "status", "blocking", "evidence_ref", "checked_at"
)

VALID_STATUSES = ("pass", "fail", "pending", "skip")

MANIFEST_REQUIRED_FIELDS = (
    "schema_version",
    "candidate_sha",
    "branch",
    "source_tree_digest",
    "frozen_at",
    "required_inputs",
    "input_digests",
    "feature_manifest_digest",
    "final_ffi_freeze_digest",
    "canonical_performance_environment_digest",
    "release_matrix_digest",
    "evidence_schema_digests",
)

DEFAULT_MANIFEST_PATH = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2"
    / "release-candidate-sha-manifest.json"
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the release candidate evidence gate."""
    parser = argparse.ArgumentParser(
        description="Validate release candidate evidence record")
    parser.add_argument("--mode", choices=("real", "fixture"), default="real")
    parser.add_argument("--record-input",
                        help="fixture mode: evidence record to validate")
    parser.add_argument("--expected-sha",
                        help="optional: expected candidate sha for "
                             "stale-digest detection")
    parser.add_argument("--manifest",
                        default=str(DEFAULT_MANIFEST_PATH),
                        help="real mode: candidate sha manifest to validate")
    parser.add_argument("--git-head",
                        action="store_true",
                        help="real mode: verify candidate_sha equals "
                             "`git rev-parse HEAD`")
    return parser


def load_json(path: str | Path, label: str) -> dict:
    """Load a JSON object, failing closed with a malformed reason."""
    validated_path = validate_read_path(path, purpose=label)
    try:
        data = json.loads(validated_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"malformed: unable to read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"malformed: {label} must be a JSON object")
    return data


def file_digest(path: Path) -> str:
    """Return the sha256 digest of a file (canonical-content format)."""
    validated_path = validate_read_path(path, purpose="release evidence input")
    return "sha256:" + hashlib.sha256(validated_path.read_bytes()).hexdigest()


def git_head_sha() -> str:
    """Return the current git HEAD SHA or raise ValueError."""
    git = resolve_approved_executable("git")
    if git is None:
        raise ValueError(
            "malformed: cannot resolve git HEAD: approved git executable "
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
        raise ValueError(f"malformed: cannot resolve git HEAD: {exc}") from exc
    if proc.returncode != 0:
        raise ValueError(
            f"malformed: cannot resolve git HEAD: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def validate_manifest(manifest: dict, git_head: bool = False) -> list[str]:
    """Validate a release-candidate-sha-manifest, returning reasons."""
    reasons = []

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        reasons.append(
            f"malformed: schema_version {manifest.get('schema_version')!r} "
            f"!= {MANIFEST_SCHEMA_VERSION!r}")

    for field in MANIFEST_REQUIRED_FIELDS:
        if field not in manifest:
            reasons.append(f"missing-observation: manifest missing {field}")

    _check_manifest_identity(manifest, reasons)
    _check_manifest_digest_fields(manifest, reasons)
    _check_required_inputs(manifest, reasons)
    _check_evidence_schemas(manifest, reasons)
    _check_git_head(manifest, git_head, reasons)

    return reasons


def _check_manifest_identity(manifest: dict, reasons: list) -> None:
    """Validate candidate SHA, branch, tree digest, and frozen_at."""
    sha = manifest.get("candidate_sha")
    if not isinstance(sha, str) or not CANDIDATE_SHA_PATTERN.fullmatch(sha):
        reasons.append(
            "malformed: candidate_sha must be 40 lowercase hex")

    branch = manifest.get("branch")
    if not isinstance(branch, str) or not branch:
        reasons.append("malformed: branch must be a non-empty string")

    tree_digest = manifest.get("source_tree_digest")
    if not isinstance(tree_digest, str) or not DIGEST_PATTERN.fullmatch(tree_digest):
        reasons.append(
            "malformed: source_tree_digest must be a sha256 digest")

    frozen_at = manifest.get("frozen_at")
    if not isinstance(frozen_at, str):
        reasons.append("malformed: frozen_at must be an ISO-8601 string")
    else:
        try:
            datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
        except ValueError:
            reasons.append("malformed: frozen_at is not ISO-8601")


def _check_manifest_digest_fields(manifest: dict, reasons: list) -> None:
    """Validate the release digest fields are strings."""
    for digest_field in (
        "feature_manifest_digest",
        "final_ffi_freeze_digest",
        "canonical_performance_environment_digest",
        "release_matrix_digest",
    ):
        value = manifest.get(digest_field)
        if value is not None and (
            not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value)
        ):
            reasons.append(
                f"malformed: {digest_field} must be a sha256 digest"
            )


def _check_required_input(
        input_path, expected: object, repo_resolved: Path, reasons: list
) -> None:
    """Validate one required input and its recorded digest."""
    if not isinstance(input_path, str) or not input_path:
        reasons.append(
            "malformed: required_inputs entries must be non-empty strings"
        )
        return
    if not isinstance(expected, str) or not DIGEST_PATTERN.fullmatch(expected):
        reasons.append(
            f"missing-observation: no digest for required input "
            f"{input_path!r}"
        )
        return
    input_file = validate_read_path(
        REPO_ROOT / input_path,
        must_exist=False,
        purpose=f"required input {input_path}",
    )
    try:
        input_file.relative_to(repo_resolved)
    except ValueError:
        reasons.append(
            f"malformed: required_inputs[{input_path!r}] "
            "escapes repository root"
        )
        return
    if not input_file.is_file():
        reasons.append(
            f"missing-observation: required input missing: {input_path}"
        )
        return
    actual = file_digest(input_file)
    if actual != expected:
        reasons.append(
            f"stale-digest: input {input_path} digest {actual} "
            f"!= recorded {expected}"
        )


def _check_required_inputs(manifest: dict, reasons: list) -> None:
    """Validate required inputs exist and their digests match."""
    repo_resolved = REPO_ROOT.resolve()

    required_inputs = manifest.get("required_inputs")
    input_digests = manifest.get("input_digests")
    if not isinstance(required_inputs, list) or not required_inputs:
        reasons.append("below-threshold: required_inputs must be a non-empty list")
        return
    if not isinstance(input_digests, dict):
        reasons.append(
            "missing-observation: input_digests must be an object")
        return
    if any(not isinstance(input_path, str) or not input_path
           for input_path in required_inputs):
        reasons.append(
            "below-threshold: required_inputs must contain non-empty strings")
        return
    if len(set(required_inputs)) != len(required_inputs):
        reasons.append("below-threshold: required_inputs must not contain duplicates")
        return
    if set(input_digests) != set(required_inputs):
        reasons.append(
            "missing-observation: input_digests keys must match "
            "required_inputs exactly")
    for input_path in required_inputs:
        expected = (input_digests.get(input_path)
                    if isinstance(input_path, str) else None)
        _check_required_input(input_path, expected, repo_resolved, reasons)


def _check_evidence_schemas(manifest: dict, reasons: list) -> None:
    """Validate evidence schema digests when declared."""
    repo_resolved = REPO_ROOT.resolve()

    def _contains(candidate: Path, label: str) -> bool:
        try:
            candidate.relative_to(repo_resolved)
        except ValueError:
            reasons.append(f"malformed: {label} escapes repository root")
            return False
        return True

    evidence_schema_digests = manifest.get("evidence_schema_digests")
    if evidence_schema_digests is None:
        return
    if not isinstance(evidence_schema_digests, dict):
        reasons.append(
            "malformed: evidence_schema_digests must be an object")
        return
    for schema_path, expected in evidence_schema_digests.items():
        if not isinstance(schema_path, str) or not schema_path:
            reasons.append(
                "malformed: evidence schema paths must be non-empty strings"
            )
            continue
        schema_file = validate_read_path(
            REPO_ROOT / schema_path,
            must_exist=False,
            purpose=f"evidence schema {schema_path}",
        )
        if not _contains(
            schema_file, f"evidence_schema_digests[{schema_path!r}]"
        ):
            continue
        if not schema_file.is_file():
            reasons.append(
                f"missing-observation: evidence schema missing: "
                f"{schema_path}")
            continue
        actual = file_digest(schema_file)
        if actual != expected:
            reasons.append(
                f"stale-digest: evidence schema {schema_path} "
                f"digest {actual} != recorded {expected}")


def _check_git_head(manifest: dict, git_head: bool, reasons: list) -> None:
    """Verify the candidate SHA equals git HEAD when requested."""
    if not git_head:
        return
    try:
        head = git_head_sha()
        if manifest.get("candidate_sha") != head:
            reasons.append(
                f"stale-digest: candidate_sha {manifest.get('candidate_sha')} "
                f"!= git HEAD {head}")
    except ValueError as exc:
        reasons.append(str(exc))


def validate_record(record: dict, expected_sha: str | None = None) -> list[str]:
    """Validate a candidate evidence record, returning rejection reasons."""
    reasons = []

    # Check schema version
    if record.get("schema_version") != SCHEMA_VERSION:
        reasons.append(
            f"malformed: schema_version {record.get('schema_version')!r} "
            f"!= {SCHEMA_VERSION!r}")

    # Check candidate_sha
    sha = record.get("candidate_sha")
    if not isinstance(sha, str) or not CANDIDATE_SHA_PATTERN.fullmatch(sha):
        reasons.append("malformed: candidate_sha must be 40 lowercase hex")
    elif expected_sha and sha != expected_sha:
        reasons.append(
            f"stale-digest: candidate_sha {sha} != expected {expected_sha}")

    # Check created_at
    if not isinstance(record.get("created_at"), str):
        reasons.append("malformed: created_at must be a string")

    # Check entries array
    entries = record.get("entries")
    if not isinstance(entries, list):
        reasons.append("malformed: entries must be an array")
        return reasons

    if not entries:
        reasons.append("below-threshold: entries array is empty")
        return reasons

    seen_ids = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            reasons.append(f"malformed: entries[{index}] must be an object")
            continue
        _check_entry(entry, index, seen_ids, reasons)

    return reasons


def _check_entry(entry: dict, index: int, seen_ids: set, reasons: list) -> None:
    """Validate one entries entry."""
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            reasons.append(
                f"missing-observation: entries[{index}] missing {field}")

    entry_id = entry.get("id")
    if entry_id is not None:
        if entry_id in seen_ids:
            reasons.append(
                f"malformed: duplicate entry id {entry_id!r}")
        seen_ids.add(entry_id)

    status = entry.get("status")
    if status is not None and status not in VALID_STATUSES:
        reasons.append(
            f"malformed: entries[{index}] status {status!r} not in "
            f"{VALID_STATUSES}")

    blocking = entry.get("blocking")
    if blocking is not None and not isinstance(blocking, bool):
        reasons.append(
            f"malformed: entries[{index}] blocking must be a boolean")

    if entry.get("blocking") is True:
        if status == "pending":
            reasons.append(
                f"blocking-pending: entries[{index}] "
                f"(gate={entry.get('gate')!r}) is blocking with "
                f"status=pending")
        elif status == "fail":
            reasons.append(
                f"blocking-pending: entries[{index}] "
                f"(gate={entry.get('gate')!r}) is blocking with "
                f"status=fail")


def run_fixture_gate(args) -> int:
    """Validate a pre-made evidence record."""
    if not args.record_input:
        raise ValueError("malformed: --record-input is required in fixture mode")

    record = load_json(args.record_input, "candidate evidence record")
    reasons = validate_record(record, expected_sha=args.expected_sha)

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(f"PASS: candidate evidence record {args.record_input} validated")
    return 0


def run_real_gate(args) -> int:
    """Validate the candidate-bound release-candidate-sha-manifest."""
    manifest_path = validate_read_path(
        args.manifest, purpose="candidate SHA manifest"
    )

    manifest = load_json(manifest_path, "candidate sha manifest")
    reasons = validate_manifest(manifest, git_head=args.git_head)

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(
        f"PASS: release candidate manifest {manifest_path} validated "
        f"(candidate_sha={manifest['candidate_sha']}, "
        f"inputs={len(manifest['required_inputs'])})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the release candidate evidence gate."""
    args = build_arg_parser().parse_args(argv)
    try:
        if args.mode == "fixture":
            return run_fixture_gate(args)
        return run_real_gate(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
