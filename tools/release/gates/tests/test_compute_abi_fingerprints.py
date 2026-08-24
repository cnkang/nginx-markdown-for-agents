"""Regression tests for the FFI symbol-set fingerprint inputs."""

from tools.release.gates.compute_abi_fingerprints import (
    HEADER_HASH_DEFINE,
    HEADER_PATH,
    symbol_export_names,
)


def test_symbol_hash_covers_every_ffi_export() -> None:
    """The hashed export set must cover all four Rust FFI modules."""
    names = symbol_export_names()

    assert len(names) == 43
    assert {
        "markdown_sha256_hex",
        "markdown_dynconf_parse",
        "markdown_dynconf_result_init",
        "markdown_dynconf_result_free",
    } <= names


def test_header_hash_pattern_matches_generated_literal() -> None:
    raw = HEADER_PATH.read_bytes()
    match = HEADER_HASH_DEFINE.search(raw)
    assert match is not None
    assert match.group(1) == b"#define MARKDOWN_HEADER_HASH "
    canonical, replacements = HEADER_HASH_DEFINE.subn(
        rb"\g<1>0ull", raw, count=1
    )
    assert replacements == 1
    assert canonical != raw


def test_header_hash_pattern_accepts_hex_literal_and_suffix() -> None:
    assert HEADER_HASH_DEFINE.search(
        b"#define MARKDOWN_HEADER_HASH 0x1d24bd7fe164e3e3ULL"
    )
