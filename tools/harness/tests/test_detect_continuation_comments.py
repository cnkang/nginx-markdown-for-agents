"""Tests for the comment-swallowing detector."""

from __future__ import annotations

import pytest

from tools.harness.detect_continuation_comments import scan_shell_text


def test_comment_after_a_continuation_is_reported() -> None:
    """The arguments after such a comment never reach the command."""
    script = "docker run --rm \\\n  # a note\n  rust:1.98.1 sh /src/build.sh\n"

    assert detect(script) == [2]


def test_a_comment_that_ends_the_command_is_allowed() -> None:
    """Nothing is lost when the comment closes the command."""
    script = "echo done \\\n  # trailing note\n"

    assert detect(script) == []


def test_a_comment_line_with_a_trailing_backslash_is_not_a_continuation() -> None:
    """The interpreter stops at the `#`, so the backslash stays in the text."""
    script = "#   docker run --rm \\\n#     rust:1.98.1 sh /src/build.sh\nset -eux\n"

    assert detect(script) == []


def test_an_escaped_backslash_does_not_continue() -> None:
    """An even number of trailing backslashes is an escaped backslash."""
    script = "printf 'a'\\\\\n  # a note\n  more\n"

    assert detect(script) == []


def probe(text: str) -> int:
    """A local helper so the fixtures above read as scripts."""
    return 0


def detect(script: str) -> list[int]:
    """Return the reported lines for a script."""
    _ = probe
    return scan_shell_text(script)


def test_a_swallowed_argument_in_a_workflow_run_block_is_reported(tmp_path) -> None:
    """The same mistake inside a `run:` block loses the image and the script."""
    from tools.harness.detect_continuation_comments import collect_errors

    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "build.yml").write_text(
        "jobs:\n  build:\n    steps:\n      - run: |\n"
        "          docker run --rm \\\n"
        "            # a note added here\n"
        "            rust:1.98.1 sh /src/build.sh\n",
        encoding="utf-8",
    )

    errors = collect_errors(tmp_path)

    assert any("build.yml" in error for error in errors), errors


def test_an_unrelated_command_below_is_not_swallowed() -> None:
    """The command ends at the next line that starts in column one."""
    script = "echo a \\\n  # a note\necho unrelated\n"

    assert detect(script) == []


def test_a_sibling_command_inside_a_function_is_not_swallowed() -> None:
    """The boundary is the continued line's indent, not column one."""
    script = "build() {\n  docker run --rm \\\n    # a note\n  echo unrelated\n}\n"

    assert detect(script) == []


def test_a_deeper_continuation_inside_a_function_is_reported() -> None:
    """A deeper line belongs to the command, so the comment does swallow it."""
    script = "build() {\n  docker run --rm \\\n    # a note\n    rust:1.97.0 sh /src/b.sh\n}\n"

    assert detect(script) == [3]


def test_a_comment_inside_a_multi_line_continuation_is_reported() -> None:
    """The boundary is where the whole command starts, not its last line."""
    script = "docker run --rm \\\n  -v /a:/a \\\n  # a note\n  rust:1.97.0 sh /src/b.sh\n"

    assert detect(script) == [3]


def test_a_sibling_command_after_a_multi_line_continuation_is_not() -> None:
    """A line that starts a new command is not part of what was swallowed."""
    script = "docker run --rm \\\n  -v /a:/a \\\n  # a note\necho unrelated\n"

    assert detect(script) == []


def test_a_same_indent_option_after_comment_is_reported() -> None:
    """An option at the command indentation is still a lost argument."""
    script = "docker run --rm \\\n  # a note\n--network host\n"

    assert detect(script) == [2]


def test_a_same_indent_image_argument_after_comment_is_reported() -> None:
    """An image or path token at the command indentation is also an argument."""
    script = "docker run --rm \\\n  # a note\nrust:1.98.1\n"

    assert detect(script) == [2]


def test_a_bare_same_indent_value_after_comment_is_reported() -> None:
    """A bare positional value at the command indentation is also lost."""
    script = "docker run --rm \\\n  # a note\nalpine\n"

    assert detect(script) == [2]


def test_a_bare_sibling_command_after_comment_is_not_reported() -> None:
    """A no-argument command at command indent runs as its own command."""
    script = "build() {\n  docker run --rm \\\n    # a note\n  true\n}\n"

    assert detect(script) == []


@pytest.mark.parametrize(
    "token",
    [
        "true",
        "false",
        ":",
        "echo",
        "exit",
        "return",
        "break",
        "continue",
        "fi",
        "done",
        "esac",
        "}",
        "then",
        "else",
        "elif",
        "do",
        ";;",
    ],
)
def test_single_token_keywords_and_commands_are_not_arguments(token: str) -> None:
    """Keywords and no-argument commands lose nothing when the comment ends."""
    script = "echo a \\\n  # a note\n" + token + "\n"

    assert detect(script) == []


def test_a_backslash_escaping_a_space_does_not_continue() -> None:
    """The backslash escapes the space, so the next line stands on its own."""
    script = "echo 'a'\\ \n  # a note\n  more\n"

    assert detect(script) == []


def test_missing_scan_root_is_an_error(tmp_path) -> None:
    """A missing root must not look like a clean scan."""
    from tools.harness.detect_continuation_comments import collect_errors

    errors = collect_errors(tmp_path / "missing")

    assert errors
    assert "missing or not a directory" in errors[0]


def test_malformed_workflow_shape_is_an_error(tmp_path) -> None:
    """A malformed workflow cannot certify that no run block is present."""
    from tools.harness.detect_continuation_comments import collect_errors

    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "broken.yml").write_text("jobs:\n  build:\n    steps: nope\n", encoding="utf-8")

    errors = collect_errors(tmp_path)

    assert any("broken.yml" in error and "cannot be read" in error for error in errors)


VIOLATION = "docker run --rm \\\n  # note\n  rust:1.98.1 sh /src/build.sh\n"


def test_an_untracked_shell_script_is_not_scanned(tmp_path) -> None:
    """Only tracked shell scripts join the blocking scan."""
    import subprocess

    from tools.harness.detect_continuation_comments import collect_errors

    (tmp_path / ".github/workflows").mkdir(parents=True)
    tracked = tmp_path / "tools" / "tracked.sh"
    tracked.parent.mkdir(parents=True)
    tracked.write_text(VIOLATION, encoding="utf-8")
    (tmp_path / "tools" / "untracked.sh").write_text(VIOLATION, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "tools/tracked.sh"], cwd=tmp_path, check=True)

    errors = collect_errors(tmp_path)

    assert any("tracked.sh" in error for error in errors)
    assert not any("untracked.sh" in error for error in errors)


def test_a_non_git_fixture_root_still_discovers_scripts(tmp_path) -> None:
    """Fixture mode keeps glob discovery so tests need no repository."""
    from tools.harness.detect_continuation_comments import collect_errors

    (tmp_path / ".github/workflows").mkdir(parents=True)
    script = tmp_path / "tools" / "fixture.sh"
    script.parent.mkdir(parents=True)
    script.write_text(VIOLATION, encoding="utf-8")

    errors = collect_errors(tmp_path)

    assert any("fixture.sh" in error for error in errors)


def test_a_missing_workflow_directory_is_an_error(tmp_path) -> None:
    """A root without .github/workflows cannot prove its run blocks."""
    from tools.harness.detect_continuation_comments import collect_errors

    errors = collect_errors(tmp_path)

    assert any("workflow directory is missing" in error for error in errors)


def test_a_clean_cli_root_reports_success(tmp_path, capsys) -> None:
    """A clean root reached through the CLI exits zero with an OK line."""
    from tools.harness.detect_continuation_comments import main

    (tmp_path / ".github/workflows").mkdir(parents=True)

    assert main(["detect_continuation_comments.py", str(tmp_path)]) == 0
    assert "OK:" in capsys.readouterr().out


def test_the_cli_root_is_validated_and_scanned(tmp_path, capsys) -> None:
    """The CLI root is resolved to a real path before the scan starts."""
    from tools.harness.detect_continuation_comments import main

    (tmp_path / ".github/workflows").mkdir(parents=True)
    script = tmp_path / "tools" / "cli.sh"
    script.parent.mkdir(parents=True)
    script.write_text(VIOLATION, encoding="utf-8")

    assert main(["detect_continuation_comments.py", str(tmp_path)]) == 1
    assert "cli.sh" in capsys.readouterr().out


def test_an_option_shaped_root_is_still_scanned(tmp_path) -> None:
    """A root whose basename starts with a dash is a directory, not an option."""
    from tools.harness.detect_continuation_comments import main

    root = tmp_path / "-dash-root"
    (root / ".github/workflows").mkdir(parents=True)
    script = root / "tools" / "dash.sh"
    script.parent.mkdir(parents=True)
    script.write_text(VIOLATION, encoding="utf-8")

    assert main(["detect_continuation_comments.py", str(root)]) == 1


def test_a_traversal_root_is_rejected(tmp_path, capsys) -> None:
    """A `..` component in the CLI root fails closed before the scan."""
    from tools.harness.detect_continuation_comments import main

    nested = tmp_path / "nested"
    nested.mkdir()

    assert (
        main(["detect_continuation_comments.py", str(nested / ".." / "nested")])
        == 1
    )
    assert "Refusing path" in capsys.readouterr().err
