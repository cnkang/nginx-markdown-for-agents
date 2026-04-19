"""Regression guards for release-related GitHub workflows."""

from __future__ import annotations

from pathlib import Path

import yaml


def _workflow_data(name: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / ".github" / "workflows" / name
    with path.open(encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.BaseLoader)


def _workflow_text(name: str) -> str:
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / ".github" / "workflows" / name
    return path.read_text(encoding="utf-8")


def test_release_binaries_only_checks_matrix_freshness_on_manual_dispatch() -> None:
    """Release-triggered binary builds must not run workflow-dispatch freshness logic."""
    text = _workflow_text("release-binaries.yml")
    assert "if: github.event_name == 'workflow_dispatch' && inputs.matrix_freshness != 'off'" in text


def test_update_matrix_pr_creation_is_non_blocking_when_repo_disallows_actions_prs() -> None:
    """Scheduled matrix refreshes should succeed even if PR creation is policy-blocked."""
    text = _workflow_text("update-matrix.yml")
    assert "continue-on-error: true" in text
    assert "Matrix update branch pushed, but automatic PR creation is blocked." in text


def test_install_verify_workflow_avoids_js_actions_on_alpine_arm64_and_uses_bash() -> None:
    """Install verification must stay runnable on Alpine arm64 GitHub containers."""
    workflow = _workflow_data("install-verify.yml")
    assert workflow["on"]["workflow_dispatch"]["inputs"]["ref"]["default"] == ""
    assert workflow["on"]["workflow_dispatch"]["inputs"]["version"]["default"] == ""

    job = workflow["jobs"]["install-verify"]
    assert job["env"]["JS_ACTIONS_SUPPORTED"] == (
        "${{ !(matrix.target.pkg_manager == 'apk' && matrix.target.arch == 'aarch64') }}"
    )

    resolve_run = workflow["jobs"]["resolve-matrix"]["steps"][2]["run"]
    assert '"variant": "mainline-upper"' in resolve_run
    assert '"expected_install_success": False' in resolve_run
    assert '"variant": "upper-bound"' in resolve_run

    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Checkout repository"]["if"] == "${{ env.JS_ACTIONS_SUPPORTED == 'true' }}"
    assert steps["Checkout repository (Alpine arm64 fallback)"]["if"] == (
        "${{ env.JS_ACTIONS_SUPPORTED != 'true' }}"
    )
    assert "git config --global --add safe.directory" in steps[
        "Checkout repository (Alpine arm64 fallback)"
    ]["run"]
    assert steps["Run install script"]["shell"] == "bash"
    assert steps["Upload verification artifacts"]["if"] == (
        "${{ always() && env.JS_ACTIONS_SUPPORTED == 'true' }}"
    )
