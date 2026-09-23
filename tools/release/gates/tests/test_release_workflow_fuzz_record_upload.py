"""Workflow-wiring tests for the fuzz qualification record handoff.

The record and the per-target raw logs are the only post-mortem evidence
when the fuzz qualification fails; the workflow's other upload steps sit on
success paths, so the record upload step is asserted to be unconditional
(``if: always()``) and to point at the paths the validator actually writes.
The paths are resolved from the workflow's own RELEASE_VERSION variable and
checked against the validator's constants, so a version bump cannot desync
the two ends.  The sibling suite
(``test_validate_fuzz_packaging.py``) attacks the shell-semantics model of
the packaging gate itself; this module keeps the wiring contracts.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

import tools.release.gates.validate_fuzz_qualification as validator
from tools.release.gates import validate_fuzz_packaging as packaging_gate

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def packaging_gate_module():
    return packaging_gate


def test_fuzz_record_upload_is_unconditional_and_matches_the_validator() -> None:
    steps = _workflow()["jobs"]["fuzz-qualification"]["steps"]
    uploads = [
        step for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Upload fuzz qualification record"
    ]
    assert len(uploads) == 1, "the record upload step is missing"
    step = uploads[0]
    uses = step.get("uses", "")
    assert uses.startswith("actions/upload-artifact"), (
        "the record upload step must actually upload an artifact")
    assert step.get("if") == "always()", (
        "the record upload must survive a failing fuzz qualification")
    path_text = step.get("with", {}).get("path", "")
    # The path derives from the workflow's own RELEASE_VERSION variable;
    # resolving it must reproduce the validator's real write paths for
    # record and logs, so a version bump cannot desync the two ends.
    release_version = _workflow().get("env", {}).get("RELEASE_VERSION")
    assert isinstance(release_version, str)
    assert release_version
    resolved = path_text.replace(
        "${{ env.RELEASE_VERSION }}", release_version)
    record = validator.DEFAULT_RECORD.rstrip("/")
    log_dir = validator.DEFAULT_LOG_DIR.rstrip("/")
    assert record in resolved
    assert log_dir + "/" in resolved
    assert record == f"artifacts/release/{release_version}/" + record.rsplit("/", 1)[-1]
    assert log_dir == f"artifacts/release/{release_version}/" + log_dir.rsplit("/", 1)[-1]


def test_fuzz_qualification_runs_in_its_own_job_and_gates_publish() -> None:
    """The fuzz soak owns a dedicated job (full budget) and every publish
    junction requires it; the release gate no longer runs it."""
    jobs = _workflow()["jobs"]
    assert "fuzz-qualification" in jobs
    module = packaging_gate_module()
    live_fuzz: list[str] = []
    for step in jobs["fuzz-qualification"]["steps"]:
        if not isinstance(step, dict) or not module._step_runs_shell(step):
            continue
        stripped = module._strip_heredocs(
            module._strip_shell_comments(step.get("run", "")))
        executable = module._join_continuations(
            module._strip_function_bodies(stripped))
        live_fuzz.extend(module._live_command_segments(executable))
    run_text = "\n".join(live_fuzz)
    # Commands behind `if false` cannot satisfy the structure contract.
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
    dead_job = yaml.safe_load(dead)["jobs"]["fuzz-qualification"]
    dead_live: list[str] = []
    for step in dead_job["steps"]:
        stripped = module._strip_heredocs(
            module._strip_shell_comments(step.get("run", "")))
        executable = module._join_continuations(
            module._strip_function_bodies(stripped))
        dead_live.extend(module._live_command_segments(executable))
    dead_text = "\n".join(dead_live)
    assert "validate_fuzz_qualification.py --mode real --git-head" not in (
        dead_text)
    assert "generate_release_gate_manifests.py" not in dead_text
    assert "validate_fuzz_qualification.py --mode real --git-head" in run_text
    # The fuzz manifests are generated at run time (not tracked), so the job
    # must generate its own candidate-bound copies before the validator runs.
    assert "generate_release_gate_manifests.py" in run_text
    # The release gate must not run the fuzz qualification any more.
    gate_text = "\n".join(
        step.get("run", "") for step in jobs["release-gate"]["steps"]
        if isinstance(step, dict)
    )
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
    its own nightly toolchain separately.
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


_PIP_REQUIREMENT_COMMAND = re.compile(
    r"(?:sudo\s+)?(?:python3?\s+-m\s+pip|pip3?)\s+install\s+"
    r"(?:-r|--requirement)(?:\s+|=)[\"']?requirements-release\.txt[\"']?\b"
)


def _live_segments(script: str) -> list[str]:
    """Live command segments of a workflow run script."""
    stripped = packaging_gate._strip_heredocs(
        packaging_gate._strip_shell_comments(script))
    executable = packaging_gate._join_continuations(
        packaging_gate._strip_function_bodies(stripped))
    return packaging_gate._live_command_segments(executable)


def _pip_install_steps(steps: list[str]) -> list[int]:
    """Steps whose live segments install the pinned release requirements
    through pip, in any shell-equivalent spelling."""
    found: list[int] = []
    for index, step in enumerate(steps):
        for segment in _live_segments(step):
            if _PIP_REQUIREMENT_COMMAND.match(
                packaging_gate._strip_provision_wrappers(segment)
            ):
                found.append(index)
    return found


def test_release_gate_job_installs_the_release_python_dependencies(
    monkeypatch,
    tmp_path,
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
    assert steps is not None
    # A step the workflow disables is not part of the job's shell.
    disabled = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - if: false\n"
        "        run: python3 -m pip install --requirement "
        "requirements-release.txt\n"
    )
    assert packaging_gate._job_run_scripts(disabled, "release-gate") == []
    assert packaging_gate._python_deps_issue(steps) is None
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
    # jsonschema backs the policy-matrix validation in the docs-check chain;
    # PyYAML backs the matrix tooling that chain runs.  Each pin must carry
    # an actual version token after ==, not just the separator; a pin
    # stripped to its bare name must fail the gate.
    assert re.search(r"^jsonschema\[format\]==[^#\s]", requirements, re.M)
    assert re.search(r"^PyYAML==[^#\s]", requirements, re.M)
    unpinned = tmp_path / "requirements-release.txt"
    unpinned.write_text(
        requirements.replace("jsonschema[format]==4.23.0", "jsonschema[format]=="),
        encoding="utf-8")
    monkeypatch.setattr(packaging_gate, "RELEASE_REQUIREMENTS", unpinned)
    assert packaging_gate._python_deps_issue(steps) is not None


def test_release_gate_job_downloads_fuzz_record_before_evidence() -> None:
    """The final evidence reads the fuzz record: the release-gate job must
    take the artifact handoff, and the producer must run first."""
    workflow = _workflow()
    job = workflow["jobs"]["release-gate"]
    assert "fuzz-qualification" in job["needs"]
    names = [step.get("name", "") for step in job["steps"]]
    assert "Download fuzz qualification record" in names
    download_at = names.index("Download fuzz qualification record")
    gate_at = names.index("Run release gates")
    assert download_at < gate_at
    download = job["steps"][download_at]
    assert download.get("with", {}).get("name") == "fuzz-qualification-record"
    # The download path derives from the workflow's own RELEASE_VERSION
    # variable, and that variable must match the version directory the
    # validator actually writes into -- otherwise the handoff silently
    # targets a stale directory after a version bump.
    download_path = download.get("with", {}).get("path")
    assert isinstance(download_path, str)
    assert "${{ env.RELEASE_VERSION }}" in download_path
    record_dir = validator.DEFAULT_RECORD.rsplit("/", 1)[0]
    workflow_doc = _workflow()
    release_version = workflow_doc.get("env", {}).get("RELEASE_VERSION")
    assert isinstance(release_version, str)
    assert release_version
    assert record_dir == f"artifacts/release/{release_version}"
    resolved_path = download_path.replace(
        "${{ env.RELEASE_VERSION }}", release_version)
    assert resolved_path.rstrip("/") == record_dir
    uploads = [
        step
        for step in workflow["jobs"]["fuzz-qualification"]["steps"]
        if step.get("name", "") == "Upload fuzz qualification record"
    ]
    assert uploads
    assert uploads[0].get("with", {}).get("name") == "fuzz-qualification-record"


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
    assert upload.get("name") == "fuzz-qualification-record"
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
    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = packaging_gate.main()
    report = buffer.getvalue()
    assert rc == 0, report
    assert "FAIL" not in report, report
    assert "pkg:release-gate-toolchain" in report, report
    assert "Summary: 15 passed, 0 failed, 0 skipped" in report, report


def test_packaging_gate_main_fails_when_the_workflow_is_unreadable(
    monkeypatch,
) -> None:
    """A missing workflow must surface as the toolchain gate's FAIL path.

    Stubbing the file read (not the check) keeps the wiring honest: the
    unreadable workflow flows through `main()` into the gate's own
    failure branch even while the file exists on disk.
    """
    import io
    import contextlib
    monkeypatch.setattr(packaging_gate, "read_safe", lambda path: "")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = packaging_gate.main()
    report = buffer.getvalue()
    assert rc == 1, report
    assert "FAIL" in report, report
    assert "pkg:release-gate-toolchain" in report, report


def test_read_safe_rejects_paths_outside_the_project_root(
    tmp_path, monkeypatch
) -> None:
    """Containment violations and missing files both read as empty.

    `read_safe` must not leak content from outside the project root, and
    the same empty signal is what the checks already turn into a FAIL.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path / "root")
    assert packaging_gate.read_safe(outside) == ""
    inside = tmp_path / "root" / "inside.txt"
    inside.parent.mkdir()
    inside.write_text("visible", encoding="utf-8")
    assert packaging_gate.read_safe(inside) == "visible"
    assert packaging_gate.read_safe(tmp_path / "root" / "missing.txt") == ""
