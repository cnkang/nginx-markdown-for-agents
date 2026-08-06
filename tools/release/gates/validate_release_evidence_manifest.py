#!/usr/bin/env python3
"""Release evidence manifest gate validator (Spec 62 Task 10.5).

Validates a final release evidence manifest against the v1 schema. The
manifest binds a candidate SHA and version to a set of evidence entries,
each with a category, status, and blocking flag. All blocking entries
must have status=pass; no blocking entry may be pending.

Fixture mode validates a pre-made evidence manifest against the schema,
rejecting it with an identifiable reason:

  - malformed             record is not JSON or lacks required structure
  - stale-digest          record candidate_sha differs from expected
  - blocking-pending      a blocking entry has status != pass
  - below-threshold       missing required entries
  - missing-observation   entry missing required fields

Exit codes:
  0 = validation passed
  1 = validation failed or could not be established
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

SCHEMA_VERSION = "release.evidence-manifest.v1"

CANDIDATE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")

REQUIRED_TOP_FIELDS = (
    "schema_version", "candidate_sha", "created_at", "version", "entries"
)

REQUIRED_ENTRY_FIELDS = (
    "id", "category", "status", "blocking", "evidence_ref", "observed_at"
)

VALID_STATUSES = ("pass", "fail", "pending", "skip")


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the release evidence manifest gate."""
    parser = argparse.ArgumentParser(
        description="Validate release evidence manifest")
    parser.add_argument("--mode", choices=("real", "fixture"), default="real")
    parser.add_argument("--record-input",
                        help="fixture mode: evidence manifest to validate")
    parser.add_argument("--expected-sha",
                        help="optional: expected candidate sha for "
                             "stale-digest detection")
    parser.add_argument(
        "--manifest",
        default=str(
            REPO_ROOT / "artifacts" / "release" / "0.9.2"
            / "final-evidence-manifest.json"
        ),
        help="real mode: final evidence manifest to validate")
    return parser


def load_json(path: Path, label: str) -> dict:
    """Load a JSON object, failing closed with a malformed reason."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"malformed: unable to read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"malformed: {label} must be a JSON object")
    return data


def validate_record(record: dict, expected_sha: str | None = None) -> list[str]:
    """Validate an evidence manifest, returning rejection reasons."""
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

    # Check version
    if not isinstance(record.get("version"), str):
        reasons.append("malformed: version must be a string")

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

        # Check required fields
        for field in REQUIRED_ENTRY_FIELDS:
            if field not in entry:
                reasons.append(
                    f"missing-observation: entries[{index}] missing {field}")

        # Validate id uniqueness
        entry_id = entry.get("id")
        if entry_id is not None:
            if entry_id in seen_ids:
                reasons.append(
                    f"malformed: duplicate entry id {entry_id!r}")
            seen_ids.add(entry_id)

        # Validate status
        status = entry.get("status")
        if status is not None and status not in VALID_STATUSES:
            reasons.append(
                f"malformed: entries[{index}] status {status!r} not in "
                f"{VALID_STATUSES}")

        # Validate blocking field
        blocking = entry.get("blocking")
        if blocking is not None and not isinstance(blocking, bool):
            reasons.append(
                f"malformed: entries[{index}] blocking must be a boolean")

        # Blocking entries must be pass
        if entry.get("blocking") is True:
            if status == "pending":
                reasons.append(
                    f"blocking-pending: entries[{index}] "
                    f"(category={entry.get('category')!r}) is blocking with "
                    f"status=pending")
            elif status == "fail":
                reasons.append(
                    f"blocking-pending: entries[{index}] "
                    f"(category={entry.get('category')!r}) is blocking with "
                    f"status=fail")

    return reasons


def run_fixture_gate(args) -> int:
    """Validate a pre-made evidence manifest."""
    if not args.record_input:
        raise ValueError(
            "malformed: --record-input is required in fixture mode")

    record = load_json(Path(args.record_input), "release evidence manifest")
    reasons = validate_record(record, expected_sha=args.expected_sha)

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(f"PASS: release evidence manifest {args.record_input} validated")
    return 0


def run_real_gate(args) -> int:
    """Validate the candidate-bound final evidence manifest and
    observation-state record against the W5 evidence schemas."""
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(
            f"ERROR: malformed: final evidence manifest missing: "
            f"{manifest_path}",
            file=sys.stderr)
        return 1

    manifest = load_json(manifest_path, "final evidence manifest")
    reasons = list(validate_record(manifest, expected_sha=args.expected_sha))

    # Instance-schema validation against the W5-published evidence schemas.
    try:
        import jsonschema
    except ImportError as exc:
        print(
            "ERROR: jsonschema required (pip install -r requirements-dev.txt)",
            file=sys.stderr)
        return 1
    schema_path = REPO_ROOT / "schemas" / "final-evidence-manifest.schema.json"
    if not schema_path.is_file():
        reasons.append(
            "missing-observation: final-evidence-manifest schema missing")
    else:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(instance=manifest, schema=schema)
        except jsonschema.ValidationError as exc:
            reasons.append(f"malformed: instance schema violation: {exc.message}")

    # observation-state record (when present) against its schema.
    obs_state_path = REPO_ROOT / "artifacts" / "release" / "0.9.2" / "observation-state.json"
    if obs_state_path.is_file():
        obs_schema_path = (
            REPO_ROOT / "schemas" / "observation-state.schema.json"
        )
        if not obs_schema_path.is_file():
            reasons.append(
                "missing-observation: observation-state schema missing")
        else:
            try:
                obs = load_json(obs_state_path, "observation state")
                obs_schema = json.loads(
                    obs_schema_path.read_text(encoding="utf-8"))
                jsonschema.validate(instance=obs, schema=obs_schema)
            except (ValueError, json.JSONDecodeError) as exc:
                reasons.append(f"malformed: observation-state invalid: {exc}")
            except jsonschema.ValidationError as exc:
                reasons.append(
                    f"malformed: observation-state schema violation: "
                    f"{exc.message}")

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(
        f"PASS: final evidence manifest {manifest_path} validated "
        f"(candidate_sha={manifest['candidate_sha']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the release evidence manifest gate."""
    args = build_arg_parser().parse_args(argv)
    try:
        if args.mode == "fixture":
            return run_fixture_gate(args)
        return run_real_gate(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
