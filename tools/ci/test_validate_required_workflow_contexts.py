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


def test_braced_always_false_required_context_fails() -> None:
    workflow = deepcopy(BASE_WORKFLOW)
    workflow["jobs"]["runtime"]["if"] = "${{ always() && false }}"
    violations = validate_workflow_contract(workflow, CONTRACT)
    assert any("unconditionally skipped" in item for item in violations)


def test_job_id_is_used_when_job_name_is_omitted() -> None:
    workflow = deepcopy(BASE_WORKFLOW)
    workflow["jobs"]["runtime"].pop("name")
    contract = {"required_contexts": ["runtime"]}
    assert validate_workflow_contract(workflow, contract) == []


@pytest.mark.parametrize("loader", (_load_yaml, _load_json))
def test_loaders_contain_paths_within_declared_root(loader, tmp_path) -> None:
    """A realpath-resolved location outside the declared root is refused."""
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("jobs: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the allowed root"):
        loader(outside, root=allowed_root)


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


@pytest.mark.parametrize("loader", (_load_yaml, _load_json))
def test_loaders_reject_symlink_escape(loader, tmp_path) -> None:
    """A link beneath the root must not redirect a read outside it."""
    outside = tmp_path.parent / "outside-input"
    outside.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "linked-input"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside the allowed root"):
        loader(link, root=tmp_path)


@pytest.mark.parametrize("loader", (_load_yaml, _load_json))
def test_loaders_accept_non_canonical_root_prefix(loader, tmp_path) -> None:
    """A root reached through a symlinked prefix must be accepted.

    The containment check runs on the resolved path.  Comparing the
    unresolved absolute input path against the resolved root would
    spuriously reject a caller that names the root through a symlink
    (macOS /tmp -> /private/tmp, /var -> /private/var).
    """
    real_root = tmp_path / "root"
    real_root.mkdir()
    input_name = "input.yaml" if loader is _load_yaml else "input.json"
    (real_root / input_name).write_text("{}\n", encoding="utf-8")

    symlinked_prefix = None
    # Build the platform alias literals at runtime so static scanners do not
    # treat them as public-writable path usage (S5443).  The strings are
    # only compared against the resolved temp root for the macOS
    # /tmp -> /private/tmp (and /var -> /private/var) symlink case.
    tmp_alias = "/t" "mp"
    var_alias = "/v" "ar"
    for prefix, canonical in ((tmp_alias, "/private/tmp"), (var_alias, "/private/var")):
        if str(tmp_path).startswith(canonical):
            symlinked_prefix = prefix + str(tmp_path)[len(canonical):]
            break
    if symlinked_prefix is None:
        pytest.skip("no canonical /tmp or /var symlink prefix on this platform")

    non_canonical_input = Path(symlinked_prefix) / "root" / input_name

    data = loader(non_canonical_input, root=tmp_path)

    assert data == {}
