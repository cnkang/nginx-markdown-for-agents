#!/usr/bin/env python3
"""Single normalization entry point for release-matrix consumers (Spec 62
Task 8.12c / Rule 62).

All matrix consumers (loader, validation, sort, diff) MUST resolve aliased
keys through this module so `nginx`/`nginx_version`, `os`/`os_type`, and
`arch`/`target` never disagree.

Fail-closed semantics:
- top-level `matrix` present together with `entries` → error
- unknown top-level keys → error
- an entry carrying both a canonical key and its legacy alias with
  different values → error
- an entry that resolves to no canonical identity → error

The canonical key set is frozen: nginx_version, os, libc, target,
artifact_type, feature_manifest_digest, abi_version.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Dict, List

# Repository path-validation helper (AGENTS.md Rule 33): CLI-derived paths
# must be validated before use.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "tools"))
from lib.path_validation import validate_read_path  # noqa: E402

CANONICAL_ENTRY_KEYS = [
    "nginx_version",
    "os",
    "libc",
    "target",
    "artifact_type",
    "feature_manifest_digest",
    "abi_version",
]

# Legacy aliases: alias -> canonical key.
LEGACY_ALIASES = {
    "nginx": "nginx_version",
    "os_type": "os",
    "arch": "target",
}

# Top-level legacy alias for the entries array.
LEGACY_TOP_LEVEL_ALIAS = "matrix"

# Keys present in the legacy data files that are not canonical and have no
# canonical alias; they are dropped during normalization (documented legacy
# metadata only, never used for identity).
DROPPED_LEGACY_KEYS = frozenset(
    {
        "nginx_channel",
        "test_level",
        "support_tier",
        "release_blocking",
        "owner_workflow",
        "managed_by",
    }
)


class MatrixNormalizationError(ValueError):
    """Raised when a matrix document fails the fail-closed normalization."""


def normalize_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a top-level matrix document to the canonical shape.

    Returns `{"schema_version": 1, "entries": [...]}` with every entry
    expressed in canonical keys.
    """
    if not isinstance(doc, dict):
        raise MatrixNormalizationError("matrix document must be an object")

    unknown = (
        set(doc.keys())
        - {"schema_version", "entries", LEGACY_TOP_LEVEL_ALIAS, "updated_at"}
    )
    if unknown:
        raise MatrixNormalizationError(
            f"unknown top-level keys: {sorted(unknown)}"
        )

    if "entries" in doc and LEGACY_TOP_LEVEL_ALIAS in doc:
        raise MatrixNormalizationError(
            "both 'entries' and legacy 'matrix' present: fail closed"
        )

    raw_entries = doc.get("entries")
    if raw_entries is None:
        raw_entries = doc.get(LEGACY_TOP_LEVEL_ALIAS)
    if raw_entries is None:
        raise MatrixNormalizationError("matrix document has no entries")

    if not isinstance(raw_entries, list):
        raise MatrixNormalizationError("entries must be an array")

    entries = [normalize_entry(entry) for entry in raw_entries]

    return {"schema_version": 1, "entries": entries}


def normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a single matrix row to canonical keys."""
    if not isinstance(entry, dict):
        raise MatrixNormalizationError("matrix entry must be an object")

    canonical = _fold_entry_keys(entry)

    for required, alias in (
        ("nginx_version", "nginx"),
        ("os", "os_type"),
        ("libc", None),
        ("target", "arch"),
        ("artifact_type", None),
    ):
        if required not in canonical:
            hint = f" (or {alias} alias)" if alias else ""
            raise MatrixNormalizationError(
                f"entry has no {required}{hint}"
            )

    return canonical


def _fold_entry_keys(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Fold aliases into canonical keys, failing closed on disagreement."""
    canonical: Dict[str, Any] = {}
    for key, value in entry.items():
        if key in CANONICAL_ENTRY_KEYS:
            canonical[key] = value
        elif key in LEGACY_ALIASES:
            canonical_key = LEGACY_ALIASES[key]
            if canonical_key in canonical and canonical[canonical_key] != value:
                raise MatrixNormalizationError(
                    f"alias {key!r} disagrees with canonical key {canonical_key!r}"
                )
            canonical[canonical_key] = value
        elif key in DROPPED_LEGACY_KEYS:
            continue
        else:
            raise MatrixNormalizationError(f"unknown matrix entry key: {key!r}")
    return canonical


def load_and_normalize(path: str) -> Dict[str, Any]:
    """Load a matrix file and normalize it; exits 1 on fail-closed errors."""
    validated = validate_read_path(path, purpose="release-matrix normalization")
    try:
        with open(validated, encoding="utf-8") as handle:
            doc = json.load(handle)
        return normalize_document(doc)
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixNormalizationError(
            f"cannot read matrix file {path}: {exc}"
        ) from exc


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(
            f"usage: {argv[0]} <matrix.json | -> — normalize and print the canonical "
            "document (fail closed on any alias/canonical disagreement); '-' reads "
            "the document from stdin",
            file=sys.stderr,
        )
        return 2
    try:
        if argv[1] == "-":
            doc = json.load(sys.stdin)
            normalized = normalize_document(doc)
        else:
            normalized = load_and_normalize(argv[1])
    except MatrixNormalizationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    json.dump(normalized, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
