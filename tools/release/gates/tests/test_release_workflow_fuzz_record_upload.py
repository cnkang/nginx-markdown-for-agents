"""The Release Gate must upload the fuzz qualification record on failure too.

The record and the per-target raw logs are the only post-mortem evidence
when the fuzz qualification fails; the workflow's other upload steps sit on
success paths, so this step is asserted to be unconditional (``if:
always()``) and to point at the paths the validator actually writes.  The
paths are taken from the validator's own constants, so renaming a record
path without updating the workflow fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

import tools.release.gates.validate_fuzz_qualification as validator

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def packaging_gate_module():
    from tools.release.gates import validate_fuzz_packaging as packaging_gate
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
    record = validator.DEFAULT_RECORD.rstrip("/")
    log_dir = validator.DEFAULT_LOG_DIR.rstrip("/")
    assert record in path_text
    assert log_dir + "/" in path_text


def test_fuzz_qualification_runs_in_its_own_job_and_gates_publish() -> None:
    """The fuzz soak owns a dedicated job (full budget) and every publish
    junction requires it; the release gate no longer runs it."""
    jobs = _workflow()["jobs"]
    assert "fuzz-qualification" in jobs
    run_text = packaging_gate_module()._strip_heredocs(
        packaging_gate_module()._strip_shell_comments(
            "\n".join(
                step.get("run", "")
                for step in jobs["fuzz-qualification"]["steps"]
                if isinstance(step, dict)
            )
        )
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
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    scripts = packaging_gate._job_run_scripts(
        WORKFLOW.read_text(encoding="utf-8"), "release-gate"
    )
    assert scripts is not None, "the release-gate job must be parseable"
    assert packaging_gate._release_gate_toolchain_issue(scripts) is None


def test_toolchain_gate_ignores_comments_and_unrelated_installs() -> None:
    """Only executable commands may satisfy the provisioning gate.

    This mirrors the incident class: the gate binds the provisioning to the
    job that runs the drift check; the pinned toolchain must come through
    the verified installer with an explicit bash invocation and the rustfmt
    component, while commented-out, echoed, heredoc-embedded, quoted-text or
    separator-bypassed commands must each fail it.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + installer + component
        )
        is None
    )

    # Raw rustup installs are rejected: the verified installer is required.
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + 'retry 5 rustup toolchain install "${RUST_TOOLCHAIN}" --profile '
        "minimal --component rustfmt\n"
        + component
    )
    # The installer must be invoked with an explicit bash.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + installer.replace("bash ./", "./") + component
    )
    # rustfmt must be provisioned for the pinned toolchain.
    assert packaging_gate._release_gate_toolchain_issue(drift + installer)
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + installer
        + "retry 5 rustup component add --toolchain nightly rustfmt\n"
    )
    # No drift check means the provisioning expectation is stale.
    assert packaging_gate._release_gate_toolchain_issue(installer + component)
    # A commented-out installer or drift check never counts.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "# " + installer + component
    )
    assert packaging_gate._release_gate_toolchain_issue(
        "# " + drift + installer + component
    )

    # Command position matters: echoes and heredoc bodies must not count.
    echo_install = "echo '" + installer.strip() + "'\n"
    assert packaging_gate._release_gate_toolchain_issue(
        drift + echo_install + component
    )
    echo_drift = "echo 'python3 tools/reason-codegen/generate.py --check'\n"
    assert packaging_gate._release_gate_toolchain_issue(
        echo_drift + installer + component
    )
    heredoc = "cat <<'EOF'\n" + installer + component + drift + "EOF\n"
    assert packaging_gate._release_gate_toolchain_issue(heredoc)
    # The backslash-escaped delimiter form is a heredoc too.
    heredoc_escaped = "cat <<\\EOF\n" + installer + component + drift + "EOF\n"
    assert packaging_gate._release_gate_toolchain_issue(heredoc_escaped)

    # A later command on the same line must not satisfy the requirement:
    # the separator ends the provisioning command.
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + "bash ./packaging/scripts/install-verified-rustup.sh; "
        'echo --toolchain "${RUST_TOOLCHAIN}"\n'
        + component
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + installer
        + 'rustup component add --toolchain "${RUST_TOOLCHAIN}"; echo rustfmt\n'
    )
    # Quoted text that spans lines is data, not a command.
    multiline_quote = (
        'echo "data\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "python3 tools/reason-codegen/generate.py --check\n"
        '"\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift + installer + multiline_quote
    )
    # The component before a trailing separator is still the same command.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift
            + installer
            + 'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" '
            "rustfmt; echo done\n"
        )
        is None
    )
    # A separator-prefixed compliant provisioning is still a command.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift
            + "echo ok && bash ./packaging/scripts/install-verified-rustup.sh "
            '--toolchain "${RUST_TOOLCHAIN}"\n'
            + component
        )
        is None
    )
    # The component cannot be added before the toolchain exists: under
    # ``set -eu`` the failed component command would skip the installer.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + component + installer
    )
    # A quoted separator stays data: it cannot satisfy the drift check.
    assert packaging_gate._release_gate_toolchain_issue(
        'echo "a; python3 tools/reason-codegen/generate.py --check"\n'
        + installer
        + component
    )
    # Normalization order is deliberate: comments and heredoc bodies are
    # removed before continuation joining, so a comment line ending in a
    # backslash never merges with (and strips) the command below it, and a
    # heredoc body line ending in a backslash never merges with the
    # terminator (which would swallow everything after it).
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "# setup note \\\n" + drift + installer + component
        )
        is None
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "cat <<'EOF'\nbody with backslash \\\nEOF\n"
            + drift
            + installer
            + component
        )
        is None
    )


def test_workflow_rejects_raw_toolchain_installs() -> None:
    """Every toolchain install in the workflow must use the verified installer.

    The release workflows provision through
    ``packaging/scripts/install-verified-rustup.sh`` (checksum-validated
    rustup-init); a raw ``rustup toolchain install`` anywhere in the
    workflow must fail the gate.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

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


def test_release_gate_job_installs_the_release_python_dependencies() -> None:
    """The release-gate job runs ``make docs-check``, whose contract-matrix
    step imports jsonschema, and a fresh runner only provides what
    requirements-release.txt installs; the install step and the pins must
    both stay in place (the first real run failed exactly here)."""
    jobs = _workflow()["jobs"]
    gate_text = "\n".join(
        step.get("run", "") for step in jobs["release-gate"]["steps"]
        if isinstance(step, dict)
    )
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    executable = packaging_gate._strip_heredocs(
        packaging_gate._strip_shell_comments(gate_text))
    install = "pip install --requirement requirements-release.txt"
    # A commented-out install line inside a heredoc body must not satisfy
    # the guard, and the install must run before the check that needs it.
    commented = (
        "# python3 -m pip install --requirement requirements-release.txt\n"
        "cat <<'NOTE'\n" + install + "\nNOTE\n"
    )
    stopped = packaging_gate._strip_heredocs(
        packaging_gate._strip_shell_comments(commented))
    assert install not in stopped
    install_at = executable.find(install)
    assert install_at >= 0, "the release-gate job must install the pins"
    check_at = executable.find("make docs-check")
    assert install_at < check_at, (
        "the install step must precede the docs-check that needs it")
    # The install must be its own command, not quoted or echoed text.
    command_segments = packaging_gate._command_segments(
        packaging_gate._join_continuations(executable))
    install_command = re.compile(
        r"(?:sudo\s+)?(?:python3?\s+-m\s+pip|pip3?)\s+install\s+"
        r"--requirement\s+requirements-release\.txt\b"
    )
    assert any(install_command.match(segment) for segment in command_segments), (
        "the install must run as a command, not as quoted text")
    requirements = (REPO_ROOT / "requirements-release.txt").read_text(
        encoding="utf-8")
    # jsonschema backs the policy-matrix validation in the docs-check chain;
    # PyYAML backs the matrix tooling that chain runs.  Each pin must carry
    # an actual version token after ==, not just the separator.
    assert re.search(r"^jsonschema\[format\]==[^#\s]", requirements, re.M), (
        "requirements-release.txt must pin jsonschema[format] to a version")
    assert re.search(r"^PyYAML==[^#\s]", requirements, re.M), (
        "requirements-release.txt must pin PyYAML to a version")


def test_toolchain_gate_rejects_runtime_expanded_heredoc_delimiters() -> None:
    """A heredoc opened with ``<<$WORD`` cannot be delimited statically.

    The shell expands a plain delimiter word at runtime, so the gate must
    refuse to interpret the script instead of scanning fake commands inside
    the body as executable content (or dropping a body it cannot find).
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    fake = "cat <<$EOF\n" + installer + component + drift + "EOF\n"
    assert packaging_gate._release_gate_toolchain_issue(fake)
    # The raw-install detector must reject the unverifiable script too: a raw
    # install could hide behind the runtime-expanded delimiter.
    workflow = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - name: b\n"
        "        run: |\n"
        "          cat <<$EOF\n"
        "          rustup toolchain install nightly --profile minimal\n"
        "          EOF\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow)
    # A quoted delimiter is static: the literal word is the terminator, the
    # body is dropped, and real provisioning after it still satisfies.
    quoted = (
        "cat <<'$EOF'\nbody\n$EOF\n" + drift + installer + component
    )
    assert packaging_gate._release_gate_toolchain_issue(quoted) is None


def test_toolchain_gate_sees_commands_after_a_multiline_quote_closes() -> None:
    """Text after the closing quote on a continued line is executable again.

    Quote state carries across lines, but the remainder of the line that
    closes the quote is real shell code: a raw install chained there must
    trip the detector.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    workflow = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - name: b\n"
        "        run: |\n"
        '          echo "x\n'
        "          \"; rustup toolchain install nightly --profile minimal\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow)


def test_toolchain_gate_handles_escaped_quotes() -> None:
    """Backslash-escaped quotes never toggle the quote state.

    Outside quotes an escaped quote is a literal quote, so a following
    separator is real and a chained raw install must be caught; inside double
    quotes an escaped quote is data, so an install in that string must not be.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    def workflow(script_line: str) -> str:
        return (
            "jobs:\n"
            "  fuzz-qualification:\n"
            "    steps:\n"
            "      - name: b\n"
            "        run: |\n"
            "          " + script_line + "\n"
        )

    # Escaped quote: the separator is real, the install is a command.
    assert packaging_gate._raw_toolchain_install_issue(
        workflow('echo \\"x; rustup toolchain install nightly\\"')
    )
    # Escaped quote inside a double-quoted string: all data, no install.
    assert (
        packaging_gate._raw_toolchain_install_issue(
            workflow('echo "x\\"; rustup toolchain install nightly"')
        )
        is None
    )


def test_toolchain_gate_requires_exact_heredoc_terminators() -> None:
    """Only an exact delimiter line ends a heredoc body.

    Shell semantics: ``<<`` requires the bare delimiter and ``<<-`` strips
    leading tabs only, so a padded line keeps the body open.  A body the gate
    cannot close must not let its content count as executable, and real
    provisioning after a properly closed body still satisfies the gate.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    rest = drift + installer + component
    # A space-padded or tab-padded line does not terminate <<EOF, so the
    # body stays open and swallows the provisioning below it.
    assert packaging_gate._release_gate_toolchain_issue(
        "cat <<EOF\nbody\n  EOF\n" + rest
    )
    assert packaging_gate._release_gate_toolchain_issue(
        "cat <<EOF\nbody\n\tEOF\n" + rest
    )
    # <<- strips leading tabs, so a tab-padded terminator closes it and the
    # provisioning after the body is real again.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "cat <<-EOF\nbody\n\tEOF\n" + rest
        )
        is None
    )
    # A space-padded line does not close <<- either.
    assert packaging_gate._release_gate_toolchain_issue(
        "cat <<-EOF\nbody\n  EOF\n" + rest
    )


def test_toolchain_gate_splits_on_all_command_separators() -> None:
    """Subshells, background separators and command substitution run code.

    ``(``, ``)``, a single ``&`` and backticks each start a command position,
    so a raw install inside them must trip the detector.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    def workflow(script_line: str) -> str:
        return (
            "jobs:\n"
            "  fuzz-qualification:\n"
            "    steps:\n"
            "      - name: b\n"
            "        run: |\n"
            "          " + script_line + "\n"
        )

    for line in (
        "echo ok & rustup toolchain install nightly --profile minimal",
        "(rustup toolchain install nightly --profile minimal)",
        "$(rustup toolchain install nightly --profile minimal)",
        "echo `rustup toolchain install nightly --profile minimal`",
        'echo "`rustup toolchain install nightly --profile minimal`"',
        'echo "$(rustup toolchain install nightly --profile minimal)"',
    ):
        assert packaging_gate._raw_toolchain_install_issue(workflow(line)), line


def test_toolchain_gate_treats_quoted_parentheses_as_data() -> None:
    """Bare parentheses inside double quotes are data, not separators.

    Only a ``$``-preceded ``(`` opens a command substitution inside double
    quotes; splitting on bare quoted parentheses would break a compliant
    installer invocation that carries them in an argument.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--note "(amd64)" --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + installer + component
        )
        is None
    )


def test_toolchain_gate_ignores_dynamic_markers_in_comments_and_bodies() -> None:
    """Dynamic-delimiter markers in comments or heredoc bodies are data.

    The rejection scans the script after comment and static-heredoc
    stripping, so an inert marker never fails a workflow that provisions
    correctly.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    rest = drift + installer + component
    assert (
        packaging_gate._release_gate_toolchain_issue("# cat <<$EOF\n" + rest)
        is None
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "cat <<'EOF'\ncat <<$EOF\nEOF\n" + rest
        )
        is None
    )


def test_toolchain_gate_tracks_every_heredoc_body() -> None:
    """A command line may open several heredocs; all bodies are data.

    Bash reads the bodies in marker order, so tracking only the first
    delimiter would expose the second body's text as executable commands.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    script = "cat <<A <<B\nA\n" + drift + installer + component + "B\n"
    assert packaging_gate._release_gate_toolchain_issue(script)


def test_toolchain_gate_handles_escaped_backticks_in_double_quotes() -> None:
    """An escaped backtick inside double quotes is data, not a substitution.

    Bash runs no command substitution for an escaped backtick, so text that
    carries fake provisioning there must not satisfy the gate.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    script = (
        'echo "x\\` python3 tools/reason-codegen/generate.py --check"\n'
        'echo "y\\` bash ./packaging/scripts/install-verified-rustup.sh '
        '--toolchain ${RUST_TOOLCHAIN}"\n'
        'echo "z\\` rustup component add --toolchain ${RUST_TOOLCHAIN} '
        'rustfmt"\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(script)


def test_toolchain_gate_ignores_function_definition_bodies() -> None:
    """Defining a function runs nothing; provisioning must be top-level.

    The gate does not track calls, so a body that no one invokes cannot
    count as executed provisioning.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    body = drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(
        "provision() {\n" + body + "}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(
        "function provision {\n" + body + "}\n"
    )


def test_toolchain_gate_accepts_escaped_heredoc_delimiters() -> None:
    """An escaped delimiter word is literal, so the body stays droppable.

    The shell performs no expansion on ``<<\\$EOF``; the terminator is the
    literal word and the body is data.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    script = "cat <<\\$EOF\nbody\n$EOF\n" + drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_toolchain_gate_keeps_escape_pairs_intact() -> None:
    """Function-body stripping must not drop the second half of an escape.

    A backslash-continued command relies on the newline surviving until the
    continuation join; dropping it would hide a compliant drift check or
    provisioning command.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py \\\n  --check\n"
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + installer + component
        )
        is None
    )
    # The same holds when the script also defines a function.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "unused() {\n  echo hi\n}\n" + drift + installer + component
        )
        is None
    )


def test_toolchain_gate_treats_escaped_space_delimiter_as_one_word() -> None:
    """A backslash-escaped space keeps the delimiter word intact.

    ``<<EOF\ BAR`` declares the delimiter ``EOF BAR``; the provisioning
    text before the closing line is body data and must not satisfy the
    gate.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    body = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    script = ": <<EOF\\ BAR\nEOF\\\n" + body + "EOF BAR\n"
    assert packaging_gate._release_gate_toolchain_issue(script) is not None


def test_toolchain_gate_joins_continued_marker_lines() -> None:
    """A continued marker line reads its delimiter from the merged command.

    ``: <<EOF \\`` followed by ``EOF`` puts the second ``EOF`` in command
    position, so the body runs to the later terminator and the text in
    between is body data.
    """
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    body = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    script = ": <<EOF \\\nEOF\n:\n" + body + "EOF\n"
    assert packaging_gate._release_gate_toolchain_issue(script) is not None


def test_toolchain_gate_drops_subshell_function_bodies() -> None:
    """A definition whose body is a subshell runs nothing until called."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    script = (
        "provision() (\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        '  python3 tools/reason-codegen/generate.py --check\n'
        ")\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is not None


def test_toolchain_gate_drops_literally_dead_branches() -> None:
    """Provisioning behind a literal short-circuit never runs."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    # `false &&` skips the installer; the gate must not count it.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "false && " + installer + component
    )
    # The chain stays dead through further `&&` links...
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "false && " + installer.strip() + " && " + component.strip()
        + "\n"
    )
    # ...while `||` revives it, so a compliant script stays accepted.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + "false && echo skipped || " + installer + component
        )
        is None
    )
    # An `if false` branch is dead until its `fi`.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "if false; then\n" + installer + component + "fi\n"
    )
    # A branch that can run is live as before.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + "if true; then\n" + installer + component + "fi\n"
        )
        is None
    )


def test_toolchain_gate_skips_conditional_and_foreign_shell_steps() -> None:
    """A disabled or non-shell step cannot satisfy the provisioning gate."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check"
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"'
    )
    component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt'
    )
    runs = "\n".join((drift, installer, component))
    conditional = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - name: a\n"
        "        if: false\n"
        "        run: |\n"
        + "".join("          " + line + "\n" for line in runs.splitlines())
    )
    scripts = packaging_gate._job_run_scripts(conditional, "release-gate")
    assert packaging_gate._release_gate_toolchain_issue(scripts) is not None
    foreign = conditional.replace("        if: false\n", "        shell: python\n")
    scripts = packaging_gate._job_run_scripts(foreign, "release-gate")
    assert packaging_gate._release_gate_toolchain_issue(scripts) is not None
    # A plain bash step still counts.
    plain = conditional.replace("        if: false\n", "")
    scripts = packaging_gate._job_run_scripts(plain, "release-gate")
    assert packaging_gate._release_gate_toolchain_issue(scripts) is None


def test_toolchain_gate_accepts_mixed_and_escaped_delimiter_words() -> None:
    """Quote removal runs on delimiter words: mixed quoting and escapes."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    # <<E"OF" declares the literal delimiter EOF.
    mixed = "cat <<E\"OF\"\nbody\nEOF\n" + drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(mixed) is None
    # <<EOF\; declares the literal delimiter `EOF;`.
    escaped_meta = "cat <<EOF\\;\nbody\nEOF;\n" + drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(escaped_meta) is None


def test_toolchain_gate_counts_called_function_bodies() -> None:
    """A called function executes its body, so the body provisions."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    drift = "python3 tools/reason-codegen/generate.py --check\n"
    installer = (
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    called = "provision() {\n" + installer + component + "}\nprovision\n"
    assert packaging_gate._release_gate_toolchain_issue(
        called + drift) is None
    # A subshell body counts the same way.
    called_subshell = (
        "provision() (\n" + installer + component + ")\nprovision\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(
        called_subshell + drift) is None


def test_toolchain_gate_accepts_same_line_brace_groups() -> None:
    """A one-line brace group executes its commands in place."""
    from tools.release.gates import validate_fuzz_packaging as packaging_gate

    script = (
        "{ python3 tools/reason-codegen/generate.py --check; "
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"; '
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt; }\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is None
