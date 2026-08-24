#!/usr/bin/env python3
"""Verify that a tag commit passed every required branch status check."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.path_validation import (  # noqa: E402,E0401,C0413
    validate_read_path,
    validate_write_path_within_root,
)

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
    """Flatten ``gh api --paginate --slurp`` output into API objects.

    Non-dict entries inside a collection are skipped defensively.  A
    collection key whose value is neither a list nor ``None`` is a
    malformed payload and raises :class:`MalformedPayloadError` so the
    release gate fail-closes instead of treating corruption as an empty
    result.
    """
    if isinstance(payload, list):
        flattened: list[dict[str, Any]] = []
        for page in payload:
            flattened.extend(_flatten_api_pages(page, collection_key))
        return flattened
    if not isinstance(payload, dict):
        return []
    if collection_key is not None:
        if collection_key not in payload:
            # No collection key at all — treat the payload itself as a
            # single API object (callers may pass a bare check-run dict).
            return [payload]
        collection = payload.get(collection_key)
        if collection is None:
            return []
        if not isinstance(collection, list):
            raise MalformedPayloadError(
                f"API collection '{collection_key}' must be a list or null "
                f"(got {type(collection).__name__})"
            )
        return [item for item in collection if isinstance(item, dict)]
    return [payload]


def _flatten_status_pages(payload: Any) -> list[dict[str, Any]]:
    """Flatten commit-status API pages, failing closed on non-dict entries.

    Unlike :func:`_flatten_api_pages`, a status entry that is not a JSON
    object is rejected so a corrupted Commit Status API response cannot
    be silently treated as an empty result.
    """
    if isinstance(payload, list):
        flattened: list[dict[str, Any]] = []
        for page in payload:
            flattened.extend(_flatten_status_pages(page))
        return flattened
    if not isinstance(payload, dict):
        return []
    if "statuses" not in payload:
        return [payload]
    collection = payload.get("statuses")
    if collection is None:
        return []
    if not isinstance(collection, list):
        raise MalformedPayloadError(
            f"API collection 'statuses' must be a list or null "
            f"(got {type(collection).__name__})"
        )
    entries: list[dict[str, Any]] = []
    for item in collection:
        if not isinstance(item, dict):
            raise MalformedPayloadError(
                "commit status entry must be a JSON object"
            )
        entries.append(item)
    return entries


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


class MalformedPayloadError(ValueError):
    """Raised when GitHub API payload cannot be reliably interpreted.

    A malformed payload must never be silently ignored or treated as a
    successful release gate; the caller fails closed with an explicit
    release-gate error instead of crashing or defaulting to success.
    """


def _normalize_status_context(value: Any) -> str:
    """Normalize a commit status context for case-insensitive comparison.

    GitHub's Commit Status API treats context as case-insensitive.
    https://docs.github.com/en/rest/commits/statuses?apiVersion=2026-03-10

    Non-string or missing contexts are rejected because they cannot be
    reliably matched; the caller fail-closes instead of guessing.
    """
    if not isinstance(value, str):
        raise MalformedPayloadError(
            f"commit status context must be a string (got {type(value).__name__})"
        )
    return value.casefold()


def _latest_matching_status(statuses: Any, context: str) -> dict[str, Any] | None:
    """Return the newest commit status object matching ``context``.

    Context matching is case-insensitive per GitHub's Commit Status API.
    Returns ``None`` when no commit status with that context exists.

    Raises :class:`MalformedPayloadError` when a status entry that
    appears to match is not a JSON object or lacks a usable ``context``
    field; such payloads cannot be reliably interpreted and must fail
    closed rather than be silently skipped.
    """
    if statuses is None:
        return None
    normalized_context = _normalize_status_context(context)
    matching: list[dict[str, Any]] = []
    for status in _flatten_status_pages(statuses):
        raw_context = status.get("context")
        if raw_context is None:
            # An entry with no context key cannot be matched; reject the
            # whole payload so a broken API response never masquerades
            # as "no matching status".
            raise MalformedPayloadError(
                "commit status entry is missing a 'context' field"
            )
        if _normalize_status_context(raw_context) == normalized_context:
            matching.append(status)
    if not matching:
        return None
    return max(matching, key=_run_order_key)


def _status_state_errors(
    status: dict[str, Any] | None, context: str,
) -> tuple[str | None, list[str]]:
    """Validate the ``state`` field of a commit status.

    Returns a tuple ``(state, errors)``:

    * ``status`` is ``None`` → ``(None, [])`` (no matching status; the
      caller decides whether that is acceptable for the context).
    * ``status`` exists but ``state`` is missing, ``null``, or not a
      recognizable string → ``(None, errors)`` so the caller fail-closes.
    * ``state`` is a valid string → ``(state, [])``.
    """
    if status is None:
        return None, []
    state = status.get("state")
    if state is None:
        return None, [
            f"Required check '{context}' has a commit status for the same "
            f"context but its 'state' field is null; when a commit status "
            f"exists for a required context it must report a valid state."
        ]
    if not isinstance(state, str):
        return None, [
            f"Required check '{context}' has a commit status for the same "
            f"context but its 'state' field is not a string "
            f"(got {type(state).__name__}); malformed GitHub API payloads "
            f"cannot satisfy a release gate."
        ]
    return state, []


def _has_matching_status(statuses: Any, context: str) -> bool:
    """Check if a commit status exists for the given context (case-insensitive).

    Malformed payloads raise :class:`MalformedPayloadError`; the caller
    fail-closes rather than treating corruption as absence.
    """
    return _latest_matching_status(statuses, context) is not None


def _status_errors(
    required: RequiredCheck, statuses: Any, has_matching_run: bool,
) -> list[str]:
    """Evaluate the Commit Status API result for a required context.

    GitHub requires that when a check run and a commit status share the
    same required context name, **both** must pass.  We never ignore a
    legacy commit status because:

    * The Commit Status API cannot prove which GitHub App produced a status,
      so an app-pinned context cannot rely on it for source verification.
      But the status still represents an independent signal for that same
      required context name, and a failing one must block rather than be
      silently discarded.

    * Without the status, the gate is already satisfied or rejected by the
      check-run path alone.

    Existence and validity are evaluated separately so a malformed status
    payload (missing/non-string ``state``) fail-closes instead of being
    silently treated as "no matching status".

    Policy per context:
    - Correct-app check run exists AND no commit status: satisfied by the
      check run.
    - Correct-app check run exists AND commit status present: status must
      be ``success`` too (same-name both-pass rule).
    - No correct-app check run AND pinned context: the status cannot
      satisfy an integration-pinned requirement.
    - No correct-app check run AND unpinned context: status must be
      ``success``.
    """
    latest = _latest_matching_status(statuses, required.context)
    state, state_errors = _status_state_errors(latest, required.context)
    if state_errors:
        # A matching status exists but its state is malformed; fail closed
        # regardless of the check-run outcome so a corrupted payload can
        # never satisfy a release gate.
        return state_errors

    if required.integration_id is not None and has_matching_run:
        # A matching check run from the correct app exists.  GitHub's
        # same-name rule still requires the commit status to pass; fail
        # closed on any non-success status.
        if latest is None:
            # Same context not reported through the Commit Status API;
            # the check run alone satisfies the pinned requirement.
            return []
        if state != "success":
            return [
                f"Required check '{required.context}' has a successful check run "
                f"from app {required.integration_id} but a non-successful commit "
                f"status for the same context (state={state!r}); when a check run "
                f"and commit status share a required context name, both must pass."
            ]
        return []

    if required.integration_id is not None:
        # Pinned context with no matching check run: commit status cannot
        # prove the App that produced it, so it cannot satisfy the pin.
        return [
            f"Required check '{required.context}' from app "
            f"{required.integration_id} has no matching check run on the tag SHA; "
            f"commit statuses cannot satisfy an integration-pinned context."
        ]

    if latest is None:
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

    try:
        runs = _flatten_api_pages(check_runs, "check_runs")
    except MalformedPayloadError as error:
        return [f"Malformed check-runs payload: {error}"]

    errors: list[str] = []
    for required in checks:
        errors.extend(
            _evaluate_one_required_check(required, runs, statuses)
        )
    return errors


def _evaluate_one_required_check(
    required: RequiredCheck,
    runs: list[dict[str, Any]],
    statuses: Any,
) -> list[str]:
    """Evaluate a single required check against runs and statuses.

    Encapsulates the per-check logic so ``validate_required_checks`` stays
    a simple loop.  Malformed payloads are caught and reported as blocking
    errors for the affected context.
    """
    try:
        matching_runs = [
            run for run in runs if _matches_required_check(run, required)
        ]
        has_matching_run = bool(matching_runs)
        has_matching_status = (
            statuses is not None
            and _has_matching_status(statuses, required.context)
        )
    except MalformedPayloadError as error:
        return [
            f"Malformed commit-status payload for required check "
            f"'{required.context}': {error}"
        ]

    if not has_matching_run and not has_matching_status:
        return _missing_errors(required)

    errors: list[str] = []
    if has_matching_run:
        errors.extend(_check_run_errors(required, matching_runs))
    if has_matching_status:
        try:
            errors.extend(_status_errors(required, statuses, has_matching_run))
        except MalformedPayloadError as error:
            errors.append(
                f"Malformed commit-status payload for required check "
                f"'{required.context}': {error}"
            )
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


def _write_required_checks(path: Path, checks: list[RequiredCheck], *, branch: str,
                           tag_sha: str) -> None:
    """Write the effective required-check enumeration for release records."""
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("output paths must be relative and cannot contain '..'")

    safe_path = validate_write_path_within_root(
        path,
        Path.cwd(),
        purpose="required-checks output",
    )
    payload = {
        "schema_version": "release.required-checks.v1",
        "branch": branch,
        "tag_sha": tag_sha,
        "required_checks": [
            {
                "context": check.context,
                "integration_id": check.integration_id,
            }
            for check in checks
        ],
    }
    serialized_payload = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    # Anchor the final open to the validated parent directory instead of
    # passing a user-derived path to a convenience helper.
    parent_fd = os.open(safe_path.parent, os.O_RDONLY)
    file_fd = -1
    try:
        file_fd = os.open(
            safe_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
        with os.fdopen(file_fd, "w", encoding="utf-8") as stream:
            file_fd = -1
            stream.write(serialized_payload)
    finally:
        if file_fd != -1:
            os.close(file_fd)
        os.close(parent_fd)


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
    parser.add_argument(
        "--required-checks-output",
        type=Path,
        default=None,
        help="Optional repository-relative JSON file receiving the effective "
        "required-check enumeration for release records.",
    )
    args = parser.parse_args()

    try:
        rules = _load_json(args.rules_file)
        checks = required_checks(rules)
        errors = validate_required_checks(
            rules,
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

    if args.required_checks_output is not None:
        try:
            _write_required_checks(
                args.required_checks_output,
                checks,
                branch=args.branch,
                tag_sha=args.tag_sha,
            )
        except (OSError, ValueError) as error:
            print(
                f"ERROR: Unable to write effective required checks: {error}",
                file=sys.stderr,
            )
            return 1

    print(
        f"Tag SHA {args.tag_sha} is contained in protected {args.branch} "
        "and passed all required checks."
    )
    print("Effective required checks:")
    for check in checks:
        source = (
            f"integration_id={check.integration_id}"
            if check.integration_id is not None
            else "integration_id=any"
        )
        print(f"- {check.context} ({source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
