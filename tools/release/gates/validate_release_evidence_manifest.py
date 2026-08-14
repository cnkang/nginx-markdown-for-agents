#!/usr/bin/env python3
"""Release evidence manifest gate validator.

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
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import validate_read_path  # noqa: E402

SCHEMA_VERSION = "release.evidence-manifest.v1"

FINAL_EVIDENCE_MANIFEST_LABEL = "final evidence manifest"

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
        _check_entry(entry, index, seen_ids, reasons)

    return reasons


def _check_required_entry_fields(entry: dict, index: int, reasons: list) -> None:
    """Record missing required fields for one evidence entry."""
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            reasons.append(
                f"missing-observation: entries[{index}] missing {field}")


def _check_entry_identity(entry: dict, seen_ids: set, reasons: list) -> None:
    """Record duplicate evidence entry identifiers."""
    entry_id = entry.get("id")
    if entry_id is not None:
        if entry_id in seen_ids:
            reasons.append(
                f"malformed: duplicate entry id {entry_id!r}")
        seen_ids.add(entry_id)


def _check_entry_status(entry: dict, index: int, reasons: list) -> None:
    """Record invalid evidence status values."""
    status = entry.get("status")
    if status is not None and status not in VALID_STATUSES:
        reasons.append(
            f"malformed: entries[{index}] status {status!r} not in "
            f"{VALID_STATUSES}")


def _check_entry_blocking(entry: dict, index: int, reasons: list) -> None:
    """Record invalid blocking flags and failed blocking entries."""
    blocking = entry.get("blocking")
    status = entry.get("status")
    if blocking is not None and not isinstance(blocking, bool):
        reasons.append(
            f"malformed: entries[{index}] blocking must be a boolean")

    if entry.get("blocking") is True and status != "pass":
        reasons.append(
            f"blocking-pending: entries[{index}] "
            f"(category={entry.get('category')!r}) is blocking with "
            f"status={status!r}; blocking entries must pass")


def _check_entry_skip_requirements(entry: dict, index: int, reasons: list) -> None:
    """Record missing justification fields for skipped entries."""
    status = entry.get("status")
    if status == "skip":
        for field in ("justification", "policy_reference"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                reasons.append(
                    f"malformed: entries[{index}] status=skip requires "
                    f"non-empty {field}")


def _check_entry(entry: dict, index: int, seen_ids: set, reasons: list) -> None:
    """Validate one evidence manifest entry."""
    _check_required_entry_fields(entry, index, reasons)
    _check_entry_identity(entry, seen_ids, reasons)
    _check_entry_status(entry, index, reasons)
    _check_entry_blocking(entry, index, reasons)
    _check_entry_skip_requirements(entry, index, reasons)


def run_fixture_gate(args) -> int:
    """Validate a pre-made evidence manifest."""
    if not args.record_input:
        raise ValueError(
            "malformed: --record-input is required in fixture mode")

    record = load_json(args.record_input, "release evidence manifest")
    reasons = validate_record(record, expected_sha=args.expected_sha)

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(f"PASS: release evidence manifest {args.record_input} validated")
    return 0


def _require_jsonschema() -> bool:
    """Return True when jsonschema is importable; else report and return
    False."""
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        print(
            "ERROR: jsonschema required (pip install -r requirements-dev.txt)",
            file=sys.stderr)
        return False
    return True


def _validate_evidence_schema(manifest: dict, reasons: list) -> None:
    """Validate the manifest instance against the release evidence schema."""
    schema_path = REPO_ROOT / "schemas" / "final-evidence-manifest.schema.json"
    if not schema_path.is_file():
        reasons.append(
            "missing-observation: final-evidence-manifest schema missing")
        return
    import jsonschema
    try:
        schema = load_json(schema_path, "final evidence manifest schema")
        jsonschema.validate(instance=manifest, schema=schema)
    except jsonschema.ValidationError as exc:
        reasons.append(f"malformed: instance schema violation: {exc.message}")


def _validate_observation_state(
    reasons: list, expected_sha: str | None, release_dir: pathlib.Path
) -> None:
    """Validate the observation-state record against its schema when
    present, resolving the record from the manifest's own release
    directory rather than a hardcoded version."""
    obs_state_path = release_dir / "observation-state.json"
    if not obs_state_path.is_file():
        return
    obs_schema_path = REPO_ROOT / "schemas" / "observation-state.schema.json"
    if not obs_schema_path.is_file():
        reasons.append(
            "missing-observation: observation-state schema missing")
        return
    import jsonschema
    try:
        obs = load_json(obs_state_path, "observation state")
        obs_schema = load_json(obs_schema_path, "observation state schema")
        jsonschema.validate(instance=obs, schema=obs_schema)
        if expected_sha and obs.get("candidate_sha") != expected_sha:
            reasons.append(
                "stale-digest: observation-state candidate_sha "
                f"{obs.get('candidate_sha')} != frozen candidate {expected_sha}"
            )
    except ValueError as exc:
        reasons.append(f"malformed: observation-state invalid: {exc}")
    except jsonschema.ValidationError as exc:
        reasons.append(
            f"malformed: observation-state schema violation: "
            f"{exc.message}")


def _resolve_expected_sha(args) -> str | None:
    """Resolve the frozen candidate SHA from the release-candidate-sha
    manifest, or use the explicit --expected-sha when given."""
    if args.expected_sha:
        return args.expected_sha
    # Derive the release version from the selected manifest's parent
    # directory (artifacts/release/<version>/...) rather than hardcoding 0.9.2.
    manifest_path = validate_read_path(
        args.manifest, purpose=FINAL_EVIDENCE_MANIFEST_LABEL
    )
    candidate_path = manifest_path.parent / "release-candidate-sha-manifest.json"
    if not candidate_path.is_file():
        raise ValueError(
            "missing-observation: release candidate manifest is required "
            "for real final-evidence validation"
        )
    candidate = load_json(candidate_path, "release candidate manifest")
    return candidate.get("candidate_sha")


def run_real_gate(args) -> int:
    """Validate the candidate-bound final evidence manifest and
    observation-state record against the release evidence schemas."""
    manifest_path = validate_read_path(
        args.manifest, purpose=FINAL_EVIDENCE_MANIFEST_LABEL
    )

    manifest = load_json(manifest_path, FINAL_EVIDENCE_MANIFEST_LABEL)
    expected_sha = _resolve_expected_sha(args)
    reasons = []
    candidate_sha = manifest.get("candidate_sha")
    if not isinstance(candidate_sha, str) or not CANDIDATE_SHA_PATTERN.fullmatch(
        candidate_sha
    ):
        reasons.append("malformed: candidate_sha must be 40 lowercase hex")
    elif expected_sha and candidate_sha != expected_sha:
        reasons.append(
            f"stale-digest: candidate_sha {candidate_sha} != expected {expected_sha}"
        )
    if not _require_jsonschema():
        if reasons:
            for reason in reasons:
                print(f"ERROR: {reason}", file=sys.stderr)
        return 1
    _validate_evidence_schema(manifest, reasons)
    _validate_observation_state(reasons, expected_sha, manifest_path.parent)

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
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
