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
    # Path is constructed from trusted repo root and test-controlled name parameter
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


def test_release_binaries_resolves_current_schema_without_mutating_matrix() -> None:
    """Release binaries must build from the checked-in current matrix schema."""
    workflow = _workflow_data("release-binaries.yml")
    steps = workflow["jobs"]["prepare"]["steps"]
    step_names = [step["name"] for step in steps if "name" in step]

    validate_step = _step_by_name(steps, "Validate release matrix consumers")
    resolve_step = _step_by_name(steps, "Extract build matrix from release-matrix.json")

    assert "python3 tools/release/matrix/update_matrix.py" not in _workflow_text(
        "release-binaries.yml"
    )
    assert "python3 tools/release/matrix/validate_workflow_matrix_consumers.py" in validate_step["run"]
    assert 'data.get("entries", [])' in resolve_step["run"]
    assert '".github/workflows/release-binaries.yml"' in resolve_step["run"]
    assert '"nginx": e["nginx_version"]' in resolve_step["run"]
    assert '"os_type": e["libc"]' in resolve_step["run"]
    assert '"amd64": "x86_64"' in resolve_step["run"]
    assert '"arm64": "aarch64"' in resolve_step["run"]
    assert 'data.get("matrix", [])' not in resolve_step["run"]
    assert 'support_tier") == "full"' not in resolve_step["run"]
    assert step_names.index("Validate release matrix consumers") < step_names.index(
        "Extract build matrix from release-matrix.json"
    )


def test_release_binaries_workflow_dispatch_can_publish_tag_assets() -> None:
    """Manual recovery runs for release tags must upload assets to that tag."""
    workflow = _workflow_data("release-binaries.yml")
    publish = workflow["jobs"]["publish-release"]
    upload = _step_by_name(publish["steps"], "Upload Assets")

    assert "workflow_dispatch" in publish["if"]
    assert "startsWith(github.event.inputs.version, 'v')" in publish["if"]
    assert upload["with"]["tag_name"] == (
        "${{ github.event.inputs.version || github.ref_name }}"
    )


def test_update_matrix_pr_creation_is_non_blocking_when_repo_disallows_actions_prs() -> None:
    """Scheduled matrix refreshes should succeed even if PR creation is policy-blocked.

    The workflow must NOT auto-approve or immediately merge its own PR.
    It may enable auto-merge (which requires branch-protection review),
    but must never bypass human review for release-matrix changes.
    """
    text = _workflow_text("update-matrix.yml")
    assert "continue-on-error: true" in text
    assert "Source: nginx.org download page." in text
    assert "Auto-approve release matrix PR" not in text
    assert 'gh pr review "$PR_NUMBER" --approve' not in text
    assert "Auto-merge release matrix PR" not in text
    assert "Enable auto-merge for release matrix PR" not in text
    assert 'gh pr merge "$PR_NUMBER"' not in text
    assert "Remind maintainer review for release matrix PR" in text
    assert "Matrix update branch pushed, but automatic PR creation is blocked." in text


def test_macos_smoke_retries_once_and_blocks_a_second_failure() -> None:
    """Darwin transport retries must not make repeated E2E failures advisory."""
    workflow = _workflow_data("macos-smoke.yml")
    job = workflow["jobs"]["darwin-native-smoke"]
    step = _step_by_name(
        job["steps"], "Run chunked streaming native smoke validation"
    )
    run = step["run"]

    assert job.get("continue-on-error") != "true"
    assert step.get("continue-on-error") != "true"
    assert "max_attempts=2" in run
    assert "for attempt in 1 2" in run
    assert "retrying once after 10 seconds" in run
    assert "sleep 10" in run
    assert "Darwin native smoke failed on both attempts" in run
    command = "./tools/e2e/verify_chunked_streaming_native_e2e.sh --profile smoke"
    assert run.count(command) == 1
    assert run.rstrip().endswith("exit 1")


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
    assert 'data.get("entries", [])' in resolve_run
    assert '"nginx": e["nginx_version"]' in resolve_run
    assert '"os_type": e["libc"]' in resolve_run
    assert '"amd64": "x86_64"' in resolve_run
    assert '"arm64": "aarch64"' in resolve_run
    assert 'data.get("matrix", [])' not in resolve_run
    assert "nginx.org/en/download.html" in resolve_run
    assert "sorted(set(upstream_versions), key=version_tuple)[-1]" in resolve_run
    assert '"variant": "upstream-upper"' in resolve_run
    assert "upstream_in_matrix = upstream_upper in full_nginx_versions" in resolve_run
    assert '"expected_install_success": upstream_in_matrix,' in resolve_run
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
