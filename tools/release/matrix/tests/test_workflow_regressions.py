"""Regression guards for release-related GitHub workflows."""

from __future__ import annotations

import os
import re
import subprocess
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
    assert "Automatic PR creation for the matrix update did not complete." in text
    assert "auto/update-matrix" in text


def test_update_matrix_installs_dependencies_before_projection() -> None:
    """The matrix job must install its schema-validation dependencies."""
    steps = _workflow_data("update-matrix.yml")["jobs"]["update-matrix"]["steps"]
    install = _step_by_name(steps, "Install matrix validation dependencies")
    projection = _step_by_name(steps, "Generate release-contract matrix projection")
    repo_root = Path(__file__).resolve().parents[4]
    release_requirements = (repo_root / "requirements-release.txt").read_text(
        encoding="utf-8"
    )
    pins = {
        line.strip().split("==", 1)[0].lower(): line.strip().split("==", 1)[1]
        for line in release_requirements.splitlines()
        if "==" in line
    }

    assert install["run"] == (
        "python3 -m pip install "
        f"'jsonschema[format]=={pins['jsonschema[format]']}' "
        f"PyYAML=={pins['pyyaml']}"
    )
    assert steps.index(install) < steps.index(projection)


def test_weekly_observation_provisions_fuzz_and_rust_toolchains() -> None:
    """Weekly jobs must pin compatible cargo-fuzz and install Rust components."""
    jobs = _workflow_data("weekly-observation.yml")["jobs"]
    fuzz_job = jobs["weekly-fuzz"]
    assert fuzz_job["env"]["CARGO_FUZZ_VERSION"] == "0.13.2"
    install = _step_by_name(fuzz_job["steps"], "Install cargo-fuzz")
    assert "--version \"${CARGO_FUZZ_VERSION}\" --locked" in install["run"]

    soak_setup = _step_by_name(jobs["weekly-soak"]["steps"], "Set up Rust toolchain")
    assert soak_setup["with"]["components"] == "rustfmt,clippy"


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
    prepare_output = _step_by_name(fuzz_steps, "Prepare non-root fuzz output")
    prepare_sarif = _step_by_name(fuzz_steps, "Prepare non-root SARIF output")
    ensure_sarif = _step_by_name(fuzz_steps, "Ensure SARIF artifact exists")

    assert prepare_output["run"] == "mkdir -p build-out"
    assert "mkdir -p out" in prepare_sarif["run"]
    assert "chmod 0777 out" in prepare_sarif["run"]
    run_fuzzers = next(
        step for step in fuzz_steps
        if step.get("uses", "").endswith(
            "/run_fuzzers@884713a6c30a92e5e8544c39945cd7cb630abcd1"
        )
    )
    assert run_fuzzers["with"]["output-sarif"] == "true"
    assert ensure_sarif["if"] == "always()"
    assert "pr-fuzz-empty.sarif" in ensure_sarif["run"]
    assert "printf '%s\\n'" in ensure_sarif["run"]
    assert "EOF" not in ensure_sarif["run"]
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


def test_release_build_bundles_pinned_brotli_decoder() -> None:
    """Official Linux builds must bundle the fixed Brotli decoder.

    AlmaLinux 9 and Alpine 3.20 carry no fixed Brotli package for
    CVE-2026-111497, so a dynamic link would ship the vulnerable host copy
    to those targets.
    """
    text = _workflow_text("release-packages.yml")
    repo_root = Path(__file__).resolve().parents[4]
    checksums = (repo_root / "packaging" / "checksums.sha256").read_text(
        encoding="utf-8"
    )
    versions = re.findall(r"^\S+\s+brotli-(\S+)$", checksums, re.MULTILINE)
    assert versions, "packaging/checksums.sha256 must pin the Brotli archive"
    assert len(set(versions)) == 1, f"multiple Brotli pins: {versions}"
    brotli_version = versions[0]

    script = (
        repo_root / "packaging" / "scripts" / "build-brotli-static.sh"
    ).read_text(encoding="utf-8")
    assert f'VERSION="{brotli_version}"' in script

    workflow = _workflow_data("release-packages.yml")
    steps = workflow["jobs"]["build"]["steps"]
    install = _step_by_name(steps, "Install build dependencies")["run"]
    assert re.search(r"^\s+cmake \\$", install, re.MULTILINE) is not None
    assert "brotli-devel" not in install
    assert "./packaging/scripts/build-brotli-static.sh" in text
    assert "PKG_CONFIG_PATH: /usr/local/lib/pkgconfig" in text
    assert (
        '--with-ld-opt="-Wl,--exclude-libs,'
        'libbrotlidec.a:libbrotlicommon.a"' in text
    )
    # The ALL form would also hide the Rust FFI exports the gate verifies.
    assert "--exclude-libs,ALL" not in text
    assert "NEEDED.*libbrotli(dec|common)" in text
    assert '$5 == "LOCAL"' in text


def test_release_dockerfiles_bundle_pinned_brotli_decoder() -> None:
    """The local release image paths must bundle the decoder too."""
    repo_root = Path(__file__).resolve().parents[4]
    for name in ("Dockerfile.glibc", "Dockerfile.musl"):
        dockerfile = (
            repo_root / "tools" / "build_release" / name
        ).read_text(encoding="utf-8")
        assert (
            "COPY packaging/scripts/build-brotli-static.sh "
            "/src/packaging/scripts/build-brotli-static.sh" in dockerfile
        ), name
        assert "/src/packaging/scripts/build-brotli-static.sh\n" in dockerfile, name
        assert "PKG_CONFIG_PATH=/usr/local/lib/pkgconfig" in dockerfile, name
        assert (
            "--with-ld-opt=\"-Wl,--exclude-libs,"
            "libbrotlidec.a:libbrotlicommon.a\"" in dockerfile
        ), name
        assert "brotli-devel" not in dockerfile, name
        assert "brotli-dev" not in dockerfile, name


def test_ubuntu_native_build_embeds_the_release_identity() -> None:
    """A published distro artifact must not carry the development identity."""
    workflow = _workflow_data("release-packages.yml")
    steps = workflow["jobs"]["ubuntu-native"]["steps"]
    build = _step_by_name(steps, "Build module against the Ubuntu NGINX package")
    assert "--release" in build["run"]

    repo_root = Path(__file__).resolve().parents[4]
    script = (
        repo_root / "packaging" / "scripts" / "build-ubuntu-native-module.sh"
    ).read_text(encoding="utf-8")
    for variable in (
        "NGX_MARKDOWN_BUILD_KIND=release",
        "NGX_MARKDOWN_SOURCE_SHA",
        "NGX_MARKDOWN_RUST_VERSION",
        "NGX_MARKDOWN_FEATURE_MANIFEST_DIGEST",
    ):
        assert variable in script, variable


def test_ubuntu_native_build_requires_a_dynamic_decoder() -> None:
    """A missing decoder must fail the build, not degrade the artifact."""
    repo_root = Path(__file__).resolve().parents[4]
    script = (
        repo_root / "packaging" / "scripts" / "build-ubuntu-native-module.sh"
    ).read_text(encoding="utf-8")
    assert "export NGX_MARKDOWN_BROTLI_STREAMING=on" in script
    assert "BROTLI_LINKAGE" in script
    assert "refusing a degraded release artifact" in script

    # The decoder entry point lives in libbrotlidec.  A module that links only
    # libbrotlicommon cannot decode.  Read the predicate that actually runs:
    # a commented-out or copied line elsewhere in the script must not satisfy
    # this check.
    active = [
        line.strip()
        for line in script.splitlines()
        if "readelf -d" in line and not line.strip().startswith("#")
    ]
    assert len(active) == 1, f"expected one active readelf check, found {active}"
    predicate = active[0]
    assert "NEEDED.*libbrotlidec\\.so" in predicate
    assert "libbrotlicommon" not in predicate
    assert "libbrotli(dec|common)" not in predicate

    verify = (
        repo_root / "packaging" / "scripts" / "verify-ubuntu-native-module.sh"
    ).read_text(encoding="utf-8")
    assert '"brotli_version"' in verify
    assert '"brotli_linkage"' in verify


def test_ubuntu_package_compares_the_full_libbrotli_version() -> None:
    """Stripping the epoch changes the meaning of the security floor check."""
    text = _workflow_text("release-packages.yml")
    assert "sed 's/^[0-9]*://'" not in text
    assert "BROTLI_MIN_VERSION=\"$(dpkg-query -W -f='${Version}' libbrotli1)\"" in text
    assert 'dpkg --compare-versions "${BROTLI_MIN_VERSION}" ge "${BROTLI_FIXED_FLOOR}"' in text


def test_package_metadata_drops_host_brotli_dependency() -> None:
    """Packages must not require a host Brotli library they no longer link."""
    repo_root = Path(__file__).resolve().parents[4]
    nfpm = (repo_root / "packaging" / "nfpm" / "nfpm.yaml").read_text(
        encoding="utf-8"
    )
    assert "libbrotli1" not in nfpm
    assert '"libbrotli"' not in nfpm

    checksums = (repo_root / "packaging" / "checksums.sha256").read_text(
        encoding="utf-8"
    )
    brotli_sha = re.search(
        r"^([0-9a-f]{64})\s+brotli-1\.2\.1$", checksums, re.MULTILINE
    )
    assert brotli_sha is not None, "checksums must pin brotli-1.2.1"

    formula = (
        repo_root
        / "packaging"
        / "homebrew"
        / "nginx-markdown-module.rb"
    ).read_text(encoding="utf-8")
    # Homebrew rejects arbitrary version floors on depends_on, so the formula
    # vendors the fixed decoder as a resource and links it statically.
    assert 'depends_on "brotli"' not in formula
    assert 'resource "brotli" do' in formula
    assert "brotli/archive/refs/tags/v1.2.1.tar.gz" in formula
    assert f'sha256 "{brotli_sha.group(1)}"' in formula
    # The install flow reads the version from the vendored pkg-config file the
    # link uses, so a downgraded resource cannot install silently.
    assert "lib/pkgconfig/libbrotlidec.pc" in formula
    assert 'Version.new(brotli_version) < Version.new("1.2.1")' in formula
    assert 'refute_match "libbrotlidec"' in formula


_FAKE_NGINX = """#!/usr/bin/env bash
# Stand-in for the nginx.org binary: records every call.
printf '%s\\n' "$*" >>"${FAKE_LOG}"
case "$*" in
    -v) echo 'nginx version: nginx/1.30.6' >&2 ;;
    -V) echo 'configure arguments: --with-compat' >&2 ;;
    *-t) ;;
    *'-s quit') rm -f "${4}/logs/nginx.pid" ;;
    *-s*) ;;
    *) echo 1 >"${4}/logs/nginx.pid" ;;
esac
exit 0
"""

# The readiness probe succeeds; the first request fails like a reset
# connection, which set -e turns into an early exit.
_FAKE_CURL = """#!/usr/bin/env bash
case "$*" in
    -fsS*) exit 0 ;;
    *) exit 7 ;;
esac
"""


def test_nginx_org_verifier_cleans_up_after_an_early_exit(tmp_path) -> None:
    """A failed request must not leave the started NGINX or its prefix."""
    repo_root = Path(__file__).resolve().parents[4]
    script = repo_root / "packaging" / "scripts" / "verify-nginx-org-module.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("nginx", _FAKE_NGINX), ("curl", _FAKE_CURL)):
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    module = tmp_path / "module.so"
    module.write_bytes(b"\0")
    log = tmp_path / "nginx-calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(work),
        "FAKE_LOG": str(log),
    }

    result = subprocess.run(
        [
            "bash",
            str(script),
            "--module",
            str(module),
            "--nginx-version",
            "1.30.6",
            "--arch",
            "amd64",
            "--out",
            str(tmp_path / "ledger.json"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode != 0
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(call.endswith("-s quit") for call in calls), calls
    assert list(work.iterdir()) == []
    assert not (tmp_path / "ledger.json").exists()


# ---------------------------------------------------------------------------
# Candidate-bound CI compatibility evidence (PR 309, issue 310).
# ---------------------------------------------------------------------------

def _job_steps(workflow: dict, job: str) -> list[dict]:
    return list(workflow["jobs"][job].get("steps", []))


def _job_text(workflow: dict, job: str) -> str:
    return yaml.safe_dump(workflow["jobs"][job], sort_keys=False)


def _needs(workflow: dict, job: str) -> set[str]:
    needs = workflow["jobs"][job].get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _evidence_uploads(workflow: dict) -> list[tuple[str, dict]]:
    uploads = []
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            name = str((step.get("with") or {}).get("name", ""))
            if str(step.get("uses", "")).startswith("actions/upload-artifact") and (
                name.startswith("compat-evidence-")
            ):
                uploads.append((job_name, step))
    return uploads


def test_release_gate_validates_only_this_runs_staged_ci_records() -> None:
    workflow = _workflow_data("release-packages.yml")
    needs = _needs(workflow, "release-gate")
    assert {
        "smoke-test",
        "ubuntu-native",
        "nginx-org-evidence",
        "official-docker-release-gate",
    } <= needs
    steps = _job_steps(workflow, "release-gate")
    names = [step.get("name") for step in steps]
    download = _step_by_name(steps, "Download CI compatibility evidence")
    stage = _step_by_name(steps, "Stage CI compatibility evidence")
    gates = _step_by_name(steps, "Run release gates")
    assert download["with"]["pattern"] == "compat-evidence-*"
    assert "merge-multiple" not in download["with"]
    assert "stage_compatibility_evidence.py" in stage["run"]
    assert (
        names.index("Download CI compatibility evidence")
        < names.index("Stage CI compatibility evidence")
        < names.index("Run release gates")
    )
    run = gates["run"]
    start = run.index("EVIDENCE_ARGS=(")
    block = run[start : run.index("validate_compatibility_evidence.py", start)]
    unconditional, _, conditional = block.partition("if [[")
    for flag in (
        '--evidence-dir "${RUNNER_TEMP}/compat-evidence"',
        "--require-ci",
        '--candidate-sha "${GITHUB_SHA}"',
        '--workflow-run-url "${RUN_URL}"',
    ):
        assert flag in unconditional, flag
    assert "--require-blocking" in conditional
    assert "--require-blocking" not in unconditional
    assert '"${GITHUB_REF_TYPE:-}" == "tag"' in conditional
    assert '"${REQUIRE_BLOCKING_INPUT:-false}" == "true"' in conditional
    assert "docs/evidence" not in block
    assert gates["env"]["RUN_URL"].endswith("/actions/runs/${{ github.run_id }}")


def test_require_blocking_dispatch_input_is_routed_through_env() -> None:
    workflow = _workflow_data("release-packages.yml")
    spec = workflow["on"]["workflow_dispatch"]["inputs"]["require_blocking"]
    assert spec["type"] == "boolean"
    assert spec["default"] == "false"
    gates = _step_by_name(_job_steps(workflow, "release-gate"), "Run release gates")
    assert gates["env"]["REQUIRE_BLOCKING_INPUT"] == "${{ inputs.require_blocking }}"
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            assert "${{ inputs.require_blocking" not in str(step.get("run", ""))


def test_nginx_org_evidence_job_produces_bound_ci_records() -> None:
    workflow = _workflow_data("release-packages.yml")
    job = workflow["jobs"]["nginx-org-evidence"]
    assert {"prepare", "build", "musl-build"} <= _needs(workflow, "nginx-org-evidence")
    assert job["permissions"] == {"contents": "read"}
    text = _job_text(workflow, "nginx-org-evidence")
    assert "secrets." not in text
    assert "nginx_org_evidence_matrix" in job["strategy"]["matrix"]["include"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "verify-nginx-org-module.sh" in runs
    assert "record_nginx_org_evidence.py" in runs
    for flag in (
        "ci:release-packages/nginx-org-evidence",
        '--source-sha "${CANDIDATE_SHA}"',
        '--workflow-run-url "${RUN_URL}"',
        "--execution native",
    ):
        assert flag in runs, flag
    upload = [s for j, s in _evidence_uploads(workflow) if j == "nginx-org-evidence"]
    assert len(upload) == 1
    name = upload[0]["with"]["name"]
    assert name.startswith("compat-evidence-nginx-org-")
    assert "matrix.libc" in name
    assert "matrix.arch" in name
    assert "matrix.nginx_version" in name


def test_ubuntu_record_has_a_unique_name_and_its_own_artifact() -> None:
    workflow = _workflow_data("release-packages.yml")
    steps = _job_steps(workflow, "ubuntu-native")
    record = _step_by_name(steps, "Write compatibility evidence record")["run"]
    assert (
        '--out "dist/compat-evidence/ubuntu-${suite_slug}-'
        '${MATRIX_NGINX_VERSION}-${MATRIX_ARCH}.json"'
    ) in record
    package = _step_by_name(steps, "Upload Ubuntu-native package")
    assert ".json" not in package["with"]["path"]
    evidence = _step_by_name(steps, "Upload Ubuntu-native compatibility evidence")
    assert evidence["with"]["name"].startswith("compat-evidence-ubuntu-")


def test_smoke_test_writes_package_row_evidence_when_selected() -> None:
    workflow = _workflow_data("release-packages.yml")
    steps = _job_steps(workflow, "smoke-test")
    step = _step_by_name(steps, "Write nginx.org package-row evidence")
    assert step["if"] == "matrix.evidence_distro != ''"
    assert '--distro "${EVIDENCE_DISTRO}"' in step["run"]
    assert "verify-nginx-org-module.sh" in step["run"]
    upload = _step_by_name(steps, "Upload nginx.org package-row evidence")
    assert upload["if"] == "matrix.evidence_distro != ''"
    assert upload["with"]["name"].startswith("compat-evidence-pkg-")


def test_every_evidence_upload_name_carries_its_matrix_identity() -> None:
    expected = {
        ("release-packages.yml", "nginx-org-evidence"): (
            "matrix.nginx_version", "matrix.libc", "matrix.arch"
        ),
        ("release-packages.yml", "ubuntu-native"): (
            "matrix.os", "matrix.nginx_version", "matrix.arch"
        ),
        ("release-packages.yml", "smoke-test"): (
            "matrix.evidence_distro", "matrix.nginx_version", "matrix.arch"
        ),
        ("official-nginx-docker.yml", "build-and-verify"): (
            "matrix.nginx_version", "matrix.os", "matrix.libc", "matrix.arch"
        ),
    }
    seen = set()
    for workflow_name in ("release-packages.yml", "official-nginx-docker.yml"):
        for job, step in _evidence_uploads(_workflow_data(workflow_name)):
            keys = expected[(workflow_name, job)]
            for key in keys:
                assert key in step["with"]["name"], (workflow_name, job, key)
            assert step["with"]["if-no-files-found"] == "error"
            seen.add((workflow_name, job))
    assert seen == set(expected)


def _run_prepare(tmp_path) -> dict[str, str]:
    import json

    repo_root = Path(__file__).resolve().parents[4]
    workflow = _workflow_data("release-packages.yml")
    resolve = [s for s in _job_steps(workflow, "prepare") if s.get("id") == "resolve"][0]
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(output),
        "RELEASE_VERSION": "0.9.3",
        "PYTHONPATH": str(repo_root),
    }
    result = subprocess.run(
        ["bash", "-c", resolve["run"]],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(
        line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
    )
    return {key: json.loads(value) for key, value in values.items() if key.endswith("matrix")}


def test_prepare_emits_one_evidence_leg_per_feasible_blocking_row(tmp_path) -> None:
    import json

    matrices = _run_prepare(tmp_path)
    module_legs = matrices["nginx_org_evidence_matrix"]
    package_legs = [e for e in matrices["smoke_matrix"] if e["evidence_distro"]]
    ubuntu_legs = matrices["ubuntu_matrix"]
    assert len(module_legs) == 24
    assert len(package_legs) == 16
    assert len(ubuntu_legs) == 4
    names = (
        [f"nginx.org-{e['nginx_version']}-{e['libc']}-{e['arch']}.json" for e in module_legs]
        + [
            f"nginx.org-{e['nginx_version']}-{e['evidence_distro']}-{e['arch']}.json"
            for e in package_legs
        ]
        + [
            f"ubuntu-{e['suite'].replace('.', '')}-{e['nginx_version']}-{e['arch']}.json"
            for e in ubuntu_legs
        ]
    )
    assert len(names) == len(set(names))

    # Every release-blocking row has a producer: the 44 rows above plus the
    # 4 container rows that official-nginx-docker.yml covers.
    repo_root = Path(__file__).resolve().parents[4]
    matrix = json.loads((repo_root / "tools" / "release-matrix.json").read_text())
    blocking = [e for e in matrix["entries"] if e.get("release_blocking") is True]
    containers = [e for e in blocking if e.get("artifact_type") == "docker-image"]
    assert len(blocking) == len(names) + len(containers)
    assert len(containers) == 4


def _dag_has_cycle(workflow: dict) -> bool:
    graph = {name: _needs(workflow, name) for name in workflow["jobs"]}
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(dep) for dep in graph.get(node, ())):
            return True
        state[node] = 2
        return False

    return any(visit(node) for node in graph)


def _ancestors(workflow: dict, job: str) -> set[str]:
    seen: set[str] = set()
    pending = list(_needs(workflow, job))
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(_needs(workflow, node))
    return seen


def test_container_evidence_producer_runs_before_the_release_gate() -> None:
    """The container producer feeds release-gate, so it cannot depend on it."""
    workflow = _workflow_data("release-packages.yml")
    assert not _dag_has_cycle(workflow)
    assert "release-gate" not in _ancestors(workflow, "official-docker-release-gate")
    assert "official-docker-release-gate" in _ancestors(workflow, "release-gate")
    # Publication still waits for both gates.
    publish = _ancestors(workflow, "publish")
    assert {"release-gate", "official-docker-release-gate"} <= publish
    official = _workflow_data("official-nginx-docker.yml")
    steps = official["jobs"]["build-and-verify"]["steps"]
    evidence = _step_by_name(steps, "Write container compatibility evidence")["run"]
    assert "record_container_evidence.py" in evidence
    assert '--image-digest "${IMAGE_DIGEST}"' in evidence
    assert '--built-image-id "${built_image_id}"' in evidence
    assert '--source-sha "${MODULE_SHA}"' in evidence
    assert '--workflow-run-url "${RUN_URL}"' in evidence
    assert "verify-nginx-org-module.sh" in evidence


def test_official_docker_build_retries_only_through_the_helper() -> None:
    official = _workflow_data("official-nginx-docker.yml")
    steps = official["jobs"]["build-and-verify"]["steps"]
    build = _step_by_name(steps, "Build from source on official nginx base")
    assert "uses" not in build
    assert "bash tools/ci/retry_transient.sh -- docker buildx build" in build["run"]
    assert '--label "org.opencontainers.image.revision=${MODULE_SHA}"' in build["run"]
    assert '--build-arg "NGINX_IMAGE=${BASE_NAME}@${BASE_DIGEST}"' in build["run"]
    # The functional check and the verifier are never retried.
    verify = _step_by_name(steps, "Verify runtime behavior")
    assert "retry_transient" not in verify["run"]


def test_retry_helper_never_wraps_verifiers_or_tests() -> None:
    forbidden = (
        "verify-nginx-org-module.sh",
        "verify_official_nginx_docker.sh",
        "smoke-test-basic.sh",
        "pytest",
        "make test",
        "cargo test",
        "cargo build",
    )
    for name in ("release-packages.yml", "official-nginx-docker.yml", "ci.yml"):
        for line in _workflow_text(name).splitlines():
            if "retry_transient.sh --" not in line:
                continue
            wrapped = line.split("retry_transient.sh --", 1)[1]
            for word in forbidden:
                assert word not in wrapped, (name, line.strip())


def test_glibc_verification_host_is_the_build_baseline_image() -> None:
    """The glibc host must stay the pinned build image (oldest supported glibc).

    The official nginx:1.24.0 image is Debian 11 (glibc 2.31), which cannot
    load a module built on AlmaLinux 9, so the host must not drift from the
    build container when that digest is bumped.
    """
    workflow = _workflow_data("release-packages.yml")
    build_image = workflow["jobs"]["build"]["container"]
    assert build_image.startswith("almalinux@sha256:")
    host = _step_by_name(
        _job_steps(workflow, "nginx-org-evidence"),
        "Start the nginx.org verification host",
    )["run"]
    assert f'image="{build_image}"' in host
    assert 'image="nginx:${NGINX_VERSION}-alpine"' in host
