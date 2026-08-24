"""Tests for the tag SHA required-check release gate."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from tools.release.gates.verify_tag_sha_checks import (
    MalformedPayloadError,
    RequiredCheck,
    _load_json,
    _status_errors,
    _status_state_errors,
    _write_required_checks,
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
    parent_path = Path("../outside.json")
    with pytest.raises(ValueError, match="relative"):
        _load_json(parent_path)


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

    response_path = Path("response.json")
    with pytest.raises(ValueError, match="within the repository"):
        _load_json(response_path)


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


def test_main_writes_effective_required_check_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A successful gate can emit the exact active ruleset checks for release evidence."""
    repository = tmp_path / "repository"
    input_dir = repository / ".tag-sha-checks"
    input_dir.mkdir(parents=True)
    (input_dir / "rules.json").write_text(
        '[{"type": "required_status_checks", "parameters": '
        '{"required_status_checks": [{"context": "CI / test", '
        '"integration_id": 123}]}}]',
        encoding="utf-8",
    )
    (input_dir / "check-runs.json").write_text(
        '[{"check_runs": [{"id": 1, "name": "CI / test", '
        '"app": {"id": 123}, "status": "completed", '
        '"conclusion": "success"}]}]',
        encoding="utf-8",
    )
    output = input_dir / "effective-required-checks.json"
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
            "a" * 40,
            "--branch",
            "main",
            "--required-checks-output",
            ".tag-sha-checks/effective-required-checks.json",
        ],
    )

    assert main() == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "branch": "main",
        "required_checks": [{"context": "CI / test", "integration_id": 123}],
        "schema_version": "release.required-checks.v1",
        "tag_sha": "a" * 40,
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "Effective required checks:" in capsys.readouterr().out


def test_required_checks_output_rejects_paths_outside_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated release evidence must stay inside the checkout."""
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)

    with pytest.raises(ValueError, match="relative"):
        _write_required_checks(
            Path("../outside.json"), [], branch="main", tag_sha="a" * 40
        )

    with pytest.raises(ValueError, match="relative"):
        _write_required_checks(
            tmp_path / "outside.json", [], branch="main", tag_sha="a" * 40
        )

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (repository / "evidence").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes root"):
        _write_required_checks(
            Path("evidence/required-checks.json"),
            [],
            branch="main",
            tag_sha="a" * 40,
        )


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


def test_commit_status_context_case_insensitive() -> None:
    """Commit status context matching is case-insensitive per GitHub API."""
    assert validate_required_checks(
        _required_rules("CI / test"),
        [],
        [{"statuses": [_commit_status("ci / test", status_id=1, state="success")]}],
        branch="main",
    ) == []


def test_commit_status_context_case_insensitive_failure() -> None:
    """Case-insensitive failure status still blocks."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [],
        [{"statuses": [_commit_status("ci / TEST", status_id=1, state="failure")]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "failure" in errors[0]


def test_check_run_and_status_same_context_different_case_both_required() -> None:
    """GitHub requires both check run and commit status to pass when they share a context name (case-insensitive).

    If check run succeeds but commit status (different case) fails, the gate must block.
    """
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_commit_status("ci / test", status_id=1, state="failure")]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "failure" in errors[0]


def test_check_run_and_status_same_context_different_case_both_pass() -> None:
    """Both check run and commit status (different case) must pass."""
    assert validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_commit_status("ci / test", status_id=1, state="success")]}],
        branch="main",
    ) == []


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


def test_same_name_pinned_check_with_status_requires_both_pass() -> None:
    """When a check run and commit status share a required context, both must pass.

    Even for an integration-pinned context, the same-name rule means a
    non-successful commit status blocks the tag; the commit Status API
    cannot prove source, but a failing status must never be silently
    ignored.
    """
    rules = _required_rules_with_apps(("CI / test", 1234))
    check_run_success = _check_run("CI / test", run_id=1, conclusion="success", app_id=1234)
    status_failure = _commit_status("CI / test", status_id=1, state="failure")
    errors = validate_required_checks(
        rules, [check_run_success], [{"statuses": [status_failure]}], branch="main"
    )
    assert len(errors) == 1
    assert "CI / test" in errors[0]
    assert "both must pass" in errors[0]


def test_same_name_pinned_check_success_status_success_passes() -> None:
    """Pinned context: matching check run plus successful commit status both pass."""
    rules = _required_rules_with_apps(("CI / test", 1234))
    check_run_success = _check_run("CI / test", run_id=1, conclusion="success", app_id=1234)
    status_success = _commit_status("CI / test", status_id=1, state="success")
    errors = validate_required_checks(
        rules, [check_run_success], [{"statuses": [status_success]}], branch="main"
    )
    assert errors == []


def test_pinned_check_run_with_no_status_passes() -> None:
    """Pinned context with correct-app check run and no commit status: satisfied."""
    rules = _required_rules_with_apps(("CI / test", 1234))
    check_run_success = _check_run("CI / test", run_id=1, conclusion="success", app_id=1234)
    assert validate_required_checks(
        rules, [check_run_success], [{"statuses": []}], branch="main"
    ) == []
    assert validate_required_checks(
        rules, [check_run_success], branch="main"
    ) == []

def test_same_name_pinned_check_missing_with_status_blocks() -> None:
    """An integration-pinned context with no matching check run blocks regardless of status."""
    rules = _required_rules_with_apps(("CI / test", 1234))
    status_success = _commit_status("CI / test", status_id=1, state="success")

    errors = validate_required_checks(
        rules, [], [{"statuses": [status_success]}], branch="main"
    )

    assert len(errors) == 1
    assert "integration-pinned" in errors[0]


def test_status_errors_helper_rejects_pinned_context_no_check_run() -> None:
    """_status_errors must reject commit statuses for integration-pinned contexts when no check run."""
    required = RequiredCheck(context="CI / test", integration_id=1234)
    statuses = [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}]

    # No matching check run -> error
    errors = _status_errors(required, statuses, has_matching_run=False)
    assert len(errors) == 1
    assert "integration-pinned" in errors[0]


def test_status_errors_helper_requires_status_success_with_pinned_check_run() -> None:
    """_status_errors must require the commit status to succeed when a matching pinned check run exists."""
    required = RequiredCheck(context="CI / test", integration_id=1234)
    # Successful status: ok
    good = [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}]
    assert _status_errors(required, good, has_matching_run=True) == []
    # Failing status: must block
    bad = [{"statuses": [_commit_status("CI / test", status_id=1, state="failure")]}]
    errors = _status_errors(required, bad, has_matching_run=True)
    assert len(errors) == 1
    assert "both must pass" in errors[0]


def test_status_errors_helper_accepts_pinned_run_without_status() -> None:
    """_status_errors accepts a pinned check run when there is no commit status at all."""
    required = RequiredCheck(context="CI / test", integration_id=1234)
    assert _status_errors(required, [{"statuses": []}], has_matching_run=True) == []


def test_status_errors_helper_accepts_successful_status() -> None:
    """_status_errors must accept a successful commit status for unpinned contexts."""
    required = RequiredCheck(context="CI / test", integration_id=None)
    statuses = [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}]

    assert _status_errors(required, statuses, has_matching_run=False) == []


def test_status_errors_helper_rejects_failed_status() -> None:
    """_status_errors must reject a failing commit status for unpinned contexts."""
    required = RequiredCheck(context="CI / test", integration_id=None)
    statuses = [{"statuses": [_commit_status("CI / test", status_id=1, state="failure")]}]

    errors = _status_errors(required, statuses, has_matching_run=False)
    assert len(errors) == 1
    assert "failure" in errors[0]


# ---------------------------------------------------------------------------
# Malformed-payload fail-closed tests (Rule 13 / tag SHA verifier hardening)
# ---------------------------------------------------------------------------


def _status_with(context_field: object, state_field: object, status_id: int = 1) -> dict:
    """Build a commit-status entry with arbitrary context/state field values."""
    entry: dict = {"id": status_id, "created_at": "2026-07-28T00:00:00Z"}
    if context_field is not _MISSING:
        entry["context"] = context_field
    if state_field is not _MISSING:
        entry["state"] = state_field
    return entry


class _Missing:
    """Sentinel for a missing dict key."""

_MISSING = _Missing()


def test_check_success_same_name_status_missing_state_blocks() -> None:
    """A matching status whose 'state' field is absent must fail closed."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_status_with("CI / test", _MISSING)]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "state" in errors[0]


def test_check_success_same_name_status_state_null_blocks() -> None:
    """A matching status whose state is JSON null must fail closed."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_status_with("CI / test", None)]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "state" in errors[0]


def test_check_success_same_name_status_state_int_blocks() -> None:
    """A matching status whose state is a non-string (int) must fail closed."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_status_with("CI / test", 123)]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "state" in errors[0]


def test_pinned_check_success_malformed_same_name_status_blocks() -> None:
    """A pinned check run success plus a malformed same-name status blocks."""
    errors = validate_required_checks(
        _required_rules_with_apps(("CI / test", 1234)),
        [_check_run("CI / test", run_id=1, conclusion="success", app_id=1234)],
        [{"statuses": [_status_with("CI / test", _MISSING)]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "state" in errors[0]


def test_status_only_context_missing_state_blocks() -> None:
    """A status-only context (no check run) with missing state blocks."""
    errors = validate_required_checks(
        _required_rules("external / status"),
        [],
        [{"statuses": [_status_with("external / status", _MISSING)]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "state" in errors[0]


def test_status_context_null_fails_closed_no_crash() -> None:
    """A status entry whose context is null must fail closed without crashing."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_status_with(None, "success")]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "Malformed" in errors[0] or "context" in errors[0]


def test_status_context_non_string_fails_closed_no_crash() -> None:
    """A status entry whose context is an int must fail closed without crashing."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_status_with(12345, "success")]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "Malformed" in errors[0] or "context" in errors[0]


def test_malformed_statuses_collection_fails_closed() -> None:
    """A 'statuses' collection that is not a list must fail closed."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": {"oops": "not a list"}}],
        branch="main",
    )
    assert len(errors) == 1
    assert "Malformed" in errors[0] or "statuses" in errors[0]


def test_different_case_failure_status_blocks() -> None:
    """A same-context status with different case and a failing state blocks."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_commit_status("ci / TEST", status_id=1, state="failure")]}],
        branch="main",
    )
    assert len(errors) == 1
    assert "failure" in errors[0]


def test_both_valid_and_successful_passes() -> None:
    """A valid check run and valid successful status both pass."""
    assert validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": [_commit_status("CI / test", status_id=1, state="success")]}],
        branch="main",
    ) == []


def test_non_dict_status_entry_fails_closed() -> None:
    """A status entry that is not a JSON object must fail closed."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{"statuses": ["not a dict"]}],
        branch="main",
    )
    assert len(errors) == 1


def test_malformed_check_runs_collection_fails_closed() -> None:
    """A 'check_runs' collection that is not a list must fail closed."""
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [{"check_runs": {"oops": "not a list"}}],
        branch="main",
    )
    assert len(errors) == 1
    assert "Malformed" in errors[0] or "check_runs" in errors[0]


def test_status_state_errors_helper_missing_state() -> None:
    """_status_state_errors reports when state is missing on a present status."""
    state, errs = _status_state_errors(
        {"context": "CI / test"}, "CI / test"
    )
    assert state is None
    assert len(errs) == 1
    assert "state" in errs[0]


def test_status_state_errors_helper_none_status() -> None:
    """_status_state_errors returns no errors when no status exists."""
    state, errs = _status_state_errors(None, "CI / test")
    assert state is None
    assert errs == []


def test_normalize_status_context_rejects_non_string() -> None:
    """_normalize_status_context rejects non-string contexts."""
    from tools.release.gates.verify_tag_sha_checks import _normalize_status_context

    with pytest.raises(MalformedPayloadError):
        _normalize_status_context(None)
    with pytest.raises(MalformedPayloadError):
        _normalize_status_context(123)
    with pytest.raises(MalformedPayloadError):
        _normalize_status_context(["a"])

def test_malformed_status_entry_in_status_path_fails_closed_no_crash() -> None:
    """A malformed status entry returned from the status API must fail closed.

    Regression test for the missing ``try/except MalformedPayloadError`` around
    the ``_status_errors`` call path.  Before the fix this entry would surface
    as a raw uncaught exception rather than a structured release-gate error.

    We inject the error by passing a status entry whose ``context`` is ``None``;
    ``_latest_matching_status`` rejects such entries as malformed, and the
    new wrapper in ``_evaluate_one_required_check`` converts that to a
    string error for the affected required check.
    """
    errors = validate_required_checks(
        _required_rules("CI / test"),
        [_check_run("CI / test", run_id=1, conclusion="success")],
        [{
            "statuses": [
                _commit_status("CI / test", status_id=1, state="success"),
                {"context": None, "state": "success", "id": 999},
            ]
        }],
        branch="main",
    )
    # No raw exception; the gate reports a structured MalformedPayloadError.
    assert len(errors) == 1
    assert "Malformed" in errors[0]
    assert "CI / test" in errors[0]
