#!/usr/bin/env python3
"""Compute the three ABI handshake identity hashes (the fourth identity,
MARKDOWN_ABI_VERSION, is a plain constant).

Formulas (documented in components/rust-converter/src/ffi/abi.rs):

- MARKDOWN_HEADER_HASH: SHA-256 of the generated header bytes after replacing
  the numeric MARKDOWN_HEADER_HASH macro value with the fixed spelling 0ull,
  truncated to the first 8 bytes interpreted big-endian.  Normalizing the
  self-referential field makes the fingerprint reproducible.
- MARKDOWN_SYMBOL_SET_HASH: SHA-256 of the sorted newline-joined
  `#[unsafe(no_mangle)] pub extern "C" fn` export names (excluding
  `#[cfg]`-gated duplicates and helpers), truncated to the first 8 bytes.
- MARKDOWN_LAYOUT_FINGERPRINT: SHA-256 of the sorted newline-joined
  `struct_name:size` lines for every shared `#[repr(C)]` struct in
  `src/ffi/abi.rs` and `src/ffi/streaming.rs` (and the encoding chain
  result structs), truncated to the first 8 bytes.

Usage:
    python3 tools/release/gates/compute_abi_fingerprints.py

Prints the three constants formatted for abi.rs.
"""

import hashlib
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ABI_PATH = REPO_ROOT / "components" / "rust-converter" / "src" / "ffi" / "abi.rs"
STREAMING_PATH = REPO_ROOT / "components" / "rust-converter" / "src" / "ffi" / "streaming.rs"
EXPORTS_PATH = REPO_ROOT / "components" / "rust-converter" / "src" / "ffi" / "exports.rs"
DYNCONF_FFI_PATH = REPO_ROOT / "components" / "rust-converter" / "src" / "dynconf" / "ffi.rs"
HEADER_PATH = REPO_ROOT / "components" / "rust-converter" / "include" / "markdown_converter.h"
FFI_EXPORT_RE = re.compile(
    r'#\[unsafe\(no_mangle\)\]\s*pub\s+(?:unsafe\s+)?extern\s+"C"\s+fn\s+(\w+)'
)
HEADER_HASH_DEFINE = re.compile(
    rb"(#define\s+MARKDOWN_HEADER_HASH\s+)\d+(?:ull|ULL)?"
)


def truncate8(digest: bytes) -> int:
    return int.from_bytes(digest[:8], "big")


def header_hash() -> int:
    raw = HEADER_PATH.read_bytes()
    # The digest cannot include its own numeric value: replacing it with a
    # fixed spelling makes the header fingerprint reproducible and gives the
    # generated constant a stable value.
    canonical = HEADER_HASH_DEFINE.sub(rb"\g<1>0ull", raw, count=1)
    if canonical == raw:
        raise ValueError("MARKDOWN_HEADER_HASH definition is missing")
    return truncate8(hashlib.sha256(canonical).digest())


def symbol_export_names() -> set[str]:
    """Collect all C export names from the Rust FFI modules."""
    names = set()
    for path in (
        EXPORTS_PATH,
        STREAMING_PATH,
        REPO_ROOT / "components" / "rust-converter" / "src" / "ffi" / "incremental.rs",
        DYNCONF_FFI_PATH,
    ):
        text = path.read_text(encoding="utf-8")
        for match in FFI_EXPORT_RE.finditer(text):
            names.add(match.group(1))
    return names


def symbol_set_hash() -> int:
    """Hash the stable sorted set of all C export names."""
    names = symbol_export_names()
    payload = "\n".join(sorted(names)).encode("utf-8")
    return truncate8(hashlib.sha256(payload).digest())


def layout_fingerprint() -> int:
    """Compute sorted struct_name:size lines for the shared repr(C) structs.

    Sizes come from the C layout-check header
    (`ngx_http_markdown_ffi_layout_check.h`), which asserts the exact Rust
    sizes with `_Static_assert`; that file is the single checked-in source
    of the C-side layout truth.  The 4-tuple handshake then verifies
    C↔Rust agreement at load time.
    """
    lines = []
    layout_check = (
        REPO_ROOT
        / "components"
        / "nginx-module"
        / "src"
        / "ngx_http_markdown_ffi_layout_check.h"
    )
    text = layout_check.read_text(encoding="utf-8")
    for match in re.finditer(
        r'_Static_assert\(sizeof\((\w+)\)\s*==\s*(\d+),',
        text,
    ):
        name, size = match.group(1), match.group(2)
        lines.append(f"{name}:{size}")
    if not lines:
        print("ERROR: no struct sizes parsed from layout-check header", file=sys.stderr)
    payload = "\n".join(sorted(set(lines))).encode("utf-8")
    return truncate8(hashlib.sha256(payload).digest())


def rust_struct_size(name: str) -> int | None:
    """Legacy probe helper (unused; sizes come from the C layout header)."""
    del name
    return None


def main() -> int:
    hdr = header_hash()
    sym = symbol_set_hash()
    layout = layout_fingerprint()
    print(f"MARKDOWN_HEADER_HASH = 0x{hdr:016x}")
    print(f"MARKDOWN_SYMBOL_SET_HASH = 0x{sym:016x}")
    print(f"MARKDOWN_LAYOUT_FINGERPRINT = 0x{layout:016x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
