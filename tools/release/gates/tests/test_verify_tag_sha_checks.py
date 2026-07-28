"""Tests for the tag SHA required-check release gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.release.gates.verify_tag_sha_checks import (
    RequiredCheck,
    _load_json,
    _status_errors,
    main,
    required_checks,
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


def _required_rules_with_apps(*checks: tuple[str, int | None]) -> list[dict[str, object]]:
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": context, "integration_id": integration_id}
                    for context, integration_id in checks
                ]
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
    app_id: int | None = None,
) -> dict[str, object]:
    run: dict[str, object] = {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
    }
    if app_id is not None:
        run["app"] = {"id": app_id}
    return run


def _commit_status(
    context: str,
    *,
    status_id: int,
    state: str,
    created_at: str = "2026-07-28T00:00:00Z",
) -> dict[str, object]:
    return {
        "id": status_id,
        "context": context,
        "state": state,
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
    """Only active required-status-check rules contribute required checks."""
    rules = [
        {"type": "deletion"},
        *_required_rules("CI / test", "CI / test"),
    ]

    assert required_checks(rules) == [
        RequiredCheck(context="CI / test", integration_id=None)
    ]


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


@pytest.mark.parametrize("conclusion", ["success", "skipped", "neutral"])
def test_completed_check_with_a_successful_conclusion_passes(conclusion: str) -> None:
    """GitHub counts success, skipped, and neutral as satisfying required checks."""
    assert validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion=conclusion)],
        branch="main",
    ) == []


@pytest.mark.parametrize(
    "conclusion",
    [
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "stale",
        "startup_failure",
        None,
    ],
)
def test_completed_check_with_an_unsuccessful_conclusion_blocks(
    conclusion: str | None,
) -> None:
    """Every non-successful conclusion must keep blocking the tag release."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion=conclusion)],
        branch="main",
    )

    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "not successful" in errors[0]


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_uncompleted_required_check_blocks(status: str) -> None:
    """A required check that has not completed must keep blocking the tag."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, status=status, conclusion=None)],
        branch="main",
    )

    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert status in errors[0]


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


def test_integration_id_requires_the_check_run_to_come_from_that_app() -> None:
    """A context pinned to an app is not satisfied by another app's check run."""
    rules = _required_rules_with_apps(("CI / test", 1234))

    assert validate_required_checks(
        rules, [_check_run("CI / test", run_id=1, app_id=1234)], branch="main"
    ) == []

    errors = validate_required_checks(
        rules, [_check_run("CI / test", run_id=1, app_id=5678)], branch="main"
    )
    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "app 1234" in errors[0]
    assert "missing" in errors[0]


def test_required_check_without_integration_id_accepts_any_source() -> None:
    """A context without integration_id accepts check runs from any app."""
    assert validate_required_checks(
        _required_rules_with_apps(("CI / test", None)),
        [_check_run("CI / test", run_id=1, app_id=5678)],
        branch="main",
    ) == []


def test_integration_id_scopes_rerun_selection_to_that_app() -> None:
    """A newer success from another app must not hide the pinned app's failure."""
    rules = _required_rules_with_apps(("CI / test", 1234))
    pinned_failure = _check_run(
        "CI / test",
        run_id=1,
        app_id=1234,
        conclusion="failure",
        created_at="2026-07-28T01:00:00Z",
    )
    foreign_success = _check_run(
        "CI / test",
        run_id=2,
        app_id=5678,
        created_at="2026-07-28T02:00:00Z",
    )

    errors = validate_required_checks(rules, [pinned_failure, foreign_success], branch="main")

    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "failure" in errors[0]


@pytest.mark.parametrize("integration_id", ["1234", 1.5, True])
def test_invalid_integration_id_fails_closed(integration_id: object) -> None:
    """A non-integer integration_id must be a parse error, never a pass."""
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": "CI / test", "integration_id": integration_id}
                ]
            },
        }
    ]

    errors = validate_required_checks(rules, [], branch="main")

    assert len(errors) == 1
    assert "integration_id" in errors[0]
    assert "CI / test" in errors[0]


def test_commit_status_satisfies_a_context_without_check_runs() -> None:
    """Contexts reported through the Commit Status API are not missing."""
    assert validate_required_checks(
        _required_rules("external / status"),
        [],
        [{"statuses": [_commit_status("external / status", status_id=1, state="success")]}],
        branch="main",
    ) == []


@pytest.mark.parametrize("state", ["error", "failure", "pending"])
def test_unsuccessful_commit_status_blocks(state: str) -> None:
    """A non-success commit status for a missing check run stays blocking."""
    errors = validate_required_checks(
        _required_rules("external / status"),
        [],
        [{"statuses": [_commit_status("external / status", status_id=1, state=state)]}],
        branch="main",
    )

    assert len(errors) == 1
    assert "external / status" in errors[0]
    assert state in errors[0]


def test_latest_commit_status_controls_the_gate() -> None:
    """A newer failing commit status overrides an older success."""
    older_success = _commit_status(
        "external / status",
        status_id=1,
        state="success",
        created_at="2026-07-28T00:00:00Z",
    )
    newer_failure = _commit_status(
        "external / status",
        status_id=2,
        state="failure",
        created_at="2026-07-28T01:00:00Z",
    )

    errors = validate_required_checks(
        _required_rules("external / status"),
        [],
        [{"statuses": [older_success, newer_failure]}],
        branch="main",
    )

    assert len(errors) == 1
    assert "failure" in errors[0]


def test_commit_status_cannot_satisfy_an_integration_pinned_context() -> None:
    """A commit status cannot prove the GitHub App that produced it."""
    errors = validate_required_checks(
        _required_rules_with_apps(("CI / test", 1234)),
        [],
        [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}],
        branch="main",
    )

    assert len(errors) == 1
    assert "app 1234" in errors[0]
    assert "integration-pinned" in errors[0]


def test_context_absent_from_check_runs_and_statuses_is_missing() -> None:
    """A context in neither API response remains a missing-check error."""
    errors = validate_required_checks(
        _required_rules("CI / test"), [], [{"statuses": []}], branch="main"
    )

    assert errors == ["Required check 'CI / test' is missing on the tag SHA."]


def test_main_accepts_a_statuses_file_for_commit_status_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI evaluates commit-status-only contexts from --statuses-file."""
    repository = tmp_path / "repository"
    input_dir = repository / ".tag-sha-checks"
    input_dir.mkdir(parents=True)
    (input_dir / "rules.json").write_text(
        '[{"type": "required_status_checks", "parameters": '
        '{"required_status_checks": [{"context": "external / status"}]}}]',
        encoding="utf-8",
    )
    (input_dir / "check-runs.json").write_text('[{"check_runs": []}]', encoding="utf-8")
    (input_dir / "statuses.json").write_text(
        '[{"statuses": [{"id": 1, "context": "external / status", '
        '"state": "success", "created_at": "2026-07-28T00:00:00Z"}]}]',
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
            "--statuses-file",
            ".tag-sha-checks/statuses.json",
            "--tag-sha",
            "abc123",
            "--branch",
            "main",
        ],
    )

    assert main() == 0
    assert "passed all required checks" in capsys.readouterr().out


def test_same_name_check_and_status_must_both_pass() -> None:
    """When a check run and commit status share a required context, both must pass."""
    rules = _required_rules("CI / test")
    check_run_success = _check_run("CI / test", run_id=1, conclusion="success")
    status_success = _commit_status("CI / test", status_id=1, state="success")

    assert validate_required_checks(
        rules, [check_run_success], [{"statuses": [status_success]}], branch="main"
    ) == []


def test_same_name_check_success_status_failure_blocks() -> None:
    """A failing commit status must block even when the check run succeeds."""
    rules = _required_rules("CI / test")
    check_run_success = _check_run("CI / test", run_id=1, conclusion="success")
    status_failure = _commit_status("CI / test", status_id=1, state="failure")

    errors = validate_required_checks(
        rules, [check_run_success], [{"statuses": [status_failure]}], branch="main"
    )

    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "failure" in errors[0]


def test_same_name_check_failure_status_success_blocks() -> None:
    """A failing check run must block even when the commit status succeeds."""
    rules = _required_rules("CI / test")
    check_run_failure = _check_run("CI / test", run_id=1, conclusion="failure")
    status_success = _commit_status("CI / test", status_id=1, state="success")

    errors = validate_required_checks(
        rules, [check_run_failure], [{"statuses": [status_success]}], branch="main"
    )

    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "failure" in errors[0]


def test_same_name_both_fail_produces_two_errors() -> None:
    """When both check run and commit status fail, both errors are reported."""
    rules = _required_rules("CI / test")
    check_run_failure = _check_run("CI / test", run_id=1, conclusion="failure")
    status_failure = _commit_status("CI / test", status_id=1, state="failure")

    errors = validate_required_checks(
        rules, [check_run_failure], [{"statuses": [status_failure]}], branch="main"
    )

    assert len(errors) == 2
    assert any("failure" in e and "conclusion" in e for e in errors)
    assert any("failure" in e and "commit status" in e for e in errors)


def test_same_name_pinned_check_with_status_requires_check_run() -> None:
    """An integration-pinned context ignores commit status even alongside a check run."""
    rules = _required_rules_with_apps(("CI / test", 1234))
    check_run_success = _check_run("CI / test", run_id=1, conclusion="success", app_id=1234)
    status_failure = _commit_status("CI / test", status_id=1, state="failure")

    errors = validate_required_checks(
        rules, [check_run_success], [{"statuses": [status_failure]}], branch="main"
    )

    assert len(errors) == 1
    assert "integration-pinned" in errors[0]


def test_same_name_pinned_check_missing_with_status_blocks() -> None:
    """An integration-pinned context with no matching check run blocks regardless of status."""
    rules = _required_rules_with_apps(("CI / test", 1234))
    status_success = _commit_status("CI / test", status_id=1, state="success")

    errors = validate_required_checks(
        rules, [], [{"statuses": [status_success]}], branch="main"
    )

    assert len(errors) == 1
    assert "integration-pinned" in errors[0]


def test_status_errors_helper_rejects_pinned_context() -> None:
    """_status_errors must reject commit statuses for integration-pinned contexts."""
    required = RequiredCheck(context="CI / test", integration_id=1234)
    statuses = [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}]

    errors = _status_errors(required, statuses)
    assert len(errors) == 1
    assert "integration-pinned" in errors[0]


def test_status_errors_helper_accepts_successful_status() -> None:
    """_status_errors must accept a successful commit status for unpinned contexts."""
    required = RequiredCheck(context="CI / test", integration_id=None)
    statuses = [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}]

    assert _status_errors(required, statuses) == []


def test_status_errors_helper_rejects_failed_status() -> None:
    """_status_errors must reject a failing commit status for unpinned contexts."""
    required = RequiredCheck(context="CI / test", integration_id=None)
    statuses = [{"statuses": [_commit_status("CI / test", status_id=1, state="failure")]}]

    errors = _status_errors(required, statuses)
    assert len(errors) == 1
    assert "failure" in errors[0]
