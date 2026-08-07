"""Regression tests for the FFI symbol-set fingerprint inputs."""

from tools.release.gates.compute_abi_fingerprints import symbol_export_names


def test_symbol_hash_covers_every_ffi_export() -> None:
    """The hashed export set must cover all four Rust FFI modules."""
    names = symbol_export_names()

    assert len(names) == 44
    assert {
        "markdown_sha256_hex",
        "markdown_dynconf_parse",
        "markdown_dynconf_result_init",
        "markdown_dynconf_result_free",
    } <= names
