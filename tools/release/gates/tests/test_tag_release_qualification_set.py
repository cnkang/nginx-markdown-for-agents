"""Tests for the tag release blocking-qualification set.

The tag release path (``.github/workflows/release-packages.yml``) must run the
same candidate-bound qualification stages as the Makefile
``release-gates-check-092`` target's [9/14]-[13/14] stages.  These tests pin
the two surfaces together so they cannot drift.
"""

from __future__ import annotations

import re
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"
MAKEFILE = REPO_ROOT / "Makefile"

# Makefile stage name -> validator module that the stage invokes.  Must stay
# in lockstep with release-gates-check-092 stages [9/14]-[13/14].
QUALIFICATION_STAGES = {
    "release-candidate-evidence-check": "validate_release_candidate_evidence.py",
    "artifact-registry-check": "validate_artifact_registry.py",
    "release-evidence-manifest-check": "validate_release_evidence_manifest.py",
    "test-rust-fuzz-qualification": "validate_fuzz_qualification.py",
    "test-e2e-rust-soak": "validate_soak_qualification.py",
}

STREAMING_EVIDENCE_VALIDATOR = "validate_streaming_evidence.py"
FUZZ_QUALIFICATION_STAGE = "test-rust-fuzz-qualification"
TAG_RULESET_VALIDATOR = "verify_tag_ref_protection.py"


def _job_body(job: str) -> str:
    """Slice one top-level job's body, ending at the next job marker.

    A fixed end marker (previously ''integrity-checksums:'') breaks as soon
    as a job is inserted between the two names: the slice then swallows
    later jobs and the assertions in this file can pass against text that
    belongs to a different job.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.find(f"  {job}:")
    if start == -1:
        raise AssertionError(f"{WORKFLOW.name}: missing '  {job}:' job marker")
    rest = text[start:]
    next_job = re.search(r"\n  [A-Za-z0-9_-]+:$", rest, flags=re.MULTILINE)
    if next_job is not None:
        rest = rest[: next_job.start()]
    return rest


def _workflow_release_gate_body() -> str:
    return _job_body("release-gate")


def _workflow_fuzz_job_body() -> str:
    return _job_body("fuzz-qualification")


def _workflow_publish_body() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.find("  publish:")
    if start == -1:
        raise AssertionError(
            f"{WORKFLOW.name}: missing '  publish:' job marker"
        )
    rest = text[start:]
    # Stop at the next top-level job marker so a later job cannot leak
    # into the publish job's own configuration.  MULTILINE makes `$`
    # match the end of any line, not only the end of the whole text.
    next_job = re.search(r"\n  [A-Za-z0-9_-]+:$", rest, flags=re.MULTILINE)
    if next_job is not None:
        rest = rest[: next_job.start()]
    return rest


def _makefile_092_target() -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.find("release-gates-check-092: release-gates-check-092-canonical")
    if start == -1:
        raise AssertionError(
            f"{MAKEFILE.name}: missing 'release-gates-check-092: "
            "release-gates-check-092-canonical' target line"
        )
    end = text.find("\nrelease-matrix-check:", start)
    if end == -1:
        raise AssertionError(
            f"{MAKEFILE.name}: missing 'release-matrix-check:' target "
            "after release-gates-check-092"
        )
    return text[start:end]


def test_release_gate_job_runs_all_qualification_validators() -> None:
    body = _workflow_release_gate_body()
    for stage, validator in QUALIFICATION_STAGES.items():
        if stage == FUZZ_QUALIFICATION_STAGE:
            # The fuzz soak owns a dedicated job; it must not leak back
            # into the release gate, and it must run there.
            assert validator not in body, (
                f"release-gate job must not run {validator} (Makefile "
                f"stage '{stage}'); it belongs to the fuzz job"
            )
            assert validator in _workflow_fuzz_job_body(), (
                f"fuzz-qualification job must run {validator} (Makefile "
                f"stage '{stage}')"
            )
            continue
        assert validator in body, (
            f"release-gate job must run {validator} (Makefile stage "
            f"'{stage}'); the tag release qualification set has drifted "
            "from release-gates-check-092"
        )


def test_makefile_092_runs_all_qualification_stages() -> None:
    target = _makefile_092_target()
    for stage in QUALIFICATION_STAGES:
        assert f"$(MAKE) {stage}" in target, (
            f"release-gates-check-092 must invoke '{stage}' as a blocking stage"
        )


def test_release_gate_runs_the_shared_contract_validators() -> None:
    """Tag CI must enforce the tag and streaming contracts directly."""
    body = _workflow_release_gate_body()
    assert TAG_RULESET_VALIDATOR in body
    assert '--repo "${GITHUB_REPOSITORY}"' in body
    assert STREAMING_EVIDENCE_VALIDATOR in body


def test_makefile_092_runs_the_shared_contract_validators() -> None:
    """The local blocking target must not omit either contract validator."""
    target = _makefile_092_target()
    assert TAG_RULESET_VALIDATOR in target
    assert "$(MAKE) streaming-evidence-check" in target


def test_publish_hard_depends_on_release_gate() -> None:
    body = _workflow_publish_body()
    # The qualification validators run inside release-gate, so publish's
    # success condition on release-gate carries the qualification; the
    # fuzz qualification runs in its own job and is gated separately.
    assert "needs.release-gate.result == 'success'" in body
    assert "needs.fuzz-qualification.result == 'success'" in body


RC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "rc-release-gates.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _release_jobs() -> dict:
    import yaml

    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _rc_jobs() -> dict:
    import yaml

    return yaml.safe_load(RC_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _rc_triggers() -> dict:
    import yaml

    document = yaml.safe_load(RC_WORKFLOW.read_text(encoding="utf-8"))
    # YAML reads a bare `on` key as the boolean True.
    return document.get("on", document.get(True, {}))


def _publish_allowed(results: dict[str, str], event: str = "push") -> bool:
    """Evaluate the publish condition with the given upstream results."""
    condition = _release_jobs()["publish"]["if"]
    # The workflow folds the condition over several lines.
    condition = " ".join(condition.split())
    condition = condition.replace("&&", " and ").replace("||", " or ")
    condition = condition.replace("always()", "True")
    condition = re.sub(
        r"github\.event_name == '([a-z_]+)'",
        lambda match: str(event == match.group(1)),
        condition,
    )
    condition = re.sub(
        r"needs\.([a-z-]+)\.result == '([a-z]+)'",
        lambda match: str(results.get(match.group(1)) == match.group(2)),
        condition,
    )
    assert "needs." not in condition, condition
    return bool(eval(condition, {"__builtins__": {}}, {}))  # noqa: S307 - our own condition


def test_the_release_workflow_calls_the_candidate_gates() -> None:
    """The canonical publication path owns the heavy qualification."""
    assert (
        _release_jobs()["rc-release-gates"]["uses"]
        == "./.github/workflows/rc-release-gates.yml"
    )


def test_the_candidate_gates_are_reusable_and_not_tag_triggered() -> None:
    """One authoritative qualification per candidate, not two."""
    triggers = _rc_triggers()
    assert "workflow_call" in triggers
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers


def test_tag_publish_needs_the_candidate_gates() -> None:
    publish = _release_jobs()["publish"]
    assert "rc-release-gates" in publish["needs"]
    assert "needs.rc-release-gates.result == 'success'" in publish["if"]


def test_any_non_success_candidate_result_blocks_publication() -> None:
    """Neither failure, cancellation nor a skip may publish the tag."""
    green = {
        "musl-build": "success",
        "integrity-checksums": "success",
        "release-gate": "success",
        "official-docker-release-gate": "success",
        "fuzz-qualification": "success",
        "integrity-signature": "success",
    }
    assert _publish_allowed({**green, "rc-release-gates": "success"}) is True
    for outcome in ("failure", "cancelled", "skipped"):
        assert _publish_allowed({**green, "rc-release-gates": outcome}) is False
    # The fuzz qualification is a candidate gate: no outcome but success
    # may publish the tag.
    for outcome in ("failure", "cancelled", "skipped"):
        assert _publish_allowed({**green, "fuzz-qualification": outcome,
                                 "rc-release-gates": "success"}) is False


def test_the_candidate_gates_run_the_called_commit() -> None:
    """The evidence records the SHA the caller checked out, not a pinned ref."""
    steps = _rc_jobs()["real-nginx-e2e"]["steps"]
    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert "ref" not in checkout.get("with", {})
    assert 'os.environ["GITHUB_SHA"]' in RC_WORKFLOW.read_text(encoding="utf-8")


def test_encoding_chain_e2e_is_wired_to_ci_and_local_e2e_aggregate() -> None:
    """The existing encoding-chain scenario runs in CI and the local profile."""
    import yaml

    ci = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = ci["jobs"]["runtime-regressions"]["steps"]
    step = next(
        step
        for step in steps
        if step.get("name") == "Run encoding-chain native E2E verification"
    )
    assert step["run"] == "./tools/e2e/verify_encoding_chain_e2e.sh"
    assert step["env"]["NGINX_BIN"] == "${{ steps.ims_runtime.outputs.nginx_bin }}"
    assert "$(MAKE) verify-encoding-chain-e2e" in MAKEFILE.read_text(encoding="utf-8")


def test_rc_evidence_records_digest_pinned_execution_inputs() -> None:
    """Candidate evidence binds every runtime and builder image to a digest."""
    import yaml

    workflow = RC_WORKFLOW.read_text(encoding="utf-8")
    jobs = _rc_jobs()
    job = jobs["real-nginx-e2e"]
    assert job["runs-on"] == "ubuntu-24.04"
    environment = yaml.safe_load(workflow)["env"]
    assert environment["NGINX_VERSION"] == "1.30.4"
    assert environment["ALPINE_VERSION"] == "3.24"
    for key, prefix in (
        ("IMAGE", r"nginx:1\.30\.4-alpine"),
        ("NGINX_BASE_IMAGE", r"nginx:1\.30\.4-alpine3\.24"),
        ("RUST_BUILDER_IMAGE", r"rust:1\.98\.1-alpine3\.24"),
    ):
        assert re.fullmatch(prefix + r"@sha256:[0-9a-f]{64}", environment[key])
    assert re.fullmatch(
        r"1\.26-alpine@sha256:[0-9a-f]{64}", environment["INCOMPATIBLE_TAG"]
    )

    steps = job["steps"]
    native_build = next(
        step
        for step in steps
        if step.get("name") == "Build a native NGINX binary for encoding-chain validation"
    )
    assert "verify_real_nginx_ims.sh" in native_build["run"]
    assert "printf 'nginx_bin=%s" in native_build["run"]
    real_checks = next(
        step
        for step in steps
        if step.get("name") == "Run the real-NGINX end-to-end checks"
    )
    assert real_checks["env"]["NGINX_BIN"] == "${{ steps.native_nginx.outputs.nginx_bin }}"
    checks = real_checks["run"]
    assert "encoding_chain|verify-encoding-chain-e2e" in checks
    assert '"${rows}" -ne 7' in checks

    evidence = next(
        step for step in steps if step.get("name") == "Write candidate-bound evidence"
    )["run"]
    for field in (
        '"module_builder_image"',
        '"runtime_images"',
        '"native_nginx_version"',
        '"runner"',
        'os.environ["RUNNER_OS"]',
        'os.environ["RUNNER_ARCH"]',
    ):
        assert field in evidence
    for scenario in (
        "realip_access_boundary", "slow_reader_backpressure",
        "graceful_reload_streaming", "module_version_mismatch",
        "auth_subrequest_observability", "helm_cluster_smoke_base",
        "helm_cluster_smoke_sidecar",
    ):
        assert scenario in evidence


def test_helm_cluster_smoke_scrapes_the_module_metrics_sidecar() -> None:
    """The real chart smoke proves the metrics family traverses its sidecar."""
    script = (REPO_ROOT / "tools/e2e/verify_helm_cluster_smoke_e2e.sh").read_text(
        encoding="utf-8"
    )
    for setting in (
        "--set metrics.enabled=true",
        "--set metrics.sidecar.enabled=true",
        "--set metrics.expose=true",
        "metrics.sidecar.image.digest=${NGINX_BASE_DIGEST}",
    ):
        assert setting in script
    assert 'get service "${SVC}" -o jsonpath=' in script
    assert '"${METRICS_PF_PORT}:${METRICS_SVC_PORT}"' in script
    assert 'http://127.0.0.1:${METRICS_PF_PORT}/metrics' in script
    assert "nginx_markdown_requests_total" in script
    assert 'outcome="converted"' in script


def _run_stubbed_helm_cluster_smoke(
    tmp_path: Path, existing_release: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the smoke script against owned command stubs."""
    tools = tmp_path / "bin"
    temp_root = tmp_path / "tmp"
    tools.mkdir()
    temp_root.mkdir()
    calls = tmp_path / "calls.log"
    module = tmp_path / "module.so"
    module.write_bytes(b"fixture module")
    stubs = {
        "docker": "printf 'docker %s\\n' \"$*\" >> \"$CALL_LOG\"\n",
        "kind": (
            "printf 'kind %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$1 $2\" == 'get clusters' ]]; then "
            "printf '%s\\n' \"$CLUSTER\"; fi\n"
        ),
        "helm": (
            "printf 'helm %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$1\" == list ]]; then printf '%s\\n' \"$EXISTING_RELEASES\"; fi\n"
        ),
        "kubectl": (
            "printf 'kubectl %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$*\" == *'rollout status'* ]]; then exit 1; fi\n"
            "if [[ \"$*\" == *'get pods'* ]]; then printf 'pod\\n'; fi\n"
        ),
    }
    for name, body in stubs.items():
        stub = tools / name
        stub.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{tools}{os.pathsep}{env['PATH']}",
        "TMPDIR": str(temp_root),
        "CALL_LOG": str(calls),
        "MODULE_SO": str(module),
        "CLUSTER": "existing-cluster",
        "EXISTING_RELEASES": existing_release,
    })

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/e2e/verify_helm_cluster_smoke_e2e.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    command_log = calls.read_text(encoding="utf-8")
    return result, command_log


def test_helm_cluster_smoke_leaves_a_preexisting_release_untouched(
    tmp_path: Path,
) -> None:
    """A reused cluster's existing release is neither adopted nor removed."""
    result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path, "markdown-smoke")

    assert result.returncode != 0
    assert "pre-existing Helm release" in result.stderr
    assert "helm list" in command_log
    assert "helm install" not in command_log
    assert "helm upgrade" not in command_log
    assert "helm uninstall" not in command_log


def test_helm_cluster_smoke_uninstalls_a_release_it_created(
    tmp_path: Path,
) -> None:
    """The ownership guard must retain cleanup for a successful own install."""
    result, command_log = _run_stubbed_helm_cluster_smoke(tmp_path, "")

    assert result.returncode != 0
    assert "helm install" in command_log
    assert "helm upgrade" not in command_log
    assert "helm uninstall" in command_log


def test_manual_qualification_is_explicitly_defined() -> None:
    """A hand-run candidate gate is defined, and it is not a second tag path."""
    assert "workflow_dispatch" in _rc_triggers()


def _signing_allowed(results: dict[str, str], event: str = "push", ref_type: str = "tag") -> bool:
    """Evaluate the signing condition with the given upstream results."""
    condition = " ".join(str(_release_jobs()["integrity-signing"]["if"]).split())
    condition = condition.replace("&&", " and ").replace("||", " or ")
    condition = re.sub(
        r"github\.event_name == '([a-z_]+)'",
        lambda match: str(event == match.group(1)),
        condition,
    )
    condition = re.sub(
        r"github\.ref_type == '([a-z]+)'",
        lambda match: str(ref_type == match.group(1)),
        condition,
    )
    condition = re.sub(
        r"needs\.([a-z-]+)\.result == '([a-z]+)'",
        lambda match: str(results.get(match.group(1)) == match.group(2)),
        condition,
    )
    assert "needs." not in condition, condition
    return bool(eval(condition, {"__builtins__": {}}, {}))  # noqa: S307 - our own condition


def test_tag_signing_requires_the_candidate_gates() -> None:
    """A protected release signature follows a completed qualification."""
    assert "rc-release-gates" in _release_jobs()["integrity-signing"]["needs"]


def test_a_non_success_candidate_result_blocks_tag_signing() -> None:
    """Neither failure, cancellation nor a skip may start protected signing."""
    green = {
        "smoke-test": "success",
        "release-gate": "success",
        "musl-build": "success",
        "official-docker-release-gate": "success",
        "fuzz-qualification": "success",
    }
    assert _signing_allowed({**green, "rc-release-gates": "success"}) is True
    for outcome in ("failure", "cancelled", "skipped"):
        assert _signing_allowed({**green, "rc-release-gates": outcome}) is False
    # A failed or missing fuzz qualification must not start protected
    # signing either.
    for outcome in ("failure", "cancelled", "skipped"):
        assert _signing_allowed({**green, "fuzz-qualification": outcome,
                                 "rc-release-gates": "success"}) is False


def test_manual_dispatch_does_not_sign() -> None:
    """An artifact-only dispatch skips signing on purpose."""
    green = {"rc-release-gates": "success"}
    assert _signing_allowed(green, event="workflow_dispatch", ref_type="branch") is False


def test_download_artifact_pins_use_the_exact_release_label() -> None:
    """Pinned download-artifact references carry the v8.0.1 tag comment."""
    action_sha = "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    workflows = (
        REPO_ROOT / ".github" / "workflows" / "release-binaries.yml",
        WORKFLOW,
    )
    for workflow in workflows:
        pinned_lines = [
            line.strip()
            for line in workflow.read_text(encoding="utf-8").splitlines()
            if f"actions/download-artifact@{action_sha}" in line
        ]
        assert pinned_lines
        assert all(line.endswith("# v8.0.1") for line in pinned_lines), workflow
