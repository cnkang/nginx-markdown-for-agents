"""Tests for the comment-swallowing detector."""

from __future__ import annotations

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


def test_a_backslash_escaping_a_space_does_not_continue() -> None:
    """The backslash escapes the space, so the next line stands on its own."""
    script = "echo 'a'\\ \n  # a note\n  more\n"

    assert detect(script) == []
