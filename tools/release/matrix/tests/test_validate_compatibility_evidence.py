"""Tests for the compatibility evidence gate.

The gate decides two things: whether a record is usable, and whether a matrix
row that claims verification really has one. Both directions get a test.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.release.matrix.tests.test_evidence_contract import _record, _ubuntu_record
from tools.release.matrix.validate_compatibility_evidence import (
    main,
    validate,
)


def _row(**overrides) -> dict:
    row = {
        "nginx_version": "1.30.6",
        "os": "linux",
        "libc": "glibc",
        "arch": "amd64",
        "artifact_type": "dynamic-module",
        "support_tier": "supported",
        "verification_state": "verified",
        "release_blocking": True,
    }
    row.update(overrides)
    return row


def _matrix(*rows: dict) -> dict:
    return {"entries": list(rows)}


def _write(directory: Path, name: str, record: dict) -> tuple[Path, dict]:
    path = directory / name
    path.write_text(json.dumps(record), encoding="utf-8")
    return path, record


def test_verified_row_with_record_passes(tmp_path: Path) -> None:
    records = [_write(tmp_path, "a.json", _record())]
    assert validate(_matrix(_row()), records) == []


def test_verified_row_without_record_fails() -> None:
    problems = validate(_matrix(_row()), [])
    assert any("verified without a matching evidence record" in p for p in problems)


def test_unverified_row_needs_no_record() -> None:
    problems = validate(_matrix(_row(verification_state="pending")), [])
    assert problems == []


def test_blocking_row_needs_a_record_only_in_release_mode() -> None:
    matrix = _matrix(_row(verification_state="pending"))
    assert validate(matrix, []) == []
    problems = validate(matrix, [], require_blocking=True)
    assert any("release-blocking without" in p for p in problems)


def test_failed_record_is_rejected(tmp_path: Path) -> None:
    tests = [
        {"name": "build", "status": "FAIL"},
        {"name": "load", "status": "PASS"},
        {"name": "request_markdown", "status": "PASS"},
        {"name": "request_passthrough", "status": "PASS"},
        {"name": "lifecycle", "status": "PASS"},
    ]
    records = [
        _write(
            tmp_path,
            "a.json",
            _record(tests=tests, **{"pass": 4, "fail": 1}),
        )
    ]
    problems = validate(_matrix(_row()), records)
    assert any("cannot support a release promise" in p for p in problems)


def test_local_record_is_rejected_at_release_stage(tmp_path: Path) -> None:
    records = [
        _write(
            tmp_path,
            "a.json",
            _record(
                verified_by="local-container",
                workflow_run_url="",
            ),
        )
    ]
    assert validate(_matrix(_row()), records) == []
    problems = validate(_matrix(_row()), records, require_ci=True)
    assert any("requires a CI record" in p for p in problems)


def test_cli_reports_a_missing_record_directory(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    rc_main = main(
        [
            "--matrix",
            str(matrix_path),
            "--evidence-dir",
            str(tmp_path / "absent"),
        ]
    )
    assert rc_main == 1


def test_candidate_sha_binding_is_enforced(tmp_path: Path) -> None:
    """A record for an older commit cannot support the frozen candidate."""
    records = [_write(tmp_path, "a.json", _record())]
    assert validate(_matrix(_row()), records) == []

    problems = validate(
        _matrix(_row()), records, candidate_sha="e" * 40
    )
    assert any("not the candidate" in problem for problem in problems)

    problems = validate(
        _matrix(_row()), records, candidate_sha=records[0][1]["commit_sha"]
    )
    assert problems == []


def test_pinned_row_with_a_different_revision_is_verified_without_record(
    tmp_path: Path,
) -> None:
    """A row pinned to another revision must not be laundered through the gate."""
    records = [_write(tmp_path, "ubuntu.json", _ubuntu_record())]
    stale = {
        "nginx_version": "1.24.0",
        "os": "ubuntu-24.04",
        "libc": "glibc",
        "arch": "amd64",
        "artifact_type": "deb-package",
        "support_tier": "supported",
        "verification_state": "verified",
        "release_blocking": True,
        "source": "ubuntu-default-repo",
        "provenance": {
            "kind": "ubuntu-default-repo",
            "reference": "https://launchpad.net/ubuntu/+source/nginx/1.24.0-2ubuntu7.17",
        },
    }
    problems = validate(_matrix(stale), records)
    assert len(problems) == 1, problems
    assert "verified without a matching evidence record" in problems[0]

    pinned = {
        **stale,
        "provenance": {
            "kind": "ubuntu-default-repo",
            "reference": records[0][1]["source_reference"],
        },
    }
    assert validate(_matrix(pinned), records) == []
