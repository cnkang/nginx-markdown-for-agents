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


def test_string_semicolon_inside_body_does_not_terminate_definition() -> None:
    """A ``;`` inside a string literal is not a prototype terminator."""
    src = 'void f() { const char *note = "has ; inside"; }\n'
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    assert src[start:end] == 'f() { const char *note = "has ; inside"; }'


def test_string_semicolon_in_pre_brace_attribute_is_masked() -> None:
    """A ``;`` inside a string literal before the opening brace stays masked.

    The literal sits in the pre-brace region (an attribute-style annotation
    ahead of the signature), so ``_find_code_semicolon`` must mask it while
    scanning and still extract the definition itself.
    """
    src = (
        '__attribute__((deprecated("use ; instead")))\n'
        "void f(void)\n"
        "{\n"
        "    return;\n"
        "}\n"
    )
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    extracted = src[start:end]
    assert extracted.startswith("f(void)")
    assert extracted.rstrip().endswith("}")


def test_division_operator_is_not_masked_as_quote() -> None:
    """A bare ``/`` division operator does not start a masked region.

    A division before the opening brace must not cause the scanner to treat
    the remainder of the region as one quoted token, which would hide a
    real prototype terminator (or, without one, still extract the body).
    """
    src = (
        "int f(void);\n"
        "int f(void) { return 4 / 2; }\n"
    )
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    assert src[start:end] == "f(void) { return 4 / 2; }"


def test_division_semicolon_before_brace_still_skips_prototype() -> None:
    """A code ``;`` after a division expression still ends the prototype.

    ``n / 2`` inside the prototype text contains a real code semicolon
    afterwards (the division must not mask it), so the matcher advances to
    the definition instead of extracting the prototype's tail.
    """
    src = (
        "int f(int n / 2 placeholder);\n"
        "int f(int n) { return n; }\n"
    )
    slice_bounds = find_function_slice(src, "f", "f(")
    assert slice_bounds is not None
    start, end = slice_bounds
    assert src[start:end] == "f(int n) { return n; }"


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
