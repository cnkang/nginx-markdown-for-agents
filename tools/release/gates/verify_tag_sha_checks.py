#!/usr/bin/env python3
"""Verify that a tag commit passed every required branch status check."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.path_validation import validate_read_path  # noqa: E402


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


def _contexts_from_rule(rule: dict[str, Any]) -> list[str]:
    """Extract contexts from one branch-effective required-check rule."""
    if rule.get("type") != "required_status_checks":
        return []
    parameters = rule.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("required_status_checks rule has invalid parameters")
    checks = parameters.get("required_status_checks")
    if not isinstance(checks, list):
        raise ValueError("required_status_checks rule has no check list")

    contexts = []
    for check in checks:
        context = check.get("context") if isinstance(check, dict) else None
        if not isinstance(context, str):
            raise ValueError("required_status_checks rule contains an invalid context")
        context = context.strip()
        if not context:
            raise ValueError("required_status_checks rule contains an empty context")
        contexts.append(context)
    return contexts


def required_check_contexts(active_rules: Any) -> list[str]:
    """Extract required check names from the branch-effective rules response."""
    contexts: set[str] = set()
    for rule in _flatten_api_pages(active_rules):
        contexts.update(_contexts_from_rule(rule))
    return sorted(contexts)


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


def validate_required_checks(
    active_rules: Any,
    check_runs: Any,
    *,
    branch: str,
) -> list[str]:
    """Return fail-closed errors for required checks on the tag commit."""
    try:
        contexts = required_check_contexts(active_rules)
    except ValueError as error:
        return [f"Unable to parse branch-effective required checks: {error}"]

    if not contexts:
        return [f"{branch} has no active required status checks; refusing tag release"]

    runs = _flatten_api_pages(check_runs, "check_runs")
    errors: list[str] = []
    for context in contexts:
        matching = [run for run in runs if run.get("name") == context]
        if not matching:
            errors.append(f"Required check '{context}' is missing on the tag SHA.")
            continue
        latest = max(matching, key=_run_order_key)
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            errors.append(
                f"Required check '{context}' is not successful on the tag SHA "
                f"(status={latest.get('status')!r}, conclusion={latest.get('conclusion')!r})."
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


def main() -> int:
    """Parse workflow inputs and verify the tag commit's required checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-file", type=Path, required=True)
    parser.add_argument("--check-runs-file", type=Path, required=True)
    parser.add_argument("--tag-sha", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()

    try:
        errors = validate_required_checks(
            _load_json(args.rules_file),
            _load_json(args.check_runs_file),
            branch=args.branch,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
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
