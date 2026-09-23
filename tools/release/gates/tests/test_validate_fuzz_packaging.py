"""Adversarial tests for the fuzz/packaging shell-semantics gate.

The gate (``tools/release/gates/validate_fuzz_packaging.py``) models the
release workflow's run-block shell closely enough to decide which commands
can execute, so provisioning text cannot satisfy a check from a comment,
heredoc body, quoted string, dead branch, or unreachable function.  These
tests attack that model: each one pins one shell rule the gate must respect,
with paired acceptance/rejection shapes wherever both directions matter.
"""

from __future__ import annotations

import subprocess

from tools.release.gates import validate_fuzz_packaging as packaging_gate

# Shell fragments shared by the scenarios.  Single-sourced so one literal
# serves every test that builds a script around it.
DRIFT = "python3 tools/reason-codegen/generate.py --check\n"
INSTALLER = (
    "bash ./packaging/scripts/install-verified-rustup.sh "
    '--toolchain "${RUST_TOOLCHAIN}"\n'
)
COMPONENT = (
    'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
)
DRIFT_NO_NL = DRIFT.rstrip("\n")
INSTALLER_NO_NL = INSTALLER.rstrip("\n")
COMPONENT_NO_NL = COMPONENT.rstrip("\n")


def test_toolchain_gate_ignores_comments_and_unrelated_installs() -> None:
    """Only executable commands may satisfy the provisioning gate.

    This mirrors the incident class: the gate binds the provisioning to the
    job that runs the drift check; the pinned toolchain must come through
    the verified installer with an explicit bash invocation and the rustfmt
    component, while commented-out, echoed, heredoc-embedded, quoted-text or
    separator-bypassed commands must each fail it.
    """
    drift = DRIFT
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
    # Provisioning behind a chained command is conditional: the analyzer
    # cannot prove the left side succeeded, so a `&&` chain is not
    # unconditional provisioning.
    assert packaging_gate._release_gate_toolchain_issue(
        drift
        + "echo ok && bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        + component
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


def test_toolchain_gate_rejects_runtime_expanded_heredoc_delimiters() -> None:
    """A heredoc opened with ``<<$WORD`` cannot be delimited statically.

    The shell expands a plain delimiter word at runtime, so the gate must
    refuse to interpret the script instead of scanning fake commands inside
    the body as executable content (or dropping a body it cannot find).
    """
    drift = DRIFT
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
    drift = DRIFT
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


def test_cached_command_segments_return_fresh_mutable_lists() -> None:
    """Callers cannot corrupt later reads of the immutable cached parse."""
    script = "echo first; echo second\n"
    expected = packaging_gate._command_segments_with_separators(script)
    mutated = packaging_gate._command_segments_with_separators(script)
    mutated.clear()
    assert packaging_gate._command_segments_with_separators(script) == expected


def test_toolchain_gate_treats_quoted_parentheses_as_data() -> None:
    """Bare parentheses inside double quotes are data, not separators.

    Only a ``$``-preceded ``(`` opens a command substitution inside double
    quotes; splitting on bare quoted parentheses would break a compliant
    installer invocation that carries them in an argument.
    """
    drift = DRIFT
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
    drift = DRIFT
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
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    script = "cat <<A <<B\nA\n" + drift + installer + component + "B\n"
    assert packaging_gate._release_gate_toolchain_issue(script)


def test_toolchain_gate_handles_escaped_backticks_in_double_quotes() -> None:
    """An escaped backtick inside double quotes is data, not a substitution.

    Bash runs no command substitution for an escaped backtick, so fake
    provisioning inside one must not satisfy the gate -- and the real tiers
    behind the decoys must still satisfy it (the positive assertion is
    non-vacuous: if the escaped backticks desynced the quote state and
    swallowed the executable lines, it would flip to an issue).
    """
    escaped_decoys = (
        'echo "x\\` python3 tools/reason-codegen/generate.py --check"\n'
        'echo "y\\` bash ./packaging/scripts/install-verified-rustup.sh '
        '--toolchain ${RUST_TOOLCHAIN}"\n'
        'echo "z\\` rustup component add --toolchain ${RUST_TOOLCHAIN} '
        'rustfmt"\n'
    )
    # Decoys alone provision nothing.
    assert packaging_gate._release_gate_toolchain_issue(escaped_decoys)
    # Real tiers behind the decoys must satisfy the gate: the escaped
    # backticks are data and cannot have swallowed the executable lines.
    assert packaging_gate._release_gate_toolchain_issue(
        escaped_decoys + DRIFT + INSTALLER + COMPONENT) is None
    # Unescaped backticks are real substitutions: bash would run an install
    # inside one, so the raw-install detector must flag it...
    workflow = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - run: |\n"
        '          echo "` rustup toolchain install nightly --profile minimal `"\n'
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow)
    # ...while the escaped spelling stays inert for that detector too.
    assert packaging_gate._raw_toolchain_install_issue(
        workflow.replace("`", "\\`")) is None


def test_toolchain_gate_ignores_function_definition_bodies() -> None:
    """Defining a function runs nothing; provisioning must be top-level.

    The gate does not track calls, so a body that no one invokes cannot
    count as executed provisioning.
    """
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    body = drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(
        "provision() {\n" + body + "}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(
        "function provision {\n" + body + "}\n"
    )


def test_function_body_scan_only_closes_on_a_command_position_brace() -> None:
    """A brace passed to a command or glued into a word is not a closer."""
    suffix = INSTALLER + COMPONENT + "}\n" + DRIFT
    separated_argument = "unused() { echo before } ; " + suffix
    glued_argument = "unused() { echo a}b; " + suffix
    assert packaging_gate._release_gate_toolchain_issue(separated_argument)
    assert packaging_gate._release_gate_toolchain_issue(glued_argument)


def test_function_body_scan_fails_closed_on_extglob_boundaries() -> None:
    """Extglob's pattern braces are not modeled as function delimiters."""
    body = "unused() { case x in @(x|})) : ;; esac; "
    issue = packaging_gate._release_gate_toolchain_issue(
        body + INSTALLER + COMPONENT + "}\n" + DRIFT
    )
    assert issue is not None
    assert "extglob" in issue
    assert packaging_gate._release_gate_toolchain_issue(
        "# extglob decoy @(x|})\n" + DRIFT + INSTALLER + COMPONENT
    ) is None
    assert packaging_gate._release_gate_toolchain_issue(
        'echo "@(x|})"\n' + DRIFT + INSTALLER + COMPONENT
    ) is None


def test_ansi_c_escaped_quote_stays_quoted_across_all_scanners() -> None:
    """An escaped apostrophe inside $'...' cannot expose command-looking text."""
    decoy = r"echo $'quoted \' rustup toolchain install nightly; dead() { '" + "\n"
    assert packaging_gate._defined_function_names(decoy) == set()
    assert packaging_gate._raw_install_in_segment(
        packaging_gate._command_segments(decoy)[0]
    ) is False
    assert packaging_gate._release_gate_toolchain_issue(
        decoy + DRIFT + INSTALLER + COMPONENT
    ) is None


def test_toolchain_gate_accepts_escaped_heredoc_delimiters() -> None:
    """An escaped delimiter word is literal, so the body stays droppable.

    The shell performs no expansion on ``<<\\$EOF``; the terminator is the
    literal word and the body is data.
    """
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    script = "cat <<\\$EOF\nbody\n$EOF\n" + drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_toolchain_gate_keeps_escape_pairs_intact() -> None:
    """Function-body stripping must not drop the second half of an escape.

    A backslash-continued command relies on the newline surviving until the
    continuation join; dropping it would hide a compliant drift check or
    provisioning command.
    """
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
    r"""A backslash-escaped space keeps the delimiter word intact.

    ``<<EOF\ BAR`` declares the delimiter ``EOF BAR``; the provisioning
    text before the closing line is body data and must not satisfy the
    gate.
    """
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
    body = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    script = ": <<EOF \\\nEOF\n:\n" + body + "EOF\n"
    assert packaging_gate._release_gate_toolchain_issue(script) is not None


def test_continuation_decision_follows_the_end_quote_state() -> None:
    """The continuation check reads the quote state at the END of the line.

    A single-quoted string carried from a previous line (or opened mid-line)
    keeps a trailing backslash literal only while it stays open; once the
    quote closes mid-line the backslash escapes the newline and the next
    line must merge.  Both directions are asserted through the normalization
    the provisioning checks consume (``_strip_heredocs`` output).
    """
    # Quote closes mid-line: the trailing backslash escapes the newline and
    # the continuation merges.
    joined = packaging_gate._strip_heredocs("echo 'x\nline' arg\\\ny z\n")
    assert joined == "echo 'x\nline' arg y z"
    # Quote stays open: the trailing backslash is literal data, no merge.
    unjoined = packaging_gate._strip_heredocs("'literal\\\nnext\n")
    assert unjoined == "'literal\\\nnext"


def test_join_command_line_merges_each_continuation_and_stops_at_the_end() -> None:
    """The continuation loop merges a chain of lines and honors the bound.

    Each continued line runs the loop body once (a three-line chain proves
    the condition is not constant), and the bound stops the merge at the
    script end so an unterminated trailing backslash stays visible.
    """
    lines = ["one \\", "two \\", "three", "end"]
    merged = packaging_gate._join_command_line(lines, "one \\", 1, None)
    assert merged == ("one  two  three", 3)
    # Bound: no line follows, so the trailing backslash stays as written.
    bounded = packaging_gate._join_command_line(["x \\"], "x \\", 1, None)
    assert bounded == ("x \\", 1)


def test_toolchain_gate_drops_subshell_function_bodies() -> None:
    """A definition whose body is a subshell runs nothing until called."""
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
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    # `false &&` skips the installer; quoted command names remain executable.
    false_cases = (
        ("bare false", drift + "false && " + installer + component),
        ("quoted false", drift + '"false" && ' + installer + component),
    )
    scripts = [script for _label, script in false_cases]
    assert len(set(scripts)) == len(scripts)
    for _label, script in false_cases:
        assert packaging_gate._release_gate_toolchain_issue(script)

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
    # Any `if` construct is conditional: the analyzer does not evaluate
    # test expressions, so its body cannot count as provisioning.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "if false; then\n" + installer + component + "fi\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "if [ 1 -eq 0 ]; then\n" + installer + component + "fi\n"
    )
    # ...including the live side: a quoted `true` keeps the chain running.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + '"true" && ' + installer + component
        )
        is None
    )
    # `exit` ends that shell: nothing after it runs.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "exit 0\n" + installer + component
    )
    # Nested conditionals stay dead for the whole construct.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "if true; then\nif false; then\n" + installer + component
        + "fi\nfi\n"
    )


def test_toolchain_gate_skips_conditional_and_foreign_shell_steps() -> None:
    """A disabled or non-shell step cannot satisfy the provisioning gate."""
    drift = DRIFT_NO_NL
    installer = INSTALLER_NO_NL
    component = COMPONENT_NO_NL
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
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    # <<E"OF" declares the literal delimiter EOF.
    mixed = "cat <<E\"OF\"\nbody\nEOF\n" + drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(mixed) is None
    # <<EOF\; declares the literal delimiter `EOF;`.
    escaped_meta = "cat <<EOF\\;\nbody\nEOF;\n" + drift + installer + component
    assert packaging_gate._release_gate_toolchain_issue(escaped_meta) is None


def test_toolchain_gate_counts_called_function_bodies() -> None:
    """A called function executes its body, so the body provisions."""
    drift = DRIFT
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
    # Mentioning the name as an argument is not a call.
    assert packaging_gate._release_gate_toolchain_issue(
        "provision() {\n" + installer + component + "}\necho provision\n"
        + drift
    )


def test_toolchain_gate_accepts_same_line_brace_groups() -> None:
    """A one-line brace group executes its commands in place."""
    script = (
        "{ python3 tools/reason-codegen/generate.py --check; "
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"; '
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt; }\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_toolchain_gate_treats_steps_as_separate_shells() -> None:
    """GitHub Actions starts each run step in a fresh shell, so a function
    defined in one step is not callable in the next."""
    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - run: |\n"
        "          provision() {\n"
        "            bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '            rustup component add --toolchain "${RUST_TOOLCHAIN}" '
        "rustfmt\n"
        "          }\n"
        "      - run: |\n"
        "          provision\n"
        "          python3 tools/reason-codegen/generate.py --check\n"
    )
    scripts = packaging_gate._job_run_scripts(workflow, "release-gate")
    assert packaging_gate._release_gate_toolchain_issue(scripts) is not None


def test_raw_install_detector_sees_command_wrappers() -> None:
    """`command`/`env`/`bash -c` wrappers do not hide a raw install."""
    for wrapped in (
        "command rustup toolchain install nightly --profile minimal",
        "env FOO=1 rustup toolchain install nightly --profile minimal",
        "exec rustup toolchain install nightly --profile minimal",
        "bash -c 'rustup toolchain install nightly --profile minimal'",
    ):
        workflow = (
            "jobs:\n"
            "  fuzz-qualification:\n"
            "    steps:\n"
            "      - name: b\n"
            "        run: |\n"
            "          " + wrapped + "\n"
        )
        assert packaging_gate._raw_toolchain_install_issue(workflow), wrapped
    # A raw install in a conditional step still fails the gate.
    conditional = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - if: true\n"
        "        run: |\n"
        "          rustup toolchain install nightly --profile minimal\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(conditional)


def test_toolchain_gate_accepts_partially_quoted_delimiters() -> None:
    """Quoting any part of a delimiter word disables expansion entirely."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    script = 'WORD=RUNTIME\ncat <<E"$WORD"\nbody\nE$WORD\n'
    assert packaging_gate._release_gate_toolchain_issue(
        script + drift + installer + component) is None


def test_toolchain_gate_ignores_dynamic_markers_in_uncalled_functions() -> None:
    """A dynamic delimiter in code that never runs cannot reject the script."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    dead = "unused() {\ncat <<$DELIM\nbody\nDELIM\n}\n"
    assert packaging_gate._release_gate_toolchain_issue(
        dead + drift + installer + component) is None


def test_toolchain_gate_ignores_unreachable_function_bodies() -> None:
    """A call inside a function nobody calls never executes."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    # inner is called only from outer's dead body, so nothing provisions.
    script = (
        "outer() {\n  inner\n}\n"
        "inner() {\n" + installer + component + "}\n" + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(script)
    # A transitive chain that starts at top level is live throughout.
    live_chain = (
        "outer() {\n  inner\n}\n"
        "inner() {\n" + installer + component + "}\n"
        "outer\n" + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(live_chain) is None


def test_toolchain_gate_keeps_step_boundary_line_inert() -> None:
    """A `: __release_gate_step_boundary__` line is just a no-op command."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    injected = (
        "set -e\n" + drift + "exit 0\n"
        ": __release_gate_step_boundary__\n" + installer + component
    )
    assert packaging_gate._release_gate_toolchain_issue(injected)


def test_toolchain_gate_rejects_redirection_carrying_false() -> None:
    """`false >/dev/null && ...` is as dead as a bare `false`."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "false >/dev/null && " + installer + component
    )
    # Any chain whose left side is not an evaluated literal is conditional.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "command -v cargo && " + installer + component
    )
    # A literal-true chain still counts, redirections included.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + "true && " + installer + component
        )
        is None
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + "true >/dev/null && " + installer + component
        )
        is None
    )
    # A dead left side revives the chain under `||`.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            drift + "false >/dev/null || " + installer + component
        )
        is None
    )


def test_raw_install_detector_sees_compound_shell_c_payloads() -> None:
    """A raw install is caught anywhere inside a `bash -c` payload."""
    workflow = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - run: |\n"
        "          bash -c 'echo before; rustup toolchain install "
        "nightly --profile minimal'\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow)
    # A raw install inside an uncalled function body is caught too.
    nested = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - run: |\n"
        "          provision() {\n"
        "            rustup toolchain install nightly --profile minimal\n"
        "          }\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(nested)


def test_toolchain_gate_accepts_ansi_c_quoted_delimiters() -> None:
    """`<<$'EOF'` quotes the delimiter, so it stays static."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    heredoc = "cat <<$'EOF'\nbody\nEOF\n"
    assert packaging_gate._release_gate_toolchain_issue(
        heredoc + drift + installer + component) is None


def test_toolchain_gate_requires_the_exact_pinned_value() -> None:
    """A suffixed value (`"${RUST_TOOLCHAIN}"-evil`) is not the pin."""
    evil_installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"-evil\n'
    )
    evil_component = (
        'rustup component add --toolchain "${RUST_TOOLCHAIN}"-evil rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(
        DRIFT + evil_installer + evil_component)
    # An installer-only suffix cannot hide behind a clean component line.
    assert packaging_gate._release_gate_toolchain_issue(
        DRIFT + evil_installer + COMPONENT)
    # The component command must carry the pinned toolchain argument.
    assert packaging_gate._release_gate_toolchain_issue(
        DRIFT + INSTALLER + "rustup component add rustfmt\n")
    assert packaging_gate._release_gate_toolchain_issue(
        DRIFT + INSTALLER + COMPONENT) is None


def test_toolchain_gate_ignores_calls_in_dead_branches() -> None:
    """A call behind a literally dead condition never activates a body."""
    dead = (
        "provision() {\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "}\n"
        "if false; then\n  provision\nfi\n"
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(dead) is not None
    # The same call guarded by a literally true condition does run.
    live = dead.replace("if false;", "if true;", 1)
    assert packaging_gate._release_gate_toolchain_issue(live) is None
    # An else-branch of a literally true condition never runs.
    else_dead = dead.replace(
        "if false; then\n  provision\nfi\n",
        "if true; then\n  echo skipped\nelse\n  provision\nfi\n",
        1,
    )
    assert packaging_gate._release_gate_toolchain_issue(else_dead) is not None


def test_toolchain_gate_ignores_quoted_and_escaped_calls() -> None:
    """Quoted or escaped text is data, not a call to a defined body."""
    body = (
        "provision() {\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "}\n"
    )
    drift = DRIFT
    # A bare call reaches the body and satisfies the gate.
    assert packaging_gate._release_gate_toolchain_issue(
        body + "provision\n" + drift) is None
    # Quoted and escaped mentions are data: the body never runs.
    quoted = body + 'echo "(provision)"\n' + drift
    assert packaging_gate._release_gate_toolchain_issue(quoted) is not None
    escaped = body + r"echo \(provision\)" + "\n" + drift
    assert packaging_gate._release_gate_toolchain_issue(escaped) is not None


def test_toolchain_gate_drops_body_commands_after_return() -> None:
    """A body that returns before provisioning never provisions."""
    script = (
        "provision() {\n"
        "  return 0\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "}\n"
        "provision\n"
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is not None
    # A return that only closes an inner branch keeps the body live.
    inner = script.replace(
        "  return 0\n  bash",
        '  if [ -n "${SKIP:-}" ]; then\n    return 0\n  fi\n  bash',
        1,
    )
    assert packaging_gate._release_gate_toolchain_issue(inner) is None


def test_toolchain_gate_drops_body_commands_after_errexit_failure() -> None:
    """A standalone failing command under set -e aborts the rest."""
    script = (
        "set -euo pipefail\n"
        "provision() {\n"
        "  false\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "}\n"
        "provision\n"
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is not None
    # Without errexit the failing command does not abort the body.
    without_errexit = script.replace(
        "set -euo pipefail\n", "set +e\n", 1
    )
    assert packaging_gate._release_gate_toolchain_issue(without_errexit) is None


def test_toolchain_gate_unwraps_wrapper_options() -> None:
    """Wrapper options cannot hide a raw install or fake a verified one."""
    drift = DRIFT
    component = 'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    for wrapper in (
        "sudo -n",
        "sudo -u root",
        "sudo --user=runner",
        "env --",
        "command --",
        "eval",
    ):
        raw = wrapper + " rustup toolchain install stable\n"
        assert packaging_gate._raw_install_in_segment(raw), wrapper
    shell = (
        "sh -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\'\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift + shell + component) is None
    prefixed = (
        "bash --noprofile -c 'rustup toolchain install stable'\n"
    )
    assert packaging_gate._raw_install_in_segment(prefixed)


def test_eval_reparses_bash_joined_arguments_and_keeps_echo_control() -> None:
    """Quoted eval payloads execute as shell text, while echo remains inert."""
    for raw in (
        "eval 'rustup toolchain install stable'",
        'eval "rustup toolchain install stable"',
        "eval rustup toolchain install stable",
    ):
        assert packaging_gate._raw_install_in_segment(raw), raw

    for control in (
        "eval 'echo rustup toolchain install stable'",
        'eval "echo rustup toolchain install stable"',
    ):
        assert not packaging_gate._raw_install_in_segment(control), control

    raw_workflow = _raw_install_workflow(
        "eval 'rustup toolchain install stable'"
    )
    assert packaging_gate._raw_toolchain_install_issue(raw_workflow) is not None
    echo_control = _raw_install_workflow(
        "eval 'echo rustup toolchain install stable'"
    )
    assert packaging_gate._raw_toolchain_install_issue(echo_control) is None


def test_toolchain_gate_unwraps_drift_check() -> None:
    """The drift check counts behind a command wrapper too."""
    installer = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    wrapped = "command python3 tools/reason-codegen/generate.py --check\n"
    assert packaging_gate._release_gate_toolchain_issue(
        installer + wrapped) is None
    quoted_path = 'python3 "tools/reason-codegen/generate.py" --check\n'
    assert packaging_gate._release_gate_toolchain_issue(
        quoted_path + installer + COMPONENT
    ) is None


def test_toolchain_gate_strip_preserves_span_offsets() -> None:
    """The stripper rewrites spans in place: a kept body's trimmed text is
    clamped to the span width so later spans never shift."""
    installer = INSTALLER_NO_NL
    inline = "live(){" + installer + ";}\nlive\n"
    other = "unused(){" + installer + ";}\n"
    drift = DRIFT
    script = inline + other + drift
    stripped = packaging_gate._strip_function_bodies(script)
    assert len(stripped) == len(script)
    # The unreachable body's text is fully erased despite the inline form,
    # and the live body's text stays in place.
    unused_at = stripped.index("unused(){")
    assert "bash" not in stripped[unused_at + len("unused(){"):unused_at + len(other)]
    assert installer in stripped[:unused_at]


def test_toolchain_gate_reads_comments_after_separators() -> None:
    """A `#` after a separator starts a comment to end of line."""
    smuggled = (
        "true;# inert; python3 tools/reason-codegen/generate.py --check; "
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"; rustup component add '
        '--toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(smuggled) is not None
    # A `#` inside a word is data, so the command still counts.
    live = (
        "echo a#b\n"
        "python3 tools/reason-codegen/generate.py --check\n"
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(live) is None


def test_toolchain_gate_requires_an_expandable_toolchain_argument() -> None:
    """Single quotes suppress expansion: the installer would get a literal."""
    drift = DRIFT
    quoted = (
        drift
        + "bash ./packaging/scripts/install-verified-rustup.sh "
        "--toolchain '${RUST_TOOLCHAIN}'\n"
        "rustup component add --toolchain '${RUST_TOOLCHAIN}' rustfmt\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(quoted) is not None
    expandable = quoted.replace("'", '"')
    assert packaging_gate._release_gate_toolchain_issue(expandable) is None


def test_toolchain_gate_models_the_whole_if_chain() -> None:
    """After a selected branch no elif or else part can run."""
    drift = DRIFT
    provisioning = (
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    elif_dead = (
        "if true; then\n  echo selected\nelif true; then\n"
        + provisioning
        + "fi\n"
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(elif_dead) is not None
    else_dead = (
        "if true; then\n  echo selected\nelse\n"
        + provisioning
        + "fi\n"
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(else_dead) is not None
    # A false first branch leaves the elif live.
    elif_live = elif_dead.replace("if true;", "if false;", 1)
    assert packaging_gate._release_gate_toolchain_issue(elif_live) is None


def test_toolchain_gate_uses_the_last_definition() -> None:
    """A redefined function runs its last body, not the earlier one."""
    drift = DRIFT
    redefined = (
        "provision() {\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "}\n"
        "provision() { echo no; }\n"
        "provision\n"
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(redefined) is not None
    # The last definition with the real body satisfies the gate.
    final = (
        "provision() { echo no; }\n"
        "provision() {\n"
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "}\n"
        "provision\n"
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(final) is None


def test_toolchain_gate_verifies_the_retry_implementation() -> None:
    """A local retry that never runs its target cannot provision."""
    drift = DRIFT
    wrapped = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    noop = "set -euo pipefail\nretry() { :; }\n" + drift + wrapped
    assert packaging_gate._release_gate_toolchain_issue(noop) is not None
    honest = (
        "set -euo pipefail\n"
        'retry() { "$@" && return 0; return 1; }\n'
        + drift
        + wrapped
    )
    assert packaging_gate._release_gate_toolchain_issue(honest) is None


def test_toolchain_gate_ends_the_shell_at_exec() -> None:
    """`exec cmd` replaces the shell, so later commands never run."""
    drift = DRIFT
    replaced = (
        drift
        + "exec bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(replaced) is not None
    # Redirection-only exec keeps the shell running.
    kept = (
        drift
        + "exec 3</dev/null\n"
        + "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(kept) is None


def test_toolchain_gate_rejects_dashed_c_payloads() -> None:
    """`bash -- -c ...` makes `-c` a filename, not the option."""
    drift = DRIFT
    dashed = (
        drift
        + "bash -- -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        "--toolchain \"${RUST_TOOLCHAIN}\"'\n"
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(dashed) is not None


def test_toolchain_gate_rejects_compound_c_payloads() -> None:
    """A multi-command payload's exit status cannot be trusted."""
    drift = DRIFT
    compound = (
        drift
        + "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        "--toolchain \"${RUST_TOOLCHAIN}\"; false'\n"
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(compound) is not None
    single = (
        drift
        + "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        "--toolchain \"${RUST_TOOLCHAIN}\"'\n"
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(single) is None


def test_toolchain_gate_keeps_non_final_chain_failures_alive() -> None:
    """errexit ignores a failing non-final && / || operand."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    chained = "set -e\nfalse && echo skipped\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(chained) is None
    standalone = "set -e\nfalse\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(standalone) is not None


def test_toolchain_gate_reads_literally_true_step_conditions() -> None:
    """An `if: ${{ true }}` step still runs; anything else stays skipped."""
    assert packaging_gate._step_runs_shell({"if": "${{ true }}", "run": "x"})
    assert packaging_gate._step_runs_shell({"if": " true ", "run": "x"})
    assert not packaging_gate._step_runs_shell({"if": "${{ false }}", "run": "x"})
    assert not packaging_gate._step_runs_shell(
        {"if": "${{ github.ref_type == 'tag' }}", "run": "x"}
    )


def test_continue_on_error_steps_never_prove_successful_provisioning() -> None:
    """Only steps with successful completion semantics count as evidence."""
    assert packaging_gate._step_runs_shell(
        {"continue-on-error": False, "run": "x"}
    )
    assert not packaging_gate._step_runs_shell(
        {"continue-on-error": True, "run": "x"}
    )
    assert not packaging_gate._step_runs_shell(
        {"continue-on-error": "${{ inputs.ignore }}", "run": "x"}
    )
    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - continue-on-error: true\n"
        "        run: |\n"
        "          python3 tools/reason-codegen/generate.py --check\n"
        "          bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '          rustup component add --toolchain "${RUST_TOOLCHAIN}" '
        "rustfmt\n"
        "      - continue-on-error: false\n"
        "        run: echo eligible\n"
    )
    scripts = packaging_gate._job_run_scripts(workflow, "release-gate")
    assert scripts == ["echo eligible"]


def test_step_shell_model_reads_success_always_and_shell_paths() -> None:
    """`success()`/`always()` run on the happy path; shells match by basename.

    A step gated on `success()` (the default gate spelled out) or
    `always()` still runs; `failure()` may skip.  The shell is matched by
    its basename (`/bin/bash` is `bash`), and a non-string shell value
    fails closed like an unexpected `if`.
    """
    assert packaging_gate._step_runs_shell({"if": "success()", "run": "x"})
    assert packaging_gate._step_runs_shell({"if": "always()", "run": "x"})
    assert not packaging_gate._step_runs_shell({"if": "failure()", "run": "x"})
    assert packaging_gate._step_runs_shell(
        {"shell": "/bin/bash", "run": "x"})
    assert packaging_gate._step_runs_shell(
        {"shell": "bash -e {0}", "run": "x"})
    assert not packaging_gate._step_runs_shell(
        {"shell": "python3", "run": "x"})
    assert not packaging_gate._step_runs_shell({"shell": 123, "run": "x"})
    assert not packaging_gate._step_runs_shell({"shell": [], "run": "x"})


def test_syntax_only_shell_modes_do_not_prove_execution() -> None:
    """Syntax-only shell flags cannot count as executed provisioning evidence."""
    for shell in (
        "bash -n {0}",
        "/bin/bash --noexec {0}",
        "sh -n {0}",
        "bash -en {0}",
        "bash -o noexec {0}",
        "bash -O noexec {0}",
    ):
        assert not packaging_gate._step_runs_shell({"shell": shell, "run": "x"})

    assert packaging_gate._step_runs_shell(
        {"shell": "bash --noprofile --norc {0}", "run": "x"}
    )
    assert packaging_gate._step_runs_shell(
        {"shell": "bash {0} -n", "run": "x"}
    )

    workflow = chr(10).join(
        (
            "jobs:",
            "  release-gate:",
            "    steps:",
            "      - shell: bash -n {0}",
            "        run: echo not-executed",
            "      - shell: bash {0}",
            "        run: echo eligible",
        )
    )
    assert packaging_gate._job_run_scripts(workflow, "release-gate") == [
        "echo eligible"
    ]


def test_shadow_guard_reads_real_definitions_only() -> None:
    """Quoted text is data: only a definition the shell would create counts.

    ``echo "define rustup() { like this"`` documents syntax; it defines
    nothing, so it must not fail the gate.  Both definition spellings
    still count when they are real code.
    """
    assert packaging_gate._shadowing_issue(
        'echo "define rustup() { like this"\n') is None
    assert packaging_gate._shadowing_issue("echo 'x dead() {'") is None
    assert packaging_gate._shadowing_issue("function bash { :; }") is not None
    assert packaging_gate._shadowing_issue("function rustup() { :; }") is not None
    assert packaging_gate._shadowing_issue("bash() { :; }") is not None
    assert packaging_gate._shadowing_issue("true; function python3 { :; }") is not None
    assert packaging_gate._shadowing_issue("bash --version") is None
    assert packaging_gate._shadowing_issue("echo rustup python3") is None
    assert packaging_gate._shadowing_issue(
        "rustup component add --toolchain x rustfmt") is None


def test_toolchain_gate_distrusts_provably_dead_retry_branches() -> None:
    """A `$@` behind a literal `false` never runs: the wrapper cannot be
    trusted to provision."""
    drift = DRIFT
    wrapped = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    dead_branch = 'retry() { if false; then\n"$@"\nfi; }\n'
    assert (
        packaging_gate._release_gate_toolchain_issue(
            dead_branch + drift + wrapped
        )
        is not None
    )
    dead_marker = 'retry() { if false; then "$@"; fi; }\n'
    assert (
        packaging_gate._release_gate_toolchain_issue(
            dead_marker + drift + wrapped
        )
        is not None
    )
    short_circuit = 'retry() { false && "$@"; }\n'
    assert (
        packaging_gate._release_gate_toolchain_issue(
            short_circuit + drift + wrapped
        )
        is not None
    )


def test_toolchain_gate_keeps_loop_wrapped_retry_invocations() -> None:
    """The real retry invokes `$@` inside a loop: that stays trusted."""
    drift = DRIFT
    wrapped = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    looped = 'retry() { while :; do "$@" && return 0; done; }\n'
    assert (
        packaging_gate._release_gate_toolchain_issue(looped + drift + wrapped)
        is None
    )
    guarded = 'retry() { if [ -n "$X" ]; then "$@"; fi; }\n'
    assert (
        packaging_gate._release_gate_toolchain_issue(guarded + drift + wrapped)
        is None
    )


def test_toolchain_gate_trims_returns_in_taken_branches() -> None:
    """`if true; then return` ends the body: later provisioning is dead."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    taken = "provision() { if true; then return 0; fi\n" + drift + provisioning + "}\nprovision\n"
    assert packaging_gate._release_gate_toolchain_issue(taken) is not None
    untaken = (
        "provision() { if false; then return 0; fi\n"
        + drift
        + provisioning
        + "}\nprovision\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(untaken) is None


def test_toolchain_gate_ends_the_shell_at_a_certain_failing_return() -> None:
    """A call to a function whose taken branch returns non-zero trips
    errexit."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    fails = (
        "set -e\nfail() { if true; then return 1; fi; }\nfail\n"
        + drift
        + provisioning
    )
    assert packaging_gate._release_gate_toolchain_issue(fails) is not None
    ok = "set -e\nok() { if true; then return 0; fi; }\nok\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(ok) is None


def test_dynamic_return_status_is_not_proven_nonzero() -> None:
    """A variable return may be zero, so it cannot prove errexit termination."""
    assert packaging_gate._return_kind('return "$CODE"') == "unknown"
    assert packaging_gate._return_failure('return "$CODE"') is False
    script = (
        'set -e\nfinish() { return "$CODE"; }\nfinish\n'
        + DRIFT + INSTALLER + COMPONENT
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_same_line_branch_markers_carry_live_commands() -> None:
    """Commands on then/else marker segments count only on live branches."""
    true_branch = (
        "if true; then " + INSTALLER.rstrip() + "; fi\n"
        + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(true_branch) is None
    false_branch = (
        "if false; then " + INSTALLER.rstrip() + "; fi\n"
        + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(false_branch) is not None
    else_live = (
        "if false; then echo skip; else " + INSTALLER.rstrip() + "; fi\n"
        + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(else_live) is None
    else_dead = (
        "if true; then echo run; else " + INSTALLER.rstrip() + "; fi\n"
        + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(else_dead) is not None


def test_raw_install_detector_reads_env_option_wrapped_commands() -> None:
    """`env` options cannot smuggle a raw install past the detector."""
    assert packaging_gate._raw_install_in_segment(
        'env -i PATH="$PATH" rustup toolchain install nightly --profile minimal'
    )
    assert packaging_gate._raw_install_in_segment(
        "env -u RUSTUP_HOME rustup toolchain install nightly"
    )
    assert packaging_gate._raw_install_in_segment(
        "FOO=1 rustup toolchain install nightly"
    )
    assert packaging_gate._raw_install_in_segment(
        "sudo FOO=1 rustup toolchain install nightly"
    )
    assert not packaging_gate._raw_install_in_segment(
        'env -i PATH="$PATH" cargo --version'
    )
    assert packaging_gate._skip_env_options(["-Z", "rustup"], 0) == 0
    assert packaging_gate._skip_env_options(["-u", "NAME", "rustup"], 0) == 2


def test_unmodeled_env_options_cannot_count_as_provisioning() -> None:
    """An unknown env option remains in command position and fails closed."""
    script = (
        DRIFT
        + "env -Z " + INSTALLER
        + "env -Z " + COMPONENT
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is not None


def test_toolchain_gate_rejects_line_separated_shell_payloads() -> None:
    """A `bash -c` payload with line-separated commands cannot satisfy
    the checks: its exit status is not predictable."""
    drift = DRIFT
    component = COMPONENT
    multiline = (
        "set -euo pipefail\n"
        + drift
        + "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        "--toolchain \"${RUST_TOOLCHAIN}\"\nfalse'\n"
        + component
    )
    assert packaging_gate._release_gate_toolchain_issue(multiline) is not None
    single = (
        "set -euo pipefail\n"
        + drift
        + "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        "--toolchain \"${RUST_TOOLCHAIN}\" '\n"
        + component
    )
    assert packaging_gate._release_gate_toolchain_issue(single) is None


def test_toolchain_gate_models_set_plus_o_errexit() -> None:
    """`set +o errexit` disables errexit: a later `false` is harmless."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    disabled = "set -e\nset +o errexit\nfalse\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(disabled) is None
    enabled = "set +o errexit\nset -o errexit\nfalse\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(enabled) is not None


def test_toolchain_gate_reads_command_wrapped_failures() -> None:
    """`command false` and pipeline failures still trip errexit."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    wrapped = "set -e\ncommand false\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(wrapped) is not None
    piped = "set -e\ntrue | command false\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(piped) is not None
    ok = "set -e\ncommand true\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(ok) is None


def test_toolchain_gate_folds_compound_conditions() -> None:
    """`if true && false` is false: its body never runs."""
    drift = DRIFT
    provisioning = (
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    dead = "if true && false; then\n" + provisioning + "fi\n" + drift
    assert packaging_gate._release_gate_toolchain_issue(dead) is not None
    live = "if true && true; then\n" + provisioning + "fi\n" + drift
    assert packaging_gate._release_gate_toolchain_issue(live) is None
    or_live = "if false || true; then\n" + provisioning + "fi\n" + drift
    assert packaging_gate._release_gate_toolchain_issue(or_live) is None


def test_pipeline_in_a_condition_reads_as_unknown_not_as_its_left_operand() -> None:
    """A `|`/`|&` in a condition evaluates its last command, not the first.

    ``if true | true`` is not the literal ``true`` the left operand names,
    so the condition must fold to unknown (None): a body under it cannot
    count as unconditional provisioning.  The gate keeps reporting the
    missing provisioning rather than trusting the left literal.
    """
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    drift = DRIFT
    piped = "if true | true; then\n" + provisioning + "fi\n" + drift
    assert packaging_gate._release_gate_toolchain_issue(piped) is not None
    # The same body under a plain literal-true condition is accepted, so
    # the rejection above is caused by the pipeline, not the body.
    plain = "if true; then\n" + provisioning + "fi\n" + drift
    assert packaging_gate._release_gate_toolchain_issue(plain) is None
    # Direct tristate check through the real tokenizer output.
    pairs = packaging_gate._command_segments_with_separators(
        "if true | true; then\n  echo x\nfi")
    assert packaging_gate._condition_tristate(pairs, 0) is None


def test_toolchain_gate_requires_retry_to_invoke_its_target() -> None:
    """Mentioning `$@` is not invoking it."""
    drift = DRIFT
    wrapped = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    echoing = 'retry() { echo "$@"; }\n' + drift + wrapped
    assert packaging_gate._release_gate_toolchain_issue(echoing) is not None
    invoking = 'retry() { "$@" && return 0; return 1; }\n' + drift + wrapped
    assert packaging_gate._release_gate_toolchain_issue(invoking) is None


def test_toolchain_gate_ends_the_shell_at_a_failing_call() -> None:
    """A call to an always-failing function trips errexit."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    fails = "set -e\nfail() { return 1; }\nfail\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(fails) is not None
    succeeds = (
        "set -e\nok() { return 0; }\nok\n" + drift + provisioning
    )
    assert packaging_gate._release_gate_toolchain_issue(succeeds) is None


def test_raw_install_detector_reads_quoted_commands() -> None:
    """Bash resolves the unquoted name: `'rustup' toolchain install` runs."""
    assert packaging_gate._raw_install_in_segment(
        "'rustup' toolchain install nightly --profile minimal"
    )


def test_toolchain_gate_models_set_plus_e() -> None:
    """`set +e` disables errexit again."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    disabled = "set -e\nset +e\nfalse\n" + drift + provisioning
    assert packaging_gate._release_gate_toolchain_issue(disabled) is None


def test_toolchain_gate_keeps_short_circuited_returns_alive() -> None:
    """`true || return` never returns: the body continues."""
    drift = DRIFT
    provisioning = (
        "  bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '  rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    skipped = (
        "provision() {\n  true || return 0\n"
        + provisioning
        + "}\nprovision\n"
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(skipped) is None
    taken = (
        "provision() {\n  false || return 0\n"
        + provisioning
        + "}\nprovision\n"
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(taken) is not None


def test_toolchain_gate_reads_a_quoted_installer_path() -> None:
    """A quoted installer path is a valid spelling of the same command."""
    drift = DRIFT
    quoted = (
        drift
        + 'bash "./packaging/scripts/install-verified-rustup.sh" '
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(quoted) is None


def test_toolchain_gate_takes_single_word_c_payloads() -> None:
    """`bash -c cmd arg...` runs only `cmd`; later words are positional."""
    drift = DRIFT
    component = COMPONENT
    joined = (
        "bash -c bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n' + component + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(joined) is not None
    combined = (
        "bash -ce 'bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\'\n' + component + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(combined) is None
    quoted = (
        "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\'\n' + component + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(quoted) is None


def test_toolchain_gate_reads_wrapped_failure_literals() -> None:
    """`env false`, `FOO=bar false` and `command exit 0` end the shell."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    for opener in ("env false", "FOO=bar false", "command exit 0"):
        assert (
            packaging_gate._release_gate_toolchain_issue(
                "set -e\n" + opener + "\n" + provisioning + drift
            )
            is not None
        ), opener
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "set -e\nenv true\n" + provisioning + drift
        )
        is None
    )


def test_toolchain_gate_distrusts_retry_after_return() -> None:
    """A retry that returns before `$@` never invokes its target."""
    drift = DRIFT
    wrapped = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    early = 'retry() { return 1; "$@"; }\n' + drift + wrapped
    assert packaging_gate._release_gate_toolchain_issue(early) is not None
    looped = 'retry() { while :; do "$@" && return 0; done; }\n' + drift + wrapped
    assert packaging_gate._release_gate_toolchain_issue(looped) is None


def test_toolchain_gate_rejects_shadowing_definitions() -> None:
    """Functions that shadow the commands the checks read are rejected."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    shadow_true = (
        "true() { return 1; }\nif true; then\n" + provisioning + "fi\n" + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(shadow_true) is not None
    for definition in (
        "true && bash() { :; }\n",
        "function rustup { :; }\n",
        "env() { :; }\n",
        # Stripped wrappers must be guarded too: a no-op `sudo()` or
        # `nohup()` makes the gate read through a shell function that
        # never runs the provisioning behind it.
        "sudo() { :; }\n",
        "nohup() { :; }\n",
    ):
        assert (
            packaging_gate._release_gate_toolchain_issue(
                definition + provisioning + drift
            )
            is not None
        ), definition
    named_retry = (
        'retry() { "$@" && return 0; return 1; }\n'
        + drift
        + "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(named_retry) is None


def test_shadow_guard_covers_every_stripped_wrapper() -> None:
    """Every wrapper the gate strips must also be shadow-guarded.

    Stripping a wrapper to reach the real command is only trustworthy when
    that wrapper cannot be redefined out from under the check; the two sets
    have to stay in step as wrappers are added.  The inventory spans names
    stripped outside `_WRAPPER_COMMANDS` too (`eval`), which the earlier
    `_WRAPPER_COMMANDS`-only difference silently missed.
    """
    # The inventory itself must span names stripped outside
    # _WRAPPER_COMMANDS: collapsing it back to the wrapper subset is what
    # made the original `eval` hole invisible to this very test.
    assert "eval" in packaging_gate._PREFIX_STRIPPED_NAMES, (
        "`eval` is stripped outside _WRAPPER_COMMANDS; the inventory must "
        "stay wide enough to cover it"
    )
    missing = (
        packaging_gate._PREFIX_STRIPPED_NAMES - packaging_gate._SHADOWED_NAMES
    )
    assert not missing, (
        "stripped wrappers missing from the shadow guard: "
        + ", ".join(sorted(missing))
    )


def test_shadow_guard_rejects_noop_eval_wrapper() -> None:
    """A no-op `eval()` cannot hide skipped provisioning from the gate.

    `eval` is stripped before the installer regex is matched, so a
    function that overrides the builtin makes the literal text match
    while bash runs nothing; the shadow guard has to reject it, exactly
    as it rejects any other stripped name redefined to a no-op.
    """
    provisioning = (
        'eval bash ./packaging/scripts/install-verified-rustup.sh '
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'eval rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "python3 tools/reason-codegen/generate.py --check\n"
    )
    shadowed = "set -euo pipefail\neval() { :; }\n" + provisioning
    issue = packaging_gate._release_gate_toolchain_issue([shadowed])
    assert issue is not None
    assert "eval" in issue
    # Same text without the redefinition still provisions: no false
    # rejection for the unshadowed spelling.
    clean = "set -euo pipefail\n" + provisioning
    assert packaging_gate._release_gate_toolchain_issue([clean]) is None


def test_literally_true_bool_conditions_keep_steps() -> None:
    """`if: true` (a YAML boolean) still runs its shell step."""
    runs = (
        "python3 tools/reason-codegen/generate.py --check\n"
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - name: a\n"
        "        if: true\n"
        "        run: |\n"
        + "".join("          " + line + "\n" for line in runs.splitlines())
    )
    scripts = packaging_gate._job_run_scripts(workflow, "release-gate")
    assert packaging_gate._release_gate_toolchain_issue(scripts) is None
    disabled = workflow.replace("        if: true\n", "        if: false\n")
    scripts = packaging_gate._job_run_scripts(disabled, "release-gate")
    assert packaging_gate._release_gate_toolchain_issue(scripts) is not None


def test_toolchain_gate_skips_command_separator_failures() -> None:
    """`command -- false` fails a shell under errexit."""
    drift = DRIFT
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "set -e\ncommand -- false\n" + provisioning + drift
        )
        is not None
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            "set -e\ncommand -- true\n" + provisioning + drift
        )
        is None
    )


def test_toolchain_gate_takes_c_payload_labels() -> None:
    """A word after a quoted `-c` payload is $0, not more payload.

    The payload keeps only its own text: the label word after it must not
    merge into the payload (a merged join would let a label satisfy the
    provisioning checks), so the labelled shape with the real toolchain
    argument inside the payload passes, and the deceptive shape whose
    payload lacks the argument (it sits after the closing quote, in label
    position) fails even though the text looks right.
    """
    labelled = (
        "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\' release-label\n' + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(labelled) is None
    # The payload's own text lacks the toolchain argument; the words after
    # the closing quote are labels and must not satisfy the check.
    deceptive = (
        "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh' "
        '--toolchain "${RUST_TOOLCHAIN}"\n' + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(deceptive) is not None
    # A label that carries a fake component command is never the real
    # component step either.
    label_carries_component = (
        "bash -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\' '
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        + DRIFT
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(label_carries_component)
        is not None
    )
    joined = (
        "bash -c bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n' + COMPONENT + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(joined) is not None


def test_toolchain_gate_handles_assigned_retry_calls() -> None:
    """Leading assignments do not hide retry calls in either direction."""
    drift = DRIFT
    wrapped = (
        "FOO=bar retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        "FOO=bar retry 5 rustup component add "
        '--toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    honest = (
        'retry() { attempts="$1"; shift; while :; do "$@" && return 0;'
        " return 1; done; }\n" + drift + wrapped
    )
    assert packaging_gate._release_gate_toolchain_issue(honest) is None
    early = 'retry() { return 1; "$@"; }\n' + drift + wrapped
    assert packaging_gate._release_gate_toolchain_issue(early) is not None


def test_toolchain_gate_rejects_double_separator_commands() -> None:
    """`command -- -- true` cannot be found (127): its chain never runs."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    doubled = (
        "set -e\n"
        "command -- -- true && " + installer
        + "command -- -- true && " + component
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(doubled) is not None
    single = (
        "set -e\n"
        "command -- true && " + installer
        + "command -- true && " + component
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(single) is None


def test_toolchain_gate_takes_escaped_payload_quotes() -> None:
    """A double-quoted `-c` payload may escape its inner quotes."""
    drift = DRIFT
    escaped = (
        'bash -c "bash ./packaging/scripts/install-verified-rustup.sh '
        '--toolchain \\"${RUST_TOOLCHAIN}\\""\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        + drift
    )
    assert packaging_gate._release_gate_toolchain_issue(escaped) is None
    hidden_raw = 'bash -c "echo \\"x\\"; rustup toolchain install nightly"'
    assert packaging_gate._raw_install_in_segment(hidden_raw) is True


def test_toolchain_gate_takes_env_separator_and_assignments() -> None:
    """`env -- FOO=bar cmd` keeps the command reachable."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    script = (
        drift
        + "env -- FOO=bar " + installer
        + "env -- FOO=bar " + component
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_toolchain_gate_takes_shell_option_arguments() -> None:
    """`bash -O extglob -c '...'` carries its option argument."""
    drift = DRIFT
    script = (
        drift
        + "bash -O extglob -c 'bash ./packaging/scripts/"
        "install-verified-rustup.sh --toolchain \"${RUST_TOOLCHAIN}\"'\n"
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_toolchain_gate_trusts_command_forwarding_retry() -> None:
    """A retry that forwards through `command \"$@\"` runs its target."""
    drift = DRIFT
    retry = (
        'retry() {\n  attempts="$1"\n  shift\n'
        '  command "$@" && return 0\n  return 1\n}\n'
    )
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        "retry 5 rustup component add "
        '--toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            retry + drift + installer + component
        )
        is None
    )


def test_toolchain_gate_treats_separator_commands_as_failures() -> None:
    """A bare `--` command cannot be found: under errexit the shell ends."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    failing = "set -e\n-- true\n" + installer + component + drift
    assert packaging_gate._release_gate_toolchain_issue(failing) is not None
    failing2 = "set -e\ncommand -- -- true\n" + installer + component + drift
    assert packaging_gate._release_gate_toolchain_issue(failing2) is not None
    control = "set -e\ncommand -- true\n" + installer + component + drift
    assert packaging_gate._release_gate_toolchain_issue(control) is None


def test_release_gate_dependency_guard_takes_valid_pip_spellings() -> None:
    """Shell-equivalent spellings of the pinned pip install all count.

    Drives the production gate itself (`_python_deps_issue`), not a
    test-local matcher: the check must accept each spelling in executable
    command position and reject masked, dead, or misordered ones.
    """
    spellings = [
        "retry 5 python3 -m pip install -r requirements-release.txt",
        'python3 -m pip install --requirement "requirements-release.txt"',
        "bash -c 'python3 -m pip install --requirement requirements-release.txt'",
        "env FOO=bar python3 -m pip install --requirement requirements-release.txt",
        'sudo python3 -m pip install -r "requirements-release.txt"',
        "pip3 install --requirement=requirements-release.txt",
    ]
    for spelling in spellings:
        assert packaging_gate._python_deps_issue(
            [spelling, "make docs-check"]) is None, spelling
    # A dead spelling does not count even with the docs-check present.
    dead_install = "set -e\nfalse && python3 -m pip install -r requirements-release.txt"
    assert packaging_gate._python_deps_issue(
        [dead_install, "make docs-check"]
    ) is not None
    # An install after its consumer does not count.
    assert packaging_gate._python_deps_issue(
        ["make docs-check",
         "python3 -m pip install -r requirements-release.txt"]
    ) is not None
    assert packaging_gate._python_deps_issue(
        ["make docs-check; python3 -m pip install -r requirements-release.txt"]
    ) is not None
    assert packaging_gate._python_deps_issue(
        ["python3 -m pip install -r requirements-release.txt; make docs-check"]
    ) is None
    # A missing install or a missing consumer fails closed.
    assert packaging_gate._python_deps_issue(["make docs-check"]) is not None
    assert packaging_gate._python_deps_issue(
        ["python3 -m pip install -r requirements-release.txt"]) is not None


def test_toolchain_gate_rejects_command_lookup_spellings() -> None:
    """`command -v` only looks names up; it never runs them."""
    drift = DRIFT_NO_NL
    installer = INSTALLER_NO_NL
    component = COMPONENT_NO_NL
    looked_up = (
        "set -euo pipefail\n"
        f"command -v {installer} || true\n"
        f"command -v {component} || true\n"
        f"command -v {drift} || true\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(looked_up) is not None
    control = (
        "set -e\n"
        f"command {installer}\n"
        f"command {component}\n"
        f"{drift}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(control) is None


def test_toolchain_gate_takes_command_default_path_spelling() -> None:
    """`command -p` runs the command through the default PATH."""
    drift = DRIFT_NO_NL
    installer = INSTALLER_NO_NL
    component = COMPONENT_NO_NL
    script = (
        "set -e\n"
        f"command -p {installer}\n"
        f"command -p {component}\n"
        f"{drift}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is None


def test_toolchain_gate_rejects_builtin_wrapped_externals() -> None:
    """`builtin` never runs external commands, so it provisions nothing."""
    drift = DRIFT_NO_NL
    installer = INSTALLER_NO_NL
    component = COMPONENT_NO_NL
    masked = (
        f"builtin -- {installer} || true\n"
        f"builtin -- {component} || true\n"
        f"builtin -- {drift} || true\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(masked) is not None
    chained = (
        "set -e\n"
        f"builtin -- true && {installer}\n"
        f"builtin -- true && {component}\n"
        f"{drift}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(chained) is None
    failing = (
        "set -e\n"
        "builtin -- false\n"
        f"{installer}\n"
        f"{component}\n"
        f"{drift}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(failing) is not None


def test_toolchain_gate_rejects_separator_argument_commands() -> None:
    """`command -- -- bash ...` runs a utility named `--` (127)."""
    drift = DRIFT_NO_NL
    installer = INSTALLER_NO_NL
    component = COMPONENT_NO_NL
    doubled = (
        f"command -- -- {installer} || true\n"
        f"command -- -- {component} || true\n"
        f"command -- -- {drift} || true\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(doubled) is not None
    single = (
        "set -e\n"
        f"command -- {installer}\n"
        f"command -- {component}\n"
        f"{drift}\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(single) is None


def test_toolchain_gate_rejects_phantom_definitions_in_quoted_text() -> None:
    """A `name() {` shape inside quoted text is data, not a definition.

    A quoted decoy used to open a phantom body: the walk lost the spans of
    the real definitions behind it, dead-function erasure silently stopped,
    and literal provisioning text inside a never-called function satisfied
    the gate.  Each quoting style must reject the unreachable provisioning,
    and the same decoy must stay inert when provisioning really runs.
    """
    drift = DRIFT
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    dead = "unused() {\n" + installer + component + "}\n"
    live = drift + installer + component
    for decoy in (
        'echo "x dead() {"',
        "echo 'x dead() {'",
        "echo $'x dead() {'",
        'echo "x dead() ("',
        'echo "x dead() { still quoted"',
    ):
        assert packaging_gate._release_gate_toolchain_issue(
            decoy + "\n" + dead
        ), decoy
        # The same decoy must not reject a script whose provisioning is
        # reachable: the quoted text stays data on both readings.
        assert packaging_gate._release_gate_toolchain_issue(
            decoy + "\n" + live
        ) is None, decoy


def test_toolchain_gate_fails_closed_on_unclosed_structure() -> None:
    """A script ending inside an open body or string is unverifiable.

    Bash cannot parse it, and a phantom definition the walk cannot place
    would misplace every later span, so the gate must reject it instead of
    trusting the spans it managed to read.
    """
    spans, state = packaging_gate._function_body_walk("provision() { true; }")
    assert state == ""
    assert [name for name, _lo, _hi in spans] == ["provision"]
    assert packaging_gate._function_body_walk("provision() {") == ([], "body")
    assert packaging_gate._function_body_walk("provision() { true") == (
        [], "body")
    assert packaging_gate._function_body_walk('echo "abc') == ([], "quote")

    live = (
        "python3 tools/reason-codegen/generate.py --check\n"
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    body_issue = packaging_gate._release_gate_toolchain_issue(
        "provision() {\n  echo hi\n"
    )
    assert body_issue is not None
    assert "open function body" in body_issue
    quote_issue = packaging_gate._release_gate_toolchain_issue(
        'echo "abc\n' + live
    )
    assert quote_issue is not None
    assert "unterminated quoted string" in quote_issue
    # A string that does close, later in the script, stays verifiable and
    # does not reject reachable provisioning.
    closed = 'echo "one\ntwo"\n' + live
    assert packaging_gate._release_gate_toolchain_issue(closed) is None


def test_raw_install_detector_models_shell_invocation_options() -> None:
    """A `-c` payload behind any modeled shell option is still a command.

    The detector used to treat every option before `-c` as flag-only, so
    `bash --rcfile file -c '…'`, `-eo errexit`, `+m` and quoted spellings
    of the shell name carried their payload past the scan unread.  Each
    shape now resolves to the payload command, and a payload the scan
    cannot resolve never counts as verified text.
    """
    raw = "rustup toolchain install 1.2.3"
    for form in (
        "bash -c 'PAY'",
        "bash -lc 'PAY'",
        "bash -cl 'PAY'",
        "bash -Ee -c 'PAY'",
        "bash --noprofile -c 'PAY'",
        "bash --norc --noprofile -c 'PAY'",
        "bash --rcfile file -c 'PAY'",
        "bash --init-file file -c 'PAY'",
        "bash -eo errexit -c 'PAY'",
        "bash -eO extglob -c 'PAY'",
        "bash +eo errexit -c 'PAY'",
        "bash +m -c 'PAY'",
        "bash -o errexit -c 'PAY'",
        "bash -O extglob -c 'PAY'",
        "bash '-c' 'PAY'",
        "'bash' -c 'PAY'",
        "'bash' '-c' 'PAY'",
        "'sh' --rcfile file -c 'PAY'",
        "sh --rcfile file -c 'PAY'",
        "dash -c 'PAY'",
        "env bash --rcfile file -c 'PAY'",
        "command bash --rcfile file -c 'PAY'",
    ):
        segment = form.replace("PAY", raw)
        assert packaging_gate._raw_install_in_segment(segment), form

    # A `-c` word consumed as an option VALUE is not command mode: the
    # payload behind it is a script-file name, and bash never runs the
    # option's argument as commands.
    assert not packaging_gate._raw_install_in_segment(
        "bash --rcfile -c 'PAY'".replace("PAY", raw))
    # `-n`/`-D`/`+D` suppress execution: nothing runs behind them.
    for suppress in ("-n", "-D", "+D"):
        assert not packaging_gate._raw_install_in_segment(
            "bash " + suppress + " -c 'PAY'".replace("PAY", raw))

    # A quoted `retry` still calls the local function: the shadow check's
    # view of a retry call must resolve the name through quote removal.
    assert packaging_gate._is_retry_call("'retry' 5 rustup toolchain install x")


def test_toolchain_gate_requires_unquoted_or_resolvable_wrapper_names() -> None:
    """Quoted wrapper and command names resolve as bash resolves them.

    `'bash' -c '…'` runs bash; the gate must unwrap it exactly like the
    bare spelling, on both the rejection side and the accepted side.
    """
    drift = DRIFT
    component = COMPONENT
    quoted_shell = (
        "'sh' -c 'bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\''
    )
    assert packaging_gate._release_gate_toolchain_issue(
        drift + quoted_shell + "\n" + component) is None
    smuggled = "'bash' -c 'rustup toolchain install stable'\n"
    assert packaging_gate._raw_install_in_segment(smuggled)


def _raw_install_workflow(script: str) -> str:
    """Put one literal script in a parseable workflow run step."""
    body = "".join("          " + line + "\n" for line in script.splitlines())
    return "jobs:\n  probe:\n    steps:\n      - run: |\n" + body


def test_raw_install_detector_follows_indirect_command_positions() -> None:
    """Raw Rust installs remain forbidden through common shell dispatchers."""
    commands = (
        "printf stable | xargs -n 1 rustup toolchain install stable",
        "timeout --signal=TERM 5s rustup toolchain install stable",
        r"find /tmp -maxdepth 1 -exec rustup toolchain install stable \;",
        "if rustup toolchain install stable; then echo done; fi",
    )
    for command in commands:
        issue = packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(command)
        )
        assert issue is not None, command

    controls = (
        "printf stable | xargs -n 1 printf safe",
        "timeout 5s printf safe",
        r"find /tmp -maxdepth 1 -exec echo rustup toolchain install stable \;",
        "if true; then echo rustup toolchain install stable; fi",
    )
    for command in controls:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(command)
        ) is None, command


def test_toolchain_liveness_respects_explicit_shell_errexit() -> None:
    """A custom ``bash {0}`` shell does not inherit GitHub's implicit ``-e``."""
    script = "false\n" + DRIFT + INSTALLER + COMPONENT
    assert packaging_gate._release_gate_toolchain_issue(script) is not None
    assert packaging_gate._release_gate_toolchain_issue(
        [{"run": script, "shell": "bash"}]
    ) is not None
    assert packaging_gate._release_gate_toolchain_issue(
        [{"run": script, "shell": "bash -e {0}"}]
    ) is not None
    assert packaging_gate._release_gate_toolchain_issue(
        [{"run": script, "shell": "bash {0}"}]
    ) is None

    actual = subprocess.run(
        ["bash", "-c", "false; printf reached"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert actual.returncode == 0
    assert actual.stdout == "reached"
    assert packaging_gate._live_command_segments(
        "false; printf reached", errexit=False
    ) == ["false", "printf reached"]


def test_verified_installer_accepts_file_descriptor_redirections() -> None:
    """Redirection ampersands do not background or hide the installer."""
    for redirection in ("2>&1", "&>rustup.log", "&>>rustup.log"):
        installer = INSTALLER.rstrip("\n") + f" {redirection}\n"
        segments = packaging_gate._command_segments_with_separators(installer)
        assert len(segments) == 1, (redirection, segments)
        assert segments[0][0].endswith(redirection), segments
        assert packaging_gate._release_gate_toolchain_issue(
            DRIFT + installer + COMPONENT
        ) is None, redirection

    background = INSTALLER.rstrip("\n") + " &\n"
    assert packaging_gate._ends_with_background_operator(background)


def test_verified_installer_must_finish_before_component_add() -> None:
    """A backgrounded installer cannot satisfy a later foreground add."""
    background = DRIFT + INSTALLER.rstrip("\n") + " &\n" + COMPONENT
    assert packaging_gate._release_gate_toolchain_issue(background) is not None
    cross_step = [
        {"run": DRIFT + INSTALLER.rstrip("\n") + " &\n"},
        {"run": COMPONENT},
    ]
    assert packaging_gate._release_gate_toolchain_issue(cross_step) is not None
    assert packaging_gate._release_gate_toolchain_issue(
        DRIFT + INSTALLER + COMPONENT
    ) is None


def test_python_dependency_gate_requires_a_real_docs_check_command() -> None:
    """Text emitted or quoted by another command is not a docs-check run."""
    install = "python3 -m pip install -r requirements-release.txt"
    decoys = (
        "echo make docs-check",
        "printf '%s' 'make docs-check'",
        "'make docs-check'",
        "# make docs-check",
        "make -n docs-check",
        "make --dry-run docs-check",
        "make --dry-run=ignored docs-check",
        "make --just-print docs-check",
        "make --recon docs-check",
        "make -q docs-check",
        "make --question docs-check",
        "make -t docs-check",
        "make --touch docs-check",
        "make -kn docs-check",
    )
    for decoy in decoys:
        assert packaging_gate._python_deps_issue([install, decoy]) is not None, decoy
    assert packaging_gate._python_deps_issue([install, "make docs-check"]) is None
    assert packaging_gate._python_deps_issue([install, "make -- docs-check"]) is None


def test_python_dependency_gate_accepts_only_supported_pip_command_forms() -> None:
    """The requirement install must invoke Python's pip module or pip itself."""
    valid = (
        "python -m pip install -r requirements-release.txt",
        "python3 -m pip install -r requirements-release.txt",
        "pip install -r requirements-release.txt",
        "pip3 install -r requirements-release.txt",
    )
    for command in valid:
        assert packaging_gate._python_deps_issue(
            [command, "make docs-check"]
        ) is None, command

    invalid = (
        "python install -r requirements-release.txt",
        "python3 install -r requirements-release.txt",
        "pip -m pip install -r requirements-release.txt",
        "pip3 -m pip install -r requirements-release.txt",
    )
    for command in invalid:
        assert packaging_gate._python_deps_issue(
            [command, "make docs-check"]
        ) is not None, command


def test_virtualenv_install_in_one_step_does_not_feed_a_later_shell() -> None:
    """A per-step activation cannot provision dependencies for another step."""
    scoped_install = (
        "python3 -m venv .venv; source .venv/bin/activate; "
        "python3 -m pip install -r requirements-release.txt"
    )
    assert packaging_gate._python_deps_issue(
        [scoped_install, "make docs-check"]
    ) is not None
    # A global install persists across run steps; same-step venv use also does.
    assert packaging_gate._python_deps_issue(
        ["python3 -m pip install -r requirements-release.txt", "make docs-check"]
    ) is None
    assert packaging_gate._python_deps_issue(
        [scoped_install + "; make docs-check"]
    ) is None


def test_virtualenv_deactivation_invalidates_same_step_runtime() -> None:
    """A same-step deactivate returns docs-check to the system environment."""
    installed = (
        "python3 -m venv .venv; source .venv/bin/activate; "
        "python3 -m pip install -r requirements-release.txt; "
        "deactivate; make docs-check"
    )
    assert packaging_gate._python_deps_issue([installed]) is not None

    reactivated = (
        "source .venv/bin/activate; "
        "python3 -m pip install -r requirements-release.txt; "
        "deactivate; source .venv/bin/activate; make docs-check"
    )
    assert packaging_gate._python_deps_issue([reactivated]) is None

    echoed = (
        "source .venv/bin/activate; "
        "python3 -m pip install -r requirements-release.txt; "
        "echo deactivate; make docs-check"
    )
    assert packaging_gate._python_deps_issue([echoed]) is None


def test_release_gate_step_env_does_not_carry_to_later_docs_check() -> None:
    """A step-local VIRTUAL_ENV must not count as the next step's runtime."""
    workflow = (
        "jobs:\n"
        f"  {packaging_gate.RELEASE_GATE_JOB_NAME}:\n"
        "    steps:\n"
        "      - run: pip install -r requirements-release.txt\n"
        "        env:\n"
        "          VIRTUAL_ENV: .venv\n"
        '          PATH: ".venv/bin:$PATH"\n'
        "      - run: make docs-check\n"
    )
    steps = packaging_gate._job_run_step_records(
        workflow, packaging_gate.RELEASE_GATE_JOB_NAME
    )
    assert steps is not None
    assert packaging_gate._python_deps_issue(steps) is not None

    shared_workflow = (
        "jobs:\n"
        f"  {packaging_gate.RELEASE_GATE_JOB_NAME}:\n"
        "    env:\n"
        "      VIRTUAL_ENV: .venv\n"
        '      PATH: ".venv/bin:$PATH"\n'
        "    steps:\n"
        "      - run: pip install -r requirements-release.txt\n"
        "      - run: make docs-check\n"
    )
    shared_steps = packaging_gate._job_run_step_records(
        shared_workflow, packaging_gate.RELEASE_GATE_JOB_NAME
    )
    assert shared_steps is not None
    assert packaging_gate._python_deps_issue(shared_steps) is None
