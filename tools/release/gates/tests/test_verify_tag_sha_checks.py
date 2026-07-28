"""Tests for the tag SHA required-check release gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.release.gates.verify_tag_sha_checks import (
    _load_json,
    main,
    required_check_contexts,
    validate_required_checks,
)


def _required_rules(*contexts: str) -> list[dict[str, object]]:
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": context} for context in contexts]
            },
        }
    ]


def _check_run(
    name: str,
    *,
    run_id: int,
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = "2026-07-28T00:00:00Z",
) -> dict[str, object]:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
    }


def test_load_json_accepts_a_file_inside_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release gate can read its generated response from the checkout."""
    repository = tmp_path / "repository"
    response = repository / ".tag-sha-checks" / "rules.json"
    response.parent.mkdir(parents=True)
    response.write_text('{"rules": []}', encoding="utf-8")
    monkeypatch.chdir(repository)

    assert _load_json(Path(".tag-sha-checks/rules.json")) == {"rules": []}


def test_load_json_rejects_absolute_and_parent_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI paths cannot select files outside the checkout."""
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(repository)

    with pytest.raises(ValueError, match="relative"):
        _load_json(outside)
    with pytest.raises(ValueError, match="relative"):
        _load_json(Path("../outside.json"))


def test_load_json_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canonicalization must not allow a checkout symlink to escape."""
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (repository / "response.json").symlink_to(outside)
    monkeypatch.chdir(repository)

    with pytest.raises(ValueError, match="within the repository"):
        _load_json(Path("response.json"))


def test_main_accepts_generated_responses_inside_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI preserves the successful release-gate path for safe inputs."""
    repository = tmp_path / "repository"
    input_dir = repository / ".tag-sha-checks"
    input_dir.mkdir(parents=True)
    (input_dir / "rules.json").write_text(
        '[{"type": "required_status_checks", "parameters": '
        '{"required_status_checks": [{"context": "CI / test"}]}}]',
        encoding="utf-8",
    )
    (input_dir / "check-runs.json").write_text(
        '[{"check_runs": [{"id": 1, "name": "CI / test", '
        '"status": "completed", "conclusion": "success"}]}]',
        encoding="utf-8",
    )
    monkeypatch.chdir(repository)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_tag_sha_checks.py",
            "--rules-file",
            ".tag-sha-checks/rules.json",
            "--check-runs-file",
            ".tag-sha-checks/check-runs.json",
            "--tag-sha",
            "abc123",
            "--branch",
            "main",
        ],
    )

    assert main() == 0
    assert "passed all required checks" in capsys.readouterr().out


def test_main_rejects_an_absolute_input_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI fails closed when an input path attempts to escape the checkout."""
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_tag_sha_checks.py",
            "--rules-file",
            str(tmp_path / "outside.json"),
            "--check-runs-file",
            "unused.json",
            "--tag-sha",
            "abc123",
            "--branch",
            "main",
        ],
    )

    assert main() == 1
    assert "input paths must be relative" in capsys.readouterr().err


def test_extracts_required_contexts_from_branch_effective_rules() -> None:
    """Only active required-status-check rules contribute contexts."""
    rules = [
        {"type": "deletion"},
        *_required_rules("CI / test", "CI / test"),
    ]

    assert required_check_contexts(rules) == ["CI / test"]


def test_empty_effective_rules_fail_closed() -> None:
    """An empty rules response must never be treated as a release approval."""
    errors = validate_required_checks([], [], branch="main")

    assert errors == ["main has no active required status checks; refusing tag release"]


def test_paginated_check_runs_include_runs_after_first_page() -> None:
    """Pagination must not hide a required check after the first 100 runs."""
    first_page = [_check_run(f"unrelated-{index}", run_id=index) for index in range(100)]
    second_page = [_check_run("CI / test", run_id=101)]

    assert validate_required_checks(
        _required_rules("CI / test"),
        [{"check_runs": first_page}, {"check_runs": second_page}],
        branch="main",
    ) == []


def test_latest_rerun_is_the_only_result_that_controls_the_gate() -> None:
    """A successful rerun replaces an older failure, while a newer failure blocks."""
    older_failure = _check_run(
        "CI / test",
        run_id=1,
        conclusion="failure",
        created_at="2026-07-28T00:00:00Z",
    )
    newer_success = _check_run(
        "CI / test",
        run_id=2,
        created_at="2026-07-28T01:00:00Z",
    )
    assert validate_required_checks(
        _required_rules("CI / test"), [older_failure, newer_success], branch="main"
    ) == []

    newest_failure = _check_run(
        "CI / test",
        run_id=3,
        conclusion="failure",
        created_at="2026-07-28T02:00:00Z",
    )
    errors = validate_required_checks(
        _required_rules("CI / test"), [older_failure, newer_success, newest_failure], branch="main"
    )
    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "failure" in errors[0]


def test_missing_or_incomplete_required_check_blocks_release() -> None:
    """Missing and in-progress required checks must remain blocking."""
    errors = validate_required_checks(
        _required_rules("CI / test", "CI / docs"),
        [_check_run("CI / test", run_id=1, status="in_progress", conclusion=None)],
        branch="main",
    )

    assert len(errors) == 2
    assert any("CI / test" in error and "in_progress" in error for error in errors)
    assert any("CI / docs" in error and "missing" in error for error in errors)
