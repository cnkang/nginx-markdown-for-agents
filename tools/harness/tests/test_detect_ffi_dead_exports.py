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


def test_guard_stack_handles_nested_ifdef_and_endif() -> None:
    """Guard tracking must pop only after the conditional body."""
    guards: list[str] = []

    detector._update_guard_stack("#ifdef OUTER", guards)
    detector._update_guard_stack("#ifdef INNER", guards)
    detector._update_guard_stack("#endif", guards)

    assert guards == ["OUTER"]


@pytest.mark.parametrize(
    "scanner",
    (detector.scan_c_callsites, detector.scan_test_references),
)
def test_scanners_reject_parent_paths(scanner) -> None:
    """Directory inputs must be rejected before recursive traversal."""
    with pytest.raises(ValueError, match="Refusing path"):
        scanner(Path("../outside"))
