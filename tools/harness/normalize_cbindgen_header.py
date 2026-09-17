#!/usr/bin/env python3
"""Normalize the cbindgen boundary between the last type and FFI functions."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER_PATH = REPO_ROOT / "components" / "rust-converter" / "include" / "markdown_converter.h"
BOUNDARY_RE = re.compile(
    r"(}\s+(?:FFI|Markdown)\w*;)\n+(?=/\*\*)"
)


def normalize_text(content: str) -> str:
    """Collapse cbindgen's repeated boundary blank lines to one blank line."""
    matches = list(BOUNDARY_RE.finditer(content))
    if not matches:
        raise RuntimeError("cbindgen type/function boundary was not found")
    match = matches[-1]
    replacement = f"{match.group(1)}\n\n"
    return content[:match.start()] + replacement + content[match.end():]


def normalize_header() -> None:
    """Normalize the committed header in place."""
    path = HEADER_PATH.resolve(strict=True)
    repo_root = REPO_ROOT.resolve(strict=True)
    if repo_root not in path.parents:
        raise RuntimeError("generated header escaped the repository root")
    content = path.read_text(encoding="utf-8")
    path.write_text(normalize_text(content), encoding="utf-8")


if __name__ == "__main__":
    normalize_header()
