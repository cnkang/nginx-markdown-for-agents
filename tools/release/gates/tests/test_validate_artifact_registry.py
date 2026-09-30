"""Type-fidelity tests for the artifact registry gate.

``tools/release/gates/validate_artifact_registry.py`` reads its records
from JSON, where a boolean is a JSON scalar.  Python's ``bool`` subclasses
``int``, so a membership test against ``(str, int, float)`` silently
accepts ``true``/``false`` as an artifact identifier.  These tests pin the
scalar contract: booleans are malformed, while numeric and string ids
still pass and still participate in duplicate detection.
"""

from __future__ import annotations

from tools.release.gates import validate_artifact_registry as artifact_gate

CANDIDATE_SHA = "a" * 40


def _artifact_row(artifact_id: object) -> dict:
    """One otherwise-valid candidate index row with the given identifier."""
    return {
        "artifact_type": "source",
        "release_matrix_row_id": "source-1",
        "artifact_id": artifact_id,
        "candidate_sha": CANDIDATE_SHA,
        "artifact_sha256": "sha256:" + "d" * 64,
        "feature_manifest_digest": "sha256:" + "b" * 64,
        "abi_version": 2,
        "verification_status": "pass",
    }


def _index(rows: list[dict]) -> dict:
    """A candidate artifact index wrapping the supplied rows."""
    return {
        "schema_version": artifact_gate.INDEX_SCHEMA_VERSION,
        "candidate_sha": CANDIDATE_SHA,
        "artifacts": rows,
    }


def _validate(monkeypatch, rows: list[dict]) -> list[str]:
    """Validate one index with the official bindings pinned in-process."""
    monkeypatch.setattr(
        artifact_gate, "frozen_feature_digest", lambda: "sha256:" + "b" * 64
    )
    monkeypatch.setattr(artifact_gate, "frozen_abi_version", lambda: 2)
    return artifact_gate.validate_index(_index(rows), CANDIDATE_SHA)


def test_boolean_artifact_id_is_malformed(monkeypatch) -> None:
    """A JSON true/false cannot pass as an artifact identifier.

    ``bool`` subclasses ``int``, so the scalar test must reject it before
    the membership check that the numeric form relies on.
    """
    for boolean in (True, False):
        reasons = _validate(monkeypatch, [_artifact_row(boolean)])
        assert any(
            "artifact_id must be a scalar, got bool" in reason
            for reason in reasons
        ), (boolean, reasons)


def test_numeric_and_string_artifact_ids_are_accepted(monkeypatch) -> None:
    """Control: the scalar fix must not reject int, float, or str ids."""
    for artifact_id in (1, 1.5, "source.tar.gz"):
        reasons = _validate(monkeypatch, [_artifact_row(artifact_id)])
        assert not any(
            "artifact_id must be a scalar" in reason for reason in reasons
        ), (artifact_id, reasons)


def test_duplicate_detection_still_applies_to_string_ids(monkeypatch) -> None:
    """Control: a repeated string id stays a blocking duplicate."""
    reasons = _validate(
        monkeypatch,
        [_artifact_row("source.tar.gz"), _artifact_row("source.tar.gz")],
    )
    assert any(
        "blocking-pending: duplicate artifact id" in reason
        for reason in reasons
    ), reasons


def test_duplicate_detection_still_applies_to_numeric_ids(monkeypatch) -> None:
    """Control: a repeated numeric id stays a blocking duplicate."""
    reasons = _validate(monkeypatch, [_artifact_row(7), _artifact_row(7)])
    assert any(
        "blocking-pending: duplicate artifact id" in reason
        for reason in reasons
    ), reasons


def test_unhashable_artifact_ids_stay_malformed(monkeypatch) -> None:
    """Lists and objects cannot participate in set membership."""
    for artifact_id in (["a"], {"a": 1}):
        reasons = _validate(monkeypatch, [_artifact_row(artifact_id)])
        assert any(
            "artifact_id must be a scalar" in reason for reason in reasons
        ), (artifact_id, reasons)
