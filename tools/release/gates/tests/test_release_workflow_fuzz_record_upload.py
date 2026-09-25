"""Wiring tests for the release workflow's fuzz qualification handoff.

The record and the per-target raw logs are the only post-mortem evidence
when the fuzz qualification fails, and the record is the input the final
evidence generator reads to decide the blocking fuzz verdict; the
workflow's other upload steps sit on success paths, so the record upload
step is asserted to be unconditional (``if: always()``), fail-closed
(``if-no-files-found: error``) and to point at the paths the validator
actually writes.  The paths are resolved from the workflow's own
RELEASE_VERSION variable and checked against the validator's path builder,
so a version bump cannot desync the two ends.  This module also covers
the job-level pipeline structure (the fuzz job owning a full budget, the
publish junctions requiring it, the release gate no longer running it)
and the packaging gate's own wiring into the workflow.  The
shell-semantics suite for that gate lives in
``test_validate_fuzz_packaging.py``.
"""

from __future__ import annotations

import contextlib
import copy
import io
import re
from pathlib import Path

import yaml
import pytest

import tools.release.gates.validate_fuzz_qualification as validator
from tools.release.gates import validate_fuzz_packaging as packaging_gate

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"

RECORD_ARTIFACT_NAME = "fuzz-qualification-record"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _workflow_with_run_command(command: str, shell: str | None = None) -> str:
    """Build a minimal workflow containing one public-gate run command."""
    step = {"run": command}
    if shell is not None:
        step["shell"] = shell
    return yaml.safe_dump(
        {"jobs": {"release-gate": {"steps": [step]}}},
        sort_keys=False,
    )


def _job_live_text(workflow_text: str, job_name: str) -> str:
    """Live command text of every executable step of one job."""
    steps = packaging_gate._job_run_step_records(workflow_text, job_name)
    assert steps is not None, f"the {job_name} job must be parseable"
    return "\n".join(
        segment
        for step in steps
        for segment in packaging_gate._step_live_commands(step)
    )


def _named_step(job: dict, name: str) -> dict:
    """The single step with this name, failing loudly when it is absent."""
    matches = [
        step for step in job["steps"]
        if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r}, found {len(matches)}")
    return matches[0]


def _assert_record_handoff(workflow: dict) -> None:
    """Check producer, download, and exact-file verification as one contract."""
    job = workflow["jobs"]["release-gate"]
    assert "fuzz-qualification" in job["needs"]
    names = [step.get("name", "") for step in job["steps"]]
    download_at = names.index("Download fuzz qualification record")
    verify_at = names.index("Verify fuzz qualification record was downloaded")
    gate_at = names.index("Run release gates")
    assert download_at < verify_at < gate_at
    download = job["steps"][download_at]
    verify = job["steps"][verify_at]
    assert download.get("uses", "").startswith("actions/download-artifact")
    assert download.get("with", {}).get("name") == RECORD_ARTIFACT_NAME
    assert download.get("if") not in (False, "false", "${{ false }}")
    assert download.get("continue-on-error") not in (True, "true", "${{ true }}")
    assert verify.get("if") not in (False, "false", "${{ false }}")
    assert verify.get("continue-on-error") not in (True, "true", "${{ true }}")
    version = workflow.get("env", {}).get("RELEASE_VERSION")
    assert isinstance(version, str)
    assert version
    paths = validator._release_artifact_paths(version)
    expected_record = paths["record"]
    download_path = download.get("with", {}).get("path")
    assert isinstance(download_path, str)
    assert download_path.replace("${{ env.RELEASE_VERSION }}", version).rstrip("/") == (
        expected_record.rsplit("/", 1)[0]
    )
    verify_run = verify.get("run", "")
    resolved_record = verify_run.replace("${RELEASE_VERSION}", version)
    assert expected_record in resolved_record
    assert "-f " in verify_run or "test -f" in verify_run
    upload = _named_step(
        workflow["jobs"]["fuzz-qualification"],
        "Upload fuzz qualification record",
    )
    assert upload.get("with", {}).get("name") == RECORD_ARTIFACT_NAME


def test_release_workflow_trigger_matrix_is_explicit() -> None:
    """Only version-tag pushes and explicit dispatch start release packaging."""
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert isinstance(triggers, dict)
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"tags": ["v*"]}
    dispatch = triggers["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    assert set(dispatch.get("inputs", {})) == {"version"}
    assert dispatch["inputs"]["version"].get("type") == "string"


def test_fuzz_record_upload_is_unconditional_and_matches_the_validator() -> None:
    workflow_doc = _workflow()
    job = workflow_doc["jobs"]["fuzz-qualification"]
    step = _named_step(job, "Upload fuzz qualification record")
    uses = step.get("uses", "")
    assert uses.startswith("actions/upload-artifact"), (
        "the record upload step must actually upload an artifact, "
        f"found uses={uses!r}")
    assert step.get("if") == "always()", (
        "the record upload must survive a failing fuzz qualification")
    assert step.get("with", {}).get("if-no-files-found") == "error", (
        "a missing record must fail the producer job instead of uploading "
        "nothing silently")
    path_text = step.get("with", {}).get("path", "")
    # The path derives from the workflow's own RELEASE_VERSION variable;
    # resolving it must reproduce the validator's real write paths for
    # record and logs, so a version bump cannot desync the two ends.
    release_version = workflow_doc.get("env", {}).get("RELEASE_VERSION")
    assert isinstance(release_version, str)
    assert release_version
    resolved = path_text.replace(
        "${{ env.RELEASE_VERSION }}", release_version)
    artifact_paths = validator._release_artifact_paths(release_version)
    record = artifact_paths["record"].rstrip("/")
    log_dir = artifact_paths["log_dir"].rstrip("/")
    assert record in resolved
    assert log_dir + "/" in resolved
    assert record == f"artifacts/release/{release_version}/" + record.rsplit("/", 1)[-1]
    assert log_dir == f"artifacts/release/{release_version}/" + log_dir.rsplit("/", 1)[-1]


def test_fuzz_job_generates_its_manifests_and_runs_the_qualification() -> None:
    """The fuzz job owns the qualification and its runtime-generated inputs.

    The fuzz manifests are candidate-bound and generated at run time (not
    tracked), so the job must generate its own candidate-bound copies
    before the validator runs, and the validator must sit on the job's
    provably executed path -- a commented-out, quoted or dead-branch copy
    must not satisfy it.
    """
    run_text = _job_live_text(
        WORKFLOW.read_text(encoding="utf-8"), "fuzz-qualification")
    assert "generate_release_gate_manifests.py" in run_text
    assert "validate_fuzz_qualification.py --mode real --git-head" in run_text


def test_fuzz_job_pins_and_exports_the_recorded_toolchain() -> None:
    """The installed compiler and runtime environment use the same date pin."""
    workflow = _workflow()
    job = workflow["jobs"]["fuzz-qualification"]
    pinned_toolchain = validator.FUZZ_TOOLCHAIN
    assert workflow.get("env", {}).get("FUZZ_TOOLCHAIN") == pinned_toolchain

    install = _named_step(job, "Install pinned Rust toolchain for fuzz qualification")
    install_run = install.get("run", "")
    assert f"--toolchain {pinned_toolchain}" in install_run
    assert f"rustup component add --toolchain {pinned_toolchain} rust-src" in install_run
    assert (
        "cargo install cargo-fuzz --version "
        f"{validator.FUZZ_CARGO_FUZZ_PACKAGE_VERSION} --locked"
    ) in install_run
    assert not re.search(r"--toolchain\s+nightly(?:\s|$)", install_run)

    run = _named_step(job, "Run fuzz qualification")
    assert run.get("env", {}).get("FUZZ_TOOLCHAIN") == "${{ env.FUZZ_TOOLCHAIN }}"
    assert run.get("env", {}).get("RUSTUP_TOOLCHAIN") == "${{ env.FUZZ_TOOLCHAIN }}"


def test_fuzz_job_structure_contract_rejects_dead_and_commented_commands() -> None:
    """The pipeline scan must read executed commands, not raw text.

    The same assertions that pass on the workflow must fail on a job whose
    only copies of those commands sit behind `if false`, so a pipeline
    that stopped running the qualification cannot pass on text alone.
    """
    dead = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - run: |\n"
        "          if false; then\n"
        "            python3 tools/release/gates/"
        "generate_release_gate_manifests.py --phase inputs\n"
        "            python3 tools/release/gates/"
        "validate_fuzz_qualification.py --mode real --git-head\n"
        "          fi\n"
    )
    dead_text = _job_live_text(dead, "fuzz-qualification")
    assert "validate_fuzz_qualification.py --mode real --git-head" not in (
        dead_text)
    assert "generate_release_gate_manifests.py" not in dead_text
    commented = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - run: |\n"
        "          # python3 tools/release/gates/"
        "validate_fuzz_qualification.py --mode real --git-head\n"
        "          # python3 tools/release/gates/"
        "generate_release_gate_manifests.py --phase inputs\n"
        "          echo ok\n"
    )
    commented_text = _job_live_text(commented, "fuzz-qualification")
    assert "validate_fuzz_qualification.py --mode real --git-head" not in (
        commented_text)
    assert "generate_release_gate_manifests.py" not in commented_text
    assert "echo ok" in commented_text


def test_fuzz_qualification_runs_in_its_own_job_and_gates_publish() -> None:
    """The fuzz soak owns a dedicated job (full budget) and every publish
    junction requires it; the release gate no longer runs it."""
    jobs = _workflow()["jobs"]
    assert "fuzz-qualification" in jobs
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    # The release gate must not run the fuzz qualification any more: the
    # check reads live segments, so a commented-out or quoted copy of the
    # command cannot masquerade as an executed one.
    gate_text = _job_live_text(workflow_text, "release-gate")
    assert "validate_fuzz_qualification.py --mode real" not in gate_text
    # Publish and every integrity/release junction must require the fuzz job.
    for job in (
        "publish",
        "integrity-checksums",
        "integrity-signing",
        "integrity-signature",
        "official-docker-release-gate",
        "rc-release-gates",
    ):
        needs = jobs[job]["needs"]
        assert "fuzz-qualification" in needs, job
    publish_if = str(jobs["publish"]["if"])
    assert "needs.fuzz-qualification.result == 'success'" in publish_if


def test_release_gate_job_provisions_the_pinned_rust_toolchain() -> None:
    """The split must keep the pinned toolchain inside the release gate.

    Gate scripts resolve cargo, rustc and rustfmt through Rustup shims
    (streaming evidence generation, the reason-codegen drift check), so the
    gate job must install the pinned toolchain itself; the fuzz job installs
    its own dated nightly toolchain separately.
    """
    scripts = packaging_gate._job_run_scripts(
        WORKFLOW.read_text(encoding="utf-8"), "release-gate"
    )
    assert scripts is not None, "the release-gate job must be parseable"
    assert packaging_gate._release_gate_toolchain_issue(scripts) is None


def test_workflow_rejects_raw_toolchain_installs() -> None:
    """Every toolchain install in the workflow must use the verified installer.

    The release workflows provision through
    ``packaging/scripts/install-verified-rustup.sh`` (checksum-validated
    rustup-init); a raw ``rustup toolchain install`` anywhere in the
    workflow must fail the gate.
    """
    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - {name: a, run: 'echo hi'}\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - name: b\n"
        "        run: |\n"
        "          retry 5 rustup toolchain install nightly --profile minimal\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow)
    # A raw install chained behind a separator is still a command.
    chained = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - name: b\n"
        "        run: |\n"
        "          echo ok && rustup toolchain install nightly --profile minimal\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(chained)
    # A quoted fragment stays data and does not trip the detector.
    quoted = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - name: b\n"
        "        run: |\n"
        '          echo "x; rustup toolchain install y"\n'
    )
    assert packaging_gate._raw_toolchain_install_issue(quoted) is None
    assert (
        packaging_gate._raw_toolchain_install_issue(
            WORKFLOW.read_text(encoding="utf-8")
        )
        is None
    )


def test_python_inline_launchers_cannot_hide_raw_toolchain_installs() -> None:
    """Python process APIs cannot conceal raw provisioning in a run step."""
    # These are inert YAML fixtures; the gate parses them and never executes them.
    commands = (
        "python3 -c 'import os; os.system(\"rustup toolchain install stable\")'",
        "python3 -c 'from os import system as run; run(\"rustup toolchain install stable\")'",
        "python3 -c 'from os import *; system(\"rustup toolchain install stable\")'",
        "python3 -c 'import subprocess; subprocess.run([\"rustup\", \"toolchain\", \"install\", \"stable\"])'",
        "python3 -c 'import subprocess as sp; sp.run([\"rustup\", \"toolchain\", \"install\", \"stable\"])'",
        "python3 -c 'from subprocess import *; run([\"rustup\", \"toolchain\", \"install\", \"stable\"])'",
        "python3 -c 'import asyncio; asyncio.create_subprocess_exec(\"rustup\", \"toolchain\", \"install\", \"stable\")'",
    )
    for command in commands:
        workflow = _workflow_with_run_command(command)
        assert packaging_gate._raw_toolchain_install_issue(workflow), command


def test_python_inline_launchers_handle_grouped_and_attached_c_options() -> None:
    """Grouped flags and attached source still expose Python ``-c`` code."""
    raw_payload = (
        'import os; os.system("rustup toolchain install stable")'
    )
    raw_commands = (
        f"python3 -Ic '{raw_payload}'",
        f"python3 -Sc '{raw_payload}'",
        f"python3 -c'{raw_payload}'",
        f"python3 -Ic'{raw_payload}'",
        f"python3 -Sc'{raw_payload}'",
    )
    for command in raw_commands:
        workflow = _workflow_with_run_command(command)
        assert packaging_gate._raw_toolchain_install_issue(workflow), command
        assert not packaging_gate._python_command_reads_stdin_script(command)

    safe_commands = (
        'python3 -Ic \'print("rustup toolchain install stable")\'',
        'python3 -Sc\'print("rustup toolchain install stable")\'',
        'python3 -c\'print("rustup toolchain install stable")\'',
    )
    for command in safe_commands:
        workflow = _workflow_with_run_command(command)
        assert packaging_gate._raw_toolchain_install_issue(workflow) is None
        assert not packaging_gate._python_command_reads_stdin_script(command)


def test_python_inline_literal_data_and_safe_launchers_remain_accepted() -> None:
    """Quoted install text and a literal harmless launcher are not installs."""
    commands = (
        "python3 -c 'print(\"rustup toolchain install stable\")'",
        "python3 -c 'import os; os.system(\"echo safe\")'",
    )
    for command in commands:
        workflow = _workflow_with_run_command(command)
        assert packaging_gate._raw_toolchain_install_issue(workflow) is None


def test_opaque_python_inline_process_command_fails_closed() -> None:
    """A dynamic process command cannot be proven free of raw provisioning."""
    workflow = _workflow_with_run_command(
        "python3 -c 'import os; os.system(command)'"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow)


def test_python_stdin_heredocs_are_analyzed_as_python_source() -> None:
    """Python stdin is checked without confusing string literals for commands."""
    unsafe = _workflow_with_run_command(
        "python3 - <<'PY'\n"
        "import os\n"
        "os.system('rustup toolchain install stable')\n"
        "PY"
    )
    safe = _workflow_with_run_command(
        "python3 - <<'PY'\n"
        "print('rustup toolchain install stable')\n"
        "PY"
    )

    assert packaging_gate._raw_toolchain_install_issue(unsafe)
    assert packaging_gate._raw_toolchain_install_issue(safe) is None


def test_public_release_gate_check_rejects_python_launcher(monkeypatch) -> None:
    """The public toolchain gate applies the inline-Python scanner."""
    workflow = _workflow_with_run_command(
        "python3 -c 'import os; os.system(\"rustup toolchain install stable\")'"
    )
    monkeypatch.setattr(packaging_gate, "read_safe", lambda _path: workflow)
    result = packaging_gate.ValidationResult()

    packaging_gate.check_release_gate_toolchain(result)

    assert result.has_failures
    assert any(
        status == "FAIL" and check_id == packaging_gate.PKG_RELEASE_GATE_TOOLCHAIN_GATE
        for status, check_id, _message in result.results
    )


def test_job_run_scripts_requires_a_parseable_job() -> None:
    """Only executable run steps of an existing job feed the gate."""
    text = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - {name: a, run: 'echo hello'}\n"
    )
    assert packaging_gate._job_run_scripts(text, "release-gate") == [
        "echo hello"
    ]
    assert packaging_gate._job_run_scripts(text, "missing") is None
    assert packaging_gate._job_run_scripts("jobs: [", "release-gate") is None


# Version-token patterns for the two pins the release gate relies on:
# jsonschema backs the policy-matrix validation in the docs-check chain,
# PyYAML backs the matrix tooling that chain runs.  Each pattern is the
# strongest form of the production check: the line must be exactly the
# pin, `==`, and a version token (never a comment or a bare separator).
_PIN_LINES = (
    (
        "jsonschema[format]",
        re.compile(r"^jsonschema\[format\]==[^#\s]\S*$", re.M),
    ),
    ("PyYAML", re.compile(r"^PyYAML==[^#\s]\S*$", re.M)),
)


def test_release_gate_job_installs_the_release_python_dependencies(
    monkeypatch,
) -> None:
    """The release-gate job runs ``make docs-check``, whose contract-matrix
    step imports jsonschema, and a fresh runner only provides what
    requirements-release.txt installs; the install step and the pins must
    both stay in place (the first real run failed exactly here).

    Drives the production check (``_python_deps_issue``): the guard must
    see the install in executable position before the docs-check, drop a
    disabled step, drop a dead branch, and refuse when either pin loses
    its version token.
    """
    steps = packaging_gate._job_run_scripts(
        WORKFLOW.read_text(encoding="utf-8"), "release-gate")
    assert steps is not None, "the release-gate job must be parseable"
    # A step the workflow disables is not part of the job's shell.
    disabled = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - if: false\n"
        "        run: python3 -m pip install --requirement "
        "requirements-release.txt\n"
    )
    assert packaging_gate._job_run_scripts(disabled, "release-gate") == [], (
        "a step the workflow disables must not feed the job's shell")
    assert packaging_gate._python_deps_issue(steps) is None, (
        "the checked-in release-gate job must satisfy the python-deps guard")
    # A dead branch cannot satisfy the guard: liveness drops `false && ...`.
    dead = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - run: |\n"
        "          set -e\n"
        "          false && python3 -m pip install --requirement "
        "requirements-release.txt\n"
        "          make docs-check\n"
    )
    dead_steps = packaging_gate._job_run_scripts(dead, "release-gate")
    assert packaging_gate._python_deps_issue(dead_steps) is not None, (
        "a dead install branch must not satisfy the guard")
    requirements = (REPO_ROOT / "requirements-release.txt").read_text(
        encoding="utf-8")
    # Each pin must carry an actual version token after ==, not just the
    # separator; a pin stripped to its bare name must fail the gate.
    for name, pattern in _PIN_LINES:
        assert pattern.search(requirements), (
            f"the checked-in requirements-release.txt must pin {name} to a "
            "version token")
    # Serve each unpinned variant through read_safe itself: a file written
    # under tmp_path would sit outside PROJECT_ROOT and read as empty, so
    # the assertion would pass on the not-found branch without ever
    # exercising the pin check.  The message assertion pins the exact
    # branch production takes.
    original_read = packaging_gate.read_safe
    for name, pattern in _PIN_LINES:
        unpinned_text, substitutions = pattern.subn(
            name + "==", requirements)
        assert substitutions >= 1, (
            f"the {name} pin must be substitutable in requirements-release.txt")
        assert unpinned_text != requirements
        monkeypatch.setattr(
            packaging_gate,
            "read_safe",
            lambda path, _text=unpinned_text: (
                _text
                if path == packaging_gate.RELEASE_REQUIREMENTS
                else original_read(path)
            ),
        )
        try:
            pin_issue = packaging_gate._python_deps_issue(steps)
        finally:
            monkeypatch.setattr(packaging_gate, "read_safe", original_read)
        assert pin_issue is not None, (
            f"an unpinned {name} must fail the python-deps guard")
        assert f"must pin {name} to a version" in pin_issue, pin_issue


def test_release_gate_preflight_proves_the_jsonschema_format_extras() -> None:
    """The release preflight must prove the jsonschema [format] extras.

    The release gate validates schemas that declare ``format: date-time``
    and passes a ``FormatChecker``; a jsonschema install without its
    ``[format]`` extras imports and reports its version normally but
    registers no checkers, so those formats would be skipped silently.
    The preflight must therefore read the checker registry on its
    executable path, not merely the version string.
    """
    run_text = _job_live_text(
        WORKFLOW.read_text(encoding="utf-8"), "release-gate")
    assert "jsonschema[format]" in (
        REPO_ROOT / "requirements-release.txt").read_text(encoding="utf-8")
    assert "from jsonschema import FormatChecker" in run_text, (
        "the release preflight must import the format checker")
    assert "FormatChecker().checkers" in run_text, (
        "the preflight must read the checker registry, since the version "
        "string alone cannot prove the extras are installed")


def test_release_gate_job_downloads_fuzz_record_before_evidence() -> None:
    """The final evidence consumes the exact record from the producer job."""
    _assert_record_handoff(_workflow())


def test_release_dependency_preflight_name_covers_all_python_gate_deps() -> None:
    """The preflight label must describe its Python and schema checks."""
    job = _workflow()["jobs"]["release-gate"]
    step = _named_step(job, "Verify release gate dependencies")
    command = step.get("run", "")

    assert "import brotli, yaml, jsonschema" in command
    assert "FormatChecker" in command


@pytest.mark.parametrize(
    ("step_name", "field", "value"),
    [
        ("Download fuzz qualification record", "if", "false"),
        ("Download fuzz qualification record", "continue-on-error", True),
        ("Verify fuzz qualification record was downloaded", "if", "false"),
        ("Verify fuzz qualification record was downloaded", "continue-on-error", True),
    ],
)
def test_record_handoff_rejects_disabled_or_soft_failed_steps(
    step_name, field, value
) -> None:
    """A disabled handoff or verifier cannot satisfy the release contract."""
    workflow = copy.deepcopy(_workflow())
    step = _named_step(workflow["jobs"]["release-gate"], step_name)
    step[field] = value

    with pytest.raises(AssertionError):
        _assert_record_handoff(workflow)


def test_job_live_text_uses_production_shell_segmenting_for_semicolon_chains():
    """The test reader matches production shell behavior without implicit -e."""
    command = "false; make docs-check"
    workflow = _workflow_with_run_command(command, shell="bash {0}")
    live = _job_live_text(workflow, "release-gate")

    assert live == "\n".join(packaging_gate._step_live_commands({
        "run": command,
        "shell": "bash {0}",
    }))
    assert "false" in live
    assert "make docs-check" in live


def test_fuzz_job_runs_the_qualification_before_uploading() -> None:
    """The producer ordering: the qualification actually runs, then the
    record uploads; the upload cannot precede the run."""
    workflow = _workflow()
    steps = workflow["jobs"]["fuzz-qualification"]["steps"]
    names = [step.get("name", "") for step in steps]
    qualification_at = names.index("Run fuzz qualification")
    upload_at = names.index("Upload fuzz qualification record")
    assert qualification_at < upload_at
    run = steps[qualification_at].get("run", "")
    assert "validate_fuzz_qualification.py" in run
    assert "--mode real" in run
    # The upload publishes the record the release gate reads.
    upload = steps[upload_at].get("with", {})
    assert upload.get("name") == RECORD_ARTIFACT_NAME
    assert "fuzz-qualification-record.json" in upload.get("path", "")


def test_release_gate_scripts_do_not_shadow_matched_commands() -> None:
    """No gate script may define a shell function that shadows a command
    the provisioning checks read.

    This drives the production guard itself (``_provisioning_shadow_issue``
    over both provisioning jobs), not a test-local regex: a narrower copy
    of the check can pass while the real gate has a hole, and the copy
    missed `eval` — the exact wrapper the shadow guard exists for.
    """
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert packaging_gate._provisioning_shadow_issue(workflow_text) is None
    # The guard must actually reject the shapes it exists for, in both
    # jobs: a no-op rustup() in the fuzz job defeats its checks exactly
    # like one in the release-gate job.
    for job in ("release-gate", "fuzz-qualification"):
        for definition in (
            "rustup() { :; }",
            "function rustup { :; }",
            "true && bash() { :; }",
            "eval() { :; }",
        ):
            poisoned = (
                "jobs:\n"
                f"  {job}:\n"
                "    steps:\n"
                "      - run: |\n"
                f"          {definition}\n"
                "          rustup component add --toolchain nightly rust-src\n"
            )
            issue = packaging_gate._provisioning_shadow_issue(poisoned)
            assert issue is not None, (job, definition)
            assert job in issue, (job, definition, issue)


def test_packaging_gate_main_runs_every_check_against_the_repo() -> None:
    """The CLI entry point wires every check and passes on this repo.

    The leaf tests cannot see a `main()` that stops calling a check, so
    this drives the production entry point itself: exit 0 with no FAIL
    rows and the toolchain gate among the reported checks.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = packaging_gate.main()
    report = buffer.getvalue()
    assert rc == 0, report
    pass_rows = [line for line in report.splitlines() if line.startswith("  PASS")]
    fail_rows = [line for line in report.splitlines() if line.startswith("  FAIL")]
    summary = re.search(
        r"Summary: (?P<passed>\d+) passed, (?P<failed>\d+) failed, "
        r"(?P<skipped>\d+) skipped",
        report,
    )
    assert summary is not None, report
    assert int(summary["passed"]) == len(pass_rows), report
    assert int(summary["failed"]) == len(fail_rows) == 0, report
    pass_ids = {line.split()[1] for line in pass_rows}
    assert packaging_gate.PKG_RELEASE_GATE_TOOLCHAIN_GATE in pass_ids, report


def test_packaging_gate_main_fails_when_the_workflow_is_unreadable(
    monkeypatch,
) -> None:
    """An unreadable workflow must surface as the toolchain gate's FAIL path.

    Stubbing the file read (not the check) keeps the wiring honest: the
    unreadable workflow flows through `main()` into the gate's own
    failure branch even while the file exists on disk.  The specific
    FAIL row is asserted, so a check that stopped being wired into
    `main()` cannot pass this test on some other check's failure.
    """
    monkeypatch.setattr(packaging_gate, "read_safe", lambda path: "")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = packaging_gate.main()
    report = buffer.getvalue()
    assert rc == 1, report
    fail_rows = [
        line for line in report.splitlines() if line.startswith("  FAIL")
    ]
    assert any(
        "pkg:release-gate-toolchain" in line and "not found" in line
        for line in fail_rows
    ), report


def test_packaging_gate_main_fails_closed_on_malformed_workflow_yaml(
    monkeypatch,
) -> None:
    """Unparseable workflow YAML must fail the gate, not skip its checks.

    The toolchain and python-deps checks both parse the workflow through
    `_job_run_scripts`; a malformed document must reach their FAIL branch
    with the release-gate job reported as unfindable rather than
    short-circuiting the entry point.
    """
    original_read = packaging_gate.read_safe
    monkeypatch.setattr(
        packaging_gate,
        "read_safe",
        lambda path: (
            "jobs: [" if path == packaging_gate.RELEASE_PACKAGES_WORKFLOW
            else original_read(path)
        ),
    )
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = packaging_gate.main()
    report = buffer.getvalue()
    assert rc == 1, report
    assert "pkg:release-gate-toolchain" in report, report
    assert any(
        line.startswith("  FAIL") and "pkg:release-gate-toolchain" in line
        and "release-gate job not found" in line
        for line in report.splitlines()
    ), report


def test_read_safe_rejects_paths_outside_the_project_root(
    tmp_path, monkeypatch
) -> None:
    """Containment violations and missing files both read as empty.

    `read_safe` must not leak content from outside the project root, and
    the same empty signal is what the checks already turn into a FAIL.
    A `..` component and a symlink that resolves outside the root are the
    two ways a path reaches past containment; both must read as empty.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", root)
    assert packaging_gate.read_safe(outside) == ""
    assert packaging_gate.read_safe(root / ".." / "outside.txt") == ""
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
        symlinked = True
    except (OSError, NotImplementedError):
        # Platforms or filesystems without symlink support: the traversal
        # fixture above still covers containment.
        symlinked = False
    if symlinked:
        assert packaging_gate.read_safe(link) == ""
    inside = root / "inside.txt"
    inside.write_text("visible", encoding="utf-8")
    assert packaging_gate.read_safe(inside) == "visible"
    assert packaging_gate.read_safe(root / "missing.txt") == ""
