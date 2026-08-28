#!/usr/bin/env python3
"""Validate the streaming parity evidence summary and registry.

The summary is a release input, not a human-edited status note.  This gate
checks the generated v2 shape, blocking pass semantics, observed and
registry-derived counts, and candidate/input provenance so stale evidence
cannot be accepted as current.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import subprocess
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
    "known_difference_ids",
    "known_difference_observation_ids",
    "unknown_difference_count",
    "error_parity_mismatch_count",
    "pass",
    "known_differences_registry",
    "known_differences_registry_total_entries",
    "corpus_root",
    "verification_command",
    "verification_result",
    "candidate_sha",
    "source_git_commit",
    "source_tree_clean",
    "corpus_sha256",
    "registry_sha256",
    "rustc_version",
}

SCHEMA_VERSION = 2
VERIFIED_BY = "tools/release/gates/generate_streaming_evidence.py"
VERIFICATION_COMMAND = (
    "cargo test --locked --manifest-path "
    "components/rust-converter/Cargo.toml --features streaming "
    "--test streaming_parity corpus_driven_differential_harness -- --nocapture"
)
EXPECTED_CORPUS_ROOT = "tests/corpus"
EXPECTED_REGISTRY_PATH = "tests/streaming/known-differences.toml"
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_EVIDENCE_AGE = timedelta(hours=24)
MAX_CLOCK_SKEW = timedelta(minutes=5)

COMPARISON_COUNT_FIELDS = (
    "total_comparisons",
    "identical_count",
    "known_difference_count",
    "unknown_difference_count",
)


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


def _registry_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index registry entries by unique non-empty ID."""
    entries = registry.get("difference")
    if not isinstance(entries, list):
        raise ValueError("known-differences registry has no difference entries")

    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("known-differences registry contains a non-table entry")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ValueError("every registry entry needs a non-empty id")
        if entry_id in indexed:
            raise ValueError(f"known-differences registry repeats id {entry_id!r}")
        indexed[entry_id] = entry
    return indexed


def _count_registry(registry: dict[str, Any]) -> tuple[Counter, Counter, int]:
    indexed = _registry_index(registry)
    drift = Counter()
    severity = Counter()
    for entry in indexed.values():
        drift_type = entry.get("drift_type")
        level = entry.get("severity")
        if not isinstance(drift_type, str) or not isinstance(level, str):
            raise ValueError("every registry entry needs drift_type and severity")
        drift[drift_type] += 1
        severity[level] += 1
    return drift, severity, len(indexed)


def _comparison_count_errors(summary: dict[str, Any]) -> list[str]:
    values = tuple(summary.get(field) for field in COMPARISON_COUNT_FIELDS)
    if not all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        for value in values
    ):
        return ["comparison counts must be non-negative integers"]
    if sum(values[1:]) != values[0]:
        return [
            "comparison counts must satisfy "
            "identical_count + known_difference_count + "
            "unknown_difference_count == total_comparisons"
        ]
    return []


def _validate_summary_status(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - summary.keys())
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    if summary.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
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

    errors.extend(_comparison_count_errors(summary))
    return errors


def _validate_registry_metadata(
    summary: dict[str, Any], registry: dict[str, Any]
) -> tuple[list[str], dict[str, dict[str, Any]] | None]:
    errors: list[str] = []
    try:
        drift, severity, entry_count = _count_registry(registry)
        registry_by_id = _registry_index(registry)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, None

    if summary.get("known_differences_registry_total_entries") != entry_count:
        errors.append("registry total does not match known-differences.toml")
    for field, actual in (
        ("known_difference_registry_by_drift_type", dict(drift)),
        ("known_difference_registry_by_severity", dict(severity)),
    ):
        if summary.get(field) != actual:
            errors.append(f"{field} does not match the registry")
    return errors, registry_by_id


def _validate_observed_id_shape(
    summary: dict[str, Any],
) -> tuple[list[str], list[str] | None, list[str] | None]:
    errors: list[str] = []
    observed_ids = summary.get("known_difference_ids")
    if not isinstance(observed_ids, list) or any(
        not isinstance(entry_id, str) or not entry_id.strip()
        for entry_id in observed_ids
    ):
        errors.append("known_difference_ids must be a list of non-empty strings")
        return errors, None, None
    if len(set(observed_ids)) != len(observed_ids):
        errors.append("known_difference_ids must contain unique registry ids")
    observation_ids = summary.get("known_difference_observation_ids")
    if not isinstance(observation_ids, list) or any(
        not isinstance(entry_id, str) or not entry_id.strip()
        for entry_id in observation_ids
    ):
        errors.append(
            "known_difference_observation_ids must be a list of "
            "non-empty strings"
        )
        return errors, observed_ids, None
    if summary.get("known_difference_count") != len(observation_ids):
        errors.append(
            "known_difference_count must match "
            "known_difference_observation_ids length"
        )
    if set(observed_ids) != set(observation_ids):
        errors.append(
            "known_difference_ids must cover all observed registry ids"
        )
    return errors, observed_ids, observation_ids


def _validate_observed_id_references(
    observation_ids: list[str], registry_by_id: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    missing_ids = sorted(set(observation_ids) - registry_by_id.keys())
    if missing_ids:
        errors.append(
            "known_difference_ids contains unknown registry ids: "
            + ", ".join(missing_ids)
        )
    non_acceptable = sorted(
        {
            entry_id
            for entry_id in observation_ids
            if entry_id in registry_by_id
            and registry_by_id[entry_id].get("acceptable") is not True
        }
    )
    if non_acceptable:
        errors.append(
            "known_difference_ids contains non-acceptable registry ids: "
            + ", ".join(non_acceptable)
        )
    return errors, missing_ids


def _validate_observed_id_counts(
    summary: dict[str, Any],
    observation_ids: list[str],
    registry_by_id: dict[str, dict[str, Any]],
    missing_ids: list[str],
) -> list[str]:
    if missing_ids:
        return []
    errors: list[str] = []
    observed_drift = Counter(
        registry_by_id[entry_id]["drift_type"] for entry_id in observation_ids
    )
    observed_severity = Counter(
        registry_by_id[entry_id]["severity"] for entry_id in observation_ids
    )
    if summary.get("known_difference_by_drift_type") != dict(observed_drift):
        errors.append("known_difference_by_drift_type does not match observed ids")
    if summary.get("known_difference_by_severity") != dict(observed_severity):
        errors.append("known_difference_by_severity does not match observed ids")
    return errors


def _validate_observed_ids(
    summary: dict[str, Any], registry_by_id: dict[str, dict[str, Any]]
) -> list[str]:
    errors, observed_ids, observation_ids = _validate_observed_id_shape(summary)
    if observed_ids is None or observation_ids is None:
        return errors
    reference_errors, missing_ids = _validate_observed_id_references(
        observation_ids, registry_by_id
    )
    errors.extend(reference_errors)
    errors.extend(
        _validate_observed_id_counts(
            summary, observation_ids, registry_by_id, missing_ids
        )
    )
    return errors


def validate(summary: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    """Validate counts, registry bindings, and observed difference IDs."""
    errors = _validate_summary_status(summary)
    registry_errors, registry_by_id = _validate_registry_metadata(summary, registry)
    errors.extend(registry_errors)
    if registry_by_id is not None:
        errors.extend(_validate_observed_ids(summary, registry_by_id))
    return errors


def _resolve_repo_relative(value: Any, *, purpose: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"{purpose} must be a relative repository path")
    raw = Path(value)
    if ".." in raw.parts:
        raise ValueError(f"{purpose} must not contain '..'")
    resolved = (REPO_ROOT / raw).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{purpose} escapes the repository") from exc
    return resolved


def _sha256_file(path: Path) -> str:
    validated = validate_read_path(path, purpose="streaming evidence input")
    digest = hashlib.sha256()
    with validated.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_tree(path: Path) -> str:
    validated_root = validate_read_path(
        path, purpose="streaming evidence tree", must_exist=True
    )
    if not validated_root.is_dir():
        raise ValueError("corpus_root must resolve to a directory")
    digest = hashlib.sha256()
    files = sorted(
        validated_root.rglob("*"),
        key=lambda item: item.relative_to(validated_root).as_posix(),
    )
    for file_path in files:
        if file_path.is_symlink():
            raise ValueError("corpus_root must not contain symlinks")
        if not file_path.is_file():
            continue
        validated_file = validate_read_path(
            file_path, purpose="streaming evidence tree file", must_exist=True
        )
        try:
            relative_path = validated_file.relative_to(validated_root)
        except ValueError as exc:
            raise ValueError("corpus file escapes corpus_root") from exc
        relative = relative_path.as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with validated_file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_output(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _validate_generator_identity(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if summary.get("verified_by") != VERIFIED_BY:
        errors.append("verified_by does not identify the evidence generator")
    if summary.get("verification_command") != VERIFICATION_COMMAND:
        errors.append("verification_command is not the pinned parity command")
    if summary.get("source_tree_clean") is not True:
        errors.append("source_tree_clean must be true")

    for field in ("candidate_sha", "source_git_commit"):
        value = summary.get(field)
        if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
            errors.append(f"{field} must be a full lowercase git commit SHA")
    if summary.get("candidate_sha") != summary.get("source_git_commit"):
        errors.append("candidate_sha and source_git_commit must match")

    return errors


def _validate_timestamp(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    verified_at = summary.get("verified_at")
    parsed_time: datetime | None = None
    if isinstance(verified_at, str):
        try:
            parsed_time = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        except ValueError:
            parsed_time = None
    if parsed_time is None or parsed_time.tzinfo is None:
        errors.append("verified_at must be an RFC 3339 timestamp with timezone")
    else:
        now = datetime.now(timezone.utc)
        if parsed_time > now + MAX_CLOCK_SKEW:
            errors.append("verified_at is in the future")
        elif now - parsed_time > MAX_EVIDENCE_AGE:
            errors.append("streaming evidence is older than 24 hours")

    return errors


def _validate_input_digests(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if summary.get("corpus_root") != EXPECTED_CORPUS_ROOT:
        errors.append(f"corpus_root must be {EXPECTED_CORPUS_ROOT}")
    if summary.get("known_differences_registry") != EXPECTED_REGISTRY_PATH:
        errors.append(
            "known_differences_registry must be " + EXPECTED_REGISTRY_PATH
        )
    try:
        corpus_path = _resolve_repo_relative(
            summary.get("corpus_root"), purpose="corpus_root"
        )
        registry_path = _resolve_repo_relative(
            summary.get("known_differences_registry"),
            purpose="known_differences_registry",
        )
        if summary.get("corpus_sha256") != _sha256_tree(corpus_path):
            errors.append("corpus_sha256 does not match the checked-out corpus")
        if summary.get("registry_sha256") != _sha256_file(registry_path):
            errors.append("registry_sha256 does not match known-differences.toml")
    except (OSError, ValueError) as exc:
        errors.append(f"unable to verify evidence input digests: {exc}")

    for field in ("corpus_sha256", "registry_sha256"):
        value = summary.get(field)
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            errors.append(f"{field} must use sha256:<64 lowercase hex digits>")

    return errors


def _validate_rustc_version(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    rustc_version = summary.get("rustc_version")
    if not isinstance(rustc_version, str) or not rustc_version.strip():
        errors.append("rustc_version must be a non-empty string")
    else:
        rustc = subprocess.run(
            ["rustc", "--version"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if rustc.returncode != 0:
            errors.append("unable to execute rustc --version")
        elif rustc.stdout.strip() != rustc_version:
            errors.append("rustc_version does not match the current rustc")

    return errors


def _validate_git_head(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    head = _git_output("rev-parse", "HEAD")
    if head is None:
        errors.append("unable to resolve the current git HEAD")
    elif summary.get("candidate_sha") != head:
        errors.append("candidate_sha does not match the current git HEAD")
    status = _git_output("status", "--porcelain", "--untracked-files=all")
    if status is None:
        errors.append("unable to inspect git worktree state")
    elif status:
        errors.append("git worktree is not clean for candidate-bound evidence")
    return errors


def _validate_provenance(summary: dict[str, Any], *, require_git_head: bool) -> list[str]:
    """Bind generated evidence to the current repository and tool inputs."""
    errors = _validate_generator_identity(summary)
    errors.extend(_validate_timestamp(summary))
    errors.extend(_validate_input_digests(summary))
    errors.extend(_validate_rustc_version(summary))
    if require_git_head:
        errors.extend(_validate_git_head(summary))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", help="path to streaming evidence summary.json")
    parser.add_argument(
        "--git-head",
        action="store_true",
        help="require the evidence candidate SHA to match a clean git HEAD",
    )
    args = parser.parse_args(argv)
    try:
        summary = _load_json(args.summary)
        registry = _load_registry(summary)
        errors = validate(summary, registry)
        errors.extend(_validate_provenance(summary, require_git_head=args.git_head))
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
