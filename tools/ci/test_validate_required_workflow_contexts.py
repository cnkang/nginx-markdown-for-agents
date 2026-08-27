"""Regression tests for protected required-check context validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from validate_required_workflow_contexts import validate_workflow_contract
from validate_required_workflow_contexts import _load_json
from validate_required_workflow_contexts import _load_yaml


CONTRACT = {
    "required_contexts": [
        "Detect Changed Areas",
        "Runtime Regressions (IMS + Chunked + Large)",
    ]
}

BASE_WORKFLOW = {
    "jobs": {
        "changes": {"name": "Detect Changed Areas"},
        "runtime": {
            "name": "Runtime Regressions (IMS + Chunked + Large)",
            "if": "needs.changes.outputs.e2e == 'true'",
        },
    }
}


def test_all_required_contexts_present_and_runnable() -> None:
    assert validate_workflow_contract(BASE_WORKFLOW, CONTRACT) == []


def test_renamed_required_context_fails() -> None:
    workflow = deepcopy(BASE_WORKFLOW)
    workflow["jobs"]["runtime"]["name"] = "Runtime Regressions"
    violations = validate_workflow_contract(workflow, CONTRACT)
    assert any("missing required workflow context" in item for item in violations)


def test_extra_job_does_not_break_required_context_contract() -> None:
    workflow = deepcopy(BASE_WORKFLOW)
    workflow["jobs"]["extra"] = {"name": "Optional Observation"}
    assert validate_workflow_contract(workflow, CONTRACT) == []


def test_unconditionally_skipped_required_context_fails() -> None:
    workflow = deepcopy(BASE_WORKFLOW)
    workflow["jobs"]["runtime"]["if"] = "false"
    violations = validate_workflow_contract(workflow, CONTRACT)
    assert any("unconditionally skipped" in item for item in violations)


@pytest.mark.parametrize("loader", (_load_yaml, _load_json))
def test_loaders_contain_paths_within_declared_root(loader, tmp_path) -> None:
    """A realpath-resolved location outside the declared root is refused."""
    outside = tmp_path / "outside.yaml"
    outside.write_text("jobs: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the allowed root"):
        loader(outside)


@pytest.mark.parametrize("loader", (_load_yaml, _load_json))
def test_loaders_accept_locations_inside_root(loader, tmp_path) -> None:
    """Locations beneath an explicitly allowed root load normally."""
    inside_yaml = tmp_path / "workflow.yml"
    inside_yaml.write_text("jobs:\n  build:\n    name: build\n", encoding="utf-8")
    inside_json = tmp_path / "contract.json"
    inside_json.write_text('{"required_contexts": []}', encoding="utf-8")
    if loader is _load_yaml:
        data = loader(inside_yaml, root=tmp_path)
        assert isinstance(data.get("jobs"), dict)
    else:
        data = loader(inside_json, root=tmp_path)
        assert "required_contexts" in data


@pytest.mark.parametrize("loader", (_load_yaml, _load_json))
def test_loaders_reject_parent_paths(loader) -> None:
    """CLI input paths must not contain traversal components."""
    with pytest.raises(ValueError, match="Refusing path"):
        loader(Path("../outside-input"))
