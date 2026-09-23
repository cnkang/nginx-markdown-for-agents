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


def test_declared_export_universe_covers_header_and_rust_modules() -> None:
    """The universe is the declared Rust exports plus the generated header."""
    universe = detector.declared_ffi_export_universe()

    rust_exports = detector.declared_rust_exports()
    assert rust_exports
    assert {"markdown_convert", "markdown_abi_version"} <= set(rust_exports)
    # The removed dynconf surface must not be part of the current universe.
    assert {
        "markdown_dynconf_parse",
        "markdown_dynconf_result_init",
        "markdown_dynconf_result_free",
    }.isdisjoint(universe)
    # Reason-code helpers are generated into the header only, so the universe
    # is the union of both sides rather than the Rust modules alone.
    header_exports = detector.parse_header_exports(detector.FFI_HEADER)
    assert universe == set(header_exports) | set(rust_exports)
    assert set(header_exports) <= universe
    assert set(rust_exports) <= universe


def test_declared_rust_exports_discover_added_ffi_modules(
    tmp_path: Path, monkeypatch
) -> None:
    """A new Rust module is included without updating a fixed module list."""
    (tmp_path / "exports.rs").write_text(
        '#[unsafe(no_mangle)] pub extern "C" fn base_export() {}\n',
        encoding="utf-8",
    )
    (tmp_path / "future.rs").write_text(
        '#[unsafe(no_mangle)] pub extern "C" fn future_export() {}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", tmp_path)
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert detector.declared_rust_exports() == ["base_export", "future_export"]


def test_current_lifecycle_pairs_all_belong_to_the_export_universe() -> None:
    """Every current pair names live exports, so the invariant passes."""
    universe = detector.declared_ffi_export_universe()

    assert detector.dangling_lifecycle_pairs(universe) == []
    assert set(detector.LIFECYCLE_PAIRS) <= universe
    assert set(detector.LIFECYCLE_PAIRS.values()) <= universe
    # The removed dynconf pairs are the stale entries this invariant exists
    # to catch; they must be gone from the table itself.
    assert {
        "markdown_dynconf_result_init",
        "markdown_dynconf_result_free",
    }.isdisjoint(detector.LIFECYCLE_PAIRS)


def test_reintroduced_dynconf_lifecycle_pair_is_rejected() -> None:
    """A pair naming a removed export must fail the universe invariant.

    This is the mutation the fix guards against: restoring an obsolete pair
    the current tree no longer declares.
    """
    universe = detector.declared_ffi_export_universe()
    obsolete_pair = {
        "markdown_dynconf_result_init": "markdown_dynconf_parse",
        "markdown_dynconf_result_free": "markdown_dynconf_parse",
    }

    mutated = dict(detector.LIFECYCLE_PAIRS)
    mutated.update(obsolete_pair)
    restored = detector.LIFECYCLE_PAIRS
    detector.LIFECYCLE_PAIRS = mutated
    try:
        dangling = detector.dangling_lifecycle_pairs(universe)
        assert dangling == [
            ("markdown_dynconf_result_free", "markdown_dynconf_parse"),
            ("markdown_dynconf_result_init", "markdown_dynconf_parse"),
        ]
        with pytest.raises(ValueError, match="declared FFI export universe"):
            detector._reject_dangling_lifecycle_pairs(universe)
        with pytest.raises(ValueError, match="markdown_dynconf_parse"):
            detector.run_audit()
    finally:
        detector.LIFECYCLE_PAIRS = restored

    assert detector.LIFECYCLE_PAIRS is restored
    assert detector.dangling_lifecycle_pairs(universe) == []


def test_run_audit_wires_the_universe_invariant() -> None:
    """The production audit path enforces the invariant on this tree."""
    inventory = detector.run_audit()

    assert inventory["summary"]["dead"] == 0
    assert inventory["total_exports"] == len(
        detector.parse_header_exports(detector.FFI_HEADER)
    )


def test_lifecycle_pairs_reject_a_rust_removed_export_even_if_header_is_stale(
    monkeypatch,
) -> None:
    """A stale generated header cannot hide a removed Rust lifecycle symbol."""
    export_name = sorted(detector.LIFECYCLE_PAIRS)[0]
    header_exports = detector.parse_header_exports(detector.FFI_HEADER)
    rust_exports = detector.declared_rust_exports()
    assert export_name in header_exports
    assert export_name in rust_exports

    stale_rust_exports = [name for name in rust_exports if name != export_name]
    universe = detector.declared_ffi_export_universe(
        header_exports, stale_rust_exports
    )
    assert export_name in universe  # the stale generated header still says it exists
    dangling = detector.dangling_lifecycle_pairs(universe, stale_rust_exports)
    assert any(key == export_name or value == export_name for key, value in dangling)
    with pytest.raises(ValueError, match=export_name):
        detector._reject_dangling_lifecycle_pairs(universe, stale_rust_exports)

    monkeypatch.setattr(
        detector, "declared_rust_exports", lambda: stale_rust_exports
    )
    with pytest.raises(ValueError, match=export_name):
        detector.run_audit()


def test_declared_rust_exports_ignore_comments_and_string_literals(
    tmp_path: Path, monkeypatch
) -> None:
    """Only live Rust declarations belong to the FFI export set."""
    module = tmp_path / "commented_exports.rs"
    module.write_text(
        'const DOC: &str = r###"#[unsafe(no_mangle)] pub extern "C" '
        'fn markdown_converter_free() {}"###;\n'
        'const COOKED: &str = "literal // and /* markers";\n'
        '// #[unsafe(no_mangle)] pub extern "C" fn markdown_converter_free() {}\n'
        '/* outer /* nested */ #[unsafe(no_mangle)] pub extern "C" '
        'fn markdown_result_init() {} */\n'
        '#[unsafe(no_mangle)]\npub extern "C" fn markdown_converter_new() {}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", tmp_path)
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    rust_exports = detector.declared_rust_exports()
    assert rust_exports == ["markdown_converter_new"]
    stale_header = frozenset(
        {"markdown_converter_new", "markdown_converter_free"}
    )
    dangling = detector.dangling_lifecycle_pairs(stale_header, rust_exports)
    assert ("markdown_converter_new", "markdown_converter_free") in dangling


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
