#!/usr/bin/env python3
"""Validate protected required-check contexts against a GitHub workflow."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import validate_read_path  # noqa: E402


_FALSE_EXPRESSION = re.compile(
    r"^(?:\$\{\{\s*)?(?:false|0|null|none)(?:\s*\}\})?$"
)
_ALWAYS_FALSE_EXPRESSION = re.compile(
    r"^(?:always\(\)\s*&&\s*)?false$"
)


def _is_unconditional_skip(value: Any) -> bool:
    if value is False or value is None:
        return True
    if isinstance(value, (int, float)) and value == 0:
        return True
    if not isinstance(value, str):
        return False
    expression = value.strip().lower()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    return bool(
        _FALSE_EXPRESSION.fullmatch(expression)
        or _ALWAYS_FALSE_EXPRESSION.fullmatch(expression)
    )


def _required_contexts(contract: dict[str, Any]) -> list[str]:
    contexts = contract.get("required_contexts")
    if not isinstance(contexts, list) or not contexts:
        raise ValueError("required_contexts must be a non-empty list")
    if any(not isinstance(context, str) or not context for context in contexts):
        raise ValueError("required_contexts must contain non-empty strings")
    if len(set(contexts)) != len(contexts):
        raise ValueError("required_contexts must not contain duplicates")
    return contexts


def _collect_job_names(
    jobs: dict[str, Any],
) -> tuple[dict[str, list[str]], list[str]]:
    names: dict[str, list[str]] = {}
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            return {}, [f"job {job_id!r} must be a mapping"]
        name = job.get("name")
        if not isinstance(name, str) or not name.strip():
            name = str(job_id)
        names.setdefault(name, []).append(str(job_id))
    return names, []


def _duplicate_context_violations(
    required: list[str], names: dict[str, list[str]]
) -> list[str]:
    violations: list[str] = []
    for context in required:
        job_ids = names.get(context, [])
        if not job_ids:
            violations.append(f"missing required workflow context: {context}")
        elif len(job_ids) > 1:
            violations.append(
                f"required workflow context is duplicated: {context} "
                f"({', '.join(job_ids)})"
            )
    return violations


def _skipped_context_violations(
    required: list[str], names: dict[str, list[str]], jobs: dict[str, Any]
) -> list[str]:
    violations: list[str] = []
    for context in required:
        for job_id in names.get(context, []):
            if "if" in jobs[job_id] and _is_unconditional_skip(
                jobs[job_id]["if"]
            ):
                violations.append(
                    f"required workflow context is unconditionally skipped: "
                    f"{context} (job {job_id})"
                )
    return violations


def validate_workflow_contract(
    workflow: dict[str, Any], contract: dict[str, Any]
) -> list[str]:
    """Return violations found between workflow job contexts and the contract."""
    try:
        required = _required_contexts(contract)
    except ValueError as error:
        return [str(error)]

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return ["workflow must contain a jobs mapping"]

    names, errors = _collect_job_names(jobs)
    if errors:
        return errors
    return _duplicate_context_violations(
        required, names
    ) + _skipped_context_violations(required, names, jobs)


def _load_yaml(
    path: Path, *, root: Path = REPO_ROOT
) -> dict[str, Any]:
    raw_path = str(path)
    if ".." in raw_path.replace("\\", "/").split("/"):
        raise ValueError(
            "Refusing path with '..' traversal component (purpose: workflow)"
        )
    resolved_root = root.resolve(strict=True)
    absolute_path = path.absolute()
    try:
        absolute_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            f"Read workflow path resolves outside the allowed root "
            f"{resolved_root}: {absolute_path}"
        ) from error
    resolved_path = absolute_path.resolve(strict=True)
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            f"Read workflow path resolves outside the allowed root "
            f"{resolved_root}: {resolved_path}"
        ) from error
    safe_path = validate_read_path(
        resolved_root / relative_path, purpose="workflow"
    )
    with safe_path.open(encoding="utf-8") as stream:
        data = yaml.load(stream, Loader=yaml.BaseLoader)  # noqa: S506
    if not isinstance(data, dict):
        raise ValueError(f"workflow must be a mapping: {safe_path}")
    return data


def _load_json(
    path: Path, *, root: Path = REPO_ROOT
) -> dict[str, Any]:
    raw_path = str(path)
    if ".." in raw_path.replace("\\", "/").split("/"):
        raise ValueError(
            "Refusing path with '..' traversal component (purpose: contract)"
        )
    resolved_root = root.resolve(strict=True)
    absolute_path = path.absolute()
    try:
        absolute_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            f"Read contract path resolves outside the allowed root "
            f"{resolved_root}: {absolute_path}"
        ) from error
    resolved_path = absolute_path.resolve(strict=True)
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(
            f"Read contract path resolves outside the allowed root "
            f"{resolved_root}: {resolved_path}"
        ) from error
    safe_path = validate_read_path(
        resolved_root / relative_path, purpose="contract"
    )
    with safe_path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"contract must be a mapping: {safe_path}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate protected required-check contexts against a workflow"
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=REPO_ROOT / ".github" / "workflows" / "ci.yml",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).with_name("required-check-contexts.json"),
    )
    args = parser.parse_args()

    try:
        workflow = _load_yaml(args.workflow)
        contract = _load_json(args.contract)
        violations = validate_workflow_contract(workflow, contract)
    # JSONDecodeError subclasses ValueError and YAML errors surface from
    # the parser itself; both are covered by this single tuple.
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if violations:
        for violation in violations:
            print(f"ERROR: {violation}", file=sys.stderr)
        return 1

    print(
        "Required workflow contexts are present and not unconditionally skipped: "
        f"{len(contract['required_contexts'])} checked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
