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
from dataclasses import dataclass
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


def test_release_dependency_preflight_reports_the_import_cause() -> None:
    """A swallowed ImportError must not hide which dependency is missing.

    The preflight captures the import check's stderr into a file and echoes
    it in the failure branch, instead of sending it to /dev/null, so the run
    log names the module that failed to import.  The exit behavior stays
    fail-closed.
    """
    jobs = _release_jobs()
    step = next(
        step
        for step in jobs["release-gate"]["steps"]
        if step.get("name") == "Verify release gate dependencies"
    )
    run = step["run"]

    assert '2>"${dependency_stderr_file}"' in run, (
        "the dependency import check must capture stderr for its diagnostics"
    )
    assert 'echo "${dependency_stderr}" >&2' in run, (
        "the failure branch must echo the captured ImportError text"
    )
    assert 'dependency_stderr="$(cat "${dependency_stderr_file}")"' in run
    assert "exit 1" in run, "the preflight must stay fail-closed"
    assert "unable to import the pinned release Python dependencies" in run
    # The import check itself must not discard its diagnostics.
    import_check = next(
        line for line in run.splitlines() if "importlib.metadata import version" in line
    )
    assert "2>/dev/null" not in import_check, import_check


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
    # The Alpine release is pinned inside the image references below; a
    # separate variable nobody reads would be dead configuration.
    assert "ALPINE_VERSION" not in environment
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
    # The mktemp files must be removed on every exit path, not only when the
    # build reaches its final line: a failed build already exits earlier.
    native_run = native_build["run"]
    assert (
        "trap 'rm -f -- \"${nginx_bin_file:-}\" \"${buildroot_file:-}\"' EXIT"
        in native_run
    ), "the native build must remove its temporary files from an EXIT trap"
    assert native_run.index("trap 'rm -f") < native_run.index(
        "verify_real_nginx_ims.sh"
    ), "the trap must be installed before the build can fail"
    real_checks = next(
        step
        for step in steps
        if step.get("name") == "Run the real-NGINX end-to-end checks"
    )
    assert real_checks["env"]["NGINX_BIN"] == "${{ steps.native_nginx.outputs.nginx_bin }}"
    checks = real_checks["run"]
    assert "encoding_chain|verify-encoding-chain-e2e" in checks
    assert 'if [[ "${rows}" -ne 7 ]]' in checks, (
        "the evidence-row count assertion must use [[ ]] per the shell rule"
    )

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


@dataclass
class _HelmStub:
    """Fixture knobs for the stubbed helm binary.

    Grouped so the runner keeps one options parameter per concern: the
    three helm behaviors plus the reported version would otherwise push
    the runner past the repository's parameter threshold.
    """

    version: str | None = None
    list_stderr: str = ""
    list_fails: bool = False
    install_fails: bool = False


def _run_stubbed_helm_cluster_smoke(
    tmp_path: Path,
    existing_release: str,
    *,
    helm: _HelmStub | None = None,
    cluster_exists: bool = True,
    namespace_exists: bool = True,
    namespace_create_fails: bool = False,
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
            "if [[ \"$1 $2\" == 'get clusters' && "
            "\"$CLUSTER_EXISTS\" == 1 ]]; then "
            "printf '%s\\n' \"$CLUSTER\"; fi\n"
        ),
        "helm": (
            "printf 'helm %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$1\" == version ]]; then\n"
            "  printf '%s\\n' \"${HELM_VERSION:-v3.19.0+fixture}\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$1\" == list ]]; then\n"
            "  if [[ -n \"${HELM_LIST_STDERR:-}\" ]]; then "
            "printf '%s\\n' \"$HELM_LIST_STDERR\" >&2; fi\n"
            "  if [[ \"${HELM_LIST_FAILS:-}\" == 1 ]]; then "
            "printf '%s\\n' 'simulated list failure' >&2; exit 1; fi\n"
            "  printf '%s\\n' \"$EXISTING_RELEASES\"\n"
            "fi\n"
            "if [[ \"$1\" == install && \"${HELM_INSTALL_FAILS:-}\" == 1 ]]; "
            "then\n"
            "  printf '%s\\n' 'simulated install failure' >&2\n"
            "  exit 1\n"
            "fi\n"
        ),
        "kubectl": (
            "printf 'kubectl %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$*\" == *'get namespace "
            "markdown-smoke --ignore-not-found -o name'* ]]; then\n"
            "  if [[ \"$NAMESPACE_EXISTS\" == 1 ]]; then "
            "printf 'namespace/markdown-smoke\\n'; fi\n"
            "fi\n"
            "if [[ \"$*\" == *'create namespace markdown-smoke'* && "
            "\"$NAMESPACE_CREATE_FAILS\" == 1 ]]; then\n"
            "  echo 'simulated namespace creation failure' >&2\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$*\" == *'rollout status'* ]]; then exit 1; fi\n"
            "if [[ \"$*\" == *'get pods'* ]]; then printf 'pod\\n'; fi\n"
        ),
    }
    helm_stub = helm if helm is not None else _HelmStub()
    for name, body in stubs.items():
        stub = tools / name
        stub.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    env = os.environ.copy()
    # Drop an ambient HELM_VERSION so a developer's shell setting cannot
    # change what the stub reports; the parameter is the only source.
    env.pop("HELM_VERSION", None)
    env.update({
        "PATH": f"{tools}{os.pathsep}{env['PATH']}",
        "TMPDIR": str(temp_root),
        "CALL_LOG": str(calls),
        "MODULE_SO": str(module),
        "CLUSTER": "existing-cluster",
        "EXISTING_RELEASES": existing_release,
        "CLUSTER_EXISTS": "1" if cluster_exists else "0",
        "NAMESPACE_EXISTS": "1" if namespace_exists else "0",
        "NAMESPACE_CREATE_FAILS": "1" if namespace_create_fails else "0",
        "HELM_LIST_STDERR": helm_stub.list_stderr,
        "HELM_LIST_FAILS": "1" if helm_stub.list_fails else "0",
        "HELM_INSTALL_FAILS": "1" if helm_stub.install_fails else "0",
    })
    if helm_stub.version is not None:
        env["HELM_VERSION"] = helm_stub.version

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
    # Context-independent: the real command carries --context between
    # 'kubectl' and 'delete', so the negative must match the operative part.
    assert "delete namespace" not in command_log
    assert "kind delete cluster" not in command_log


def test_helm_cluster_smoke_initializes_a_fresh_cluster_namespace_first(
    tmp_path: Path,
) -> None:
    """A fresh cluster gets its namespace before Helm lists releases."""
    result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        cluster_exists=False,
        namespace_exists=False,
    )

    assert result.returncode != 0
    commands = command_log.splitlines()
    cluster_create = next(
        index for index, command in enumerate(commands)
        if command.startswith("kind create cluster ")
    )
    namespace_lookup = next(
        index for index, command in enumerate(commands)
        if "get namespace markdown-smoke --ignore-not-found -o name" in command
    )
    namespace_create = next(
        index for index, command in enumerate(commands)
        if "create namespace markdown-smoke" in command
    )
    helm_list = next(
        index for index, command in enumerate(commands)
        if command.startswith("helm list ")
    )

    assert cluster_create < namespace_lookup < namespace_create < helm_list
    # Explicit state flags are the one spelling both helm majors accept;
    # `--all` works on v3 but was removed in v4.
    helm_list_command = commands[helm_list]
    for flag in (
        "--deployed",
        "--failed",
        "--pending",
        "--uninstalled",
        "--uninstalling",
        "--superseded",
    ):
        assert flag in helm_list_command, flag
    assert "--all" not in helm_list_command
    assert "helm install" in command_log


def test_helm_cluster_smoke_fails_when_namespace_creation_fails(
    tmp_path: Path,
) -> None:
    """Namespace permission and API errors must not be hidden."""
    result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        cluster_exists=False,
        namespace_exists=False,
        namespace_create_fails=True,
    )

    assert result.returncode != 0
    assert "simulated namespace creation failure" in result.stderr
    assert "ERROR: unable to create namespace markdown-smoke" in result.stderr
    assert "helm list" not in command_log
    assert "helm install" not in command_log


def test_helm_cluster_smoke_uninstalls_a_release_it_created(
    tmp_path: Path,
) -> None:
    """The ownership guard must retain cleanup for a successful own install."""
    result, command_log = _run_stubbed_helm_cluster_smoke(tmp_path, "")

    assert result.returncode != 0
    assert "helm install" in command_log
    assert "helm upgrade" not in command_log
    assert "helm uninstall" in command_log
    # Context-independent: the real command carries --context between
    # 'kubectl' and 'delete', so the negative must match the operative part.
    assert "delete namespace" not in command_log
    assert "kind delete cluster" not in command_log


def test_helm_cluster_smoke_installs_atomically(tmp_path: Path) -> None:
    """A failed or timed-out install must be removed by helm itself.

    The rollback flag is selected by the installed helm's major version:
    Helm 3 spells it ``--atomic``, Helm 4 ``--rollback-on-failure``.  With
    the fixture reporting a v3 version, the install must carry ``--atomic``.
    """
    _result, command_log = _run_stubbed_helm_cluster_smoke(tmp_path, "")

    install_commands = [
        command for command in command_log.splitlines()
        if command.startswith("helm install ")
    ]
    assert install_commands, command_log
    for command in install_commands:
        assert " --atomic" in command, (
            f"helm install must pass --atomic for Helm 3 so a failed install "
            f"is removed automatically: {command}"
        )


def test_helm_cluster_smoke_selects_the_v4_rollback_flag(
    tmp_path: Path,
) -> None:
    """A Helm 4 binary gets its own rollback flag spelling.

    Regression for the renamed flag: Helm 4 offers
    ``--rollback-on-failure`` and keeps ``--atomic`` only as a deprecated
    alias, so the smoke must pass the current spelling when it detects a
    v4 binary (and never emit deprecation noise).  The version is supplied
    through the helper's parameter, which also scrubs any ambient
    HELM_VERSION so a developer's shell setting cannot skew the fixture.
    """
    _result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path, "", helm=_HelmStub(version="v4.3.0+fixture")
    )

    install_commands = [
        command for command in command_log.splitlines()
        if command.startswith("helm install ")
    ]
    assert install_commands, command_log
    for command in install_commands:
        assert " --rollback-on-failure" in command, (
            f"a Helm 4 install must pass --rollback-on-failure: {command}"
        )
        assert " --atomic" not in command, (
            f"a Helm 4 install must not pass the deprecated --atomic: "
            f"{command}"
        )


def test_helm_cluster_smoke_ignores_helm_list_stderr_on_success(
    tmp_path: Path,
) -> None:
    """A warning on helm list's stderr must not read as a release owner."""
    result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        helm=_HelmStub(list_stderr="WARNING: Kubernetes configuration file is group-readable"),
    )

    assert "helm list" in command_log
    assert "helm install" in command_log, (
        "an empty stdout from helm list means the name is free; a stderr "
        f"warning must not refuse the install: {result.stderr}"
    )
    assert "pre-existing Helm release" not in result.stderr
    # The warning belongs to a successful query, so it is not echoed back.
    assert "group-readable" not in result.stderr


def test_helm_cluster_smoke_reports_helm_list_stderr_on_failure(
    tmp_path: Path,
) -> None:
    """A failing ownership query must surface the captured stderr."""
    result, _command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        helm=_HelmStub(list_fails=True),
    )

    assert result.returncode != 0
    assert "unable to determine ownership of Helm release" in result.stderr
    assert "simulated list failure" in result.stderr


def test_helm_cluster_smoke_uninstalls_after_a_failed_install(
    tmp_path: Path,
) -> None:
    """Ownership starts before the install, so a failure still cleans up."""
    result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        helm=_HelmStub(install_fails=True),
    )

    assert result.returncode != 0
    assert "helm install" in command_log
    assert "helm uninstall" in command_log, (
        "a failed install leaves state behind, so cleanup must still "
        f"uninstall the release it owned: {result.stderr}"
    )


def test_helm_cluster_smoke_removes_a_namespace_it_created_on_a_reused_cluster(
    tmp_path: Path,
) -> None:
    """Cleanup must not leak markdown-smoke on a reused cluster."""
    _result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        cluster_exists=True,
        namespace_exists=False,
    )

    assert "create namespace markdown-smoke" in command_log
    assert "kubectl --context kind-existing-cluster delete namespace markdown-smoke" \
        in command_log
    assert "kind delete cluster" not in command_log


def test_helm_cluster_smoke_keeps_a_preexisting_namespace(
    tmp_path: Path,
) -> None:
    """A namespace this run did not create must survive cleanup."""
    _result, command_log = _run_stubbed_helm_cluster_smoke(
        tmp_path,
        "",
        cluster_exists=True,
        namespace_exists=True,
    )

    assert "create namespace markdown-smoke" not in command_log
    # Context-independent: the real command carries --context between
    # 'kubectl' and 'delete', so the negative must match the operative part.
    assert "delete namespace" not in command_log


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


def test_helm_cluster_smoke_serializes_concurrent_runs_on_one_cluster() -> None:
    """Concurrent runs must not free each other's fixed-name release.

    Regression for the ownership race: two runs on a shared cluster can both
    observe the fixed release name as free, and the loser's cleanup would
    then uninstall the winner's release.  A per-cluster lock must span the
    ownership check through cleanup, and cleanup must release it.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/e2e/verify_helm_cluster_smoke_e2e.sh"
    ).read_text(encoding="utf-8")

    assert "acquire_cluster_lock" in script
    acquire_call = script.index("\nacquire_cluster_lock\n")
    ownership_check = script.index("--filter \"^${RELEASE}$\"")
    install = script.index("helm install \"${RELEASE}\"")
    assert acquire_call < ownership_check < install, (
        "the lock must be held before the ownership check and the install"
    )
    cleanup = script.split("cleanup() {", 1)[1].split("\n}", 1)[0]
    assert "release_cluster_lock" in cleanup, (
        "cleanup must release the cluster lock"
    )
    assert "flock" in script and "mkdir" in script, (
        "the lock needs the flock path and a directory fallback"
    )


def test_helm_cluster_smoke_serializes_stale_lock_reclaim() -> None:
    """Stale-lock examination and reclaim are mutually exclusive.

    Regression: two waiters could both see a stale lock; the winner could
    re-acquire the canonical path, and the slower waiter's rename then
    displaced that LIVE owner's lock, admitting a third run onto the shared
    cluster.  A short-lived reaper mutex now serializes the examination and
    reclaim, so a live owner's lock is never displaced, and a reaper held by
    a dead pid is broken by the next waiter.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/e2e/verify_helm_cluster_smoke_e2e.sh"
    ).read_text(encoding="utf-8")

    acquire = script.split("acquire_cluster_lock() {", 1)[1]
    acquire = acquire.split("\n}\n", 1)[0]
    assert "acquire_lock_reaper" in acquire, (
        "stale-lock examination must run under the reaper mutex"
    )
    # The reaper wraps the liveness check and the reclaim together.
    assert acquire.index("acquire_lock_reaper") < acquire.index(
        "dir_lock_owner_alive"
    )
    assert acquire.index("dir_lock_owner_alive") < acquire.index(
        "release_lock_reaper"
    )

    reaper = script.split("acquire_lock_reaper() {", 1)[1]
    reaper = reaper.split("\n}\n", 1)[0]
    # A reaper held by a dead pid is broken so it cannot wedge the smoke.
    assert "kill -0" in reaper
    # Breaking is atomic: the stale directory is CLAIMED with a rename
    # before deletion, so two waiters cannot both reclaim it and one
    # cannot remove a replacement mutex the other just created.
    assert "mv \"${LOCK_PATH}.reaper\" \"${reaper_claim}\"" in reaper
    assert "rm -rf \"${reaper_claim}\"" in reaper
    # An ownerless directory (a waiter died between mkdir and its pid
    # write) is reclaimed only after a grace period so a merely slow
    # publisher is never displaced.
    assert "-mmin +1" in reaper
    # After taking the mutex the waiter re-reads the recorded pid and
    # proceeds only while it names this wait: a recovery by another
    # waiter can displace the directory between the mkdir and the write,
    # and a phantom mutex must not admit its holder into the critical
    # section.
    verify_line = (
        'if [[ "$(cat "${LOCK_PATH}.reaper/pid" 2>/dev/null || true)" == "$$" ]]'
    )
    assert verify_line in reaper, (
        "the acquirer must re-read its ownership before returning"
    )
    printf_line = 'printf \'%s\\n\' "$$" > "${LOCK_PATH}.reaper/pid"'
    assert reaper.index(printf_line) < reaper.index(verify_line)
    # A claimed directory whose owner is alive is restored or parked, and
    # the claim name carries the loop counter so a parked claim from an
    # earlier iteration is never a rename target again.
    assert "${LOCK_PATH}.reaper.stale.$$.${waited}" in reaper
    live_claim = reaper.split("claimed_pid", 1)[1].split("else", 1)[0]
    assert "kill -0 \"${claimed_pid}\"" in live_claim
    assert "mv \"${reaper_claim}\" \"${LOCK_PATH}.reaper\"" in live_claim
    assert "rm -rf \"${reaper_claim}\"" not in live_claim, (
        "a live owner's claimed mutex must never be deleted"
    )


def test_helm_cluster_smoke_deletes_the_claimed_stale_directory() -> None:
    """A claimed stale lock directory is removed, not leaked.

    Under the reaper mutex a claim is only ever taken when the recorded
    owner is dead, so the claim can be deleted immediately; the canonical
    path is then free for this waiter's own acquisition attempt.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/e2e/verify_helm_cluster_smoke_e2e.sh"
    ).read_text(encoding="utf-8")

    block = script.split("acquire_cluster_lock() {", 1)[1]
    block = block.split("\n}\n", 1)[0]
    claim = block.split("stale_claim=\"${LOCK_PATH}.stale.$$\"", 1)[1]
    claim = claim.split("continue", 1)[0]
    assert 'rm -rf "${stale_claim}"' in claim, (
        "a claimed stale directory must be deleted"
    )
