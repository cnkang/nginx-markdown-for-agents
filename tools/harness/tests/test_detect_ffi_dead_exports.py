"""Regression tests for the dead FFI export detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.harness import detect_ffi_dead_exports as detector


def test_header_fallback_finds_multiline_declarations(
    tmp_path: Path, monkeypatch
) -> None:
    """Fallback parsing retains declarations missed by the typed pattern."""
    header = tmp_path / "markdown_converter.h"
    header.write_text(
        "typedef struct markdown_handle markdown_handle;\n"
        "markdown_handle * markdown_custom_export(\n"
        "    markdown_handle *handle\n"
        ");\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "ROOT", tmp_path)

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


def test_production_scan_counts_callback_function_references(
    tmp_path: Path, monkeypatch
) -> None:
    """Function addresses and callback arguments keep Rust exports live."""
    repository = tmp_path / "repo"
    source_dir = repository / "components" / "nginx-module" / "src"
    source_dir.mkdir(parents=True)
    source = source_dir / "callbacks.c"
    source.write_text(
        "void register_callbacks(void) {\n"
        "    callback = &markdown_address_callback;\n"
        "    register_callback(markdown_bare_callback);\n"
        "    markdown_direct_call(NULL);\n"
        '    const char *text = "markdown_string_only";\n'
        "    /* markdown_comment_only */\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "ROOT", repository)
    monkeypatch.setattr(
        detector,
        "_validate_repository_read_path",
        lambda path, *, purpose: Path(path).resolve(),
    )

    callsites = detector.scan_c_callsites(source_dir, include_headers=False)

    assert set(callsites) == {
        "markdown_address_callback",
        "markdown_bare_callback",
        "markdown_direct_call",
    }
    assert "markdown_string_only" not in callsites
    assert "markdown_comment_only" not in callsites


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
    # Reason-code helpers live outside `ffi`; their source is included in the
    # Rust declarations so the generated header and source remain cross-checked.
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
    monkeypatch.setattr(detector, "RUST_ADDITIONAL_FFI_SOURCES", ())
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert detector.declared_rust_exports() == ["base_export", "future_export"]


def test_declared_rust_exports_recognizes_old_style_no_mangle(
    tmp_path: Path, monkeypatch
) -> None:
    """The legacy #[no_mangle] attribute is recognized alongside #[unsafe(no_mangle)]."""
    (tmp_path / "legacy.rs").write_text(
        '#[no_mangle] pub extern "C" fn legacy_export() {}\n',
        encoding="utf-8",
    )
    (tmp_path / "mixed.rs").write_text(
        '#[unsafe(no_mangle)] pub extern "C" fn mixed_export() {}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", tmp_path)
    monkeypatch.setattr(detector, "RUST_ADDITIONAL_FFI_SOURCES", ())
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert set(detector.declared_rust_exports()) == {"legacy_export", "mixed_export"}


def test_declared_rust_exports_includes_reason_code_source(
    tmp_path: Path, monkeypatch
) -> None:
    """Auxiliary reason-code exports are part of the Rust declaration scan."""
    ffi_dir = tmp_path / "ffi"
    ffi_dir.mkdir()
    (ffi_dir / "base.rs").write_text(
        '#[unsafe(no_mangle)] pub extern "C" fn base_export() {}\n',
        encoding="utf-8",
    )
    reason_source = tmp_path / "decision" / "reason_code.rs"
    reason_source.parent.mkdir()
    reason_source.write_text(
        '#[unsafe(no_mangle)] pub extern "C" fn markdown_reason_code_str() {}\n'
        '#[unsafe(no_mangle)] pub extern "C" fn markdown_reason_code_metric_key() {}\n'
        '#[unsafe(no_mangle)] pub extern "C" fn markdown_reason_code_count() {}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", ffi_dir)
    monkeypatch.setattr(
        detector, "RUST_ADDITIONAL_FFI_SOURCES", (reason_source,)
    )
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert set(detector.declared_rust_exports()) == {
        "base_export",
        "markdown_reason_code_count",
        "markdown_reason_code_metric_key",
        "markdown_reason_code_str",
    }


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


def test_reintroduced_dynconf_lifecycle_pair_is_rejected(monkeypatch) -> None:
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
    monkeypatch.setattr(detector, "LIFECYCLE_PAIRS", mutated)
    dangling = detector.dangling_lifecycle_pairs(universe)
    assert dangling == [
        ("markdown_dynconf_result_free", "markdown_dynconf_parse"),
        ("markdown_dynconf_result_init", "markdown_dynconf_parse"),
    ]
    with pytest.raises(ValueError, match="live Rust exports"):
        detector._reject_dangling_lifecycle_pairs(universe)
    with pytest.raises(ValueError, match="markdown_dynconf_parse"):
        detector.run_audit()

    monkeypatch.undo()
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


def test_rust_raw_string_scanner_tracks_exact_hash_delimiters():
    """Raw byte/string prefixes skip shorter-quoted content and bound EOF."""
    source = 'r##"inside "# one-hash text, then close"##tail'
    end = detector._rust_raw_string_end(source, 0)
    assert end is not None
    assert source[end:] == "tail"

    byte_source = 'br###"byte raw payload"###after'
    byte_end = detector._rust_raw_string_end(byte_source, 0)
    assert byte_end is not None
    assert byte_source[byte_end:] == "after"

    unterminated = 'r#"no closing delimiter'
    assert detector._rust_raw_string_end(unterminated, 0) == len(unterminated)
    assert detector._rust_raw_string_end("bravo", 0) is None


def test_declared_rust_exports_ignore_comments_and_string_literals(
    tmp_path: Path, monkeypatch
) -> None:
    """Only live Rust declarations belong to the FFI export set."""
    module = tmp_path / "commented_exports.rs"
    module.write_text(
        'const DOC: &str = r###"#[unsafe(no_mangle)] pub extern "C" '
        'fn markdown_converter_free() {}"###;\n'
        'const COOKED: &str = "#[unsafe(no_mangle)] pub extern \\"C\\" '
        'fn markdown_converter_free() {} // literal comment markers";\n'
        '// #[unsafe(no_mangle)] pub extern "C" fn markdown_converter_free() {}\n'
        '/* outer /* nested */ #[unsafe(no_mangle)] pub extern "C" '
        'fn markdown_result_init() {} */\n'
        '#[unsafe(no_mangle)]\npub extern "C" fn markdown_converter_new() {}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", tmp_path)
    monkeypatch.setattr(detector, "RUST_ADDITIONAL_FFI_SOURCES", ())
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    nested_comment = "/* outer /* inner */ tail */"
    assert (
        detector._rust_block_comment_end(nested_comment, 0) == len(nested_comment)
    )

    rust_exports = detector.declared_rust_exports()
    assert rust_exports == ["markdown_converter_new"]
    stale_header = frozenset(
        {"markdown_converter_new", "markdown_converter_free"}
    )
    dangling = detector.dangling_lifecycle_pairs(stale_header, rust_exports)
    assert ("markdown_converter_new", "markdown_converter_free") in dangling


def test_masking_many_rust_literals_preserves_only_abi_strings() -> None:
    """Large literal sets remain correctly masked without repeated prefixes."""
    literal_count = 600
    source = "\n".join(
        line
        for index in range(literal_count)
        for line in (
            f'const DOC_{index}: &str = "C";',
            f'pub extern "C" fn abi_{index}() {{}}',
        )
    )

    masked = detector._mask_rust_non_code(source)

    assert masked.count('extern "C"') == literal_count
    assert masked.count('const DOC_') == literal_count
    assert masked.count('"C"') == literal_count


def test_abi_string_restoration_scans_the_identifier_stream_once(
    monkeypatch,
) -> None:
    """Large ABI string sets retain the single-pass masking optimization."""
    source = 'extern "C" fn first() {}\nextern "C" fn second() {}\n'
    candidates = []
    for start in (source.index('"C"'), source.rindex('"C"')):
        candidates.append((start, start + len('"C"')))
    masked = list(source)
    for start, end in candidates:
        masked[start:end] = " " * (end - start)

    class _CountingIdentifierPattern:
        def __init__(self, pattern) -> None:
            self.pattern = pattern
            self.finditer_calls = 0

        def finditer(self, text: str):
            self.finditer_calls += 1
            return self.pattern.finditer(text)

    identifiers = _CountingIdentifierPattern(detector._RUST_IDENTIFIER_RE)
    monkeypatch.setattr(detector, "_RUST_IDENTIFIER_RE", identifiers)

    detector._restore_rust_abi_strings(source, masked, candidates)

    assert identifiers.finditer_calls == 1
    assert "".join(masked).count('"C"') == 2


def test_rust_character_literals_do_not_hide_later_exports(
    tmp_path: Path, monkeypatch
) -> None:
    """Quote characters inside Rust char literals leave later code visible."""
    module = tmp_path / "character_literals.rs"
    module.write_text(
        r'''const DOUBLE_QUOTE: char = '"';
const SINGLE_QUOTE: char = '\'';
const BYTE_QUOTE: u8 = b'"';
const HEX_QUOTE: char = '\x22';
const UNICODE_QUOTE: char = '\u{27}';
#[unsafe(no_mangle)]
pub extern "C" fn markdown_after_character_literals() {}
''',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", tmp_path)
    monkeypatch.setattr(detector, "RUST_ADDITIONAL_FFI_SOURCES", ())
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert detector.declared_rust_exports() == [
        "markdown_after_character_literals"
    ]


def test_declared_rust_exports_allow_stacked_attributes(
    tmp_path: Path, monkeypatch
) -> None:
    """Attributes between no_mangle and the declaration do not hide exports."""
    module = tmp_path / "stacked_attributes.rs"
    module.write_text(
        '#[unsafe(no_mangle)]\n'
        '#[allow(non_snake_case)]\n'
        'pub extern "C" fn first_stacked_export() {}\n'
        '#[cfg(feature = "extra_export")]\n'
        '#[unsafe(no_mangle)]\n'
        'pub unsafe extern "C" fn second_stacked_export() {}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "RUST_FFI_DIR", tmp_path)
    monkeypatch.setattr(detector, "RUST_ADDITIONAL_FFI_SOURCES", ())
    monkeypatch.setattr(
        detector,
        "_read_text",
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert detector.declared_rust_exports() == [
        "first_stacked_export",
        "second_stacked_export",
    ]


def test_scanner_rejects_source_symlink_outside_repository(
    tmp_path: Path, monkeypatch
) -> None:
    """A symlink inside the source tree cannot authorize reading outside it."""
    repository = tmp_path / "repo"
    source_dir = repository / "components" / "nginx-module" / "src"
    source_dir.mkdir(parents=True)
    outside = tmp_path / "outside.c"
    outside.write_text("void f(void) { markdown_convert(NULL); }\n", encoding="utf-8")
    (source_dir / "outside.c").symlink_to(outside)
    monkeypatch.setattr(detector, "ROOT", repository)

    with pytest.raises(ValueError, match="outside repository root"):
        detector.scan_c_callsites(source_dir)


def test_unreadable_c_source_fails_the_scan(
    tmp_path: Path, monkeypatch
) -> None:
    """An undecodable .c file must fail closed, not silently pass.

    Silently skipping the file would classify exports from an incomplete
    callsite set (dead instead of called).  The scanner mirrors the Rust
    declaration scanner and raises instead.
    """
    repository = tmp_path / "repo"
    source_dir = repository / "components" / "nginx-module" / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "good.c").write_text(
        "void f(void) { markdown_convert(NULL); }\n", encoding="utf-8"
    )
    undecodable = source_dir / "undecodable.c"
    undecodable.write_bytes(
        b"void g(void) { \xff\xfe markdown_decompress(NULL); }\n"
    )
    monkeypatch.setattr(detector, "ROOT", repository)
    monkeypatch.setattr(
        detector,
        "_validate_repository_read_path",
        lambda path, *, purpose: Path(path).resolve(),
    )

    with pytest.raises(ValueError, match="cannot read C callsite source"):
        detector.scan_c_callsites(source_dir, include_headers=False)


def test_readable_sources_still_scan_without_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Sanity control: a fully readable tree scans and returns callsites."""
    repository = tmp_path / "repo"
    source_dir = repository / "components" / "nginx-module" / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "good.c").write_text(
        "void f(void) { markdown_convert(NULL); }\n", encoding="utf-8"
    )
    monkeypatch.setattr(detector, "ROOT", repository)
    monkeypatch.setattr(
        detector,
        "_validate_repository_read_path",
        lambda path, *, purpose: Path(path).resolve(),
    )

    callsites = detector.scan_c_callsites(source_dir, include_headers=False)

    assert set(callsites) == {"markdown_convert"}


def test_typed_prototypes_are_not_counted_as_callsites(
    tmp_path: Path, monkeypatch
) -> None:
    """Bare-int/ngx/char-pointer prototypes are declarations, not callsites."""
    repository = tmp_path / "repo"
    source_dir = repository / "components" / "nginx-module" / "src"
    source_dir.mkdir(parents=True)
    source = source_dir / "prototypes.c"
    source.write_text(
        "int markdown_bare_int(void);\n"
        "static ngx_int_t markdown_static_status(void);\n"
        "char * markdown_char_pointer(void);\n"
        "unsigned markdown_unsigned_only(void);\n"
        "size_t markdown_size_only(void);\n"
        "void real_caller(void) { markdown_convert(NULL); }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(detector, "ROOT", repository)
    monkeypatch.setattr(
        detector,
        "_validate_repository_read_path",
        lambda path, *, purpose: Path(path).resolve(),
    )

    callsites = detector.scan_c_callsites(source_dir, include_headers=False)

    assert "markdown_convert" in callsites
    for name in (
        "markdown_bare_int",
        "markdown_static_status",
        "markdown_char_pointer",
        "markdown_unsigned_only",
        "markdown_size_only",
    ):
        assert name not in callsites, callsites[name]


def test_declaration_pattern_keeps_plain_calls_out_of_declarations() -> None:
    """A call embedded in an expression must not look like a declaration."""
    assert detector.DECLARATION_LINE_RE.match("    markdown_call_now(x);") is None
    assert (
        detector.DECLARATION_LINE_RE.match("    if (markdown_convert(x)) {}")
        is None
    )
    assert (
        detector.DECLARATION_LINE_RE.match("    rc = markdown_convert(x);")
        is None
    )
    assert detector.DECLARATION_LINE_RE.match("foo * markdown_p(void)") is None
    assert detector.DECLARATION_LINE_RE.match("int markdown_x(void)") is not None
    assert (
        detector.DECLARATION_LINE_RE.match("char * markdown_y(void)") is not None
    )


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
