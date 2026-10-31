"""Tests for the release-stage evidence staging tool.

The tool sits between the artifact download and the evidence gate, so every
unsafe layout must fail before anything is copied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tools.release.matrix import stage_compatibility_evidence as stager
from tools.release.matrix import validate_compatibility_evidence as gate
from tools.release.matrix.tests.test_evidence_contract import _record, _ubuntu_record

CI_SHA = "d" * 40
CI_RUN = "https://github.com/cnkang/nginx-markdown-for-agents/actions/runs/42"


def _artifact(source: Path, name: str, record_name: str, payload: dict) -> Path:
    directory = source / name
    directory.mkdir(parents=True)
    path = directory / record_name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(source: Path, dest: Path) -> int:
    return stager.main(["--source", str(source), "--dest", str(dest)])


def test_happy_path_stages_one_flat_copy_per_artifact(tmp_path: Path, capsys) -> None:
    source = tmp_path / "download"
    originals = [
        _artifact(source, f"compat-evidence-{index}", f"r{index}.json", {"i": index})
        for index in range(3)
    ]
    dest = tmp_path / "staged"

    assert _run(source, dest) == 0
    assert "staged 3 records" in capsys.readouterr().out
    assert sorted(path.name for path in dest.iterdir()) == ["r0.json", "r1.json", "r2.json"]
    for original in originals:
        assert (dest / original.name).read_bytes() == original.read_bytes()


def test_duplicate_record_name_fails_without_a_partial_destination(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "same.json", {"a": 1})
    _artifact(source, "compat-evidence-b", "same.json", {"b": 1})
    dest = tmp_path / "staged"

    assert _run(source, dest) == 1
    err = capsys.readouterr().err
    assert "compat-evidence-a" in err
    assert "compat-evidence-b" in err
    assert not dest.exists()


def test_symlinked_record_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (source / "compat-evidence-a").mkdir(parents=True)
    os.symlink(outside, source / "compat-evidence-a" / "r.json")

    assert _run(source, tmp_path / "staged") == 1


def test_symlinked_artifact_directory_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    source.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "r.json").write_text("{}", encoding="utf-8")
    os.symlink(elsewhere, source / "compat-evidence-a")

    assert _run(source, tmp_path / "staged") == 1


def test_symlinked_source_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _artifact(real, "compat-evidence-a", "r.json", {})
    link = tmp_path / "download"
    os.symlink(real, link)

    assert _run(link, tmp_path / "staged") == 1


def test_nested_directory_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    (source / "compat-evidence-a" / "nested").mkdir(parents=True)

    assert _run(source, tmp_path / "staged") == 1


def test_top_level_stray_file_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "r.json", {})
    (source / "stray.json").write_text("{}", encoding="utf-8")

    assert _run(source, tmp_path / "staged") == 1


def test_unexpected_artifact_name_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "pkg-deb-ubuntu-24.04-amd64", "r.json", {})

    assert _run(source, tmp_path / "staged") == 1


def test_non_json_record_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "r.txt", {})

    assert _run(source, tmp_path / "staged") == 1


def test_unsafe_record_name_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "-r.json", {})

    assert _run(source, tmp_path / "staged") == 1


def test_artifact_with_two_files_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "r.json", {})
    (source / "compat-evidence-a" / "s.json").write_text("{}", encoding="utf-8")

    assert _run(source, tmp_path / "staged") == 1


def test_empty_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    source.mkdir()

    assert _run(source, tmp_path / "staged") == 1


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    assert _run(tmp_path / "absent", tmp_path / "staged") == 1


def test_non_empty_destination_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "r.json", {})
    dest = tmp_path / "staged"
    dest.mkdir()
    (dest / "old.json").write_text("{}", encoding="utf-8")

    assert _run(source, dest) == 1
    assert sorted(path.name for path in dest.iterdir()) == ["old.json"]


def test_empty_existing_destination_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-a", "r.json", {})
    dest = tmp_path / "staged"
    dest.mkdir()

    assert _run(source, dest) == 0
    assert (dest / "r.json").is_file()


def _ci(record: dict) -> dict:
    record.update(
        verified_by="ci:release-packages/test",
        workflow_run_url=CI_RUN,
        commit_sha=CI_SHA,
    )
    return record


def _handoff_matrix(tmp_path: Path) -> Path:
    org = {
        "nginx_version": "1.30.6",
        "os": "linux",
        "libc": "glibc",
        "arch": "amd64",
        "artifact_type": "dynamic-module",
        "support_tier": "supported",
        "verification_state": "pending",
        "release_blocking": True,
        "source": "nginx.org",
    }
    ubuntu = {
        **org,
        "nginx_version": "1.24.0",
        "os": "ubuntu-24.04",
        "artifact_type": "deb-package",
        "source": "ubuntu-default-repo",
        "provenance": {
            "kind": "ubuntu-default-repo",
            "reference": _ubuntu_record()["source_reference"],
        },
    }
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps({"entries": [org, ubuntu]}), encoding="utf-8")
    return path


def _gate(matrix: Path, dest: Path) -> int:
    return gate.main(
        [
            "--matrix", str(matrix),
            "--evidence-dir", str(dest),
            "--require-ci",
            "--candidate-sha", CI_SHA,
            "--workflow-run-url", CI_RUN,
            "--require-blocking",
        ]
    )


def test_staged_ci_records_satisfy_the_release_gate(tmp_path: Path) -> None:
    """End to end: download layout -> stage -> gate."""
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-org", "org.json", _ci(_record()))
    _artifact(source, "compat-evidence-ubuntu", "ubuntu.json", _ci(_ubuntu_record()))
    matrix = _handoff_matrix(tmp_path)
    dest = tmp_path / "staged"

    assert _run(source, dest) == 0
    assert _gate(matrix, dest) == 0


def test_a_missing_producer_artifact_fails_the_release_gate(tmp_path: Path) -> None:
    source = tmp_path / "download"
    _artifact(source, "compat-evidence-org", "org.json", _ci(_record()))
    matrix = _handoff_matrix(tmp_path)
    dest = tmp_path / "staged"

    assert _run(source, dest) == 0
    assert _gate(matrix, dest) == 1
