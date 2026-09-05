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
    assert 'canonical_arch(e["target"])' in resolve_step["run"]
    assert 'data.get("matrix", [])' not in resolve_step["run"]
    assert 'support_tier") == "full"' not in resolve_step["run"]
    assert step_names.index("Validate release matrix consumers") < step_names.index(
        "Extract build matrix from release-matrix.json"
    )


def test_release_packages_normalizes_matrix_aliases_before_projection() -> None:
    """Package matrices must not read compatibility aliases directly."""
    workflow = _workflow_data("release-packages.yml")
    steps = workflow["jobs"]["prepare"]["steps"]
    resolve_step = _step_by_name(steps, "Resolve matrix from release-matrix.json")
    run = resolve_step["run"]

    assert "normalize_compatibility_document(json.load(f))" in run
    assert "canonical_arch" in run
    assert 'package_arch(e["target"])' in run
    source_load = run.split(
        "with open(\"tools/release-matrix.json\", \"r\") as f:", 1
    )[1].split("owner =", 1)[0]
    assert 'e["arch"]' not in source_load


def test_release_binaries_workflow_dispatch_can_publish_tag_assets() -> None:
    """Manual recovery publishes only artifacts that passed integrity checks."""
    workflow = _workflow_data("release-binaries.yml")
    assert "workflow_dispatch" in workflow["on"]
    package = workflow["jobs"]["package-artifacts"]
    upload = _step_by_name(package["steps"], "Upload workflow artifacts")

    assert set(package["needs"]) == {
        "prepare",
        "completeness-check",
        "integrity-checksums",
    }
    assert "if" not in package
    assert upload["with"]["if-no-files-found"] == "error"


def test_release_binaries_delegates_signed_checksum_chain_to_release_packages() -> None:
    """Only the canonical package workflow handles release signing."""
    workflow = _workflow_data("release-binaries.yml")
    jobs = workflow["jobs"]
    checksum_job = jobs["integrity-checksums"]
    publish = jobs["package-artifacts"]
    workflow_text = _workflow_text("release-binaries.yml")

    assert set(checksum_job["needs"]) == {"prepare", "completeness-check"}
    checksum_step = _step_by_name(
        checksum_job["steps"], "Generate and verify SHA256SUMS"
    )
    assert "generate-checksums.sh -d artifacts/" in checksum_step["run"]
    assert "sha256sum --check SHA256SUMS" in checksum_step["run"]
    assert "integrity-signing" not in jobs
    assert "gpg-sign-checksums.sh" not in workflow_text
    assert "SHA256SUMS.asc" not in workflow_text
    assert set(publish["needs"]) == {
        "prepare",
        "completeness-check",
        "integrity-checksums",
    }
    release_packages = _workflow_text("release-packages.yml")
    assert "integrity-signing:" in release_packages
    assert "gpg-sign-checksums.sh" in release_packages


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


def test_cflite_pr_sarif_job_has_artifact_when_fuzz_finds_no_crashes() -> None:
    """The trusted SARIF job must not fail on a clean fuzz run."""
    workflow_text = _workflow_text("cflite_pr.yml")
    workflow = _workflow_data("cflite_pr.yml")

    fuzz_steps = workflow["jobs"]["pr-fuzz"]["steps"]
    ensure_sarif = _step_by_name(fuzz_steps, "Ensure SARIF artifact exists")

    assert ensure_sarif["if"] == "always()"
    assert "pr-fuzz-empty.sarif" in ensure_sarif["run"]
    assert "name: pr-fuzz-out" in workflow_text
    assert "  pr-fuzz-sarif:" in workflow_text


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


def test_official_docker_failure_artifacts_use_safe_matrix_tags() -> None:
    """Failure artifact names must not contain slash-delimited row IDs."""
    workflow_text = _workflow_text("official-nginx-docker.yml")

    assert (
        "name: official-nginx-docker-${{ matrix.docker_tag }}-failure"
        in workflow_text
    )
    assert (
        "name: official-nginx-docker-${{ matrix.matrix_row_id }}-failure"
        not in workflow_text
    )


def test_official_docker_verifier_sanitizes_container_names() -> None:
    """Container names must not inherit the colon from an image reference."""
    repo_root = Path(__file__).resolve().parents[4]
    verifier = (repo_root / "tools" / "ci" / "verify_official_nginx_docker.sh").read_text(
        encoding="utf-8"
    )

    assert "tr -c '[:alnum:]._-' '-'" in verifier
    assert "tr -c '[:alnum:].:_-' '-'" not in verifier


def test_official_docker_runtime_installs_module_runtime_libraries() -> None:
    """The runtime image must carry libraries needed by the built module."""
    repo_root = Path(__file__).resolve().parents[4]
    dockerfile = (
        repo_root / "examples" / "docker" / "Dockerfile.official-nginx-source-build"
    ).read_text(encoding="utf-8")

    assert "libgcc-s1" in dockerfile
    assert "apk add --no-cache libgcc" in dockerfile


def test_macos_smoke_retries_once_and_blocks_a_second_failure() -> None:
    """Ensure macOS native smoke validation retries once and fails after a second unsuccessful attempt."""
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
    support_step = steps["Determine JS actions support"]
    assert support_step["env"]["MATRIX_SUPPORTED"] == (
        "${{ !(matrix.target.pkg_manager == 'apk' && "
        "matrix.target.arch == 'aarch64') }}"
    )
    assert support_step["run"] == (
        'echo "supported=${MATRIX_SUPPORTED}" >> "$GITHUB_OUTPUT"\n'
    )
    assert "matrix." not in support_step["run"]

    resolve_step = _step_by_name(
        workflow["jobs"]["resolve-matrix"]["steps"],
        "Select representative matrix entries",
    )
    assert resolve_step["env"]["REQUESTED_VERSION"] == (
        "${{ github.event.inputs.version || '' }}"
    )
    resolve_run = resolve_step["run"]
    assert 'data.get("entries", [])' in resolve_run
    assert '"nginx": e["nginx_version"]' in resolve_run
    assert '"os_type": e["libc"]' in resolve_run
    assert '"amd64": "x86_64"' in resolve_run
    assert '"arm64": "aarch64"' in resolve_run
    assert 'data.get("matrix", [])' not in resolve_run
    assert "from urllib.request import Request, urlopen" in resolve_run
    assert "releases/latest" in resolve_run
    assert "release_assets" in resolve_run
    assert "has_signed_manifest" in resolve_run
    assert "signed_asset_names" in resolve_run
    assert 'parts[1].lstrip("*")' in resolve_run
    assert '"expected_error_category": expected_error_category' in resolve_run
    assert 'in release_assets' in resolve_run
    assert "nginx.org/en/download.html" in resolve_run
    assert "sorted(set(upstream_versions), key=version_tuple)[-1]" in resolve_run
    assert '"variant": "upstream-upper"' in resolve_run
    assert "upstream_in_matrix" not in resolve_run
    assert '"expected_install_success": upstream_in_matrix,' not in resolve_run
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
    validate_step = _step_by_name(
        workflow["jobs"]["install-verify"]["steps"],
        "Validate install outcome against target expectation",
    )
    assert "EXPECTED_ERROR_CATEGORY" in validate_step["env"]
    assert '"${ERROR_CATEGORY}" != "${EXPECTED_ERROR_CATEGORY}"' in validate_step[
        "run"
    ]
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


def test_install_paths_provide_installer_runtime_dependencies() -> None:
    """Installer execution paths must provide awk and release-signature tools."""
    workflow_text = _workflow_text("install-verify.yml")
    assert "apk add --no-cache bash curl gawk gnupg python3" in workflow_text
    assert "apt-get install -y -qq curl gawk gnupg2 python3" in workflow_text

    repo_root = Path(__file__).resolve().parents[4]
    installer = (repo_root / "tools" / "install.sh").read_text(encoding="utf-8")
    assert "bootstrap_system_tool()" in installer
    assert '"/bin/${name}"' in installer
    assert "printf '%s\\n' \"$candidate\"" in installer
    assert '"$stat_bin" -L -f' in installer
    assert '"$stat_bin" -L -c' in installer
    assert 'write_embedded_release_key "$TMP_DIR/release-key.asc"' in installer
    assert 'releases/download/${RELEASE_TAG}/nginx-markdown-for-agents-release.asc' not in installer
    assert 'validate_privileged_destination "$NGINX_PREFIX" "NGINX prefix"' not in installer
    assert (
        'validate_privileged_destination "$NGINX_MODULES_PATH" "NGINX modules path"'
        in installer
    )
    assert (
        'validate_privileged_destination "$NGINX_CONF_PATH" "NGINX configuration"'
        in installer
    )

    key_start = installer.index("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    key_end = installer.index("-----END PGP PUBLIC KEY BLOCK-----", key_start)
    embedded_key = installer[key_start : key_end + len("-----END PGP PUBLIC KEY BLOCK-----")]
    packaging_key = (
        repo_root / "packaging" / "nginx-markdown-for-agents-release.asc"
    ).read_text(encoding="utf-8").rstrip("\n")
    assert embedded_key == packaging_key

    example = (
        repo_root / "tools" / "build_release" / "Dockerfile.install-example"
    ).read_text(encoding="utf-8")
    assert "    gawk \\\n" in example
    assert "    gnupg \\\n" in example

    workflow_test = (repo_root / "tools" / "test_install_workflow.sh").read_text(
        encoding="utf-8"
    )
    assert "apt-get install -y --no-install-recommends curl gawk python3" in workflow_test
