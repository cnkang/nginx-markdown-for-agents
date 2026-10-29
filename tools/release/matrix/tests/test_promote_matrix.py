"""Tests for evidence-gated matrix promotion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.matrix.promote_matrix import (
    PromotionError,
    build_promoted_matrix,
    main,
)
from tools.release.matrix.tests.test_evidence_contract import _record, _ubuntu_record


def _row(**overrides) -> dict:
    row = {
        "nginx_version": "1.30.6",
        "os": "linux",
        "libc": "glibc",
        "arch": "amd64",
        "artifact_type": "dynamic-module",
        "support_tier": "best-effort",
        "support_stage": "best-effort",
        "verification_state": "pending",
        "release_blocking": False,
    }
    row.update(overrides)
    return row


def _matrix(*rows: dict) -> dict:
    return {"schema_version": 1, "entries": list(rows)}


def test_promotion_sets_the_release_promise() -> None:
    matrix = _matrix(_row())
    promoted, summary = build_promoted_matrix(
        matrix, [_record()], demote_superseded=False
    )
    row = promoted["entries"][0]
    assert row["support_tier"] == "supported"
    assert row["support_stage"] == "primary"
    assert row["verification_state"] == "verified"
    assert row["release_blocking"] is True
    assert row["date"] == "2026-10-29"
    assert row["source"] == "nginx.org"
    assert summary == ["promote 1.30.6 linux/glibc/amd64: 1 row(s)"]


def test_record_without_a_row_is_refused() -> None:
    with pytest.raises(PromotionError, match="covers no matrix row"):
        build_promoted_matrix(
            _matrix(_row(arch="arm64")), [_record()], demote_superseded=False
        )


def test_manual_row_is_never_overwritten() -> None:
    matrix = _matrix(
        _row(
            nginx_version="1.24.0",
            os="ubuntu-24.04",
            artifact_type="deb-package",
            managed_by="manual",
        )
    )
    record = _ubuntu_record()
    with pytest.raises(PromotionError, match="managed_by=manual"):
        build_promoted_matrix(matrix, [record], demote_superseded=False)


def test_container_rows_are_not_promoted_by_evidence() -> None:
    matrix = _matrix(_row(artifact_type="docker-image"))
    with pytest.raises(PromotionError, match="covers no matrix row"):
        build_promoted_matrix(matrix, [_record()], demote_superseded=False)


def test_superseded_patch_is_demoted_without_losing_its_row() -> None:
    matrix = _matrix(
        _row(nginx_version="1.30.5", support_tier="supported", release_blocking=True),
        _row(nginx_version="1.30.6"),
    )
    promoted, summary = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )
    old = promoted["entries"][0]
    assert old["nginx_version"] == "1.30.5"
    assert old["support_tier"] == "best-effort"
    assert old["support_stage"] == "best-effort"
    assert old["release_blocking"] is False
    assert old["verification_state"] == "verified"
    assert "demote superseded patch 1.30.5 to best-effort" in summary


def test_superseded_demotion_keeps_other_tracks() -> None:
    matrix = _matrix(
        _row(nginx_version="1.28.3", support_tier="supported", release_blocking=True),
        _row(nginx_version="1.30.6"),
    )
    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )
    assert promoted["entries"][0]["support_tier"] == "supported"


def test_newer_existing_patch_is_not_demoted() -> None:
    matrix = _matrix(
        _row(nginx_version="1.30.6", support_tier="supported", release_blocking=True),
        _row(nginx_version="1.30.5", support_tier="supported", release_blocking=True),
    )
    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=False
    )
    assert promoted["entries"][1]["release_blocking"] is True


def test_promotion_is_idempotent() -> None:
    matrix = _matrix(_row())
    first, _ = build_promoted_matrix(matrix, [_record()], demote_superseded=False)
    snapshot = json.dumps(first, sort_keys=True)
    second, _ = build_promoted_matrix(first, [_record()], demote_superseded=False)
    assert json.dumps(second, sort_keys=True) == snapshot


def test_invalid_record_is_refused() -> None:
    matrix = _matrix(_row())
    with pytest.raises(PromotionError, match="is not promotable"):
        build_promoted_matrix(
            matrix, [_record(module_sha256="short")], demote_superseded=False
        )


def _write_evidence(path: Path, **overrides) -> Path:
    record = _record(**overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_check_mode_reports_a_stale_matrix(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    evidence = _write_evidence(tmp_path / "record.json")
    rc = main(
        [
            "--matrix",
            str(matrix_path),
            "--evidence",
            str(evidence),
            "--check",
        ],
        repo_root=tmp_path,
    )
    assert rc == 2


def test_write_mode_then_check_mode_passes(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    evidence = _write_evidence(tmp_path / "record.json")
    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(evidence)],
            repo_root=tmp_path,
        )
        == 0
    )
    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(evidence), "--check"],
            repo_root=tmp_path,
        )
        == 0
    )
