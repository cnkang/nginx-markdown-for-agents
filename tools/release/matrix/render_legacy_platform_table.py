#!/usr/bin/env python3
"""Regenerate the legacy auto-generated platform table in INSTALLATION.md.

The block between ``<!-- BEGIN AUTO-GENERATED MATRIX -->`` and
``<!-- END AUTO-GENERATED MATRIX -->`` predates the release-contract matrix.
It lists one row per (libc, architecture) pair for every NGINX version that has
a ``linux`` dynamic-module row, and it repeats that row's support tier.

The release-contract section of the same document is generated from the same
matrix by ``tools/render_release_matrix_docs.py``.  This tool keeps the legacy
block in step after a promotion, so the two sections cannot disagree.

Usage:
    python3 tools/release/matrix/render_legacy_platform_table.py [--check]

Exit codes:
    0  Block regenerated, or --check found it current
    1  The document or the block is missing
    2  --check found a stale block
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(SCRIPT_DIR))

from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)
from update_matrix import (  # noqa: E402
    DOC_MARKER_BEGIN,
    DOC_MARKER_END,
    update_doc_table,
)

DOC_PATH = REPO_ROOT / "docs" / "guides" / "INSTALLATION.md"
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"

# The legacy table predates the architecture field on every artifact family.
# It describes the binary cross-product, which the linux dynamic-module rows
# still carry.
_LEGACY_ARTIFACT = "dynamic-module"
_LEGACY_OS = "linux"
# The legacy table spells the architectures in GCC triple form.
_LEGACY_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}


def legacy_entries(matrix: dict[str, Any]) -> list[dict[str, str]]:
    """Return the rows the legacy table must show, derived from the matrix.

    One row per linux dynamic-module entry.  The table must not invent a
    libc or architecture pair that the matrix does not carry: a version that
    ships only one architecture would otherwise claim four.
    """
    seen: dict[tuple[str, str, str], str] = {}
    for entry in matrix["entries"]:
        if entry.get("artifact_type") != _LEGACY_ARTIFACT:
            continue
        if entry.get("os") != _LEGACY_OS:
            continue
        version = entry.get("nginx_version")
        libc = entry.get("libc")
        arch = _LEGACY_ARCH.get(str(entry.get("arch")))
        tier = entry.get("support_tier")
        if not isinstance(version, str) or arch is None or not isinstance(tier, str):
            continue
        key = (version, str(libc), arch)
        if key in seen:
            raise ValueError(f"duplicate linux module row for {key}")
        seen[key] = tier

    return [
        {"nginx": version, "os_type": libc, "arch": arch, "support_tier": tier}
        for (version, libc, arch), tier in seen.items()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    try:
        matrix_path = validate_read_path(MATRIX_PATH, purpose="release matrix")
        doc_path = validate_read_path(DOC_PATH, purpose="installation guide")
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    import json

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    try:
        rows = legacy_entries(matrix)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    original = doc_path.read_text(encoding="utf-8")
    if DOC_MARKER_BEGIN not in original or DOC_MARKER_END not in original:
        print(f"FAIL: the auto-generated block is missing from {DOC_PATH}", file=sys.stderr)
        return 1

    updated = update_doc_table(doc_path, rows)

    if args.check:
        if updated != original:
            print(
                "FAIL: the auto-generated platform table is stale; run without --check",
                file=sys.stderr,
            )
            return 2
        print("PASS: the auto-generated platform table matches the matrix")
        return 0

    if updated == original:
        print("PASS: the auto-generated platform table already matches the matrix")
        return 0

    target = validate_write_path_within_root(DOC_PATH, root=REPO_ROOT)
    target.write_text(updated, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
