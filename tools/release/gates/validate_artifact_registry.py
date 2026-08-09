#!/usr/bin/env python3
"""Artifact registry gate validator.

Validates an artifact registry record against the v1 schema. The record
binds a candidate SHA to a set of artifacts with storage class, producer,
consumer, and cryptographic digest information.

Real mode validates the candidate-bound release artifact index
(`artifacts/release/0.9.2/candidate-release-artifact-index.json`)
produced by release tooling: candidate identity against the frozen
release-candidate-sha-manifest, per-artifact digest/AIDB bindings,
feature-manifest digest and ABI version consistency with the official
manifest and frozen ABI header, and authoritative-attempt semantics.

Fixture mode validates a pre-made registry record against the schema,
rejecting it with an identifiable reason:

  - malformed             record is not JSON or lacks required structure
  - stale-digest          record candidate_sha differs from expected
  - blocking-pending      duplicate artifact IDs detected
  - below-threshold       empty artifacts array or invalid digests
  - missing-observation   artifact missing required fields

Exit codes:
  0 = validation passed
  1 = validation failed or could not be established
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import (  # noqa: E402
    validate_read_path,
)

SCHEMA_VERSION = "release.artifact-registry.v1"
INDEX_SCHEMA_VERSION = "release.candidate-artifact-index.v1"

CANDIDATE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")

REQUIRED_TOP_FIELDS = ("schema_version", "candidate_sha", "created_at", "artifacts")

REQUIRED_ARTIFACT_FIELDS = (
    "id", "path_or_url", "storage_class", "producer", "consumer",
    "digest", "retention"
)

VALID_STORAGE_CLASSES = ("source", "candidate", "release")

INDEX_REQUIRED_ARTIFACT_FIELDS = (
    "artifact_type",
    "release_matrix_row_id",
    "artifact_id",
    "candidate_sha",
    "artifact_sha256",
    "feature_manifest_digest",
    "abi_version",
    "verification_status",
)

VALID_VERIFICATION_STATUSES = ("pass", "pending", "fail", "skip")

DEFAULT_INDEX_PATH = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2"
    / "candidate-release-artifact-index.json"
)
DEFAULT_CANDIDATE_MANIFEST = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2"
    / "release-candidate-sha-manifest.json"
)
FEATURE_MANIFEST_PATH = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2"
    / "official-build-feature-manifest.json"
)
ABI_HEADER_PATH = (
    REPO_ROOT / "components" / "rust-converter" / "include"
    / "markdown_converter.h"
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the artifact registry gate."""
    parser = argparse.ArgumentParser(
        description="Validate artifact registry record")
    parser.add_argument("--mode", choices=("real", "fixture"), default="real")
    parser.add_argument("--record-input",
                        help="fixture mode: registry record to validate")
    parser.add_argument("--expected-sha",
                        help="optional: expected candidate sha for "
                             "stale-digest detection")
    parser.add_argument("--index",
                        default=str(DEFAULT_INDEX_PATH),
                        help="real mode: candidate artifact index to validate")
    parser.add_argument("--candidate-manifest",
                        default=str(DEFAULT_CANDIDATE_MANIFEST),
                        help="real mode: frozen release candidate manifest")
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


def canonical_digest_of(path: Path) -> str:
    """Canonical-content digest of a JSON manifest file."""
    validated_path = validate_read_path(path, purpose="JSON digest input")
    doc = json.loads(validated_path.read_text(encoding="utf-8"))
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode(
        "utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def frozen_feature_digest() -> str:
    """Official feature-manifest digest (canonical-content convention)."""
    return canonical_digest_of(FEATURE_MANIFEST_PATH)


def frozen_abi_version() -> int:
    """MARKDOWN_ABI_VERSION from the generated header."""
    validated_path = validate_read_path(
        ABI_HEADER_PATH, purpose="generated ABI header"
    )
    text = validated_path.read_text(encoding="utf-8")
    match = re.search(r"#define\s+MARKDOWN_ABI_VERSION\s+(\d+)", text)
    if not match:
        raise ValueError(
            "malformed: MARKDOWN_ABI_VERSION not found in generated header")
    return int(match.group(1))


def validate_index(index: dict, candidate_sha: str | None) -> list[str]:
    """Validate a candidate artifact index, returning rejection reasons."""
    reasons = []

    if index.get("schema_version") != INDEX_SCHEMA_VERSION:
        reasons.append(
            f"malformed: schema_version {index.get('schema_version')!r} "
            f"!= {INDEX_SCHEMA_VERSION!r}")

    sha = index.get("candidate_sha")
    if not isinstance(sha, str) or not CANDIDATE_SHA_PATTERN.fullmatch(sha):
        reasons.append("malformed: candidate_sha must be 40 lowercase hex")
    elif candidate_sha and sha != candidate_sha:
        reasons.append(
            f"stale-digest: index candidate_sha {sha} != frozen "
            f"candidate {candidate_sha}")

    try:
        expected_digest = frozen_feature_digest()
        expected_abi = frozen_abi_version()
    except (OSError, json.JSONDecodeError) as exc:
        reasons.append(f"malformed: cannot resolve official bindings: {exc}")
        return reasons

    artifacts = index.get("artifacts")
    if not isinstance(artifacts, list):
        reasons.append("malformed: artifacts must be an array")
        return reasons

    if not artifacts:
        reasons.append("below-threshold: artifacts array is empty")
        return reasons

    seen_ids = set()
    for index_pos, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            reasons.append(f"malformed: artifacts[{index_pos}] must be an object")
            continue
        _check_index_artifact(artifact, index_pos, seen_ids, expected_digest,
                              expected_abi, reasons)

    return reasons


def _check_index_artifact(artifact: dict, index_pos: int, seen_ids: set,
                          expected_digest: str, expected_abi: int,
                          reasons: list) -> None:
    """Validate one candidate artifact index row."""
    for field in INDEX_REQUIRED_ARTIFACT_FIELDS:
        if field not in artifact:
            reasons.append(
                f"missing-observation: artifacts[{index_pos}] missing "
                f"{field}")

    artifact_id = artifact.get("artifact_id")
    if artifact_id is not None:
        if artifact_id in seen_ids:
            reasons.append(
                f"blocking-pending: duplicate artifact id {artifact_id!r}")
        seen_ids.add(artifact_id)

    artifact_sha = artifact.get("artifact_sha256")
    if artifact_sha is not None and (
        not isinstance(artifact_sha, str)
        or not DIGEST_PATTERN.fullmatch(artifact_sha)
    ):
        reasons.append(
            f"below-threshold: artifacts[{index_pos}] artifact_sha256 "
            f"{artifact_sha!r} is not valid sha256 format")

    feature_digest = artifact.get("feature_manifest_digest")
    if feature_digest is not None and feature_digest != expected_digest:
        reasons.append(
            f"stale-digest: artifacts[{index_pos}] "
            f"feature_manifest_digest {feature_digest} != official "
            f"{expected_digest}")

    abi = artifact.get("abi_version")
    if abi is not None and abi != expected_abi:
        reasons.append(
            f"stale-digest: artifacts[{index_pos}] abi_version {abi} "
            f"!= frozen ABI {expected_abi}")

    status = artifact.get("verification_status")
    if status is not None and status not in VALID_VERIFICATION_STATUSES:
        reasons.append(
            f"malformed: artifacts[{index_pos}] verification_status "
            f"{status!r} not in {VALID_VERIFICATION_STATUSES}")

    _check_local_artifact_digest(artifact, index_pos, reasons)


def _check_local_artifact_digest(artifact: dict, index_pos: int,
                                 reasons: list) -> None:
    """Verify digest against local bytes when the artifact is present."""
    repo_resolved = REPO_ROOT.resolve()

    def _contains(candidate: Path, label: str) -> bool:
        try:
            candidate.relative_to(repo_resolved)
        except ValueError:
            reasons.append(
                f"malformed: {label} escapes repository root")
            return False
        return True

    artifact_sha_value = artifact.get("artifact_sha256")
    artifact_type = artifact.get("artifact_type")
    artifact_path = artifact.get("artifact_id", "")
    if not artifact_sha_value or artifact_type not in ("deb", "rpm", "source"):
        return
    if not isinstance(artifact_path, str) or not artifact_path:
        reasons.append(
            f"malformed: artifacts[{index_pos}].artifact_id must be a path"
        )
        return
    local = validate_read_path(
        REPO_ROOT / artifact_path,
        must_exist=False,
        purpose=f"artifacts[{index_pos}].artifact_id",
    )
    if not _contains(local, f"artifacts[{index_pos}].artifact_id"):
        return
    if local.is_file():
        actual = "sha256:" + hashlib.sha256(
            local.read_bytes()).hexdigest()
        if actual != artifact_sha_value:
            reasons.append(
                f"stale-digest: artifacts[{index_pos}] local "
                f"artifact {artifact_path} digest {actual} != "
                f"recorded {artifact_sha_value}")


def validate_record(record: dict, expected_sha: str | None = None) -> list[str]:
    """Validate an artifact registry record, returning rejection reasons."""
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

    # Check artifacts array
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list):
        reasons.append("malformed: artifacts must be an array")
        return reasons

    if not artifacts:
        reasons.append("below-threshold: artifacts array is empty")
        return reasons

    seen_ids = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            reasons.append(f"malformed: artifacts[{index}] must be an object")
            continue
        _check_registry_artifact(artifact, index, seen_ids, reasons)

    return reasons


def _check_required_strings(artifact: dict, index: int, reasons: list) -> None:
    """Required registry string fields must be non-empty."""
    for field in ("id", "path_or_url", "producer", "consumer", "retention"):
        val = artifact.get(field)
        if val is not None and (not isinstance(val, str) or not val):
            reasons.append(
                f"malformed: artifacts[{index}].{field} must be "
                f"a non-empty string")


def _check_registry_artifact(artifact: dict, index: int, seen_ids: set,
                             reasons: list) -> None:
    """Validate one registry artifact record."""
    for field in REQUIRED_ARTIFACT_FIELDS:
        if field not in artifact:
            reasons.append(
                f"missing-observation: artifacts[{index}] missing {field}")

    _check_required_strings(artifact, index, reasons)

    artifact_id = artifact.get("id")
    if artifact_id is not None:
        if artifact_id in seen_ids:
            reasons.append(
                f"blocking-pending: duplicate artifact id "
                f"{artifact_id!r}")
        seen_ids.add(artifact_id)

    storage_class = artifact.get("storage_class")
    if (storage_class is not None
            and storage_class not in VALID_STORAGE_CLASSES):
        reasons.append(
            f"malformed: artifacts[{index}] storage_class "
            f"{storage_class!r} not in {VALID_STORAGE_CLASSES}")

    digest = artifact.get("digest")
    if digest is not None:
        if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
            reasons.append(
                f"below-threshold: artifacts[{index}] digest "
                f"{digest!r} is not valid sha256 format")


def run_fixture_gate(args) -> int:
    """Validate a pre-made artifact registry record."""
    if not args.record_input:
        raise ValueError(
            "malformed: --record-input is required in fixture mode")

    record = load_json(args.record_input, "artifact registry record")
    reasons = validate_record(record, expected_sha=args.expected_sha)

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(f"PASS: artifact registry record {args.record_input} validated")
    return 0


def run_real_gate(args) -> int:
    """Validate the candidate-bound release artifact index."""
    index_path = validate_read_path(
        args.index, purpose="candidate artifact index"
    )

    candidate_sha = None
    candidate_manifest_path = validate_read_path(
        args.candidate_manifest, purpose="candidate manifest"
    )
    candidate = load_json(candidate_manifest_path, "candidate manifest")
    candidate_sha = candidate.get("candidate_sha")

    index = load_json(index_path, "candidate artifact index")
    reasons = validate_index(index, candidate_sha)

    if reasons:
        for reason in reasons:
            print(f"ERROR: {reason}", file=sys.stderr)
        return 1

    print(
        f"PASS: candidate artifact index {index_path} validated "
        f"(candidate_sha={index['candidate_sha']}, "
        f"artifacts={len(index['artifacts'])})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the artifact registry gate."""
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
