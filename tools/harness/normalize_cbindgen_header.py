#!/usr/bin/env python3
"""Normalize the cbindgen boundary between the last type and FFI functions."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER_PATH = REPO_ROOT / "components" / "rust-converter" / "include" / "markdown_converter.h"
BOUNDARY_RE = re.compile(
    r"(}\s+(?:FFI|Markdown)\w*;)\n+(?=/\*\*)"
)
# The ABI handshake constants are u64 values printed by cbindgen as bare
# decimal literals.  A hash whose high bit is set (value >= 2**63) then
# trips `-Werror=overflow` in the C module build ("integer constant is so
# large that it is unsigned").  Appending the ULL suffix keeps the constant
# unsigned and the warning silent.  compute_abi_fingerprints.py canonicalizes
# the value to `0ull` before hashing, so the suffix does not affect the
# fingerprint.
ABI_CONSTANT_DEFINE_RE = re.compile(
    r"(#define\s+(?:MARKDOWN_HEADER_HASH|MARKDOWN_SYMBOL_SET_HASH"
    r"|MARKDOWN_LAYOUT_FINGERPRINT)\s+)(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)"
)


def _suffix_abi_constants(content: str) -> tuple[str, bool]:
    """Append `ULL` to the ABI handshake constants so values >= 2**63 compile.

    Returns the updated content and whether any constant was modified.
    """
    changed = [False]

    def _repl(match: re.Match[str]) -> str:
        value_text = match.group(2)
        suffix = match.group(3) or ""
        value = int(value_text, 0)
        if value < 2**63:
            # Values below 2**63 compile as signed int; leave them bare so
            # the canonical fingerprint bytes stay unchanged for constants
            # that never needed the suffix.
            return match.group(0)
        if suffix:
            # Already carries an unsigned suffix; keep the existing spelling.
            return match.group(0)
        changed[0] = True
        # The header value has no suffix after cbindgen; append ULL to the
        # decimal spelling when the value needs an unsigned literal.
        return f"{match.group(1)}{value_text}ULL"

    updated = ABI_CONSTANT_DEFINE_RE.sub(_repl, content)
    return updated, changed[0]


def normalize_header() -> None:
    """Collapse cbindgen's repeated boundary blank lines to one blank line."""
    path = HEADER_PATH.resolve(strict=True)
    repo_root = REPO_ROOT.resolve(strict=True)
    if repo_root not in path.parents:
        raise RuntimeError("generated header escaped the repository root")
    content = path.read_text(encoding="utf-8")
    matches = list(BOUNDARY_RE.finditer(content))
    if not matches:
        raise RuntimeError("cbindgen type/function boundary was not found")
    match = matches[-1]
    replacement = f"{match.group(1)}\n\n"
    normalized = content[:match.start()] + replacement + content[match.end():]
    normalized, suffix_changed = _suffix_abi_constants(normalized)
    path.write_text(normalized, encoding="utf-8")
    if suffix_changed:
        print("normalize_cbindgen_header: added ULL suffix to ABI constants")


if __name__ == "__main__":
    normalize_header()
