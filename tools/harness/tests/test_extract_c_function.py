"""Tests for the c-extract function slicer's semicolon handling.

A ``;`` inside a comment or string literal that appears between the
function signature and the first opening brace must not be treated as a
prototype terminator: the definition itself must be extracted.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "c-extract")
)

from extract_c_function import find_function_slice  # noqa: E402


def test_comment_semicolon_does_not_terminate_definition() -> None:
    """A ``;`` inside a block comment is not a prototype terminator."""
    src = (
        "void f() /* ; */ {}\n"
        "\n"
        "int unrelated(void);\n"
        "int unrelated(void) { return 1; }\n"
    )
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    assert src[start:end] == "f() /* ; */ {}"


def test_string_semicolon_does_not_terminate_definition() -> None:
    """A ``;`` inside a string literal is not a prototype terminator."""
    src = 'void f() { const char *note = "has ; inside"; }\n'
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    assert src[start:end] == 'f() { const char *note = "has ; inside"; }'


def test_code_semicolon_still_skips_prototype() -> None:
    """A real prototype (code ``;`` before the brace) is still skipped."""
    src = (
        "void f(void);\n"
        "void f(void) { ; }\n"
    )
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    assert src[start:end] == "f(void) { ; }"
