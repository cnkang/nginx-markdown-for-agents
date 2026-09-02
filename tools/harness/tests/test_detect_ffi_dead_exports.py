"""Regression tests for the dead FFI export detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.harness import detect_ffi_dead_exports as detector


def test_header_fallback_finds_multiline_declarations(tmp_path: Path) -> None:
    """Fallback parsing retains declarations missed by the typed pattern."""
    header = tmp_path / "markdown_converter.h"
    header.write_text(
        "typedef struct markdown_handle markdown_handle;\n"
        "markdown_handle * markdown_custom_export(\n"
        "    markdown_handle *handle\n"
        ");\n",
        encoding="utf-8",
    )

    assert "markdown_custom_export" in detector.parse_header_exports(header)


def test_declaration_pattern_does_not_duplicate_uint8_type_alternative() -> None:
    """Keep integer type alternatives non-overlapping for safe matching."""
    assert "uint8_t" not in detector.DECLARATION_LINE_RE.pattern
    assert "uint8_t" not in detector.C_PROTOTYPE_RE.pattern


def test_guard_stack_handles_nested_ifdef_and_endif() -> None:
    """Guard tracking must pop only after the conditional body."""
    guards: list[str] = []

    detector._update_guard_stack("#ifdef OUTER", guards)
    detector._update_guard_stack("#ifdef INNER", guards)
    detector._update_guard_stack("#endif", guards)

    assert guards == ["OUTER"]


def test_guard_stack_preserves_ifndef_else_and_elif_polarity() -> None:
    """Conditional branches must remain distinguishable in callsite records."""
    guards: list[str] = []

    detector._update_guard_stack("#ifndef FEATURE", guards)
    assert guards == ["!FEATURE"]
    detector._update_guard_stack("#else", guards)
    assert guards == ["FEATURE"]
    detector._update_guard_stack("#elif defined(OTHER)", guards)
    assert guards == ["OTHER"]


def test_guard_stack_accepts_whitespace_in_directives() -> None:
    """Guard parsing preserves names with spaced preprocessor expressions."""
    guards: list[str] = []

    detector._update_guard_stack(" \t#if\tdefined ( FEATURE )  ", guards)

    assert guards == ["FEATURE"]


def test_guard_stack_ignores_non_directive_and_incomplete_lines() -> None:
    """Only complete conditional directives change the guard stack."""
    guards: list[str] = []

    detector._update_guard_stack("#ifdef FEATURE", guards)
    detector._update_guard_stack("text #ifdef FEATURE", guards)
    detector._update_guard_stack("#if", guards)
    detector._update_guard_stack("#ifdefined FEATURE", guards)
    detector._update_guard_stack("#endif/* comment */", guards)

    assert guards == []


@pytest.mark.parametrize(
    "scanner",
    (detector.scan_c_callsites, detector.scan_test_references),
)
def test_scanners_reject_parent_paths(scanner) -> None:
    """Directory inputs must be rejected before recursive traversal."""
    with pytest.raises(ValueError, match="Refusing path"):
        scanner(Path("../outside"))


def test_block_comment_state_tracks_across_lines() -> None:
    """Multi-line comment bodies are not scanned for callsites."""
    code, state, _, _ = detector._mask_inline_comments(
        "/* documentation example: markdown_convert(...)", False
    )
    assert state is True
    assert "markdown_convert" not in code
    code, state, _, _ = detector._mask_inline_comments(
        "continued prose markdown_decompress(...)", state
    )
    assert state is True
    assert "markdown_decompress" not in code
    code, state, _, _ = detector._mask_inline_comments("*/", state)
    assert state is False

    # A pointer-store assignment is not a comment continuation.
    code, state, _, _ = detector._mask_inline_comments("*p = markdown_convert(x);", False)
    assert state is False
    assert "markdown_convert" in code

    # Open and close on the same line leaves the state untouched.
    code, state, _, _ = detector._mask_inline_comments(
        "fn /* markdown_convert */ markdown_decompress()", False
    )
    assert state is False
    assert "markdown_convert" not in code
    assert "markdown_decompress" in code


def test_callsite_names_ignores_multiline_comment_body() -> None:
    """A comment body line without a leading '*' must not count as a reference."""
    text = (
        "/*\n"
        " * header\n"
        "markdown_convert is documented here\n"
        "markdown_decompress too\n"
        " */\n"
        "void real_call(void) { markdown_convert(NULL); }\n"
    )
    names = detector._callsite_names(text)
    assert "markdown_convert" in names
    assert "markdown_decompress" not in names


def test_mask_keeps_string_literal_callsites() -> None:
    """URLs and strings containing // or /* must not hide real callsites."""
    code, state, _, _ = detector._mask_inline_comments(
        'url = "http://example.com/x"; markdown_convert(NULL);', False
    )
    assert state is False
    assert "markdown_convert" in code

    code, state, _, _ = detector._mask_inline_comments(
        's = "a/*b"; markdown_decompress(NULL);', False
    )
    assert state is False
    assert "markdown_decompress" in code

    code, state, _, _ = detector._mask_inline_comments(
        "s = 'it\\'s // fine'; markdown_convert(NULL); // real comment", False
    )
    assert state is False
    assert "markdown_convert" in code
    # The trailing comment is masked, the single-quoted literal is not
    # treated as a comment delimiter.  Assert the trailing comment text is
    # actually removed, not merely that the call text survives in some form.
    assert "// real comment" not in code, (
        "trailing comment must be masked, not left in place"
    )
    assert code.endswith("markdown_convert(NULL);  ") or \
        "markdown_convert(NULL);" in code


def test_mask_keeps_string_state_across_continuation_lines() -> None:
    """A backslash-continuation inside a string keeps the literal state."""
    # Line 1: string literal opened, ends with a backslash continuation.
    code, block, in_dq, in_sq = detector._mask_inline_comments(
        'const char *s = "part1 \\', False
    )
    assert in_dq is True
    assert "markdown_convert" not in code
    # Line 2: still inside the string; a // here is literal, not a comment,
    # and the string content is masked so it cannot be scanned as code.
    code, block, in_dq, in_sq = detector._mask_inline_comments(
        "part2 // still literal\"; markdown_convert(NULL);", block, in_dq, in_sq
    )
    assert in_dq is False
    assert "markdown_convert" in code
    # The string content (including the literal //) is masked, not treated
    # as a comment delimiter that would hide the real callsite.
    assert "part2" not in code
