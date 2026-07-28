#!/usr/bin/env python3
"""Verify that a tag commit passed every required branch status check."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.path_validation import validate_read_path  # noqa: E402,E0401,C0413

# GitHub treats these check-run conclusions as satisfying a required
# status check; see
# https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
# Every other conclusion (failure, cancelled, timed_out, action_required,
# stale, startup_failure, or a missing conclusion) must keep blocking the tag.
SUCCESSFUL_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


@dataclass(frozen=True)
class RequiredCheck:
    """A required status check context plus its optional required source app.

    Rulesets may pin a required context to the GitHub App that must produce
    it via ``integration_id``; when unset, a result from any source satisfies
    the requirement.
    """

    context: str
    integration_id: int | None


def _flatten_api_pages(payload: Any, collection_key: str | None = None) -> list[dict[str, Any]]:
    """Flatten ``gh api --paginate --slurp`` output into API objects."""
    if isinstance(payload, list):
        flattened: list[dict[str, Any]] = []
        for page in payload:
            flattened.extend(_flatten_api_pages(page, collection_key))
        return flattened
    if not isinstance(payload, dict):
        return []
    if collection_key is not None:
        collection = payload.get(collection_key)
        if isinstance(collection, list):
            return [item for item in collection if isinstance(item, dict)]
    return [payload]


def _checks_from_rule(rule: dict[str, Any]) -> list[RequiredCheck]:
    """Extract required checks from one branch-effective required-check rule."""
    if rule.get("type") != "required_status_checks":
        return []
    parameters = rule.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("required_status_checks rule has invalid parameters")
    checks = parameters.get("required_status_checks")
    if not isinstance(checks, list):
        raise ValueError("required_status_checks rule has no check list")

    required = []
    for check in checks:
        context = check.get("context") if isinstance(check, dict) else None
        if not isinstance(context, str):
            raise ValueError("required_status_checks rule contains an invalid context")
        context = context.strip()
        if not context:
            raise ValueError("required_status_checks rule contains an empty context")
        integration_id = check.get("integration_id")
        if integration_id is not None and (
            isinstance(integration_id, bool) or not isinstance(integration_id, int)
        ):
            raise ValueError(
                "required_status_checks rule contains an invalid integration_id "
                f"for context '{context}'"
            )
        required.append(RequiredCheck(context=context, integration_id=integration_id))
    return required


def required_checks(active_rules: Any) -> list[RequiredCheck]:
    """Extract required checks from the branch-effective rules response."""
    checks: set[RequiredCheck] = set()
    for rule in _flatten_api_pages(active_rules):
        checks.update(_checks_from_rule(rule))
    return sorted(checks, key=lambda check: (check.context, check.integration_id or 0))


def _timestamp(run: dict[str, Any]) -> float:
    """Return a sortable timestamp for a check run, defaulting safely to zero."""
    for field in ("started_at", "created_at", "completed_at"):
        value = run.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _run_order_key(run: dict[str, Any]) -> tuple[float, int]:
    """Order reruns by time and use the check-run ID as a deterministic tie-breaker."""
    run_id = run.get("id", 0)
    return _timestamp(run), int(run_id) if isinstance(run_id, int) else 0


def _run_app_id(run: dict[str, Any]) -> int | None:
    """Return the GitHub App id that produced a check run, when present."""
    app = run.get("app")
    if isinstance(app, dict):
        app_id = app.get("id")
        if isinstance(app_id, int) and not isinstance(app_id, bool):
            return app_id
    return None


def _matches_required_check(run: dict[str, Any], required: RequiredCheck) -> bool:
    """A run satisfies a required check by name and, when pinned, by source app."""
    if run.get("name") != required.context:
        return False
    if required.integration_id is None:
        return True
    return _run_app_id(run) == required.integration_id


def _latest_status_state(statuses: Any, context: str) -> str | None:
    """Return the state of the newest commit status for context, if any."""
    if statuses is None:
        return None
    matching = [
        status
        for status in _flatten_api_pages(statuses, "statuses")
        if status.get("context") == context
    ]
    if not matching:
        return None
    latest = max(matching, key=_run_order_key)
    state = latest.get("state")
    return state if isinstance(state, str) else None


def _status_errors(required: RequiredCheck, statuses: Any) -> list[str]:
    """Evaluate the Commit Status API result for a required context.

    A commit status cannot prove which GitHub App produced it, so it never
    satisfies a context pinned to an integration_id.  When integration_id is
    None, the latest commit status state must be ``success``.
    """
    if required.integration_id is not None:
        return [
            f"Required check '{required.context}' from app "
            f"{required.integration_id} has no matching check run on the tag SHA; "
            f"commit statuses cannot satisfy an integration-pinned context."
        ]
    state = _latest_status_state(statuses, required.context)
    if state is None:
        return [f"Required check '{required.context}' is missing on the tag SHA."]
    if state != "success":
        return [
            f"Required check '{required.context}' is not successful on the tag SHA "
            f"(commit status state={state!r})."
        ]
    return []


def _missing_errors(required: RequiredCheck) -> list[str]:
    """Error when a required context has neither a check run nor a commit status."""
    if required.integration_id is not None:
        return [
            f"Required check '{required.context}' from app "
            f"{required.integration_id} is missing on the tag SHA."
        ]
    return [f"Required check '{required.context}' is missing on the tag SHA."]


def _check_run_errors(required: RequiredCheck, matching_runs: list[dict[str, Any]]) -> list[str]:
    """Error when the latest matching check run is not successful."""
    latest = max(matching_runs, key=_run_order_key)
    if (
        latest.get("status") != "completed"
        or latest.get("conclusion") not in SUCCESSFUL_CONCLUSIONS
    ):
        return [
            f"Required check '{required.context}' is not successful on the tag SHA "
            f"(status={latest.get('status')!r}, conclusion={latest.get('conclusion')!r})."
        ]
    return []


def validate_required_checks(
    active_rules: Any,
    check_runs: Any,
    statuses: Any = None,
    *,
    branch: str,
) -> list[str]:
    """Return fail-closed errors for required checks on the tag commit.

    GitHub requires that when a check run and a commit status share the
    same required context name, **both** must pass.  This function
    implements that semantics:

    * Neither exists → missing.
    * Only check run → check run must be completed with a successful
      conclusion.
    * Only commit status → commit status state must be ``success``
      (unless the context is pinned to an integration_id, which commit
      statuses cannot satisfy).
    * Both exist → both must pass independently.
    """
    try:
        checks = required_checks(active_rules)
    except ValueError as error:
        return [f"Unable to parse branch-effective required checks: {error}"]

    if not checks:
        return [f"{branch} has no active required status checks; refusing tag release"]

    runs = _flatten_api_pages(check_runs, "check_runs")
    errors: list[str] = []
    for required in checks:
        matching_runs = [run for run in runs if _matches_required_check(run, required)]
        has_matching_run = bool(matching_runs)
        has_matching_status = (
            statuses is not None
            and _latest_status_state(statuses, required.context) is not None
        )

        if not has_matching_run and not has_matching_status:
            errors.extend(_missing_errors(required))
            continue

        if has_matching_run:
            errors.extend(_check_run_errors(required, matching_runs))

        if has_matching_status:
            errors.extend(_status_errors(required, statuses))
    return errors


def _resolve_repository_input(path: Path) -> Path:
    """Resolve an input file without allowing it to escape the checkout."""
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input paths must be relative and cannot contain '..'")

    repository_root = Path.cwd().resolve()
    resolved = (repository_root / path).resolve(strict=True)
    if not resolved.is_relative_to(repository_root):
        raise ValueError("input path must remain within the repository checkout")
    return resolved


def _load_json(path: Path) -> Any:
    """Load a JSON API response from a workflow temporary file."""
    safe_path = validate_read_path(
        _resolve_repository_input(path),
        purpose="GitHub API response",
    )
    with safe_path.open(encoding="utf-8") as stream:
        return json.load(stream)


def main() -> int:
    """Parse workflow inputs and verify the tag commit's required checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-file", type=Path, required=True)
    parser.add_argument("--check-runs-file", type=Path, required=True)
    parser.add_argument(
        "--statuses-file",
        type=Path,
        default=None,
        help="Optional combined commit status API response; required contexts "
        "reported through the Commit Status API instead of check runs are "
        "evaluated from it.",
    )
    parser.add_argument("--tag-sha", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()

    try:
        errors = validate_required_checks(
            _load_json(args.rules_file),
            _load_json(args.check_runs_file),
            _load_json(args.statuses_file) if args.statuses_file else None,
            branch=args.branch,
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: Unable to read GitHub API response: {error}", file=sys.stderr)
        return 1

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"Tag SHA {args.tag_sha} is contained in protected {args.branch} "
        "and passed all required checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
