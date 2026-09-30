"""Adversarial tests for the fuzz/packaging shell-semantics gate.

The gate (``tools/release/gates/validate_fuzz_packaging.py``) models the
release workflow's run-block shell closely enough to decide which commands
can execute, so provisioning text cannot satisfy a check from a comment,
heredoc body, quoted string, dead branch, or unreachable function.  These
tests attack that model: each one pins one shell rule the gate must respect,
with paired acceptance/rejection shapes wherever both directions matter.
"""

from __future__ import annotations

import pathlib
import shlex
import textwrap
import subprocess

import pytest

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


def test_toolchain_gate_keeps_heredoc_body_quotes_out_of_later_comments() -> None:
    """A heredoc apostrophe cannot make a later shell comment executable."""
    for body in ("ordinary heredoc text", "it's ordinary heredoc text"):
        script = (
            "cat <<'EOF' >/dev/null\n"
            + body
            + "\nEOF\n"
            + "echo inert; # function fake() {\n"
            + DRIFT
            + INSTALLER
            + COMPONENT
        )
        assert packaging_gate._release_gate_toolchain_issue(script) is None

    # A commented-out installer still cannot satisfy the required provisioning.
    commented_installer = (
        "cat <<'EOF' >/dev/null\n"
        "it's ordinary heredoc text\n"
        "EOF\n"
        + "echo inert; # "
        + INSTALLER_NO_NL
        + "\n"
        + DRIFT
        + COMPONENT
    )
    assert packaging_gate._release_gate_toolchain_issue(commented_installer) is not None


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
        f"{drift}# {installer}{component}"
    )
    assert packaging_gate._release_gate_toolchain_issue(
        f"# {drift}{installer}{component}"
    )

    # Command position matters: echoes and heredoc bodies must not count.
    echo_install = f"echo '{installer.strip()}" + "'\n"
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


def test_toolchain_gate_treats_expansion_chars_as_literal_delimiters() -> None:
    """Bash removes quotes but does not expand heredoc delimiter words."""
    drift = DRIFT
    installer = (
        "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    component = (
        'retry 5 rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
    )
    fake = "cat <<$EOF\n" + installer + component + drift + "$EOF\n"
    assert packaging_gate._release_gate_toolchain_issue(fake)
    # This raw install is heredoc data passed to cat, not an executable
    # command. A dollar sign in the delimiter is literal shell syntax.
    workflow = (
        "jobs:\n"
        "  fuzz-qualification:\n"
        "    steps:\n"
        "      - name: b\n"
        "        run: |\n"
        "          cat <<$EOF\n"
        "          rustup toolchain install nightly --profile minimal\n"
        "          $EOF\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow) is None
    backtick_heredoc = (
        "cat <<`EOF`\n" + installer + component + drift + "`EOF`\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(backtick_heredoc)
    # A quoted delimiter is static: the literal word is the terminator, the
    # body is dropped, and real provisioning after it still satisfies.
    quoted = (
        "cat <<'$EOF'\nbody\n$EOF\n" + drift + installer + component
    )
    assert packaging_gate._release_gate_toolchain_issue(quoted) is None


def test_raw_install_scan_continues_after_literal_dollar_delimiter() -> None:
    """A dollar-sign delimiter must not hide a later shell-input body."""
    dynamic_only = "delimiter=END\ncat <<$delimiter\nbenign data\n$delimiter\n"
    static_raw = (
        "bash -s <<'SCRIPT'\n"
        "rustup toolchain install stable\n"
        "SCRIPT\n"
    )
    combined = dynamic_only + static_raw

    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(dynamic_only)
    ) is None
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(static_raw)
    )
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(combined)
    )


def test_raw_install_scan_continues_after_unresolved_ansi_c_delimiter() -> None:
    """An unsupported delimiter spelling cannot hide later shell input."""
    opaque_delimiter = "cat <<$'E\\tOF'\nopaque data\nE\tOF\n"
    static_raw = (
        "bash -s <<'SCRIPT'\n"
        "rustup toolchain install stable\n"
        "SCRIPT\n"
    )
    workflow = _raw_install_workflow(opaque_delimiter + static_raw)

    assert packaging_gate._raw_toolchain_install_issue(workflow)


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
    false_cases = ("bare false", f"{drift}false && {installer}{component}"), (
        "quoted false",
        f'{drift}"false" && {installer}{component}',
    ), (
        "single-quoted false",
        f"{drift}'false' && {installer}{component}",
    )
    scripts = [script for _label, script in false_cases]
    assert len(set(scripts)) == len(scripts)
    for _label, script in false_cases:
        assert packaging_gate._release_gate_toolchain_issue(script)

    # The chain stays dead through further `&&` links...
    assert packaging_gate._release_gate_toolchain_issue(
        (f"{drift}false && {installer.strip()} && {component.strip()}" + "\n")
    )
    # ...while `||` revives it, so a compliant script stays accepted.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            f"{drift}false && echo skipped || {installer}{component}"
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
            f'{drift}"true" && {installer}{component}'
        )
        is None
    )
    # `exit` ends that shell: nothing after it runs.
    assert packaging_gate._release_gate_toolchain_issue(
        drift + "exit 0\n" + installer + component
    )
    # A carried command on a branch marker cannot revive a terminated shell.
    for terminator in ("exit 0", "false"):
        unreachable_component = (
            drift + installer + terminator + "\nif true; then "
            + component.strip() + "; fi\n"
        )
        assert packaging_gate._release_gate_toolchain_issue(
            unreachable_component
        ) is not None
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
        + "".join(f"          {line}" + "\n" for line in runs.splitlines())
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


def test_toolchain_gate_uses_definition_active_at_each_call() -> None:
    """A later uncalled redefinition cannot satisfy an earlier function call."""
    called_after_redefinition = _assert_call_order_issue_and_build_followup(
        "provision() { echo safe; }\n" "provision\n" "provision() {\n",
        "}\n",
        "provision() { echo safe; }\n" "provision() {\n",
        "}\n" "provision\n",
    )
    assert packaging_gate._release_gate_toolchain_issue(
        called_after_redefinition
    ) is None

    call_in_false_branch_after_definition = _assert_call_order_issue_and_build_followup(
        "provision() { echo safe; }\n"
        "if false; then\n"
        "  echo __release_gate_definition_1\n"
        "  provision() {\n",
        "  }\n" "fi\n" "provision\n",
        "provision() {\n",
        "}\n"
        "if false; then\n"
        "  unused() { :; };\n"
        "  provision;\n"
        "fi\n",
    )
    assert packaging_gate._release_gate_toolchain_issue(
        call_in_false_branch_after_definition
    ) is not None


def _assert_call_order_issue_and_build_followup(
    issue_script_prefix: str,
    issue_script_suffix: str,
    followup_script_prefix: str,
    followup_script_suffix: str,
) -> str:
    """Assert a call-order issue and build a follow-up script.

    Args:
        issue_script_prefix: Rejected script content before the install sequence.
        issue_script_suffix: Rejected script content after the install sequence.
        followup_script_prefix: Follow-up content before the install sequence.
        followup_script_suffix: Follow-up content after the install sequence.

    Returns:
        The follow-up script with the toolchain drift check.
    """
    issue_script = (
        issue_script_prefix + INSTALLER + COMPONENT + issue_script_suffix + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(
        issue_script
    ) is not None

    return (
        followup_script_prefix
        + INSTALLER
        + COMPONENT
        + followup_script_suffix
        + DRIFT
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
    """Modeled shell and command wrappers do not hide raw installs."""
    for wrapped in (
        "command rustup toolchain install nightly --profile minimal",
        "env FOO=1 rustup toolchain install nightly --profile minimal",
        "exec rustup toolchain install nightly --profile minimal",
        "bash -c 'rustup toolchain install nightly --profile minimal'",
        "zsh -c 'rustup toolchain install nightly --profile minimal'",
        "zsh --unmodeled-option -c 'rustup toolchain install nightly'",
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


def test_raw_install_workflow_rejects_nohup_with_valid_provisioning_present() -> None:
    """A verified setup elsewhere in the workflow cannot hide a raw install."""
    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - name: provision\n"
        "        run: |\n"
        "          bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        '          rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        "          nohup rustup toolchain install stable\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(workflow) is not None


def test_raw_install_scan_reads_shell_stdin_heredocs_only() -> None:
    """Shell-fed heredoc commands execute; inert heredoc bodies remain data."""
    for shell_command in (
        "bash -s",
        "bash -o pipefail",
        "bash -eo pipefail",
    ):
        shell_input = (
            "jobs:\n"
            "  release-gate:\n"
            "    steps:\n"
            "      - run: |\n"
            f"          {shell_command} <<'EOF'\n"
            "          rustup toolchain install stable\n"
            "          EOF\n"
        )
        assert packaging_gate._raw_toolchain_install_issue(shell_input) is not None

    inert_shell_input = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - run: |\n"
        "          bash --noprofile -c 'true' <<'EOF'\n"
        "          rustup toolchain install stable\n"
        "          EOF\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(inert_shell_input) is None

    inert_input = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - run: |\n"
        "          cat <<'EOF'\n"
        "          rustup toolchain install stable\n"
        "          EOF\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(inert_input) is None


def test_toolchain_gate_accepts_partially_quoted_delimiters() -> None:
    """Quoting any part of a delimiter word disables expansion entirely."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    script = 'WORD=RUNTIME\ncat <<E"$WORD"\nbody\nE$WORD\n'
    assert packaging_gate._release_gate_toolchain_issue(
        script + drift + installer + component) is None


def test_toolchain_gate_ignores_literal_dollar_delimiter_in_uncalled_functions() -> None:
    """An unused function's literal ``$DELIM`` heredoc cannot reject the script."""
    drift = DRIFT
    installer = INSTALLER
    component = COMPONENT
    dead = "unused() {\ncat <<$DELIM\nbody\n$DELIM\n}\n"
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
        f"{drift}false >/dev/null && {installer}{component}"
    )
    # Any chain whose left side is not an evaluated literal is conditional.
    assert packaging_gate._release_gate_toolchain_issue(
        f"{drift}command -v cargo && {installer}{component}"
    )
    # A literal-true chain still counts, redirections included.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            f"{drift}true && {installer}{component}"
        )
        is None
    )
    assert (
        packaging_gate._release_gate_toolchain_issue(
            f"{drift}true >/dev/null && {installer}{component}"
        )
        is None
    )
    # A dead left side revives the chain under `||`.
    assert (
        packaging_gate._release_gate_toolchain_issue(
            f"{drift}false >/dev/null || {installer}{component}"
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


def test_eval_command_substitution_cannot_split_into_inert_fragments() -> None:
    """Opaque eval substitutions fail closed across quotes and backticks."""
    raw_substitutions = (
        'eval "$(./gen.sh)"',
        'eval "$(printf \'%s\' \'rustup toolchain install stable\')"',
        'eval "`printf \'%s\' \'rustup toolchain install stable\'`"',
    )
    for script in raw_substitutions:
        workflow = _raw_install_workflow(script)
        assert packaging_gate._raw_toolchain_install_issue(workflow), script

    safe_controls = (
        _raw_install_workflow("eval 'echo safe'"),
        _raw_install_workflow("echo 'eval \"$(./gen.sh)\"'"),
    )
    for safe_control in safe_controls:
        assert packaging_gate._raw_toolchain_install_issue(safe_control) is None


def test_eval_expands_static_variable_before_raw_install_check() -> None:
    """Variable-held raw installs cannot hide behind Bash eval."""
    scripts = (
        ("cmd='rustup toolchain install nightly'\neval \"$cmd\"\n", True),
        ("cmd='echo rustup toolchain install nightly'\neval \"$cmd\"\n", False),
        ('eval "echo $PAYLOAD"\n', True),
        ("eval \"$unknown_command\"\n", True),
    )
    for eval_script, has_raw_install in scripts:
        workflow = packaging_gate.yaml.safe_dump(
            {
                "jobs": {
                    "release-gate": {
                        "steps": [
                            {
                                "shell": "bash",
                                "run": DRIFT + INSTALLER + COMPONENT + eval_script,
                            }
                        ]
                    }
                }
            },
            sort_keys=False,
        )
        records = packaging_gate._job_run_step_records(workflow, "release-gate")
        assert records is not None
        assert packaging_gate._release_gate_toolchain_issue(records) is None
        issue = packaging_gate._raw_toolchain_install_issue(workflow)
        assert (issue is not None) is has_raw_install


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


def test_step_shell_model_rejects_nonexecuting_bash_templates() -> None:
    """A shell template must feed the generated script to an interpreter."""
    script = DRIFT + INSTALLER + COMPONENT
    invalid_shells = (
        "bash --version {0}",
        "bash -c ':' {0}",
        "bash -c : {0}",
        "bash -n {0}",
        "zsh -n {0}",
    )
    for shell in invalid_shells:
        step = {"shell": shell, "run": script}
        assert not packaging_gate._step_runs_shell(step), shell
        workflow = packaging_gate.yaml.safe_dump(
            {"jobs": {"release-gate": {"steps": [step]}}},
            sort_keys=False,
        )
        records = packaging_gate._job_run_step_records(workflow, "release-gate")
        assert records is not None
        assert records == [], shell
        assert packaging_gate._release_gate_toolchain_issue(records) is not None

    for shell in (
        "bash",
        "/bin/bash",
        "bash {0}",
        "bash -e {0}",
        "bash --noprofile --norc -e -o pipefail {0}",
        "bash -euo pipefail {0}",
        "bash -euxo pipefail {0}",
        "zsh {0}",
        "/usr/bin/zsh -e {0}",
        "zsh -o pipefail {0}",
    ):
        assert packaging_gate._step_runs_shell({"shell": shell, "run": script})


def test_step_shell_model_reads_success_always_and_shell_paths() -> None:
    """`success()`/`always()` run on the happy path; shells match by basename.

    A step gated on `success()` (the default gate spelled out) or
    `always()` still runs; `failure()` may skip.  The shell is matched by
    its basename (`/bin/bash` is `bash`), and a non-string shell value
    fails closed like an unexpected `if`.
    """
    _assert_step_field_accepts_shell_values(
        "if", "success()", "always()", "failure()"
    )
    _assert_step_field_accepts_shell_values(
        "shell", "/bin/bash", "bash -e {0}", "python3"
    )
    assert not packaging_gate._step_runs_shell({"shell": 123, "run": "x"})
    assert not packaging_gate._step_runs_shell({"shell": [], "run": "x"})


def _assert_step_field_accepts_shell_values(
    field_name: str,
    first_supported_value: str,
    second_supported_value: str,
    unsupported_value: str,
) -> None:
    assert packaging_gate._step_runs_shell(
        {field_name: first_supported_value, "run": "x"}
    )
    assert packaging_gate._step_runs_shell(
        {field_name: second_supported_value, "run": "x"}
    )
    assert not packaging_gate._step_runs_shell(
        {field_name: unsupported_value, "run": "x"}
    )


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
    _assert_provisioning_requires_a_live_branch(
        "if true; then ", "if false; then "
    )
    _assert_provisioning_requires_a_live_branch(
        "if false; then echo skip; else ", "if true; then echo run; else "
    )


def _assert_provisioning_requires_a_live_branch(
    live_branch_prefix: str, dead_branch_prefix: str
) -> None:
    live_branch = (
        f"{live_branch_prefix}{INSTALLER.rstrip()}; fi\n"
        + COMPONENT
        + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(live_branch) is None
    dead_branch = (
        f"{dead_branch_prefix}{INSTALLER.rstrip()}; fi\n"
        + COMPONENT
        + DRIFT
    )
    assert packaging_gate._release_gate_toolchain_issue(dead_branch) is not None


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


def test_shell_errexit_scan_skips_value_option_operands() -> None:
    """An option's operand is not an option word.

    `bash --rcfile -e {0}` hands `-e` to `--rcfile` as its file operand, so
    the body runs WITHOUT errexit (verified: the whole body executes even
    with a failing command).  Reading `-e` as the errexit switch would
    certify a masked docs check.
    """
    assert packaging_gate._shell_initial_errexit("bash --rcfile -e {0}") is False
    assert (
        packaging_gate._shell_initial_errexit("bash --init-file -e {0}") is False
    )
    assert packaging_gate._shell_initial_errexit("bash --rcfile X {0}") is False
    # A real errexit switch (before or after the operand form) still counts.
    assert packaging_gate._shell_initial_errexit("bash -e --rcfile X {0}") is True
    assert (
        packaging_gate._shell_initial_errexit("bash --init-file X -e {0}") is True
    )
    assert packaging_gate._shell_initial_errexit("bash -o errexit {0}") is True


def test_shell_state_changes_around_the_check_fail_certification() -> None:
    """`set +e`, a split export, and `builtin cd` all defeat certification.

    Each was verified live before the fix: `set +e` disables errexit for
    the rest of the script so a later command can swallow the check's
    failure; `MAKEFLAGS=-n; export MAKEFLAGS` hands make the flag through a
    split assignment; and `builtin cd DIR` moves the shell away from the
    repository root exactly as `cd DIR` does.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for script in (
        "set +e\nmake docs-check\necho done",
        "set +e\nmake docs-check; echo ok",
        "MAKEFLAGS=-n\nexport MAKEFLAGS\nmake docs-check",
        'builtin cd "$RUNNER_TEMP/noop"\nmake docs-check',
        'command cd "$RUNNER_TEMP/noop"\nmake docs-check',
    ):
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is not None
        ), script
    # The plain and explicitly-errexit forms still certify.
    assert packaging_gate._python_deps_issue(
        [install, {"run": "make docs-check"}]
    ) is None
    assert packaging_gate._python_deps_issue(
        [install, {"run": "set -e\nmake docs-check"}]
    ) is None
    assert packaging_gate._python_deps_issue(
        [install, {"run": "MAKEFLAGS=-n\nexport OTHER\nmake docs-check"}]
    ) is None


def test_exported_make_flags_disqualify_a_later_check() -> None:
    """An export earlier in the same run block reaches the later make.

    `export MAKEFLAGS=-n` changes every later invocation in that shell
    (verified: make only prints the recipe), so the step scan must carry
    the exported value forward; a static-environment-only read missed it.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for script in (
        "export MAKEFLAGS=-n\nmake docs-check",
        "export MAKEFLAGS=n\nmake docs-check",
        "export GNUMAKEFLAGS=-q\nmake docs-check",
        "export MAKE=/usr/bin/true\nmake docs-check",
    ):
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is not None
        ), script
    # An unrelated export and a plain invocation still certify.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "export FOO=1\nmake docs-check"}]
        )
        is None
    )
    assert packaging_gate._python_deps_issue([install, "make docs-check"]) is None


def test_export_attribute_persists_across_a_later_plain_assignment() -> None:
    """A bare export arm keeps the attribute when a later statement sets it.

    Regression (outside-diff review): the scan refreshed only the plain
    assignment table, so ``export MAKEFLAGS=`` followed by
    ``MAKEFLAGS=-n`` lost the flag although bash keeps the export
    attribute and hands ``-n`` to every later make (verified live: the
    recipe only prints and the step exits 0).  The attribute and the value
    are tracked separately now; a command-local prefix still does not
    persist (verified: a later make runs its recipes).
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for script in (
        "export MAKEFLAGS=\nMAKEFLAGS=-n\nmake docs-check",
        "export MAKEFLAGS=\nMAKEFLAGS=n\nmake docs-check",
        "export MAKEFLAGS\nexport MAKEFLAGS=\nMAKEFLAGS=-n\nmake docs-check",
        "MAKEFLAGS=-n\nexport MAKEFLAGS\nmake docs-check",
    ):
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is not None
        ), script
    # A command-local prefix does not persist: the later make runs.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "MAKEFLAGS=-n make docs-check\nmake docs-check"}]
        )
        is None
    )
    # An overwrite back to an executing value certifies again.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "export MAKEFLAGS=-n\nMAKEFLAGS=\nmake docs-check"}]
        )
        is None
    )


def test_unresolved_expansion_in_make_flags_is_rejected() -> None:
    """A make flag left as a shell reference cannot certify the check.

    Regression (outside-diff review): ``FLAGS=-n`` followed by
    ``export MAKEFLAGS=$FLAGS`` reads as a literal ``$FLAGS`` token in the
    scan while the shell hands ``-n`` to make at run time (verified live:
    the recipe is only printed and the step exits 0).  The scan cannot
    attribute the resolved flags, so it fails closed; a literal value
    keeps its normal classification, and a literal re-assignment restores
    certification after a substitution.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for script in (
        "FLAGS=-n\nexport MAKEFLAGS=$FLAGS\nmake docs-check",
        "FLAGS=-n\nexport MAKEFLAGS=${FLAGS}\nmake docs-check",
        "export MAKEFLAGS=$UNKNOWN_FLAGS\nmake docs-check",
        "FLAGS=-n\nMAKEFLAGS=$FLAGS make docs-check",
        "export MAKEFLAGS=$(getflags)\nmake docs-check",
        "export MAKEFLAGS=`getflags`\nmake docs-check",
    ):
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is not None
        ), script
    # The environment scope carries the same failure mode.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "$FLAGS"}}]
        )
        is not None
    )
    # A literal value and a literal re-assignment still certify.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "FLAGS=s\nexport MAKEFLAGS=s\nmake docs-check"}]
        )
        is None
    )
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "export MAKEFLAGS=`getflags`\nMAKEFLAGS=-s\nmake docs-check"}]
        )
        is None
    )


def test_shell_special_parameters_in_make_flags_are_rejected() -> None:
    """Every shell expansion form in a make flag fails closed.

    Regression (round-4 review): the first expansion class covered names,
    digits, ``$(``/``${`` and a trailing ``$``, but the shell's special
    parameters and quoted forms still certified - ``export MAKEFLAGS=$-``
    expands to the shell's option letters and reaches make (verified
    live), yet the scan read a literal token.  The class now covers
    ``$- $@ $* $# $? $!``, the ``$'...'``/``$"..."`` openers, and the
    quoted forms; a literal value keeps its normal classification.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for parameter in (
        "$-", "$@", "$*", "$#", "$?", "$!", "$0", "$1",
        "$'x'", '$"x"', "$FLAGS", "${FLAGS}", "$(getflags)",
    ):
        script = f"export MAKEFLAGS={parameter}\nmake docs-check"
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is not None
        ), parameter
    for literal in ("", "-s", "s"):
        script = f"export MAKEFLAGS={literal}\nmake docs-check"
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is None
        ), literal


def test_substitution_taint_follows_the_last_assignment() -> None:
    """Only the LAST assignment decides whether a name stays tainted.

    Regression (round-5 review): a literal assignment before a later
    backtick substitution cleared the taint, so
    ``MAKEFLAGS=-s; export MAKEFLAGS=\\`getflags\\``` certified although the
    command's output reaches make (verified live: the recipe only prints).
    The reverse order leaves the literal in force and still certifies
    (verified live: the recipe runs).
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "MAKEFLAGS=-s\nexport MAKEFLAGS=`getflags`\nmake docs-check"}]
        )
        is not None
    )
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "export MAKEFLAGS=`getflags`\nMAKEFLAGS=-s\nmake docs-check"}]
        )
        is None
    )


def test_unreachable_errexit_change_does_not_flip_the_mode() -> None:
    """A ``set`` behind a short-circuit must not change the shell's mode.

    Regression (round-5 review): the state scan applied every ``set`` it
    saw, so ``false && set -e; make docs-check; true`` read the make as
    errexit-protected and certified, although the ``set`` never ran and
    the trailing ``true`` swallowed the failure (verified live: step exit
    0).  A reachable ``set`` still applies.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "false && set -e; make docs-check\ntrue", "shell": "bash {0}"}]
        )
        is not None
    )
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "true && set -e; make docs-check\ntrue", "shell": "bash {0}"}]
        )
        is None
    )


def test_step_environment_presets_the_export_attribute() -> None:
    """A make name carried by the step environment is already exported.

    Regression (round-5 review): the scan seeded the export attributes
    only from in-script ``export`` statements, so a step-level
    ``MAKEFLAGS`` followed by a plain ``MAKEFLAGS=-n`` certified although
    bash keeps the attribute and hands ``-n`` to make (verified live: the
    recipe only prints).  Without the environment value the plain
    assignment does not reach make and still certifies.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "MAKEFLAGS=-n\nmake docs-check", "env": {"MAKEFLAGS": ""}}]
        )
        is not None
    )
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "MAKEFLAGS=-n\nmake docs-check"}]
        )
        is None
    )
    # An executing value in the environment keeps certifying.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "-s"}}]
        )
        is None
    )


def test_errexit_change_does_not_detach_following_commands() -> None:
    """A ``set`` mid-script must not move later commands out of the scan.

    Regression (outside-diff review): the scan split the script at its
    errexit changes and analyzed each region separately, so in
    ``make docs-check; set -e; true`` the make read as its region's last
    command while the trailing ``true`` actually decided the step's status
    (verified live: failing make, step exit 0 with ``bash {0}``).  Every
    command now carries the mode in force at its own position.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for step in (
        {"run": "make docs-check; set -e; true", "shell": "bash {0}"},
        {"run": "make docs-check; set -e", "shell": "bash {0}"},
        {"run": "make docs-check\ntrue", "shell": "bash {0}"},
    ):
        assert (
            packaging_gate._python_deps_issue([install, step]) is not None
        ), step
    # With errexit already on (or a shell that enables it), the failed
    # check aborts the script and the trailing command cannot swallow it.
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "make docs-check; true"}]
        )
        is None
    )
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "set -e\nmake docs-check\ntrue", "shell": "bash {0}"}]
        )
        is None
    )


def test_shell_errexit_scan_stops_at_the_script_operand() -> None:
    """Words after `{0}` are positional parameters, not shell options.

    Regression: the errexit model scanned every word after the shell name,
    so `bash {0} -e` (which passes the literal `-e` as the script's `$1`
    and runs the body without errexit - verified against bash) was misread
    as errexit-enabled, and the masking analysis then dropped commands the
    script would actually run.
    """
    assert packaging_gate._shell_initial_errexit("bash {0} -e") is False
    assert packaging_gate._shell_initial_errexit("bash {0} -n") is False
    assert packaging_gate._shell_initial_errexit("bash {0} --errexit") is False
    # Options before the operand still count.
    assert packaging_gate._shell_initial_errexit("bash -e {0}") is True
    assert packaging_gate._shell_initial_errexit("bash -o errexit {0}") is True
    assert packaging_gate._shell_initial_errexit("bash -euo pipefail {0}") is True
    # A shell without the operand keeps the default (built-in forms add -e).
    assert packaging_gate._shell_initial_errexit("bash -e") is True


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
        "pip() { :; }\n",
        "pip3() { :; }\n",
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


def test_toolchain_gate_rejects_active_command_aliases() -> None:
    """Enabled shell aliases cannot replace checked provisioning commands."""
    provisioning = (
        "bash ./packaging/scripts/install-verified-rustup.sh "
        '--toolchain "${RUST_TOOLCHAIN}"\n'
        'rustup component add --toolchain "${RUST_TOOLCHAIN}" rustfmt\n'
        + DRIFT
    )
    shadowed = (
        "shopt -s expand_aliases\n"
        "alias rustup='echo ignored'\n"
        + provisioning
    )
    assert packaging_gate._release_gate_toolchain_issue(shadowed) is not None

    inactive = "alias rustup='echo ignored'\n" + provisioning
    assert packaging_gate._release_gate_toolchain_issue(inactive) is None


def _provisioning_workflow(
    run: str, env_scope: str | None = None, shell: str | None = None
) -> str:
    """Build one provisioning job with BASH_ENV at the requested scope."""
    workflow_env = (
        "env:\n  BASH_ENV: ./shadow.sh\n"
        if env_scope == "workflow"
        else ""
    )
    job_env = (
        "    env:\n      BASH_ENV: ./shadow.sh\n"
        if env_scope == "job"
        else ""
    )
    step_env = (
        "      - env:\n          BASH_ENV: ./shadow.sh\n"
        "        run: |\n"
        if env_scope == "step"
        else ""
    )
    run_block = "".join(f"          {line}\n" for line in run.splitlines())
    run_step = (
        f"      - shell: {shell}\n        run: |\n"
        if shell
        else "      - run: |\n"
    )
    return (
        workflow_env
        + "jobs:\n"
        "  release-gate:\n"
        + job_env
        + "    steps:\n"
        + (step_env or run_step)
        + run_block
    )


def test_dot_source_invocations_are_recognized_and_followed(
    tmp_path, monkeypatch
) -> None:
    """A dot-sourced script runs in-shell, so its operand is followed.

    Regression: ``Path(".").name`` is empty, so the ``. ./script`` spelling
    was not recognized as a followed invocation at all (the ``source``
    keyword was).  Both spellings now resolve to the interpreter head, the
    wrapper dispatcher follows a dot-sourced operand like any other invoked
    script, and a dot-source with no operand fails closed.
    """
    assert packaging_gate._invocation_head(". ./x.sh") == "."
    assert packaging_gate._invocation_head("source ./x.sh") == "source"
    assert packaging_gate._raw_install_from_wrapper(["."], 0) is True, (
        "a dot-source without an operand cannot be inspected: fail closed"
    )

    # With a resolvable repository-local script, the dot-sourced body is
    # actually scanned: a raw install inside it is found through both
    # spellings.
    helper = tmp_path / "helper.sh"
    helper.write_text(
        "rustup toolchain install 1.2.3 --profile minimal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    for operand in (".", "source"):
        assert packaging_gate._raw_install_from_invoked_scripts(
            f"{operand} ./helper.sh\n", 0, None
        ) is True, operand


def test_provisioning_shadow_guard_rejects_external_shell_inputs() -> None:
    """Sourced files, eval and inherited startup files are not modeled."""
    for source in (
        "source ./shadow.sh\n",
        ". ./shadow.sh\n",
        "bash -c 'source ./shadow.sh; rustup component add rustfmt'\n",
        "setup() { source ./shadow.sh; }\nsetup\n",
        "eval 'rustup() { :; }'\n",
    ):
        issue = packaging_gate._provisioning_shadow_issue(
            _provisioning_workflow(source)
        )
        assert issue is not None, source

    for env_scope in ("workflow", "job", "step"):
        workflow = _provisioning_workflow(INSTALLER, env_scope)
        assert packaging_gate._provisioning_shadow_issue(workflow) is not None

    inline_assignment = _provisioning_workflow(
        "export BASH_ENV=./shadow.sh\n" + INSTALLER
    )
    assert packaging_gate._provisioning_shadow_issue(inline_assignment) is not None


def test_provisioning_shadow_guard_rejects_function_defined_aliases() -> None:
    """Indirect alias setup cannot replace provisioning commands."""
    script = (
        "shopt -s expand_aliases\n"
        "define_shadow() { alias rustup='echo ignored'; }\n"
        "define_shadow\n"
        + COMPONENT
    )
    issue = packaging_gate._provisioning_shadow_issue(
        _provisioning_workflow(script)
    )
    assert issue is not None


def test_provisioning_shadow_guard_models_default_alias_shells() -> None:
    """sh/zsh expand active aliases by default; bash requires shopt."""
    alias_script = "alias rustup='echo ignored'\n" + COMPONENT
    for shell in ("sh {0}", "zsh {0}"):
        workflow = _provisioning_workflow(alias_script, shell=shell)
        assert packaging_gate._provisioning_shadow_issue(workflow) is not None

    bash_workflow = _provisioning_workflow(alias_script, shell="bash {0}")
    assert packaging_gate._provisioning_shadow_issue(bash_workflow) is None


def test_shadow_guard_keeps_quoted_alias_tokens_for_shell_parsing() -> None:
    """Quoted option and alias words remain visible to quote-aware parsing."""
    script = (
        'shopt -s "expand_aliases"; '
        'alias "rustup=echo ignored"; '
        "rustup component add --toolchain stable rustfmt"
    )
    assert packaging_gate._defined_alias_names(script) == {"rustup"}
    assert packaging_gate._shadowing_issue(script) is not None

    inert = 'echo "alias rustup=echo ignored; shopt -s expand_aliases"'
    assert packaging_gate._defined_alias_names(inert) == set()
    assert packaging_gate._shadowing_issue(inert) is None


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
        + "".join(f"          {line}" + "\n" for line in runs.splitlines())
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
    installer = INSTALLER
    component = COMPONENT
    drift = DRIFT
    script = f"{drift}env -- FOO=bar {installer}env -- FOO=bar {component}"
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


def test_python_dependency_gate_rejects_untrusted_retry_wrapper() -> None:
    """A no-op local retry function cannot make pip look like an install."""
    no_op = (
        "retry() { :; }\n"
        "retry 5 python3 -m pip install -r requirements-release.txt\n"
        "make docs-check"
    )
    assert packaging_gate._python_deps_issue([no_op]) is not None

    forwarding = (
        'retry() { "$@"; }\n'
        "retry 5 python3 -m pip install -r requirements-release.txt\n"
        "make docs-check"
    )
    assert packaging_gate._python_deps_issue([forwarding]) is None


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


def test_raw_install_detector_scans_shell_and_python_here_strings() -> None:
    """Here-strings are executable stdin source, not inert heredocs."""
    raw_python = (
        "import subprocess; "
        "subprocess.run(['rustup', 'toolchain', 'install', 'nightly'])"
    )
    raw_scripts = (
        "bash <<< 'rustup toolchain install nightly'",
        "bash -s <<< 'rustup toolchain install nightly'",
        f"python3 <<< {shlex.quote(raw_python)}",
    )
    for script in raw_scripts:
        assert packaging_gate._raw_install_in_script(script), script

    safe_python = "print(1)"
    safe_scripts = (
        "bash <<< 'echo safe'",
        "bash -s <<< 'echo safe'",
        f"python3 <<< {shlex.quote(safe_python)}",
        "bash --rcfile -c 'rustup toolchain install 1.2.3'",
    )
    for script in safe_scripts:
        assert not packaging_gate._raw_install_in_script(script), script


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
            f"bash {suppress}" + " -c 'PAY'".replace("PAY", raw)
        )

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


def _raw_install_workflow(script: str, shell: str | None = None) -> str:
    """Put one literal script in a parseable workflow run step."""
    body = "".join(f"          {line}" + "\n" for line in script.splitlines())
    step = (
        "      - run: |\n"
        if shell is None
        else f"      - shell: {shell}\n        run: |\n"
    )
    return "jobs:\n  probe:\n    steps:\n" + step + body


def test_raw_install_detector_scans_python_shell_steps() -> None:
    """Workflow Python shells are parsed as Python, including wrapped forms."""
    source = (
        "import subprocess\n"
        "subprocess.run(['rustup', 'toolchain', 'install', 'stable'])\n"
    )
    for shell in ("python", "python3 {0}", "uv run python {0}"):
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(source, shell=shell)
        ) is not None, shell

    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("print('safe')\n", shell="python")
    ) is None


def test_raw_install_detector_follows_local_reusable_workflows(
    tmp_path, monkeypatch
) -> None:
    """Local reusable jobs are scanned; unreadable or remote uses fail closed."""
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    reusable = workflows / "reusable.yml"
    reference = (
        "jobs:\n"
        "  forwarded:\n"
        "    uses: ./.github/workflows/reusable.yml\n"
    )
    reusable.write_text(
        _raw_install_workflow("rustup toolchain install stable\n"),
        encoding="utf-8",
    )
    assert packaging_gate._raw_toolchain_install_issue(reference) is not None

    reusable.write_text(_raw_install_workflow("echo safe\n"), encoding="utf-8")
    assert packaging_gate._raw_toolchain_install_issue(reference) is None

    remote_reference = (
        "jobs:\n"
        "  forwarded:\n"
        "    uses: vendor/repo/.github/workflows/reusable.yml@v1\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(remote_reference) is not None


def test_raw_install_detector_inspects_python_script_launchers(
    tmp_path, monkeypatch
) -> None:
    """Python script paths are inspected; opaque and missing paths fail closed."""
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(DRIFT_NO_NL)
    ) is None
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    script_dir = tmp_path / "tools"
    script_dir.mkdir()
    raw_script = script_dir / "raw_install.py"
    raw_script.write_text(
        "import subprocess\n"
        "subprocess.run(['rustup', 'toolchain', 'install', 'stable'])\n",
        encoding="utf-8",
    )
    safe_script = script_dir / "safe.py"
    safe_script.write_text("print('ordinary helper')\n", encoding="utf-8")
    local_helper = tmp_path / "helper.py"
    local_helper.write_text(
        "import subprocess\n"
        "def install():\n"
        "    subprocess.run(['rustup', 'toolchain', 'install', 'stable'])\n",
        encoding="utf-8",
    )
    imported_script = tmp_path / "imported.py"
    imported_script.write_text(
        "from helper import install\ninstall()\n", encoding="utf-8"
    )
    safe_helper = tmp_path / "safe_helper.py"
    safe_helper.write_text("def run():\n    return None\n", encoding="utf-8")
    safe_import_script = tmp_path / "safe_import.py"
    safe_import_script.write_text(
        "from safe_helper import run\nrun()\n", encoding="utf-8"
    )
    opaque_script = script_dir / "opaque.py"
    opaque_script.write_text(
        "import subprocess\nsubprocess.run(command)\n", encoding="utf-8"
    )
    module_dir = tmp_path / "raw_module"
    module_dir.mkdir()
    (module_dir / "__init__.py").write_text("", encoding="utf-8")
    (module_dir / "__main__.py").write_text(
        "import subprocess\n"
        "subprocess.run(['rustup', 'toolchain', 'install', 'stable'])\n",
        encoding="utf-8",
    )
    runpy_module = tmp_path / "runpy_target.py"
    runpy_module.write_text(
        "import subprocess\n"
        "subprocess.run(['rustup', 'toolchain', 'install', 'stable'])\n",
        encoding="utf-8",
    )

    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("python3 tools/raw_install.py")
    ) is not None
    _assert_one_safe_and_three_raw_install_launchers(
        safe_launcher="python3 tools/safe.py",
        raw_install_launchers=(
            "python3 tools/opaque.py",
            "python3 imported.py",
            "python3 -c 'from helper import install; install()'",
        ),
    )
    _assert_one_safe_and_three_raw_install_launchers(
        safe_launcher="python3 safe_import.py",
        raw_install_launchers=(
            "python3 -m raw_module",
            "python3 -m runpy runpy_target",
            "python3 tools/missing.py",
        ),
    )


def _assert_one_safe_and_three_raw_install_launchers(
    safe_launcher, raw_install_launchers
):
    """Assert the safe launcher is accepted and each raw-install one is flagged."""
    assert (
        packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(safe_launcher)
        )
        is None
    )
    for raw_install_launcher in raw_install_launchers:
        assert (
            packaging_gate._raw_toolchain_install_issue(
                _raw_install_workflow(raw_install_launcher)
            )
            is not None
        )


def test_release_validator_script_scans_through_the_static_project_root():
    """The known repository-root variable still resolves a Python script."""
    rooted = (
        'python3 "${PROJECT_ROOT}/tools/release/gates/'
        'validate_fuzz_qualification.py"'
    )
    assert packaging_gate._raw_install_in_script(rooted) is False
    unknown = rooted.replace("PROJECT_ROOT", "UNTRUSTED_ROOT")
    assert packaging_gate._raw_install_in_script(unknown) is True


def test_popen_version_probe_wrapper_is_safe_only_for_forwarded_argv():
    """A version probe can use Popen, but a raw install mutation is caught."""
    source = (
        packaging_gate.PROJECT_ROOT
        / "tools/release/gates/validate_fuzz_qualification.py"
    ).read_text(encoding="utf-8")
    assert not packaging_gate._python_inline_raw_install(source, 0, None)
    before = "subprocess.Popen(\n            command,"
    after = (
        "subprocess.Popen(\n"
        "            [\"rustup\", \"toolchain\", \"install\", \"stable\"],"
    )
    assert before in source
    mutated = source.replace(before, after, 1)
    assert mutated != source
    assert packaging_gate._python_inline_raw_install(mutated, 0, None)


def test_python_wrapper_allowlist_uses_static_executable_value() -> None:
    """Wrapper allowlists validate a variable's value, not its identifier."""
    prefix = (
        "import subprocess\n"
        "def _run_toolchain_version_command(command):\n"
        "    return subprocess.run(command)\n"
    )
    cases = (
        (
            'cargo = "rustup"\n'
            '_run_toolchain_version_command(\n'
            '    [cargo, "toolchain", "install", "nightly"]\n'
            ')\n',
            True,
        ),
        (
            'rustup = "cargo"\n'
            '_run_toolchain_version_command([rustup, "--version"])\n',
            False,
        ),
        (
            '_run_toolchain_version_command([unknown_binary, "--version"])\n',
            True,
        ),
    )
    for body, is_raw_install in cases:
        assert packaging_gate._python_inline_raw_install(
            prefix + "\n" + body, 0, None
        ) is is_raw_install


def test_python_subprocess_dispatchers_fail_closed_on_dynamic_argv() -> None:
    """Known process wrappers cannot hide a dynamic toolchain command."""
    wrappers = {
        "sudo": '["sudo", choose_command(), "toolchain", "install", "stable"]',
        "command": '["command", choose_command(), "toolchain", "install", "stable"]',
        "exec": '["exec", choose_command(), "toolchain", "install", "stable"]',
        "retry": '["retry", "5", choose_command(), "toolchain", "install", "stable"]',
        "time": '["time", choose_command(), "toolchain", "install", "stable"]',
        "setsid": '["setsid", choose_command(), "toolchain", "install", "stable"]',
    }
    for wrapper, argv in wrappers.items():
        source = (
            "import subprocess\n"
            "def choose_command():\n"
            "    return input()\n"
            f"subprocess.run({argv})\n"
        )
        assert packaging_gate._python_inline_raw_install(
            source, 0, None
        ), wrapper

    static_argvs = {
        "sudo": ["sudo", "rustup", "toolchain", "install", "stable"],
        "command": ["command", "rustup", "toolchain", "install", "stable"],
        "exec": ["exec", "rustup", "toolchain", "install", "stable"],
        "retry": ["retry", "5", "rustup", "toolchain", "install", "stable"],
        "time": ["time", "rustup", "toolchain", "install", "stable"],
        "setsid": ["setsid", "rustup", "toolchain", "install", "stable"],
    }
    for wrapper, argv in static_argvs.items():
        source = f"import subprocess\nsubprocess.run({argv!r})\n"
        assert packaging_gate._python_inline_raw_install(
            source, 0, None
        ), wrapper


def test_raw_install_detector_follows_indirect_command_positions() -> None:
    """Raw Rust installs remain forbidden through common shell dispatchers."""
    commands = (
        "printf stable | xargs -n 1 rustup toolchain install stable",
        "timeout --signal=TERM 5s rustup toolchain install stable",
        r"find /tmp -maxdepth 1 -exec rustup toolchain install stable \;",
        "if rustup toolchain install stable; then echo done; fi",
        "~/.cargo/bin/rustup toolchain install nightly-2026-09-21",
        "/home/kang/.cargo/bin/rustup toolchain install nightly-2026-09-21",
        "echo 'rustup toolchain install stable' | bash",
        "echo 'rustup toolchain install stable' | bash -s",
        "echo 'rustup toolchain install stable' | bash --rcfile -n",
        "echo 'rustup toolchain install stable' | bash --rcfile -D",
        "echo 'import os; os.system(\"rustup toolchain install stable\")' | python3",
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
        "echo safe | cat",
        "bash <<'SH'\necho safe\nSH",
        "python3 <<'PY'\nprint(\"safe\")\nPY",
        "python3 --version",
        "python3 -V",
        "python3 --help",
        "python3 -m json.tool",
        "python3 -W ignore -m json.tool",
        "bash --version",
        "bash --help",
    )
    for command in controls:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(command)
        ) is None, command


def test_raw_install_detector_follows_transparent_process_prefixes() -> None:
    """Process-control prefixes cannot hide a raw Rust install."""
    commands = (
        "time rustup toolchain install stable",
        "/usr/bin/time --format=%E rustup toolchain install stable",
        "nice -n 10 rustup toolchain install stable",
        "nice --adjustment=10 rustup toolchain install stable",
        "nice -10 rustup toolchain install stable",
        "setsid -f rustup toolchain install stable",
        "stdbuf -oL rustup toolchain install stable",
        "stdbuf --output=L rustup toolchain install stable",
        "nice stdbuf -oL rustup toolchain install stable",
    )
    for command in commands:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(command)
        ) is not None, command

    controls = (
        "time -p echo rustup toolchain install stable",
        "nice -n 10 printf '%s' rustup toolchain install stable",
        "setsid -f echo rustup toolchain install stable",
        "stdbuf -oL printf '%s' rustup toolchain install stable",
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
    for docs_command in (
        "gmake docs-check",
        "timeout 60 make docs-check",
        "/usr/bin/timeout 60 gmake docs-check",
    ):
        assert packaging_gate._python_deps_issue(
            [install, docs_command]
        ) is None, docs_command


def test_python_dependency_gate_rejects_shadowed_install_and_consumer() -> None:
    """Shell functions and aliases cannot spoof pip or the docs consumer."""
    install = "python3 -m pip install -r requirements-release.txt"
    scripts = (
        "pip() { :; }; pip install -r requirements-release.txt; "
        "make docs-check",
        "shopt -s expand_aliases; alias pip='echo ignored'; "
        "pip install -r requirements-release.txt; make docs-check",
        f"{install}; make() {{ :; }}; make docs-check",
        f"{install}; shopt -s expand_aliases; "
        "alias make='echo ignored'; make docs-check",
    )
    for script in scripts:
        assert packaging_gate._python_deps_issue([script]) is not None, script


def test_python_dependency_gate_accepts_only_supported_pip_command_forms() -> None:
    """The requirement install must invoke Python's pip module or pip itself."""
    valid = (
        "python -m pip install -r requirements-release.txt",
        "python3 -m pip install -r requirements-release.txt",
        "pip install -r requirements-release.txt",
        "pip3 install -r requirements-release.txt",
        "python3.12 -m pip install -r requirements-release.txt",
        "pip3.12 install -r requirements-release.txt",
        "nohup python3 -m pip install -r requirements-release.txt",
        "timeout 60 /usr/local/bin/python3.12 -m pip install -r requirements-release.txt",
    )
    for command in valid:
        assert packaging_gate._python_deps_issue(
            [command, "make docs-check"]
        ) is None, command

    for command in (
        "/usr/bin/sudo ./python3 -m pip install -r requirements-release.txt",
        "/usr/bin/sudo /usr/local/bin/pip3.12 install -r requirements-release.txt",
    ):
        assert packaging_gate._python_deps_issue(
            [command, "make docs-check"]
        ) is None, command

    invalid = (
        "python install -r requirements-release.txt",
        "python3 install -r requirements-release.txt",
        "pip -m pip install -r requirements-release.txt",
        "pip3 -m pip install -r requirements-release.txt",
        "pip install -r requirements-release.txt.bak",
        "python3 -m pip install -r requirements-release.txt --dry-run",
        "pip3 install --requirement=requirements-release.txt --dry-run",
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
    assert (
        packaging_gate._python_deps_issue(
            [f"{scoped_install}; make docs-check"]
        )
        is None
    )


def test_virtualenv_prefix_assignments_are_command_scoped() -> None:
    """A prefixed virtualenv applies to that command, not the next one."""
    prefix = "VIRTUAL_ENV=/workspace/.venv PATH=/workspace/.venv/bin:$PATH "
    install = f"{prefix}python3 -m pip install -r requirements-release.txt"
    docs_check = f"{prefix}make docs-check"

    assert (
        packaging_gate._python_deps_issue([f"{install}; make docs-check"])
        is not None
    )
    assert packaging_gate._python_deps_issue([f"{install}; {docs_check}"]) is None


def test_system_bin_path_does_not_create_virtualenv_mismatch() -> None:
    """Ordinary system PATH entries do not imply a virtual environment."""
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    docs_check = {
        "run": "make docs-check",
        "env": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
    }
    assert packaging_gate._python_deps_issue([install, docs_check]) is None
    assert packaging_gate._virtualenv_markers_from_path(
        "/workspace/.venv/bin:/usr/local/bin"
    ) == {"venv:/workspace/.venv"}


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
    steps = _release_gate_steps_from_yaml(
        ':\n    steps:\n      - run: pip install -r requirements-release.txt\n        env:\n          VIRTUAL_ENV: .venv\n          PATH: ".venv/bin:$PATH"\n      - run: make docs-check\n'
    )
    assert packaging_gate._python_deps_issue(steps) is not None

    shared_steps = _release_gate_steps_from_yaml(
        ':\n    env:\n      VIRTUAL_ENV: .venv\n      PATH: ".venv/bin:$PATH"\n    steps:\n      - run: pip install -r requirements-release.txt\n      - run: make docs-check\n'
    )
    assert packaging_gate._python_deps_issue(shared_steps) is None


def test_toolchain_gate_ignores_final_dead_function_at_eof() -> None:
    """A final function definition cannot satisfy provisioning before a call."""
    dead = "unused() {\n" + DRIFT + INSTALLER + COMPONENT + "}"
    assert packaging_gate._release_gate_toolchain_issue(dead) is not None
    assert packaging_gate._release_gate_toolchain_issue(dead + "\nunused\n") is None


def test_toolchain_gate_ignores_nested_dead_function_inside_live_function() -> None:
    """A live outer function does not make its uncalled nested body live."""
    script = (
        "outer() {\n"
        "  unused() {\n" + DRIFT + INSTALLER + COMPONENT + "  }\n"
        "  :\n"
        "}\n"
        "outer\n"
    )
    assert packaging_gate._release_gate_toolchain_issue(script) is not None

    called = script.replace("  :\n", "  unused\n", 1)
    assert packaging_gate._release_gate_toolchain_issue(called) is None


def test_toolchain_gate_preserves_function_closer_after_return() -> None:
    """Stripping post-return commands keeps the function's closing delimiter."""
    script = "run() { return; rustup toolchain install nightly; }\nrun\n"
    stripped = packaging_gate._strip_function_bodies(script)
    assert len(stripped) == len(script)
    assert stripped.count("}") == 1
    assert "rustup toolchain install nightly" not in stripped


def test_toolchain_gate_models_quoted_set_flags() -> None:
    """Quote removal still makes set's option flags effective shell options."""
    provisioning = "set +e\nset '-e'\nfalse\n" + DRIFT + INSTALLER + COMPONENT
    assert packaging_gate._release_gate_toolchain_issue(provisioning) is not None

    option_word = "set +e\nset '-o' errexit\nfalse\n" + DRIFT + INSTALLER + COMPONENT
    assert packaging_gate._release_gate_toolchain_issue(option_word) is not None


def test_live_command_segments_models_negated_false_chain_operand() -> None:
    """`! false` succeeds, so its following `&&` command is reachable."""
    actual = subprocess.run(
        ["bash", "-c", "! false && printf reached"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert actual.returncode == 0
    assert actual.stdout == "reached"
    assert packaging_gate._live_command_segments(
        "! false && printf reached", errexit=False
    ) == ["! false", "printf reached"]


def _assert_malformed_workflow_is_rejected(workflow_content: str) -> None:
    issue = packaging_gate._raw_toolchain_install_issue(workflow_content)
    assert issue is not None, workflow_content


def test_job_run_step_records_parses_workflow_once(monkeypatch) -> None:
    """Workflow YAML is parsed once while collecting scoped run-step data."""
    original_load = packaging_gate.yaml.safe_load
    parse_count = 0

    def counted_load(content: str) -> object:
        nonlocal parse_count
        parse_count += 1
        return original_load(content)

    monkeypatch.setattr(packaging_gate.yaml, "safe_load", counted_load)
    workflow = "jobs:\n  build:\n    steps:\n      - run: echo safe\n"

    records = packaging_gate._job_run_step_records(workflow, "build")

    assert records == [
        {
            "run": "echo safe",
            "shell": None,
            "working-directory": None,
            "env": {},
            "env_scopes": {"workflow": {}, "job": {}, "step": {}},
        }
    ]
    assert parse_count == 1


def test_raw_install_detector_rejects_non_mapping_job() -> None:
    """A scalar job entry cannot hide raw toolchain installation text."""
    _assert_malformed_workflow_is_rejected(
        "jobs:\n  malformed: 'rustup toolchain install nightly'\n"
    )


def test_raw_install_detector_rejects_non_list_steps() -> None:
    """A scalar steps field cannot silently remove a job from scanning."""
    _assert_malformed_workflow_is_rejected(
        "jobs:\n  build:\n    steps: 'rustup toolchain install nightly'\n"
    )


def test_raw_install_detector_rejects_non_mapping_step() -> None:
    """A scalar step cannot silently remove a run command from scanning."""
    _assert_malformed_workflow_is_rejected(
        "jobs:\n  build:\n    steps:\n      - 'rustup toolchain install nightly'\n"
    )


def test_raw_install_detector_rejects_non_string_run_command() -> None:
    """A non-string run field cannot bypass step command analysis."""
    _assert_malformed_workflow_is_rejected(
        "jobs:\n  build:\n    steps:\n      - run: ['rustup', 'toolchain', 'install']\n"
    )


def test_raw_install_detector_expands_command_position_variables() -> None:
    """Unquoted static command variables are checked after shell expansion."""
    raw = (
        "installer='rustup toolchain install nightly'\n"
        "$installer\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(raw)
    ) is not None

    safe = "installer='echo safe'\n$installer\n"
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(safe)
    ) is None

    quoted = 'installer="rustup toolchain install nightly"\n"$installer"\n'
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(quoted)
    ) is None


def test_raw_install_detector_scans_substitutions_without_scanning_value_suffixes():
    """Executable substitution bodies are scanned; their quoted suffix is data."""
    assert packaging_gate._raw_install_in_script(
        'VERSION="$(printf safe)"'
    ) is False
    assert packaging_gate._raw_install_in_script(
        'VERSION="$(rustup toolchain install nightly)"'
    ) is True
    assert packaging_gate._raw_install_in_script(
        'VALUE="$(printf "%s" "$(rustup toolchain install nightly)")"'
    ) is True
    assert packaging_gate._raw_install_in_script(
        'MESSAGE="\\$(rustup toolchain install nightly)"'
    ) is False
    assert packaging_gate._raw_install_in_script(
        'VALUE="$(<"${INPUT_FILE}")"'
    ) is False
    assert packaging_gate._raw_install_in_script(
        'VALUE="$(<"${INPUT_FILE}"; rustup toolchain install nightly)"'
    ) is True
    assert packaging_gate._raw_install_in_script('VALUE="$(echo') is True


def test_raw_install_detector_handles_timeout_separator_and_xargs_short_i() -> None:
    """Both wrappers leave their command operand visible to the detector."""
    commands = (
        "timeout -- 5s rustup toolchain install stable",
        "printf stable | xargs -i rustup toolchain install stable",
    )
    for command in commands:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(command)
        ) is not None, command

    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("timeout -- 5s printf safe")
    ) is None
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("printf stable | xargs -i printf safe")
    ) is None


def test_raw_install_detector_follows_for_while_until_loops() -> None:
    """Loop bodies are scanned for raw installs."""
    loop_forms = (
        "for i in a b; do rustup toolchain install stable; done",
        "while true; do rustup toolchain install stable; done",
        "until false; do rustup toolchain install stable; done",
    )
    for loop in loop_forms:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(loop)
        ) is not None, loop

    multi_command_loops = (
        "for i in a b; do echo safe; rustup toolchain install stable; done",
        "while true; do\n  echo safe\n  rustup toolchain install stable\ndone",
        "until false; do echo safe; echo safe; "
        "rustup toolchain install stable; done",
    )
    for loop in multi_command_loops:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(loop)
        ) is not None, loop

    # Safe loop bodies
    for loop in (
        "for i in a b; do echo safe; done",
        "while true; do echo safe; done",
        "until false; do echo safe; done",
        "for i in a b; do echo safe; echo safe; done",
        "while true; do\n  echo safe\n  echo safe\ndone",
    ):
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(loop)
        ) is None, loop


def test_raw_install_detector_resumes_after_bracket_tests() -> None:
    """Bracket-test delimiters cannot hide later commands across boundaries."""
    scripts = (
        '[[ -n "$X" ]] 2>/dev/null\nrustup toolchain install nightly',
        '[ -n "$X" ] 2>/dev/null; rustup toolchain install nightly',
        '[[ -n "$X" &&\n   -n "$Y" ]] 2>/dev/null\n'
        'rustup toolchain install nightly',
    )
    for script in scripts:
        parsed = subprocess.run(
            ["bash", "-n", "-c", script], check=False, capture_output=True
        )
        assert parsed.returncode == 0, parsed.stderr
        assert packaging_gate._raw_install_in_script(script), script

    unterminated = '[[ -n "$X" &&\n rustup toolchain install nightly'
    assert packaging_gate._raw_install_in_script(unterminated)

    safe = '[[ -n "$X" &&\n   -n "$Y" ]] 2>/dev/null\necho safe'
    assert not packaging_gate._raw_install_in_script(safe)


def test_raw_install_detector_treats_array_assignments_as_data() -> None:
    """Array values are data, but command substitutions inside them run."""
    safe = (
        "MISSING=()",
        'MISSING+=("${symbol}")',
        'MISSING+=("rustup toolchain install nightly")',
    )
    for script in safe:
        assert not packaging_gate._raw_install_in_script(script), script

    assert packaging_gate._raw_install_in_script(
        'MISSING+=("$(rustup toolchain install nightly)")'
    )


def test_raw_install_detector_follows_verified_retry_call_sites() -> None:
    """A forwarding retry loop is safe only with a scanned call site."""
    retry = '''retry() {
  attempts="$1"
  shift
  count=1
  while :; do
    "$@" && return 0
    if [ "$count" -ge "$attempts" ]; then
      return 1
    fi
    count=$((count + 1))
  done
}
'''
    safe = (
        retry
        + "retry 5 bash ./packaging/scripts/install-verified-rustup.sh "
        '--arch amd64 --toolchain "${RUST_TOOLCHAIN}"\n'
    )
    raw = retry + "retry 5 rustup toolchain install nightly\n"
    assert not packaging_gate._raw_install_in_script(safe)
    assert packaging_gate._raw_install_in_script(raw)


def test_raw_install_detector_follows_case_statement() -> None:
    """Case statement bodies are scanned for raw installs."""
    case_script = """case "$ARCH" in
        x86_64) rustup toolchain install stable ;;
        arm64) rustup toolchain install stable ;;
        *) echo safe ;;
    esac"""
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(case_script)
    ) is not None

    safe_case = """case "$ARCH" in
        x86_64) echo safe ;;
        arm64) echo safe ;;
        *) echo safe ;;
    esac"""
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow(safe_case)
    ) is None


def test_raw_install_detector_skips_rustup_global_options() -> None:
    """Rustup global options (-v, --verbose, etc.) are skipped before subcommand."""
    raw_forms = (
        "rustup -v toolchain install stable",
        "rustup --verbose toolchain install stable",
        "rustup -V toolchain install stable",
        "rustup --version toolchain install stable",
        "rustup -q toolchain install stable",
        "rustup --quiet toolchain install stable",
        "rustup -v -V --verbose toolchain install stable",
    )
    for form in raw_forms:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(form)
        ) is not None, form

    # Global option without toolchain install is not a hit
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("rustup -v component list")
    ) is None
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("rustup --verbose self update")
    ) is None


def test_make_docs_check_rejects_missing_option_operand() -> None:
    """A dangling `-C` cannot prove that make ran the required target."""
    assert packaging_gate._make_targets_after_options(["-C"], 0) is None
    assert packaging_gate._make_targets_after_options(
        ["-C", ".", "docs-check"], 0
    ) == ["docs-check"]


def test_make_docs_check_rejects_a_directory_redirect() -> None:
    """`-C <other>` resolves another directory's Makefile.

    GNU Make changes directory before reading makefiles, so a `-C` operand
    other than the repository root cannot prove the repository docs-check
    chain ran (`make -C "$RUNNER_TEMP/noop" docs-check` succeeds against a
    planted no-op Makefile).  The root spellings still certify.
    """
    assert packaging_gate._make_targets_after_options(
        ["-C", "tools", "docs-check"], 0
    ) is None
    assert packaging_gate._make_targets_after_options(
        ["--directory", "tools", "docs-check"], 0
    ) is None
    assert packaging_gate._make_targets_after_options(
        ["--directory=tools", "docs-check"], 0
    ) is None
    assert packaging_gate._make_targets_after_options(
        ["-C.", "docs-check"], 0
    ) == ["docs-check"]
    assert packaging_gate._make_targets_after_options(
        ["--directory=.", "docs-check"], 0
    ) == ["docs-check"]


def test_make_docs_check_models_optional_and_attached_operands() -> None:
    """-j/-l operands are optional; -O takes attached arguments only.

    GNU Make consumes a separated -j operand only when it is all digits
    and a separated -l operand only when it starts with a digit or a dot,
    so `make -j docs-check` runs the repository target and must certify.
    -O/--output-sync accept their argument attached only, and a separated
    word then stays a goal.
    """
    for words in (
        ["-j", "docs-check"],
        ["-j4", "docs-check"],
        ["-j", "4", "docs-check"],
        ["-l", "docs-check"],
        ["-l", "1.5", "docs-check"],
        ["-O", "docs-check"],
        ["-Oline", "docs-check"],
        ["--output-sync", "docs-check"],
        ["--output-sync=line", "docs-check"],
        ["--jobs", "docs-check"],
        ["--jobs=4", "docs-check"],
        ["--jobs", "4", "docs-check"],
    ):
        assert packaging_gate._make_targets_after_options(words, 0) == [
            "docs-check"
        ], words
    # A separated non-numeric operand stays a goal, not a -j argument.
    assert packaging_gate._make_targets_after_options(
        ["-j", "extra", "docs-check"], 0
    ) == ["extra", "docs-check"]


def test_make_long_option_abbreviations_fail_closed() -> None:
    """Abbreviations resolve against the running make's catalog.

    GNU Make's getopt_long accepts unique abbreviations, so ``--dry`` is
    ``--dry-run`` (recipes skipped) and ``--eva`` is ``--eval`` (the
    repository makefile never read).  An abbreviation or unknown name
    cannot be proven harmless, so it stops certification on the
    command-line path and disqualifies a MAKEFLAGS value.
    """
    for words in (
        ["--dry", "docs-check"],
        ["--eva=SHELL=/bin/true", "docs-check"],
        ["--ver", "docs-check"],
        ["--fil", "/dev/null", "docs-check"],
        ["--d", "docs-check"],
    ):
        assert packaging_gate._make_targets_after_options(words, 0) is None, words
    for value in ("--eva=SHELL=/bin/true", "--dry", "--fil /dev/null"):
        assert packaging_gate._make_flags_value_prevents_execution(value) or (
            packaging_gate._make_flags_value_uncertifiable(value)
        ), value
    # Exact harmless names still certify.
    assert packaging_gate._make_targets_after_options(
        ["--no-print-directory", "docs-check"], 0
    ) == ["docs-check"]
    assert packaging_gate._make_targets_after_options(
        ["--jobserver-auth=3,4", "docs-check"], 0
    ) == ["docs-check"]
    assert not packaging_gate._make_flags_value_prevents_execution(
        "--jobserver-auth=3,4"
    )


def test_run_step_records_resolve_the_effective_working_directory() -> None:
    """The record carries the working directory GitHub would use.

    Regression: `_job_run_step_records` carried only run/shell/env, so a
    step-level or `defaults.run.working-directory` away from the root was
    invisible to the certification scan.  The record now resolves step >
    job defaults > workflow defaults.
    """
    body = (
        "          python3 -m pip install -r requirements-release.txt\n"
        "          make docs-check\n"
    )

    def workflow(step_wd=None, job_wd=None, wf_wd=None) -> str:
        lines = ["jobs:", "  release-gate:"]
        if wf_wd:
            lines += ["    defaults:", "      run:",
                      f"        working-directory: {wf_wd}"]
        if job_wd:
            lines += ["    defaults:", "      run:",
                      f"        working-directory: {job_wd}"]
        lines += ["    steps:", "      - run: |", body.rstrip("\n")]
        if step_wd:
            lines.append(f"        working-directory: {step_wd}")
        return "\n".join(lines) + "\n"

    def records(content):
        return packaging_gate._job_run_step_records(content, "release-gate")

    assert records(workflow())[0]["working-directory"] is None
    assert records(workflow(step_wd="/tmp/noop"))[0][
        "working-directory"
    ] == "/tmp/noop"
    assert records(workflow(wf_wd="/tmp/noop"))[0][
        "working-directory"
    ] == "/tmp/noop"
    assert records(workflow(job_wd="/tmp/a"))[0][
        "working-directory"
    ] == "/tmp/a"
    # Step wins over job wins over workflow.
    assert records(workflow(step_wd=".", job_wd="/tmp/a", wf_wd="/tmp/b"))[0][
        "working-directory"
    ] == "."
    # And the resolved value disqualifies a docs-check run.
    content = workflow(step_wd="/tmp/noop")
    assert packaging_gate._python_deps_issue(records(content)) is not None
    assert packaging_gate._python_deps_issue(records(workflow())) is None


def test_make_toolchain_overrides_and_foreign_directories_fail() -> None:
    """MAKE/MAKEFILES overrides and a foreign directory defeat the check.

    ``MAKE=/usr/bin/true`` re-points every ``$(MAKE)`` recursion and a
    ``MAKEFILES`` preload precedes the repository Makefile; both were
    verified to exit 0 without running the check on GNU Make 4.3 and
    4.4.1.  A step that changes directory (or declares a foreign
    ``working-directory``) runs another Makefile, verified with a planted
    no-op chain.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for scope in ("MAKE", "MAKEFILES"):
        assert (
            packaging_gate._python_deps_issue(
                [install, {"run": "make docs-check", "env": {scope: "/usr/bin/true"}}]
            )
            is not None
        ), scope
    assert (
        packaging_gate._python_deps_issue(
            [install, "MAKE=/usr/bin/true make docs-check"]
        )
        is not None
    )
    # cwd tracking, same-step form (each GitHub run step starts at the
    # repository root, so only a cd within the SAME step redirects a later
    # make): 'cd ... && make' and the newline spelling both disqualify.
    for script in (
        'cd "$RUNNER_TEMP/noop" && make -C . docs-check',
        'cd "$RUNNER_TEMP/noop"\nmake docs-check',
    ):
        assert (
            packaging_gate._python_deps_issue([install, {"run": script}])
            is not None
        ), script
    # A bare check with no cd still certifies.
    assert packaging_gate._python_deps_issue([install, "make docs-check"]) is None
    # working-directory away from the root disqualifies.
    away_wd = [install, {"run": "make docs-check", "working-directory": "/tmp/noop"}]
    assert packaging_gate._python_deps_issue(away_wd) is not None
    # An explicit same-root working directory keeps it.
    root_wd = [install, {"run": "make docs-check", "working-directory": "."}]
    assert packaging_gate._python_deps_issue(root_wd) is None


def test_make_variable_assignments_fail_certification() -> None:
    """An assignment shapes the whole run, so it cannot certify a check.

    `make SHELL=/usr/bin/true docs-check` executes every recipe through
    ``true`` and exits 0 without doing the work (verified on 3.81 and
    4.4.1), and the same value reaches MAKEFLAGS; ``--`` does not turn an
    assignment into a target.  -p/--print-data-base also stops the recipe
    on 3.81.  All of these must fail certification.
    """
    for words in (
        ["SHELL=/usr/bin/true", "docs-check"],
        [".SHELLFLAGS=q", "docs-check"],
        ["--", "SHELL=/usr/bin/true", "docs-check"],
        ["-p", "docs-check"],
        ["--print-data-base", "docs-check"],
    ):
        assert packaging_gate._make_targets_after_options(words, 0) is None, words
    for value in ("SHELL=/usr/bin/true", ".SHELLFLAGS=q", "n=1"):
        assert packaging_gate._make_flags_value_uncertifiable(value), value
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    assert (
        packaging_gate._python_deps_issue(
            [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "SHELL=/usr/bin/true"}}]
        )
        is not None
    )
    # Plain invocations still certify.
    assert packaging_gate._make_targets_after_options(
        ["docs-check"], 0
    ) == ["docs-check"]
    assert packaging_gate._make_targets_after_options(
        ["--", "docs-check"], 0
    ) == ["docs-check"]


def test_make_flags_values_that_replace_the_makefile_fail_certification() -> None:
    """MAKEFLAGS --eval/-f values defeat a docs-check certification.

    `MAKEFLAGS='--eval=SHELL=/bin/true'` (or an environment -f pointing at
    another makefile) takes effect before the repository Makefile is read,
    so a step carrying it cannot prove the repository chain ran.  The
    check covers the environment scope and a command-local assignment.
    """
    for value in (
        "--eval=SHELL=/bin/true",
        "--eval",
        "-E SHELL=x",
        "-ESHELL=x",
        "-f /dev/null",
        "--file=/dev/null",
        "--makefile other.mk",
        "n --eval=x",
        "-o docs-check",
        "-W docs-check",
        "--what-if=docs-check",
        "--assume-new docs-check",
    ):
        assert packaging_gate._make_flags_value_uncertifiable(value), value
    for value in ("", "n", "kn", "-j4", "w --jobserver-auth=3,4"):
        assert not packaging_gate._make_flags_value_uncertifiable(value), value
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for value in ("--eval=SHELL=/bin/true", "-f /dev/null"):
        assert (
            packaging_gate._python_deps_issue(
                [install, {"run": "make docs-check", "env": {"MAKEFLAGS": value}}]
            )
            is not None
        ), value
    assert (
        packaging_gate._python_deps_issue(
            [install, "MAKEFLAGS=--eval=x make docs-check"]
        )
        is not None
    )


def test_make_docs_check_rejects_supplied_makefiles_and_eval() -> None:
    """A self-supplied makefile cannot certify the repository docs-check.

    `make -f /dev/null --eval='docs-check: ;' docs-check` exits 0 while the
    repository chain never runs, so any invocation that supplies its own
    makefile or evaled text must not count as the live docs check.
    """
    for words in (
        ["-f", "/dev/null", "docs-check"],
        ["--file", "/dev/null", "docs-check"],
        ["--file=/dev/null", "docs-check"],
        ["--makefile", "other.mk", "docs-check"],
        ["-E", "docs-check: ;", "docs-check"],
        ["--eval", "docs-check: ;", "docs-check"],
        ["--eval=x", "docs-check"],
    ):
        assert packaging_gate._make_targets_after_options(words, 0) is None, words
    # Plain invocations still certify.
    assert packaging_gate._make_targets_after_options(
        ["-C", ".", "docs-check"], 0
    ) == ["docs-check"]
    assert packaging_gate._make_targets_after_options(
        ["-j2", "docs-check"], 0
    ) == ["docs-check"]
    # `-Wn`'s n is W's argument, and W is uncertifiable: an old-file/
    # what-if operand can name the checked target and skip its recipe.
    assert packaging_gate._make_targets_after_options(
        ["-Wn", "docs-check"], 0
    ) is None


def test_dynamic_eval_parser_fails_closed_on_unterminated_quote() -> None:
    """Malformed shell quoting cannot hide whether eval receives dynamic text."""
    assert packaging_gate._eval_has_dynamic_substitution("eval 'unterminated")
    assert not packaging_gate._eval_has_dynamic_substitution("eval 'literal'")


def _release_gate_steps_from_yaml(job_yaml_fragment: str) -> list[dict]:
    workflow = f"jobs:\n  {packaging_gate.RELEASE_GATE_JOB_NAME}{job_yaml_fragment}"
    result = packaging_gate._job_run_step_records(
        workflow, packaging_gate.RELEASE_GATE_JOB_NAME
    )
    assert result is not None
    return result


def test_raw_install_gate_flags_dynamic_rustup_subcommands() -> None:
    """A rustup subcommand built at run time must fail closed.

    The gate matches the literal ``toolchain install`` pair, but a
    subcommand word carrying an unresolved ``$`` or backtick expansion can
    expand to that pair at run time. Each spelling must be treated as a raw
    install, while the literal benign subcommands stay accepted.
    """
    dynamic = (
        'rustup "$SUBCOMMAND" install nightly',
        "rustup toolchain${SUFFIX:-} install nightly",
        "rustup ${SUBCOMMAND} install nightly",
        "rustup $SUBCOMMAND install nightly",
        "rustup `echo toolchain` install nightly",
        'rustup "`echo toolchain`" install nightly',
        "rustup '`echo toolchain`' install nightly",
        "rustup toolchain`echo \"\"` install nightly",
        "rustup $(printf toolchain) install nightly",
    )
    for script in dynamic:
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(script)
        ) is not None, script

    # Control: the literal pair is flagged, and literal non-install
    # subcommands stay accepted.
    assert packaging_gate._raw_toolchain_install_issue(
        _raw_install_workflow("rustup toolchain install nightly")
    ) is not None
    for benign in (
        "rustup show",
        "rustup -v show",
        "rustup toolchain list",
        "rustup component add --toolchain nightly rustfmt",
        "rustup --verbose component add rustfmt",
    ):
        assert packaging_gate._raw_toolchain_install_issue(
            _raw_install_workflow(benign)
        ) is None, benign


def _bash_negation_parity() -> dict[str, int] | None:
    """Return ``!`` run statuses measured in GNU bash 5.2, or None.

    The host shell may be bash 3.2, which rejects a repeated ``!`` outright,
    so the measurement runs in a bash:5.2 container.  A host without a
    usable Docker daemon returns None and the caller skips the
    corroboration instead of failing on missing infrastructure.
    """
    expressions = (
        "! true",
        "! ! true",
        "! ! ! true",
        "! false",
        "! ! false",
        "! ! ! false",
    )
    script = "\n".join(
        f"{expression}; printf '%s\\n' \"$?\"" for expression in expressions
    )
    try:
        probe = subprocess.run(
            ["docker", "run", "--rm", "bash:5.2", "bash", "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        # A host without a docker binary or with a hung daemon must degrade
        # to the documented skip, not error the suite.
        return None
    try:
        probe.check_returncode()
    except subprocess.CalledProcessError:
        return None
    statuses = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
    if len(statuses) != len(expressions):
        return None
    return {
        expression: int(status)
        for expression, status in zip(expressions, statuses)
    }


def test_negation_run_toggles_per_bang_word() -> None:
    """Consecutive ``!`` words toggle: an even count is a no-op.

    Bash 5.2 evaluates each ``!`` as its own inversion, so a doubled
    negation leaves the command status untouched.  The expected values here
    are the ones the corroboration test below measures.
    """
    cases = (
        ("! true", False),
        ("! ! true", True),
        ("! ! ! true", False),
        ("! false", True),
        ("! ! false", False),
        ("! ! ! false", True),
    )
    for expression, expected in cases:
        assert packaging_gate._segment_literal(expression) is expected, expression


def test_negation_parity_matches_reference_bash() -> None:
    """The modeled parity is the one GNU bash 5.2 reports.

    Corroboration for the toggle semantics above; it needs a Docker daemon
    and skips when that infrastructure is absent.
    """
    parity = _bash_negation_parity()
    if parity is None:
        pytest.skip("docker with bash:5.2 unavailable")
    for expression, status in parity.items():
        assert packaging_gate._segment_literal(expression) is (status == 0), (
            expression,
            status,
        )


def test_negation_run_keeps_chain_reachability_in_step_with_bash() -> None:
    """Chain reachability after a ``!`` run follows the parity just pinned.

    Under the old collapsed model a doubled negation always read as one
    negation: ``! ! true`` was modeled false (dropping its ``&&`` operand
    even though bash runs it) and ``! ! false`` was modeled true (keeping
    an operand bash never reaches).
    """
    assert packaging_gate._live_command_segments(
        "! ! true && printf reached", errexit=False
    ) == ["! ! true", "printf reached"]
    assert packaging_gate._live_command_segments(
        "! ! true && printf reached", errexit=True
    ) == ["! ! true", "printf reached"]
    # An even count of ``!`` on a false command keeps status 1, so the
    # ``&&`` operand stays unreachable.
    assert packaging_gate._live_command_segments(
        "! ! false && printf unreachable", errexit=False
    ) == ["! ! false"]
    # A single negation on false succeeds, so its operand runs.
    assert packaging_gate._live_command_segments(
        "! false && printf reached", errexit=False
    ) == ["! false", "printf reached"]
    # A single negation on true fails, and dies under errexit.
    assert packaging_gate._live_command_segments(
        "! true && printf unreachable", errexit=True
    ) == ["! true"]


def test_negation_prefix_stays_transparent_after_time() -> None:
    """``! time f`` negates once, ``time f`` not at all."""
    assert packaging_gate._segment_literal("! time false") is True
    assert packaging_gate._segment_literal("time false") is False


def test_run_step_records_resolve_effective_shell_precedence() -> None:
    """Run-step records resolve the effective shell and filter by it.

    Regression for the gate-integrity gap: a step that omits ``shell`` was
    recorded as ``None`` and every consumer then assumed bash, so a job or
    workflow ``defaults.run.shell`` naming Python would hide a raw install
    inside that step from the shell-only scan.  Precedence is step, then job
    default, then workflow default; a step whose EFFECTIVE shell is not a
    shell is excluded, exactly as an explicit non-shell override is.
    """
    workflow = (
        "defaults:\n"
        "  run:\n"
        "    shell: bash {0}\n"
        "jobs:\n"
        "  gate:\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: python3 {0}\n"
        "    steps:\n"
        "      - run: print(1)\n"
        "      - run: echo hi\n"
        "        shell: bash {0}\n"
    )
    records = packaging_gate._job_run_step_records(workflow, "gate")
    assert records is not None
    # The inherited python default excludes the first step; the explicit bash
    # step stays.
    assert [record["shell"] for record in records] == ["bash {0}"]

    workflow_level_only = (
        "defaults:\n"
        "  run:\n"
        "    shell: python3 {0}\n"
        "jobs:\n"
        "  gate:\n"
        "    steps:\n"
        "      - run: print(1)\n"
    )
    records = packaging_gate._job_run_step_records(workflow_level_only, "gate")
    assert records is not None
    assert records == []

    # The all-jobs helper backs the raw-install scan, which analyzes python
    # payloads itself; it must keep the step AND its resolved shell so the
    # scan routes it to the python analyzer.
    records = packaging_gate._all_job_run_step_records(workflow_level_only)
    assert records is not None
    assert [record["shell"] for record in records] == ["python3 {0}"]


def test_job_default_python_shell_raw_install_is_flagged() -> None:
    """An inherited Python default makes the raw-install scan see Python.

    The scan routes a step to the Python payload analyzer when the resolved
    shell names Python; with the precedence recorded, a raw install inside a
    default-shelled step is caught instead of being read as bash text.
    """
    workflow = (
        "defaults:\n"
        "  run:\n"
        "    shell: python3 {0}\n"
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - run: 'import subprocess; "
        "subprocess.run([\"rustup\", \"toolchain\", \"install\", \"nightly\"])'\n"
    )
    records = packaging_gate._all_job_run_step_records(workflow)
    assert records is not None
    assert packaging_gate._workflow_shell_uses_python(records[0]["shell"])


def test_nested_interpreter_template_routes_python_to_the_python_scan() -> None:
    """A Python interpreter nested inside a quoted argument is recognized.

    Regression for the evasion: `shell: 'bash -c "python3 {0}"'` keeps
    `python3 {0}` as a single shlex word, so the placeholder was not a
    standalone token and the Python interpreter went unrecognized.  The
    step was then routed to the shell analyzer, where a raw install written
    as a Python argument list (quoted words separated by commas) matches
    nothing, and the gate accepted an unverified installer invocation.
    """
    shell = 'bash -c "python3 {0}"'
    assert packaging_gate._workflow_shell_uses_python(shell)

    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - shell: 'bash -c \"python3 {0}\"'\n"
        "        run: |\n"
        "          import subprocess\n"
        "          subprocess.run([\"rustup\", \"toolchain\", \"install\","
        " \"nightly\"])\n"
    )
    issue = packaging_gate._raw_toolchain_install_issue(workflow)
    assert issue is not None

    # Controls: the same template with a benign Python payload stays
    # accepted, and a non-Python nested command is not misrouted.
    benign = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - shell: 'bash -c \"python3 {0}\"'\n"
        "        run: 'print(\"hello\")'\n"
    )
    assert packaging_gate._raw_toolchain_install_issue(benign) is None
    assert not packaging_gate._workflow_shell_uses_python('bash -c "echo {0}"')


def test_script_chain_cannot_hide_a_raw_install_behind_a_markerless_file(
    tmp_path, monkeypatch
) -> None:
    """A two-hop script chain is followed through invocation operands.

    Regression: the marker prefilter skipped an outer script whose text did
    not mention the install markers, so `outer.sh` -> `bash inner.sh` hid an
    install in a file the gate never scanned.  A marker-less file is now
    followed through the literal script operands of its shell and Python
    invocations.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    inner = tmp_path / "inner.sh"
    inner.write_text(
        "#!/bin/bash\nrustup toolchain install nightly\n", encoding="utf-8"
    )
    outer = tmp_path / "outer.sh"
    outer.write_text("#!/bin/bash\nbash inner.sh\n", encoding="utf-8")

    assert packaging_gate._raw_install_from_shell_script_file(
        "outer.sh", 0, None
    )

    _assert_control_script_file_is_not_flagged(
        tmp_path, "inert.sh", "#!/bin/bash\necho hello\n"
    )
    _assert_control_script_file_is_not_flagged(
        tmp_path,
        "dynamic.sh",
        '#!/bin/bash\npython3 "$toolchain_file" 2>&1 <<PY\nprint(1)\nPY\n',
    )


def _assert_control_script_file_is_not_flagged(
    tmp_path, script_name, script_contents
):
    # Controls: an inert file and a dynamic operand are not treated as
    # evidence, so neither fails the gate.
    control_script = tmp_path / script_name
    control_script.write_text(script_contents, encoding="utf-8")
    assert not packaging_gate._raw_install_from_shell_script_file(
        script_name, 0, None
    )


def test_decoy_python_token_cannot_route_a_shell_template_to_python() -> None:
    """Only a command-position consumer decides the template's interpreter.

    Regression: any `python3` token before the placeholder routed the step
    to the Python analyzer, so `bash -c "echo python3 {0}; bash {0}"` sent a
    shell block to the Python scan.  A shell launcher in an earlier segment
    was masked and its script went unanalyzed.  Consumers are now read at
    command position, and templates whose consumers span both interpreters
    fail closed by applying both analyses.
    """
    decoy = 'bash -c "echo python3 {0}; bash {0}"'
    assert not packaging_gate._workflow_shell_uses_python(decoy)
    assert not packaging_gate._shell_template_conflicting_placeholder_consumers(
        decoy
    )

    # The genuine nested-python shape still routes to Python.
    assert packaging_gate._workflow_shell_uses_python('bash -c "python3 {0}"')

    # A template consuming the block under both interpreters is flagged.
    both = 'bash -c "bash {0}; python3 {0}"'
    assert packaging_gate._shell_template_conflicting_placeholder_consumers(
        both
    )


def test_decoy_template_step_with_shell_launcher_is_analyzed(
    tmp_path, monkeypatch
) -> None:
    """The decoy template's shell launcher is followed by the scan."""
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    (tmp_path / "install-toolchain.sh").write_text(
        "#!/bin/bash\nrustup toolchain install nightly\n", encoding="utf-8"
    )
    workflow = (
        "jobs:\n"
        "  release-gate:\n"
        "    steps:\n"
        "      - shell: 'bash -c \"echo python3 {0}; bash {0}\"'\n"
        "        run: 'bash install-toolchain.sh'\n"
    )

    assert packaging_gate._raw_toolchain_install_issue(workflow) is not None


def test_chain_follow_ignores_unresolvable_operands_but_scans_inline_c(
    tmp_path, monkeypatch
) -> None:
    """Unresolvable operands are not evidence; ``-c`` strings are scripts.

    Regression for the false-positive class the chain-follow introduced: a
    marker-less helper that hands ``bash -c`` an inline string, names an
    absolute/out-of-root operand, or names a not-yet-generated file must not
    fail the gate, while a true two-hop chain and a raw install inside an
    inline ``-c`` payload must still be caught.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)

    inline_benign = tmp_path / "inline.sh"
    inline_benign.write_text(
        '#!/bin/bash\nbash -c "echo hello world"\n', encoding="utf-8"
    )
    assert not packaging_gate._raw_install_from_shell_script_file(
        "inline.sh", 0, None
    )

    inline_raw = tmp_path / "inline_raw.sh"
    inline_raw.write_text(
        '#!/bin/bash\nbash -c "rustup toolchain install nightly"\n',
        encoding="utf-8",
    )
    assert packaging_gate._raw_install_from_shell_script_file(
        "inline_raw.sh", 0, None
    )

    absolute = tmp_path / "absolute.sh"
    absolute.write_text(
        "#!/bin/bash\nbash /src/packaging/scripts/verify-checksum.sh\n",
        encoding="utf-8",
    )
    assert not packaging_gate._raw_install_from_shell_script_file(
        "absolute.sh", 0, None
    )

    generated = tmp_path / "generated.sh"
    generated.write_text(
        "#!/bin/bash\npython3 generated_output.py\n", encoding="utf-8"
    )
    assert not packaging_gate._raw_install_from_shell_script_file(
        "generated.sh", 0, None
    )

    inner = tmp_path / "inner.sh"
    inner.write_text(
        "#!/bin/bash\nrustup toolchain install nightly\n", encoding="utf-8"
    )
    outer = tmp_path / "outer.sh"
    outer.write_text("#!/bin/bash\nbash inner.sh\n", encoding="utf-8")
    assert packaging_gate._raw_install_from_shell_script_file(
        "outer.sh", 0, None
    )


def test_wrapped_shell_invocations_are_followed(tmp_path, monkeypatch) -> None:
    """A marker-less file's wrapped shell invocation is still followed.

    Regression: only the first command word was recognized as an
    interpreter, so `exec bash inner.sh`, `nohup bash inner.sh` and
    `command bash inner.sh` were skipped and a two-hop raw install went
    unanalyzed.  The same bounded wrapper unwrapper the marker-present scan
    uses now resolves the head.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    inner = tmp_path / "inner.sh"
    inner.write_text(
        "#!/bin/bash\nrustup toolchain install nightly\n", encoding="utf-8"
    )
    for wrapper in (
        "exec bash inner.sh",
        "nohup bash inner.sh",
        "command bash inner.sh",
        "sudo bash inner.sh",
        "retry 3 bash inner.sh",
        "env X=1 bash inner.sh",
    ):
        outer = tmp_path / "outer.sh"
        outer.write_text(f"#!/bin/bash\n{wrapper}\n", encoding="utf-8")
        assert packaging_gate._raw_install_from_shell_script_file(
            "outer.sh", 0, None
        ), wrapper


def test_conflicting_template_runs_the_other_analyzer(
    tmp_path, monkeypatch
) -> None:
    """A dual-interpreter template fails closed via the un-routed analyzer.

    Regression: when a template consumed GitHub's placeholder under both
    Bash and Python, only the Python analyzer ran, so a shell script invoked
    by the run body went unanalyzed and a raw install passed.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    (tmp_path / "run.sh").write_text(
        "#!/bin/bash\nrustup toolchain install nightly\n", encoding="utf-8"
    )
    for template in (
        'bash -c "bash {0}; python3 {0}"',
        'bash -c "python3 {0}; bash {0}"',
    ):
        workflow = (
            "jobs:\n"
            "  release-gate:\n"
            "    steps:\n"
            f"      - shell: '{template}'\n"
            "        run: 'bash run.sh'\n"
        )
        assert packaging_gate._raw_toolchain_install_issue(workflow), template


def test_interpreter_module_flag_is_not_a_script_operand(
    tmp_path, monkeypatch
) -> None:
    """A ``-m`` module name is never followed as a script file.

    `python3 -m name` resolves a module through sys.path, so a repository
    file that happens to share the name is not executed by it.  The scan
    must therefore stop at `-m` instead of following the following words as
    paths: with a file named like the module present (and carrying a raw
    install), the step must still not be flagged.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    collision = tmp_path / "package"
    collision.write_text(
        "rustup toolchain install nightly\n", encoding="utf-8"
    )
    module_step = tmp_path / "module_step.sh"
    module_step.write_text(
        "#!/bin/bash\npython3 -m package\n", encoding="utf-8"
    )
    assert not packaging_gate._raw_install_from_shell_script_file(
        "module_step.sh", 0, None
    )


def test_dash_headed_operand_routes_to_the_shell_scan(
    tmp_path, monkeypatch
) -> None:
    """A ``dash`` invocation follows its script like the other shells.

    Regression: ``_invocation_head`` accepts dash, but the follow routing
    tuple omitted it, so a dash-headed operand fell through to the
    Python-only branch and a shell script's raw install went unanalyzed.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    _write_raw_install_script_chain(
        tmp_path, "inner.sh", "outer.sh", "#!/bin/bash\ndash inner.sh\n"
    )
    assert packaging_gate._raw_install_from_shell_script_file(
        "outer.sh", 0, None
    )

    _write_raw_install_script_chain(
        tmp_path, "deep.sh", "middle.sh", "#!/bin/bash\nbash deep.sh\n"
    )
    hop = tmp_path / "hop.sh"
    hop.write_text("#!/bin/bash\ndash middle.sh\n", encoding="utf-8")
    assert packaging_gate._raw_install_from_shell_script_file(
        "hop.sh", 0, None
    )


def _write_raw_install_script_chain(
    tmp_path, inner_script_name, outer_script_name, outer_script_contents
):
    inner_script = tmp_path / inner_script_name
    inner_script.write_text(
        "#!/bin/bash\nrustup toolchain install nightly\n", encoding="utf-8"
    )
    outer_script = tmp_path / outer_script_name
    outer_script.write_text(outer_script_contents, encoding="utf-8")


def test_runpy_script_target_is_followed_in_a_marker_less_chain(
    tmp_path, monkeypatch
) -> None:
    """``python3 -m runpy <script>`` runs a file and must be followed.

    Regression: the ``-m`` module-name guard stopped the scan before runpy's
    script operand, so a marker-less chain could hand the install to a file
    the scanner never read.  Ordinary module names stay unscanned.
    """
    monkeypatch.setattr(packaging_gate, "PROJECT_ROOT", tmp_path)
    (tmp_path / "install.py").write_text(
        "import subprocess\n"
        "subprocess.run(['rustup', 'toolchain', 'install', 'nightly'])\n",
        encoding="utf-8",
    )
    outer = tmp_path / "outer.sh"
    outer.write_text(
        "#!/bin/bash\npython3 -m runpy install.py\n", encoding="utf-8"
    )
    assert packaging_gate._raw_install_from_shell_script_file(
        "outer.sh", 0, None
    )

    # An ordinary module name is still not followed as a path.
    module_step = tmp_path / "module.sh"
    module_step.write_text(
        "#!/bin/bash\npython3 -m pip install package\n", encoding="utf-8"
    )
    assert not packaging_gate._raw_install_from_shell_script_file(
        "module.sh", 0, None
    )


def test_backgrounded_prerequisites_do_not_satisfy_the_pip_gate() -> None:
    """A backgrounded install or docs-check commands nothing.

    Regression: the live-command scan dropped the separator, so
    ``pip install ... &`` and ``make docs-check &`` counted as satisfied
    prerequisites even though the shell never waits for their exit status.
    """
    backgrounded = {
        "run": (
            "python3 -m pip install -r requirements-release.txt &\n"
            "make docs-check &\n"
        ),
        "shell": "bash",
    }
    install_at, docs_at = packaging_gate._pip_first_steps([backgrounded])
    assert install_at is None
    assert docs_at is None

    foreground = {
        "run": (
            "python3 -m pip install -r requirements-release.txt\n"
            "make docs-check\n"
        ),
        "shell": "bash",
    }
    install_at, docs_at = packaging_gate._pip_first_steps([foreground])
    assert install_at == (0, 0)
    assert docs_at == (0, 1)

    # A backgrounded install followed by a foreground docs-check: the
    # install must not count, the check must.
    mixed = {
        "run": (
            "python3 -m pip install -r requirements-release.txt &\n"
            "make docs-check\n"
        ),
        "shell": "bash",
    }
    install_at, docs_at = packaging_gate._pip_first_steps([mixed])
    assert install_at is None
    assert docs_at == (0, 0)


def test_compound_backgrounded_list_does_not_satisfy_the_pip_gate() -> None:
    """An async ``&&`` list is backgrounded as a whole.

    Regression: only the segment adjacent to a trailing ``&`` was marked,
    so ``pip install ... && printf done &`` left the install counted and a
    later ``make docs-check`` accepted an install whose completion was
    never observed.
    """
    compound = {
        "run": (
            "python3 -m pip install --requirement requirements-release.txt"
            " && printf 'done' &\nmake docs-check\n"
        ),
        "shell": "bash",
    }
    foreground = packaging_gate._foreground_live_commands(compound)
    assert all("pip install" not in segment for segment in foreground)
    assert any("docs-check" in segment for segment in foreground)

    two_step = [
        {
            "run": (
                "python3 -m pip install --requirement"
                " requirements-release.txt && printf 'done' &\n"
            ),
            "shell": "bash",
        },
        {"run": "make docs-check\n", "shell": "bash"},
    ]
    install_at, docs_at = packaging_gate._pip_first_steps(two_step)
    assert install_at is None
    assert docs_at == (1, 0)

    # Controls: a truly foreground pair still counts both.
    plain = {
        "run": (
            "python3 -m pip install -r requirements-release.txt\n"
            "make docs-check\n"
        ),
        "shell": "bash",
    }
    install_at, docs_at = packaging_gate._pip_first_steps([plain])
    assert install_at == (0, 0)
    assert docs_at == (0, 1)


def test_virtualenv_markers_read_the_foreground_command_view() -> None:
    """Marker lookup indexes the same list as the prerequisite positions.

    Regression: `_pip_first_steps` indexes `_foreground_live_commands`, but
    the marker walk read `_step_live_commands`.  A backgrounded segment
    changes the two lists' offsets, so the marker prefix could be read from
    the wrong command and a virtualenv mismatch accepted or rejected
    spuriously.
    """
    step = {
        "run": (
            "export PATH=/venv/bin:$PATH &\n"
            "python3 -m pip install -r r.txt\n"
            "make docs-check\n"
        ),
        "shell": "bash",
    }
    foreground = packaging_gate._foreground_live_commands(step)
    assert foreground == ["python3 -m pip install -r r.txt", "make docs-check"]
    install_at, docs_at = packaging_gate._pip_first_steps([step])
    assert docs_at == (0, 1)
    # The backgrounded export must NOT contribute a marker: it ran in a
    # different (asynchronous) context.  With the unfiltered list, the
    # docs-check prefix reached the export and reported its venv marker.
    markers = packaging_gate._virtualenv_markers(step, docs_at[1])
    assert markers == set(), markers

    # A foreground virtualenv on the docs-check command is observed.
    with_venv = {
        "run": (
            "python3 -m pip install -r r.txt\n"
            "PATH=/venv/bin:$PATH make docs-check\n"
        ),
        "shell": "bash",
    }
    install_at, docs_at = packaging_gate._pip_first_steps([with_venv])
    assert docs_at == (0, 1)
    assert packaging_gate._virtualenv_markers(with_venv, docs_at[1])


def test_dynamic_import_call_targets_are_resolved_or_fail_closed() -> None:
    """Dynamic-import launchers are resolved, and fail closed when unknown.

    Regression: `__import__('os').system(...)` and the `getattr`/
    `importlib.import_module` equivalents build their callable from a call
    rather than a name, so `_python_call_name` returned None and the raw
    install behind them went unflagged.  A literal module name is now
    resolved; an unresolved dynamic import fails closed.
    """
    payloads = [
        "__import__('os').system('rustup toolchain install stable')",
        (
            "import importlib; importlib.import_module('os').system("
            "'rustup toolchain install stable')"
        ),
        "getattr(__import__('os'), 'system')('rustup toolchain install stable')",
    ]
    for payload in payloads:
        assert packaging_gate._python_inline_raw_install(payload, 0, None), payload

    # A non-literal module name cannot be resolved and must fail closed.
    variable_module = (
        "m = 'os'; __import__(m).system('rustup toolchain install stable')"
    )
    assert packaging_gate._python_inline_raw_install(
        variable_module, 0, None
    )

    # Controls: ordinary calls and benign payloads are unaffected.
    assert not packaging_gate._python_inline_raw_install(
        "subprocess.run(['cargo', 'build'])", 0, None
    )
    assert not packaging_gate._python_inline_raw_install(
        "print('hello')", 0, None
    )


def test_dotted_import_resolves_by_import_semantics() -> None:
    """``__import__``'s dotted-name behavior is modeled, not assumed.

    Regression: `__import__('os.path')` returns the TOP-LEVEL ``os`` module
    (no fromlist), so `.system` is really ``os.system``; resolving it as
    ``os.path.system`` matched no launcher and the install went unflagged.
    A non-empty literal fromlist returns the submodule instead.
    """
    payloads = [
        # Bare dotted name returns the top-level package at runtime.
        "__import__('os.path').system('rustup toolchain install stable')",
        # A non-empty fromlist returns the submodule, whose launcher members
        # still match.
        (
            "__import__('os', fromlist=['system']).system("
            "'rustup toolchain install stable')"
        ),
        (
            "__import__('subprocess', fromlist=['run']).run(["
            "'rustup', 'toolchain', 'install', 'stable'])"
        ),
    ]
    for payload in payloads:
        assert packaging_gate._python_inline_raw_install(payload, 0, None), payload

    # Control: a benign dotted dynamic import stays accepted.
    assert not packaging_gate._python_inline_raw_install(
        "__import__('os.path').join('a', 'b')", 0, None
    )


def test_relative_dynamic_imports_fail_closed() -> None:
    """A relative dynamic import cannot resolve to a launcher; fail closed.

    Regression: `importlib.import_module('.helper', package='...')` was
    treated as a resolved module name, so a call through it never matched a
    launcher and the payload passed unexamined.  A relative name resolves
    only at runtime against its package, so it fails closed now; absolute
    dynamic imports keep resolving.
    """
    relative = (
        "import importlib; importlib.import_module("
        "'.helper', package='tools.release.gates').install()"
    )
    assert packaging_gate._python_inline_raw_install(relative, 0, None)

    bare_relative = (
        "import importlib; importlib.import_module('.helper').install()"
    )
    assert packaging_gate._python_inline_raw_install(bare_relative, 0, None)

    # Controls: an absolute dynamic import of a benign module stays accepted,
    # and a launcher behind one is still flagged.
    assert not packaging_gate._python_inline_raw_install(
        "import importlib; importlib.import_module('json').dumps({})",
        0,
        None,
    )
    assert packaging_gate._python_inline_raw_install(
        "import importlib; importlib.import_module('os').system("
        "'rustup toolchain install stable')",
        0,
        None,
    )


def test_relative_import_levels_fail_closed() -> None:
    """``__import__`` with a nonzero/unknown ``level`` resolves relatively.

    Regression: the resolver rejected dotted-lead names but ignored the
    ``level`` argument, so ``__import__("helper", ..., level=1).install()``
    resolved as the absolute ``helper.install`` and a locally imported
    helper could hide a raw toolchain installation.  A literal 0 stays
    absolute; every other level fails closed.
    """
    keyword_level = (
        "__import__('helper', globals(), locals(), ['install'], "
        "level=1).install()"
    )
    positional_level = (
        "__import__('helper', globals(), locals(), ['install'], 1).install()"
    )
    variable_level = (
        "level = 1\n__import__('helper', level=level).install()"
    )
    for payload in (keyword_level, positional_level, variable_level):
        assert packaging_gate._python_inline_raw_install(payload, 0, None), (
            payload
        )

    # Controls: an explicit literal-zero level keeps the absolute model.
    assert not packaging_gate._python_inline_raw_install(
        "__import__('os', level=0).getcwd()", 0, None
    )
    assert not packaging_gate._python_inline_raw_install(
        "__import__('os.path').join('a', 'b')", 0, None
    )


def test_dependency_gate_rejects_failure_masked_prerequisites() -> None:
    """A prerequisite whose failure ``||`` swallows must not satisfy the gate.

    Regression: ``pip install ... || true`` and ``make docs-check || true``
    counted as present, so the gate could certify a job in which the
    dependency install or the docs-check chain fails without notice.  The
    masked command no longer counts; chains that propagate failure (``||
    exit 1``) still satisfy it.
    """
    masked_install = [
        {"run": "python3 -m pip install -r requirements-release.txt || true"},
        {"run": "make docs-check"},
    ]
    masked_docs = [
        {"run": "python3 -m pip install -r requirements-release.txt"},
        {"run": "make docs-check || true"},
    ]
    masked_echo = [
        {
            "run": (
                "python3 -m pip install -r requirements-release.txt"
                " || echo failed"
            )
        },
        {"run": "make docs-check || echo failed"},
    ]
    for steps, needle in (
        (masked_install, "failure-masking"),
        (masked_docs, "failure-masking"),
        (masked_echo, "failure-masking"),
    ):
        issue = packaging_gate._python_deps_issue(steps)
        assert issue is not None, steps
        assert needle in issue, issue

    # A ``||`` right-hand that is not provably unsuccessful masks failure,
    # so an unknown form fails closed too.
    assert packaging_gate._shell_rhs_discards_failure("echo failed")
    assert packaging_gate._shell_rhs_discards_failure("cd /tmp")
    assert packaging_gate._shell_rhs_discards_failure("")

    # Controls: propagating and plain forms satisfy the gate.
    propagated = [
        {"run": "python3 -m pip install -r requirements-release.txt || exit 1"},
        {"run": "make docs-check || exit 1"},
    ]
    assert packaging_gate._python_deps_issue(propagated) is None
    propagating_forms = [
        {"run": "python3 -m pip install -r requirements-release.txt || false"},
        {"run": "make docs-check || exit"},
    ]
    assert packaging_gate._python_deps_issue(propagating_forms) is None
    for rhs in ("false", "exit 1", "exit", "return 2", "return"):
        assert not packaging_gate._shell_rhs_discards_failure(rhs), rhs
    plain = [
        {"run": "python3 -m pip install -r requirements-release.txt"},
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(plain) is None


def test_expanded_import_arguments_fail_closed() -> None:
    """Starred positionals and ``**`` mappings can supply ``level``.

    Regression: the level check read only the fifth explicit positional
    and a named ``level`` keyword, so ``__import__("helper", *[globals(),
    locals(), ["install"], 1])`` and ``__import__("helper", **{"level":
    1})`` resolved as absolute and a locally imported helper could hide a
    raw installation.  Both expansions now fail closed; a starred list
    whose level slot is literally 0 keeps the absolute model.
    """
    expanded = [
        "__import__('helper', *[globals(), locals(), ['install'], 1]).install()",
        "__import__('helper', **{'level': 1}).install()",
        "__import__('helper', *extra).install()",
    ]
    for payload in expanded:
        assert packaging_gate._python_inline_raw_install(payload, 0, None), (
            payload
        )
    # A starred list that provably fills level=0 stays absolute.
    assert not packaging_gate._python_inline_raw_install(
        "__import__('os', *[globals()], level=0).getcwd()", 0, None
    )


def test_chained_failure_masking_is_detected() -> None:
    """A ``||`` chain that ends in success still swallows the failure.

    Regression: only the segment immediately before the first ``||`` was
    examined, so ``pip install ... || false || true`` left the install
    unmarked even though the chain reaches ``true`` and succeeds.  The
    full chain is walked now; a chain ending ``|| false`` propagates the
    failure and still counts.
    """
    chained = [
        {
            "run": (
                "python3 -m pip install -r requirements-release.txt"
                " || false || true"
            )
        },
        {"run": "make docs-check"},
    ]
    issue = packaging_gate._python_deps_issue(chained)
    assert issue is not None
    assert "failure-masking" in issue
    propagated = [
        {"run": "python3 -m pip install -r requirements-release.txt || false"},
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(propagated) is None


def test_make_version_and_help_options_do_not_run_targets() -> None:
    """``make -v docs-check`` prints the version and never runs the target.

    Regression: only dry-run/question/touch options were recognized, so
    ``make -v docs-check`` or ``make -h docs-check`` satisfied the live
    docs-check requirement although GNU Make exits after printing the
    version/help.  Both are non-executing now.
    """
    for words in (
        ["make", "-v", "docs-check"],
        ["make", "--version", "docs-check"],
        ["make", "-h", "docs-check"],
        ["make", "--help", "docs-check"],
    ):
        assert packaging_gate._make_targets_after_options(words, 1) is None, (
            words
        )
    assert packaging_gate._make_targets_after_options(
        ["make", "docs-check"], 1
    ) == ["docs-check"]


def test_pip_dry_run_environment_is_recognized() -> None:
    """``PIP_DRY_RUN`` disqualifies an install like the ``--dry-run`` flag.

    Regression: only the literal flag was recognized, so
    ``PIP_DRY_RUN=1 python3 -m pip install ...`` (prefix) or a step-level
    ``env: {PIP_DRY_RUN: 1}`` counted as a real install even though pip
    installs nothing.  Any value except a proven-off spelling enables the
    dry run.
    """
    prefix = [
        {"run": "PIP_DRY_RUN=1 python3 -m pip install -r requirements-release.txt"},
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(prefix) is not None
    env_scoped = [
        {
            "run": "python3 -m pip install -r requirements-release.txt",
            "env": {"PIP_DRY_RUN": "1"},
        },
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(env_scoped) is not None
    # Proven-off spellings keep the install valid.
    disabled = [
        {
            "run": "python3 -m pip install -r requirements-release.txt",
            "env": {"PIP_DRY_RUN": "0"},
        },
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(disabled) is None


def test_exit_status_is_normalized_to_eight_bits() -> None:
    """``exit 256`` exits successfully, so it masks a prerequisite failure.

    Regression: a literal nonzero status was assumed to propagate failure,
    but the shell truncates the exit status to its low 8 bits, so
    ``pip install ... || exit 256`` reaches a successful exit and the
    install is not proven.  Only statuses that stay nonzero after
    truncation propagate.
    """
    masking = [
        {"run": "python3 -m pip install -r requirements-release.txt || exit 256"},
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(masking) is not None
    # 511 % 256 == 255: still a failure, so it propagates.
    propagating = [
        {"run": "python3 -m pip install -r requirements-release.txt || exit 511"},
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(propagating) is None


def test_followed_propagating_chain_still_masks_without_errexit() -> None:
    """Without errexit a later command replaces the chain's failed status.

    Regression: the walk stopped at the chain, so ``pip install ... ||
    false; true`` left the install unmarked; without ``-e`` the shell
    continues and the step exits successfully.  With errexit the shell
    aborts on the failed chain, so the later command cannot swallow it.
    """
    script = "python3 -m pip install -r requirements-release.txt || false; true"
    steps = [
        {"run": script, "shell": "bash {0}"},  # no implicit -e
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(steps) is not None
    # The chain propagates the failure, but the shell continues past it and
    # the trailing `true` decides the step's status: without errexit the
    # chain is masked.
    masked = packaging_gate._failure_masked_command_segments(
        script, errexit=False
    )
    assert script.split("||")[0].strip() in masked
    # Under an errexit shell the failed chain aborts the script, so the
    # trailing command cannot replace its status: not masked.
    masked = packaging_gate._failure_masked_command_segments(
        script, errexit=True
    )
    assert script.split("||")[0].strip() not in masked


def test_pip_dry_run_accepts_short_false_spellings() -> None:
    """``PIP_DRY_RUN=n`` and ``=f`` are false spellings pip accepts.

    Regression: the false-value set omitted ``n`` and ``f``, so a real
    install using either value was treated as a dry run and the workflow
    validator could reject a valid job.
    """
    for value in ("n", "f", "N", "F"):
        assert not packaging_gate._pip_dry_run_value_active(value), value
    steps = [
        {
            "run": "python3 -m pip install -r requirements-release.txt",
            "env": {"PIP_DRY_RUN": "n"},
        },
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(steps) is None


def test_masking_walks_the_connected_and_or_list() -> None:
    """``&&`` shares the list with ``||``, so the terminator masks members.

    Regression: only segments with a DIRECTLY following ``||`` were
    examined, so ``pip install ... && echo ok || true`` masked ``echo ok``
    while the pip command counted.  In one connected list a failing pip
    short-circuits to ``true`` exactly as in ``pip || true``; every member
    is masked now, and a purely propagating list still counts.
    """
    combined = [
        {
            "run": (
                "python3 -m pip install -r requirements-release.txt"
                " && echo ok || true"
            )
        },
        {"run": "make docs-check"},
    ]
    issue = packaging_gate._python_deps_issue(combined)
    assert issue is not None
    assert "failure-masking" in issue

    # A propagating list keeps every member countable.
    propagating = [
        {
            "run": (
                "python3 -m pip install -r requirements-release.txt"
                " && echo ok || exit 1"
            )
        },
        {"run": "make docs-check"},
    ]
    assert packaging_gate._python_deps_issue(propagating) is None


def test_cleanup_waits_for_the_owned_namespace_deletion() -> None:
    """Both smokes wait (bounded) for their owned namespace before returning.

    Regression: the cleanup branch used ``--wait=false``, so a subsequent
    run could acquire the lock while the namespace was still terminating.
    The deletion is now a bounded wait (``--timeout=120s``), still
    best-effort, and in the e2e smoke it completes before the lock is
    released.
    """
    root = pathlib.Path(__file__).resolve().parents[4]
    gate4 = (root / "tools/release/gates/gate4_local_k8s_smoke.sh").read_text(
        encoding="utf-8"
    )
    cleanup = gate4.split("cleanup_owned_helm_resources() {", 1)[1].split(
        "\n}", 1
    )[0]
    assert "--wait=true --timeout=120s" in cleanup
    assert "--wait=false" not in cleanup

    e2e = (root / "tools/e2e/verify_helm_cluster_smoke_e2e.sh").read_text(
        encoding="utf-8"
    )
    e2e_cleanup = e2e.split("cleanup() {", 1)[1].split("\n}", 1)[0]
    delete = e2e_cleanup.split("delete namespace", 1)[1].split("fi", 1)[0]
    assert "--wait=true --timeout=120s" in delete
    assert e2e_cleanup.index("delete namespace") < e2e_cleanup.index(
        "release_cluster_lock"
    )


def test_return_status_is_normalized_to_eight_bits() -> None:
    """``return 257`` fails (status 1), so later commands are unreachable.

    Regression: function-body return classification treated values above
    255 as unknown (potentially successful), so a function ending in
    ``return 257`` looked ambiguous and commands after a call to it
    counted as reachable provisioning evidence, though bash truncates the
    status to its low 8 bits and the function fails.  ``return 256``
    exits 0 and stays in the zero family.
    """
    assert packaging_gate._return_kind("return 257") == "nonzero"
    assert packaging_gate._return_kind("return 511") == "nonzero"
    assert packaging_gate._return_kind("return 256") == "zero"
    assert packaging_gate._return_kind("return 0") == "zero"
    assert packaging_gate._return_kind("return $rc") == "unknown"

    script = (
        "set -e\n"
        "fail() { return 257; }\n"
        "fail\n"
        "python3 -m pip install -r requirements-release.txt\n"
        "make docs-check\n"
    )
    issue = packaging_gate._python_deps_issue([{"run": script}])
    assert issue is not None, (
        "commands after a failing return must not count as reachable"
    )


def _heredoc_body_workflow(body: str) -> str:
    return f"""jobs:
  release-gate:
    steps:
      - run: |
{textwrap.indent(body, '          ')}
"""


def test_unquoted_heredoc_substitutions_are_scanned() -> None:
    """An unquoted heredoc expands its body, so substitutions execute.

    Regression: ``cat <<EOF`` with a ``$(rustup toolchain install ...)``
    line hid the installer -- the body was data for command scanning and
    the substitution scan ran only on the stripped script.  Unquoted
    heredoc bodies now have their command substitutions scanned; a quoted
    delimiter suppresses expansion and its body stays literal.
    """
    unquoted = _heredoc_body_workflow(
        "cat <<EOF\n$(rustup toolchain install nightly)\nEOF"
    )
    assert packaging_gate._raw_toolchain_install_issue(unquoted) is not None

    quoted = _heredoc_body_workflow(
        "cat <<'EOF'\n$(rustup toolchain install nightly)\nEOF"
    )
    assert packaging_gate._raw_toolchain_install_issue(quoted) is None

    benign = _heredoc_body_workflow(
        "cat <<EOF\ntext $(date) only\nEOF"
    )
    assert packaging_gate._raw_toolchain_install_issue(benign) is None


def test_shell_template_commands_are_scanned() -> None:
    """A custom shell template can execute its own commands.

    Regression: ``shell: "bash -c 'rustup toolchain install nightly' {0}"``
    runs the installer from the template while the scanned run block
    stays harmless.  The template's ``-c`` payloads are scanned now; a
    benign template (and the bare ``bash {0}`` form) stays accepted.
    """
    yaml = """jobs:
  release-gate:
    steps:
      - shell: "bash -c 'rustup toolchain install nightly' {0}"
        run: "echo harmless"
"""
    assert packaging_gate._raw_toolchain_install_issue(yaml) is not None
    benign = """jobs:
  release-gate:
    steps:
      - shell: "bash -c 'echo hi' {0}"
        run: "echo harmless"
"""
    assert packaging_gate._raw_toolchain_install_issue(benign) is None


def test_masking_does_not_overmask_commands_after_a_masking_branch() -> None:
    """A command after ``false || true &&`` still runs and reports itself.

    Regression: the list-level walk masked EVERY member once any ``||``
    discarded failure, so ``false || true && pip install ...`` excluded
    the install even though it runs and its failure is the list's final
    status.  Each member is simulated individually now.
    """
    script = (
        "false || true && "
        "python3 -m pip install -r requirements-release.txt"
    )
    steps = [{"run": script, "shell": "bash"}, {"run": "make docs-check"}]
    assert packaging_gate._python_deps_issue(steps) is None
    masked = packaging_gate._failure_masked_command_segments(
        script, errexit=True
    )
    assert "false" in masked
    assert not any(
        segment.startswith("python3 -m pip") for segment in masked
    )


def test_shell_template_option_clusters_and_placeholders_are_scanned() -> None:
    """Option clusters and ``{0}``-carrying payloads must not hide installs.

    Regression: the template scanner recognized only a standalone ``-c``
    token, so ``bash -ec '...' 0 {0}`` was skipped whole; and a payload
    containing ``{0}`` was skipped, so ``bash -c 'bash {0}; rustup ...'``
    hid an install behind the placeholder.  Clusters are parsed, and the
    placeholder is substituted with an inert sentinel (the generated
    script is the run block, which the caller scans separately).
    """
    cluster = """jobs:
  release-gate:
    steps:
      - shell: "bash -ec 'rustup toolchain install nightly' 0 {0}"
        run: "echo harmless"
"""
    assert packaging_gate._raw_toolchain_install_issue(cluster) is not None

    placeholder_payload = """jobs:
  release-gate:
    steps:
      - shell: "bash -c 'bash {0}; rustup toolchain install nightly' 0"
        run: "echo harmless"
"""
    assert packaging_gate._raw_toolchain_install_issue(
        placeholder_payload
    ) is not None

    # Controls: benign clusters, placeholder-only payloads, and bare
    # interpreter forms stay accepted.
    benign_cluster = """jobs:
  release-gate:
    steps:
      - shell: "bash -ec 'echo hi' 0 {0}"
        run: "echo harmless"
"""
    assert packaging_gate._raw_toolchain_install_issue(benign_cluster) is None
    benign_placeholder = """jobs:
  release-gate:
    steps:
      - shell: "bash -c 'bash {0}; echo ok' 0"
        run: "echo harmless"
"""
    assert packaging_gate._raw_toolchain_install_issue(
        benign_placeholder
    ) is None
    python_template = """jobs:
  release-gate:
    steps:
      - shell: 'bash -c "python3 {0}"'
        run: 'print("hello")'
"""
    assert packaging_gate._raw_toolchain_install_issue(python_template) is None


def test_rustup_toolchain_override_is_skipped_before_the_subcommand() -> None:
    """`+toolchain` shifts the subcommand; both orders must be flagged.

    Regression: the skip loop only stepped over `-` flags, so
    `rustup +stable toolchain install nightly` left `+stable` as the
    subcommand head and neither the literal pair nor the unresolved-word
    check matched -- a raw install passed the gate.
    """
    for command in (
        "rustup +stable toolchain install nightly",
        "rustup toolchain install nightly",
        "rustup +stable +nightly toolchain install nightly",
        "rustup --verbose +stable toolchain install nightly",
    ):
        assert packaging_gate._raw_install_in_segment(command), command

    # A benign subcommand behind the override stays accepted.
    assert not packaging_gate._raw_install_in_segment("rustup +stable show")
    assert not packaging_gate._raw_install_in_segment(
        "rustup +stable toolchain list"
    )


def test_masked_and_backgrounded_sets_use_the_live_view_text() -> None:
    """A `then`/`do`/`else` carrier must compare as the command it runs.

    Regression: the masked set stored the raw segment text
    (`then pip install ...`) while the live-command view carries the
    command alone, so a masked install inside a provably-running branch
    never matched and satisfied the gate.
    """
    script = (
        "if true; then "
        "python3 -m pip install -r requirements-release.txt || true; fi\n"
        "make docs-check"
    )
    steps = [{"run": script}]
    assert packaging_gate._python_deps_issue(steps) is not None

    masked = packaging_gate._failure_masked_command_segments(script)
    assert (
        "python3 -m pip install -r requirements-release.txt" in masked
    ), masked
    assert not any(segment.startswith("then ") for segment in masked), masked

    backgrounded = packaging_gate._backgrounded_command_segments(
        "if true; then python3 -m pip install -r requirements-release.txt & fi\n"
        "make docs-check"
    )
    assert not any(
        segment.startswith("then ") for segment in backgrounded
    ), backgrounded


def test_make_environment_and_cli_masking_modes_disqualify_docs_check() -> None:
    """-i/-silent hide a failing docs check on both surfaces.

    `make -i f` exits 0 with the failure "ignored" (verified), and the
    short spelling `-silent` is read by make as `-s -i -l ent`, so a
    cluster can carry the masking flag.  A step whose environment or
    command line carries one cannot prove the check succeeded.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    for value in ("-i", "i", "silent", "-si", "-is", "--ignore-errors"):
        assert (
            packaging_gate._python_deps_issue(
                [install, {"run": "make docs-check", "env": {"MAKEFLAGS": value}}]
            )
            is not None
        ), value
    for command in (
        "make -i docs-check",
        "make --ignore-errors docs-check",
        "make -silent docs-check",
        "MAKEFLAGS=-i make docs-check",
    ):
        assert (
            packaging_gate._python_deps_issue([install, command]) is not None
        ), command
    # A plain check still certifies.
    assert packaging_gate._python_deps_issue([install, "make docs-check"]) is None


def test_make_environment_nonexecuting_modes_disqualify_docs_check() -> None:
    """A non-executing make mode in the environment stops the docs check.

    Regression: the docs-check detector read only the command words, so a
    step (or job/workflow) environment of ``MAKEFLAGS=-n`` still counted
    as a live docs check although GNU Make merely prints recipes.  GNU Make
    also accepts the dash-less first-word spellings (``n``, ``kn``) and
    the long forms (``--dry-run``), so those disqualify the step too.  A
    command-local assignment is covered as well.
    """
    install = {"run": "python3 -m pip install -r requirements-release.txt"}
    rejected = [
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "-n"}}],
        [install, {"run": "make docs-check", "env": {"GNUMAKEFLAGS": "-q"}}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "-t"}}],
        [install, {"run": "MAKEFLAGS=-n make docs-check"}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "n"}}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "kn"}}],
        [install, {"run": "make docs-check", "env": {"GNUMAKEFLAGS": "n"}}],
        [
            install,
            {"run": "make docs-check", "env": {"MAKEFLAGS": "--dry-run"}},
        ],
        [
            install,
            {"run": "make docs-check", "env": {"GNUMAKEFLAGS": "--dry-run"}},
        ],
        [
            install,
            {"run": "make docs-check", "env": {"MAKEFLAGS": "--question"}},
        ],
        [
            install,
            {"run": "make docs-check", "env": {"MAKEFLAGS": "--touch"}},
        ],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "silent"}}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "-i"}}],
    ]
    for steps in rejected:
        assert packaging_gate._python_deps_issue(steps) is not None, steps

    accepted = [
        [install, {"run": "make docs-check"}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": ""}}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "s"}}],
        [install, {"run": "make docs-check", "env": {"MAKEFLAGS": "k"}}],
        [
            install,
            {"run": "make docs-check", "env": {"MAKEFLAGS": "--trace"}},
        ],
        [
            install,
            {
                "run": "make docs-check",
                "env": {"MAKEFLAGS": "w --jobserver-auth=3,4"},
            },
        ],
    ]
    for steps in accepted:
        assert packaging_gate._python_deps_issue(steps) is None, steps


def test_make_flags_cluster_scan_stops_at_argument_options() -> None:
    """Letters after an argument-taking option are that argument.

    GNU Make's switch table gives ``W``/``f``/``C`` (and friends) an
    argument, so ``-Wn`` asks for file ``n`` and does not select
    just-print.  A model that scanned past the argument would reject a
    step whose make invocation actually runs.
    """
    assert packaging_gate._make_flags_value_prevents_execution("n") is True
    assert packaging_gate._make_flags_value_prevents_execution("kn") is True
    assert packaging_gate._make_flags_value_prevents_execution("silent") is False
    assert packaging_gate._make_flags_value_prevents_execution("trace") is True
    assert packaging_gate._make_flags_value_prevents_execution("-Wn") is False
    assert packaging_gate._make_flags_value_prevents_execution("-fn") is False
    assert (
        packaging_gate._make_flags_value_prevents_execution("--dry-run") is True
    )
    assert (
        packaging_gate._make_flags_value_prevents_execution("--dry-run=x")
        is True
    )
    assert (
        packaging_gate._make_flags_value_prevents_execution("VAR=n") is False
    )
    assert packaging_gate._make_flags_value_prevents_execution("n=1") is False


def test_command_line_cluster_scan_matches_make_switch_semantics() -> None:
    """Both paths scan short-option clusters with one shared model.

    Regression: the command-line path used a plain substring search, so
    ``-Wn``/``-fn`` (where the argument-taking ``W``/``f`` consumes the
    letter ``n``) were classified as non-executing although make actually
    runs the recipe (``-Wn``) or fails before it (``-fn``).  The shared
    scanner stops at argument-taking letters, so both paths agree.
    """
    for word in ("-Wn", "-fn", "-In", "-Cn", "-En"):
        assert packaging_gate._make_option_prevents_execution(word) is False, word
        # And the MAKEFLAGS path reads the same word the same way.
        assert (
            packaging_gate._make_flags_value_prevents_execution(word) is False
        ), word
    for word in ("-kn", "-nv", "-vh", "-n", "-q", "-t", "-v", "-h"):
        assert packaging_gate._make_option_prevents_execution(word) is True, word
    # A dash-less word is not an option on the command line.
    assert packaging_gate._make_option_prevents_execution("trace") is False


def test_environment_cluster_letters_stay_within_verified_make_behavior() -> None:
    """The MAKEFLAGS letter set excludes version-dependent spellings.

    ``h``/``--help`` are ignored by make 3.81 and 4.3 when they arrive
    through MAKEFLAGS (the recipe runs), so treating them as
    non-executing would wrongly disqualify a live docs check on those
    versions.  ``v``/``--version`` stop every verified version and stay.
    """
    for value in ("v", "-v", "--version"):
        assert packaging_gate._make_flags_value_prevents_execution(value) is True, value
    for value in ("h", "-h", "--help"):
        assert packaging_gate._make_flags_value_prevents_execution(value) is False, value
    assert packaging_gate._make_flags_value_prevents_execution("n") is True
    assert packaging_gate._make_flags_value_prevents_execution("kn") is True


def _heredoc_workflow(body: str) -> str:
    return (
        "jobs:\n  release-gate:\n    steps:\n      - run: |\n"
        + "".join(f"          {line}\n" for line in body.splitlines())
    )


def test_partially_quoted_heredoc_delimiters_suppress_expansion() -> None:
    """Quoting any part of a delimiter makes the body literal.

    Regression: only a leading quote was recognized, so `<<E'OF'` (whose
    body bash does NOT expand) still had its `$(...)` text scanned and
    could block a workflow that is actually safe.
    """
    expanding = _heredoc_workflow(
        "cat <<EOF\n$(rustup toolchain install nightly)\nEOF"
    )
    assert packaging_gate._raw_toolchain_install_issue(expanding) is not None

    literal = [
        "cat <<E'OF'\n$(rustup toolchain install nightly)\nEOF",
        'cat <<"E"OF\n$(rustup toolchain install nightly)\nEOF',
        "cat <<E\\OF\n$(rustup toolchain install nightly)\nEOF",
    ]
    for body in literal:
        assert (
            packaging_gate._raw_toolchain_install_issue(
                _heredoc_workflow(body)
            )
            is None
        ), body
