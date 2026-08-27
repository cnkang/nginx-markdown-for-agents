#!/usr/bin/env python3
"""Compute and verify the ABI handshake identity hashes.

The script computes three hashes; the fourth identity,
MARKDOWN_ABI_VERSION, is a plain constant.  It reads the generated C header,
Rust FFI export modules, and the checked-in C layout assertions so the inputs
match the artifacts used by the ABI handshake.

Formulas (documented in components/rust-converter/src/ffi/abi.rs):

- MARKDOWN_HEADER_HASH: SHA-256 of the generated header bytes after replacing
  the numeric MARKDOWN_HEADER_HASH macro value with the fixed spelling 0ull,
  truncated to the first 8 bytes interpreted big-endian.  Normalizing the
  self-referential field makes the fingerprint reproducible.
- MARKDOWN_SYMBOL_SET_HASH: SHA-256 of the sorted newline-joined names of
  every matching ``#[unsafe(no_mangle)] pub extern "C" fn`` found by
  ``FFI_EXPORT_RE`` in the three export modules (``exports.rs``,
  ``streaming.rs``, and ``dynconf/ffi.rs``), truncated to the first 8
  bytes.  The scan does not exclude cfg-gated duplicates or helpers.
- MARKDOWN_LAYOUT_FINGERPRINT: SHA-256 of the sorted unique
  ``struct_name:size`` lines parsed from the generated NGINX FFI
  layout-check header's ``_Static_assert`` entries, truncated to the first
  8 bytes.

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
    rb"(#define\s+MARKDOWN_HEADER_HASH\s+)"
    rb"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uUlL]+)?"
)
ABI_CONSTANTS = re.compile(
    r"pub const (MARKDOWN_(?:HEADER_HASH|SYMBOL_SET_HASH|LAYOUT_FINGERPRINT))"
    r"\s*:\s*u64\s*=\s*(0x[0-9a-fA-F]+)\s*;"
)


def _repo_file(path: pathlib.Path) -> pathlib.Path:
    """Resolve a checked-in source file without permitting symlink escape."""
    root = REPO_ROOT.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"source path escapes repository root: {path}")
    if not resolved.is_file():
        raise ValueError(f"source path is not a regular file: {path}")
    return resolved


def truncate8(digest: bytes) -> int:
    return int.from_bytes(digest[:8], "big")


def header_hash() -> int:
    raw = _repo_file(HEADER_PATH).read_bytes()
    # The digest cannot include its own numeric value: replacing it with a
    # fixed spelling makes the header fingerprint reproducible and gives the
    # generated constant a stable value.
    if HEADER_HASH_DEFINE.search(raw) is None:
        raise ValueError("MARKDOWN_HEADER_HASH definition is missing")
    canonical = HEADER_HASH_DEFINE.sub(rb"\g<1>0ull", raw, count=1)
    return truncate8(hashlib.sha256(canonical).digest())


def symbol_export_names() -> set[str]:
    """Collect all C export names from the Rust FFI modules."""
    names = set()
    for path in (
        EXPORTS_PATH,
        STREAMING_PATH,
        DYNCONF_FFI_PATH,
    ):
        text = _repo_file(path).read_text(encoding="utf-8")
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
    text = _repo_file(layout_check).read_text(encoding="utf-8")
    for match in re.finditer(
        r'_Static_assert\(sizeof\((\w+)\)\s*==\s*(\d+),',
        text,
    ):
        name, size = match.group(1), match.group(2)
        lines.append(f"{name}:{size}")
    if not lines:
        raise ValueError("no struct sizes parsed from layout-check header")
    payload = "\n".join(sorted(set(lines))).encode("utf-8")
    return truncate8(hashlib.sha256(payload).digest())


def checked_in_abi_constants() -> dict[str, int]:
    """Read the constants that the Rust handshake exports."""
    content = _repo_file(ABI_PATH).read_text(encoding="utf-8")
    values = {
        name: int(value, 16) for name, value in ABI_CONSTANTS.findall(content)
    }
    expected = {
        "MARKDOWN_HEADER_HASH",
        "MARKDOWN_SYMBOL_SET_HASH",
        "MARKDOWN_LAYOUT_FINGERPRINT",
    }
    if set(values) != expected:
        raise ValueError("abi.rs is missing one or more fingerprint constants")
    return values


def main() -> int:
    hdr = header_hash()
    sym = symbol_set_hash()
    layout = layout_fingerprint()
    computed = {
        "MARKDOWN_HEADER_HASH": hdr,
        "MARKDOWN_SYMBOL_SET_HASH": sym,
        "MARKDOWN_LAYOUT_FINGERPRINT": layout,
    }
    checked_in = checked_in_abi_constants()
    if computed != checked_in:
        raise SystemExit(
            "ABI fingerprint drift: "
            + ", ".join(
                f"{name} expected 0x{computed[name]:016x} "
                f"found 0x{checked_in[name]:016x}"
                for name in sorted(computed)
                if computed[name] != checked_in[name]
            )
        )
    print(f"MARKDOWN_HEADER_HASH = 0x{hdr:016x}")
    print(f"MARKDOWN_SYMBOL_SET_HASH = 0x{sym:016x}")
    print(f"MARKDOWN_LAYOUT_FINGERPRINT = 0x{layout:016x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
