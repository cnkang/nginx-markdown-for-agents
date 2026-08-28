"""Regression tests for generated streaming parity evidence."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from tools.release.gates import validate_streaming_evidence as validator


REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY_PATH = REPO_ROOT / "tests" / "streaming" / "known-differences.toml"


def _current_evidence() -> tuple[dict, dict]:
    registry = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_drift, registry_severity, registry_total = validator._count_registry(
        registry
    )
    observed = next(
        entry
        for entry in registry["difference"]
        if entry.get("acceptable") is True
    )
    summary = {
        "schema_version": validator.SCHEMA_VERSION,
        "verified_by": validator.VERIFIED_BY,
        "verified_at": "2099-01-01T00:00:00Z",
        "total_comparisons": 1,
        "identical_count": 0,
        "known_difference_count": 1,
        "known_difference_by_drift_type": {observed["drift_type"]: 1},
        "known_difference_by_severity": {observed["severity"]: 1},
        "known_difference_registry_by_drift_type": dict(registry_drift),
        "known_difference_registry_by_severity": dict(registry_severity),
        "known_difference_ids": [observed["id"]],
        "known_difference_observation_ids": [observed["id"]],
        "unknown_difference_count": 0,
        "error_parity_mismatch_count": 0,
        "pass": True,
        "known_differences_registry": validator.EXPECTED_REGISTRY_PATH,
        "known_differences_registry_total_entries": registry_total,
        "corpus_root": validator.EXPECTED_CORPUS_ROOT,
        "verification_command": validator.VERIFICATION_COMMAND,
        "verification_result": "PASS",
        "candidate_sha": "0" * 40,
        "source_git_commit": "0" * 40,
        "source_tree_clean": True,
        "corpus_sha256": "sha256:" + "0" * 64,
        "registry_sha256": "sha256:" + "0" * 64,
        "rustc_version": "rustc test",
    }
    return summary, registry


def test_generated_streaming_evidence_satisfies_partition() -> None:
    """A generated-shaped evidence document remains a valid partition."""
    summary, registry = _current_evidence()

    assert validator.validate(summary, registry) == []


def test_streaming_evidence_rejects_double_counted_comparisons() -> None:
    """Known and identical counts cannot exceed the total comparisons."""
    summary, registry = _current_evidence()
    summary = copy.deepcopy(summary)
    summary["known_difference_count"] += 1

    errors = validator.validate(summary, registry)

    assert any("identical_count + known_difference_count" in error
               for error in errors)


def test_streaming_evidence_rejects_boolean_or_negative_counts() -> None:
    """JSON booleans and negative values are not valid comparison counts."""
    for field, value in (
        ("total_comparisons", True),
        ("identical_count", -1),
    ):
        summary, registry = _current_evidence()
        summary[field] = value

        errors = validator.validate(summary, registry)

        assert "comparison counts must be non-negative integers" in errors


def test_streaming_evidence_rejects_unknown_observed_registry_id() -> None:
    """A self-consistent count cannot invent a known-difference exemption."""
    summary, registry = _current_evidence()
    summary["known_difference_ids"] = ["DIFF-NOT-IN-REGISTRY"]
    summary["known_difference_observation_ids"] = ["DIFF-NOT-IN-REGISTRY"]

    errors = validator.validate(summary, registry)

    assert any("unknown registry ids" in error for error in errors)


def test_streaming_evidence_accepts_repeated_observations_for_one_registry_id() -> None:
    """One registry difference may classify multiple real comparisons."""
    summary, registry = _current_evidence()
    observed_id = summary["known_difference_ids"][0]
    summary["known_difference_observation_ids"] = [observed_id, observed_id]
    summary["known_difference_count"] = 2
    summary["total_comparisons"] = 2
    summary["identical_count"] = 0
    summary["known_difference_by_drift_type"] = {"whitespace": 2}
    summary["known_difference_by_severity"] = {"low": 2}

    assert validator.validate(summary, registry) == []


def test_streaming_evidence_rejects_duplicate_unique_registry_ids() -> None:
    """The unique registry-id projection cannot contain duplicates."""
    summary, registry = _current_evidence()
    observed_id = summary["known_difference_ids"][0]
    summary["known_difference_ids"] = [observed_id, observed_id]
    summary["known_difference_observation_ids"] = [observed_id, observed_id]
    summary["known_difference_count"] = 2
    summary["total_comparisons"] = 2
    summary["identical_count"] = 0

    errors = validator.validate(summary, registry)

    assert "known_difference_ids must contain unique registry ids" in errors


def test_stale_evidence_is_rejected_even_when_counts_are_self_consistent() -> None:
    """A forged old PASS summary is not current evidence."""
    summary, _ = _current_evidence()
    summary["verified_at"] = "2020-01-01T00:00:00Z"

    errors = validator._validate_provenance(summary, require_git_head=False)

    assert "streaming evidence is older than 24 hours" in errors
