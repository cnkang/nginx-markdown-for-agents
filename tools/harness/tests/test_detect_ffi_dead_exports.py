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
