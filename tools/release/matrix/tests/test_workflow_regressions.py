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
    """Manual recovery runs package-artifacts only after integrity checks pass."""
    workflow = _workflow_data("release-binaries.yml")
    assert "workflow_dispatch" in workflow["on"]
    package = workflow["jobs"]["package-artifacts"]
    upload = _step_by_name(package["steps"], "Upload workflow artifacts")

    assert "needs.completeness-check.result == 'success'" in package["if"]
    assert "needs.integrity-checksums.result == 'success'" in package["if"]
    assert upload["with"]["if-no-files-found"] == "error"


def test_release_binaries_publishes_signed_checksum_chain() -> None:
    """Binary releases must bind every archive to a signed checksum file."""
    workflow = _workflow_data("release-binaries.yml")
    jobs = workflow["jobs"]
    checksum_job = jobs["integrity-checksums"]
    signing_job = jobs["integrity-signing"]
    publish = jobs["package-artifacts"]
    workflow_text = _workflow_text("release-binaries.yml")

    assert set(checksum_job["needs"]) == {"prepare", "completeness-check"}
    checksum_step = _step_by_name(
        checksum_job["steps"], "Generate and verify SHA256SUMS"
    )
    assert "generate-checksums.sh -d artifacts/" in checksum_step["run"]
    assert "sha256sum --check SHA256SUMS" in checksum_step["run"]
    assert set(signing_job["needs"]) == {"prepare", "integrity-checksums"}
    assert "needs.prepare.outputs.publication_tag != ''" in signing_job["if"]
    assert signing_job["environment"] == "release-signing"
    assert "gpg-sign-checksums.sh" in workflow_text
    assert "binary-checksums-signature" in workflow_text
    assert "SHA256SUMS.asc" in workflow_text
    assert "needs.integrity-checksums.result == 'success'" in publish["if"]
    assert "needs.integrity-signing.result == 'success'" in publish["if"]


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


def test_non_streaming_verifier_changes_trigger_runtime_regressions() -> None:
    """Verifier changes must classify as E2E so the blocking job runs."""
    workflow = _workflow_data("ci.yml")
    changes_steps = workflow["jobs"]["changes"]["steps"]
    filter_step = _step_by_name(changes_steps, "Filter changed paths")
    filters = yaml.load(filter_step["with"]["filters"], Loader=yaml.BaseLoader)

    assert "tools/ci/verify_non_streaming_nginx_module.sh" in filters["e2e"]


def test_release_packages_tag_gate_requires_exact_approved_candidate() -> None:
    """Release tags must match an independently approved protected candidate."""
    workflow = _workflow_data("release-packages.yml")
    workflow_text = _workflow_text("release-packages.yml")
    release_gate = workflow["jobs"]["release-gate"]
    verify_step = _step_by_name(release_gate["steps"], "Verify tag SHA is main CI-approved")

    assert release_gate["environment"] == "release-signing"
    assert 'compare/${TAG_SHA}...${DEFAULT_BRANCH}' in workflow_text
    assert 'branch_relation="$(gh api ' in workflow_text
    assert 'ahead|identical)' in workflow_text
    assert verify_step["env"]["APPROVED_CANDIDATE_SHA"] == (
        "${{ secrets.RELEASE_APPROVED_CANDIDATE_SHA }}"
    )
    assert verify_step["env"]["REF_TYPE"] == "${{ github.ref_type }}"
    assert verify_step["env"]["REF_NAME"] == "${{ github.ref_name }}"
    assert '[[ -z "${APPROVED_CANDIDATE_SHA}"' in verify_step["run"]
    assert '"${TAG_SHA}" != "${APPROVED_CANDIDATE_SHA}"' in verify_step["run"]
    assert "verify_tag_candidate.py" in verify_step["run"]


def test_release_packages_routes_matrix_values_through_step_environment() -> None:
    """Shell run blocks must not interpolate matrix values directly."""
    workflow = _workflow_data("release-packages.yml")

    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run")
            if isinstance(run, str):
                assert "${{ matrix." not in run, (
                    f"{job_name}/{step.get('name', '<unnamed>')} "
                    "interpolates a matrix value inside run"
                )


def test_release_packages_publish_waits_for_official_docker_gate() -> None:
    """Release-blocking Docker validation must be in the canonical DAG."""
    workflow = _workflow_data("release-packages.yml")
    publish_needs = workflow["jobs"]["publish"]["needs"]

    assert "official-docker-release-gate" in publish_needs
    assert "official-docker-release-gate" in workflow["jobs"]
    assert (
        "./.github/workflows/official-nginx-docker.yml"
        in _workflow_text("release-packages.yml")
    )

    official = _workflow_data("official-nginx-docker.yml")
    assert "workflow_call" in official["on"]
    assert "CALLER_MODULE_SHA" in _workflow_text("official-nginx-docker.yml")


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
