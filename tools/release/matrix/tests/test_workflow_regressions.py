"""Regression guards for release-related GitHub workflows."""

from __future__ import annotations

from pathlib import Path

import yaml


def _workflow_data(name: str) -> dict[str, object]:
    """Load workflows with BaseLoader so workflow keys stay stable strings.

    yaml.BaseLoader intentionally returns only strings, lists, and dicts.
    That avoids YAML 1.1 boolean coercion, where an unquoted `on:` key can
    become `True` and break lookups like `workflow["on"]["workflow_dispatch"]`.
    """
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / ".github" / "workflows" / name
    with path.open(encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.BaseLoader)  # noqa: S506


def _workflow_text(name: str) -> str:
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / ".github" / "workflows" / name
    return path.read_text(encoding="utf-8")


def _step_by_name(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing workflow step: {name}")


def test_release_binaries_updates_matrix_before_resolving_builds() -> None:
    """Release binaries must refresh nginx.org matrix data before building."""
    workflow = _workflow_data("release-binaries.yml")
    steps = workflow["jobs"]["prepare"]["steps"]
    step_names = [step["name"] for step in steps if "name" in step]

    update_step = _step_by_name(steps, "Update Release Matrix")
    validate_step = _step_by_name(steps, "Validate updated release matrix")

    assert "python3 tools/release/matrix/update_matrix.py" in update_step["run"]
    assert "tools/matrix-diff.json" in update_step["run"]
    assert "python3 tools/release/matrix/validate_doc_matrix_sync.py" in validate_step["run"]
    assert (
        "python3 tools/release/matrix/validate_matrix_install_consistency.py"
        in validate_step["run"]
    )
    assert step_names.index("Update Release Matrix") < step_names.index(
        "Extract build matrix from release-matrix.json"
    )
    assert step_names.index("Validate updated release matrix") < step_names.index(
        "Extract build matrix from release-matrix.json"
    )


def test_update_matrix_pr_creation_is_non_blocking_when_repo_disallows_actions_prs() -> None:
    """Scheduled matrix refreshes should succeed even if PR creation is policy-blocked."""
    text = _workflow_text("update-matrix.yml")
    assert "continue-on-error: true" in text
    assert "Source: nginx.org download page." in text
    assert "Auto-approve release matrix PR" in text
    assert 'gh pr review "$PR_NUMBER" --approve' in text
    assert "Auto-merge release matrix PR" in text
    assert 'gh pr merge "$PR_NUMBER" --squash --delete-branch --auto' in text
    assert "Matrix update branch pushed, but automatic PR creation is blocked." in text


def test_install_verify_workflow_avoids_js_actions_on_alpine_arm64_and_uses_bash() -> None:
    """Install verification must stay runnable on Alpine arm64 GitHub containers."""
    workflow = _workflow_data("install-verify.yml")
    assert workflow["on"]["workflow_dispatch"]["inputs"]["ref"]["default"] == ""
    assert workflow["on"]["workflow_dispatch"]["inputs"]["version"]["default"] == ""

    job = workflow["jobs"]["install-verify"]
    steps = {step["name"]: step for step in job["steps"]}
    step_names = [step["name"] for step in job["steps"]]
    assert "Determine JS actions support" in steps
    assert steps["Determine JS actions support"]["run"] == (
        'echo "supported=${{ !(matrix.target.pkg_manager == \'apk\' && matrix.target.arch == \'aarch64\') }}" '
        '>> "$GITHUB_OUTPUT"\n'
    )

    resolve_step = _step_by_name(
        workflow["jobs"]["resolve-matrix"]["steps"],
        "Select representative matrix entries",
    )
    resolve_run = resolve_step["run"]
    assert "nginx.org/en/download.html" in resolve_run
    assert "sorted(set(upstream_versions), key=version_tuple)[-1]" in resolve_run
    assert '"variant": "upstream-upper"' in resolve_run
    assert '"expected_install_success": False' in resolve_run
    assert '"latest upstream"' in resolve_run

    assert steps["Checkout repository"]["if"] == (
        "${{ steps.js_actions_support.outputs.supported == 'true' }}"
    )
    assert steps["Checkout repository (Alpine arm64 fallback)"]["if"] == (
        "${{ steps.js_actions_support.outputs.supported != 'true' }}"
    )
    assert "git config --global --add safe.directory" in steps[
        "Checkout repository (Alpine arm64 fallback)"
    ]["run"]
    assert steps["Run install script"]["shell"] == "bash"
    assert steps["Upload verification artifacts"]["if"] == (
        "${{ always() && steps.js_actions_support.outputs.supported == 'true' }}"
    )
    assert steps["Dump verification artifacts"]["if"] == (
        "${{ always() && steps.js_actions_support.outputs.supported != 'true' }}"
    )
    assert "install-stdout.json" in steps["Dump verification artifacts"]["run"]
    assert "GITHUB_STEP_SUMMARY" in steps["Dump verification artifacts"]["run"]
    assert step_names.index("Upload verification artifacts") < step_names.index(
        "Dump verification artifacts"
    )
