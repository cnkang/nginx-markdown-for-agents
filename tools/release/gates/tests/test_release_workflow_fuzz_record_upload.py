"""The Release Gate must upload the fuzz qualification record on failure too.

The record and the per-target raw logs are the only post-mortem evidence
when the fuzz qualification fails; the workflow's other upload steps sit on
success paths, so this step is asserted to be unconditional (``if:
always()``) and to point at the paths the validator actually writes.  The
paths are taken from the validator's own constants, so renaming a record
path without updating the workflow fails here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import tools.release.gates.validate_fuzz_qualification as validator

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_fuzz_record_upload_is_unconditional_and_matches_the_validator() -> None:
    steps = _workflow()["jobs"]["fuzz-qualification"]["steps"]
    uploads = [
        step for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Upload fuzz qualification record"
    ]
    assert len(uploads) == 1, "the record upload step is missing"
    step = uploads[0]
    assert step.get("if") == "always()", (
        "the record upload must survive a failing fuzz qualification")
    path_text = step.get("with", {}).get("path", "")
    record = validator.DEFAULT_RECORD.rstrip("/")
    log_dir = validator.DEFAULT_LOG_DIR.rstrip("/")
    assert record in path_text
    assert log_dir + "/" in path_text


def test_fuzz_qualification_runs_in_its_own_job_and_gates_publish() -> None:
    """The fuzz soak owns a dedicated job (full budget) and every publish
    junction requires it; the release gate no longer runs it."""
    jobs = _workflow()["jobs"]
    assert "fuzz-qualification" in jobs
    run_text = "\n".join(
        step.get("run", "") for step in jobs["fuzz-qualification"]["steps"]
        if isinstance(step, dict)
    )
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
    # Publish and the integrity junctions must require the fuzz job.
    for job in ("publish", "integrity-checksums", "official-docker-release-gate"):
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
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    scripts = packaging_gate._job_run_scripts(
        WORKFLOW.read_text(encoding="utf-8"), "release-gate"
    )
    assert scripts is not None, "the release-gate job must be parseable"
    assert packaging_gate._release_gate_toolchain_issue(scripts) is None


def test_toolchain_gate_ignores_comments_and_unrelated_installs() -> None:
    """Only executable commands may satisfy the provisioning gate.

    This mirrors the incident class: the gate binds the provisioning to the
    job that runs the drift check, and a commented-out install or drift
    check, a missing rustfmt component, or an unpinned toolchain install
    must each fail it.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    install = (
        'retry 5 rustup toolchain install "${RUST_TOOLCHAIN}" '
        "--profile minimal \\\n"
        "  --component rustfmt --no-self-update\n"
    )

    assert packaging_gate._release_gate_toolchain_issue(drift)  # no install
    assert packaging_gate._release_gate_toolchain_issue(  # commented install
        drift + "# " + install
    )
    assert packaging_gate._release_gate_toolchain_issue(  # no rustfmt
        drift + install.replace("--component rustfmt ", "")
    )
    assert packaging_gate._release_gate_toolchain_issue(  # unpinned toolchain
        drift + "rustup toolchain install nightly --component rustfmt\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(  # commented drift check
        "# " + drift + install
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift
            + "bash ./packaging/scripts/install-verified-rustup.sh "
            '--arch x86_64 --toolchain "${RUST_TOOLCHAIN}"\n'
        )
        is None
    )
    assert packaging_gate._release_gate_toolchain_issue(drift + install) is None

    # Command position matters: echoes and heredoc bodies must not count.
    echo_install = 'echo \'rustup toolchain install "${RUST_TOOLCHAIN}" --component rustfmt\'\n'
    assert packaging_gate._release_gate_toolchain_issue(drift + echo_install)
    echo_drift = "echo 'python3 tools/reason-codegen/generate.py --check'\n"
    assert packaging_gate._release_gate_toolchain_issue(echo_drift + install)
    heredoc = (
        "cat <<'EOF'\n"
        + "rustup toolchain install " + '"${RUST_TOOLCHAIN}" --component rustfmt\n'
        + "python3 tools/reason-codegen/generate.py --check\n"
        + "EOF\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(heredoc)

    # A later command on the same line must not satisfy the requirement:
    # the separator ends the install command, so an echoed component after
    # `;`, `&&` or a pipe never counts.
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + 'rustup toolchain install "${RUST_TOOLCHAIN}"; echo --component rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + 'rustup toolchain install "${RUST_TOOLCHAIN}" && echo --component rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + 'rustup toolchain install "${RUST_TOOLCHAIN}" | echo --component rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + './packaging/scripts/install-verified-rustup.sh; '
        'echo --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    # The component before a trailing separator is still the same command.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift
            + 'retry 5 rustup toolchain install "${RUST_TOOLCHAIN}" '
            "--profile minimal --component rustfmt; echo done\n"
        )
        is None
    )


def test_job_run_scripts_requires_a_parseable_job() -> None:
    """Only executable run steps of an existing job feed the gate."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    text = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - {name: a, run: 'echo hello'}\n"
    )
    assert packaging_gate._job_run_scripts(text, "release-gate") == "echo hello"
    assert packaging_gate._job_run_scripts(text, "missing") is None
    assert packaging_gate._job_run_scripts("jobs: [", "release-gate") is None
