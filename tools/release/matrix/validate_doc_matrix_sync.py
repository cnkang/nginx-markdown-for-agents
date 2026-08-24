#!/usr/bin/env python3
"""Validate that the compatibility matrix in INSTALLATION.md is in sync with release-matrix.json.

Parses the "Platform Compatibility Matrix" markdown table from the installation
guide and compares it against the canonical release-matrix.json definition.

Exit code 0 = in sync, exit code 1 = out of sync or parse error.

Usage:
    python3 tools/release/matrix/validate_doc_matrix_sync.py
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.path_validation import validate_read_path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_matrix import (  # noqa: E402
    canonical_arch,
    normalize_compatibility_document,
)

# Paths relative to the repository root
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
DOC_PATH = REPO_ROOT / "docs" / "guides" / "INSTALLATION.md"


def _normalize_tier(tier: str) -> str:
    """
    Normalize a support tier string to a canonical form.

    In addition to trimming, lowercasing, and replacing spaces and
    hyphens with underscores, this function maps the presentation labels
    that INSTALLATION.md retains for historical reasons to their canonical
    matrix tier: ``supported`` maps to ``full`` and ``best_effort`` maps
    to ``source_only``.

    Returns:
        normalized_tier (str): The input string trimmed, lowercased, with spaces and hyphens replaced by underscores.
    """
    normalized = tier.strip().lower().replace(" ", "_").replace("-", "_")
    # INSTALLATION.md retains historical display labels while the canonical
    # release matrix uses machine-facing tier names. Compare the vocabularies
    # at this presentation boundary instead of reintroducing legacy matrix
    # values into the source of truth.
    if normalized == "supported":
        return "full"
    if normalized == "best_effort":
        return "source_only"
    return normalized


def _normalize_target(target: str) -> str:
    """Normalize a target to canonical architecture identity.

    Delegates to the shared canonical_arch helper in normalize_matrix.py
    so the doc-sync validator and the matrix normalizer never disagree.
    """
    return canonical_arch(target)


def _is_supported_dynamic_entry(item: dict) -> bool:
    """Return whether an entry represents a supported packaged platform."""
    return (
        item.get("artifact_type") == "dynamic-module"
        and item.get("support_tier") == "supported"
        and item.get("libc") in {"glibc", "musl"}
        and canonical_arch(item.get("target", "")) in {"x86_64", "aarch64"}
    )


def _covered_versions(data: dict) -> set[str]:
    """Return versions that already have supported packaged platforms."""
    return {
        item["nginx_version"]
        for item in data.get("entries", [])
        if _is_supported_dynamic_entry(item)
    }


def _is_required_source_fallback(
    item: dict, covered_versions: set[str]
) -> bool:
    """Return whether an uncovered version needs its source fallback row."""
    return (
        item.get("artifact_type") == "source"
        and item.get("support_tier") == "best-effort"
        and item.get("libc") == "n/a"
        and item.get("target") == "any"
        and item.get("nginx_version") not in covered_versions
    )


def load_matrix_entries(path: Path) -> list[tuple[str, str, str, str]]:
    """
    Load matrix entries from a release-matrix JSON file and normalize their support tier.

    Parameters:
        path (Path): Path to the release-matrix.json file.

    Returns:
        list[tuple[str, str, str, str]]: Sorted list of (nginx, os_type, arch, tier) tuples where `tier` has been normalized: trimmed, lowercased, spaces/hyphens replaced with underscores, and the supported/full and best_effort/source_only mappings applied.
    """
    validated = validate_read_path(path, purpose="doc matrix")
    with open(validated, "r", encoding="utf-8") as f:
        data = normalize_compatibility_document(json.load(f))

    covered_versions = _covered_versions(data)
    entries = []
    entries.extend(
        (
            item["nginx_version"],
            item["libc"],
            _normalize_target(item["target"]),
            _normalize_tier(item["support_tier"]),
        )
        for item in data.get("entries", [])
        if _is_supported_dynamic_entry(item)
        or _is_required_source_fallback(item, covered_versions)
    )
    return sorted(entries)


def _is_table_header_or_separator(nginx: str) -> bool:
    """
    Determine whether a markdown table cell in the "nginx" column is a header or a separator row.
    
    Returns:
        `true` if the cell equals "nginx version" (case-insensitive), contains only dashes, or starts with "-", `false` otherwise.
    """
    return (
        nginx.lower() == "nginx version"
        or not set(nginx.replace("-", ""))
        or nginx.startswith("-")
    )


def _parse_table_row(line: str) -> tuple[str, str, str, str] | None:
    """
    Parse a markdown table row into a 4-tuple of its cells.
    
    Returns:
        tuple[str, str, str, str] | None: A 4-tuple (nginx, os_type, arch, tier) of trimmed cell strings if `line` is a markdown table row with exactly four non-empty cells (must start and end with `|`); `None` otherwise.
    """
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None

    cells = [cell.strip() for cell in stripped[1:-1].split("|")]
    return None if len(cells) != 4 or "" in cells else tuple(cells)


def _normalize_doc_matrix_row(
    row: tuple[str, str, str, str],
) -> tuple[str, str, str, str] | None:
    """Normalize one parsed documentation row, ignoring table scaffolding."""
    nginx, os_type, arch, tier = row
    if _is_table_header_or_separator(nginx):
        return None

    normalized_tier = _normalize_tier(tier)
    if (
        normalized_tier == "source_only"
        and os_type.lower() == "unlisted"
        and arch.lower() == "unlisted"
    ):
        # Human-facing docs avoid implying that the source fallback is a
        # real platform. Compare its canonical n/a/any identity instead.
        os_type, arch = "n/a", "any"

    return nginx, os_type, arch, normalized_tier


def _parse_doc_matrix_entries(
    content: str,
) -> list[tuple[str, str, str, str]]:
    """Parse matrix rows from the document content."""
    entries = []
    in_matrix_section = False

    for line in content.splitlines():
        # Detect the start of the Platform Compatibility Matrix section.
        if "Platform Compatibility Matrix" in line and line.strip().startswith("#"):
            in_matrix_section = True
            continue

        # Stop at the next heading after the matrix section.
        if in_matrix_section and line.strip().startswith("#") and "Platform Compatibility Matrix" not in line:
            break

        if not in_matrix_section:
            continue

        row = _parse_table_row(line)
        if row is None:
            continue

        normalized = _normalize_doc_matrix_row(row)
        if normalized is not None:
            entries.append(normalized)

    return entries


def parse_doc_matrix(path: Path) -> list[tuple[str, str, str, str]]:
    """
    Extract the Platform Compatibility Matrix entries from INSTALLATION.md.

    Parses the markdown table under the "Platform Compatibility Matrix" heading,
    skipping table header and separator rows, and normalizes the `tier` value to
    a canonical form.

    Parameters:
        path (Path): Path to the INSTALLATION.md file to parse.

    Returns:
        list[tuple[str, str, str, str]]: Sorted list of (nginx, os_type, arch, tier) tuples where `tier` is normalized (lowercased; spaces and hyphens replaced with underscores).
    """
    content = path.read_text(encoding="utf-8")

    return sorted(_parse_doc_matrix_entries(content))


def compare_matrices(
    json_entries: list[tuple[str, str, str, str]],
    doc_entries: list[tuple[str, str, str, str]],
) -> list[str]:
    """
    Compare release matrix entries from the canonical JSON and the documentation and report any discrepancies.
    
    Parameters:
        json_entries (list[tuple[str, str, str, str]]): Entries loaded from release-matrix.json as (nginx, os_type, arch, tier).
        doc_entries (list[tuple[str, str, str, str]]): Entries parsed from the documentation as (nginx, os_type, arch, tier).
    
    Returns:
        diffs (list[str]): A list of human-readable difference messages. Each message describes either an entry present only in JSON, an entry present only in the docs, or a tier mismatch for an entry present in both (formats include:
          - "In JSON but missing from doc: nginx=... os_type=... arch=... tier=..."
          - "In doc but missing from JSON: nginx=... os_type=... arch=... tier=..."
          - "Tier mismatch for nginx=... os_type=... arch=...: JSON=..., doc=...").
    """
    diffs: list[str] = []

    # Build lookup dicts keyed by (nginx, os_type, arch) for tier mismatch detection
    json_by_key = {(n, o, a): t for n, o, a, t in json_entries}
    doc_by_key = {(n, o, a): t for n, o, a, t in doc_entries}

    # Entries in JSON but not in doc (considering tier mismatches separately)
    json_keys = set(json_by_key.keys())
    doc_keys = set(doc_by_key.keys())

    only_in_json = json_keys - doc_keys
    only_in_doc = doc_keys - json_keys
    common_keys = json_keys & doc_keys

    for key in sorted(only_in_json):
        nginx, os_type, arch = key
        tier = json_by_key[key]
        diffs.append(
            f"In JSON but missing from doc: "
            f"nginx={nginx} os_type={os_type} arch={arch} tier={tier}"
        )

    for key in sorted(only_in_doc):
        nginx, os_type, arch = key
        tier = doc_by_key[key]
        diffs.append(
            f"In doc but missing from JSON: "
            f"nginx={nginx} os_type={os_type} arch={arch} tier={tier}"
        )

    for key in sorted(common_keys):
        json_tier = json_by_key[key]
        doc_tier = doc_by_key[key]
        if json_tier != doc_tier:
            nginx, os_type, arch = key
            diffs.append(
                f"Tier mismatch for nginx={nginx} os_type={os_type} arch={arch}: "
                f"JSON={json_tier}, doc={doc_tier}"
            )

    return diffs


def main() -> int:
    """
    Validate that INSTALLATION.md's "Platform Compatibility Matrix" matches release-matrix.json.
    
    Reads the canonical matrix and the documentation matrix, prints errors or a list of differences to stderr when mismatches or file/parse problems occur, and prints a success summary when they match.
    
    Returns:
        int: `0` if the matrices are in sync, `1` if they are out of sync or a required file/parse error occurred.
    """
    if not MATRIX_PATH.exists():
        print(f"ERROR: Matrix file not found: {MATRIX_PATH}", file=sys.stderr)
        return 1

    if not DOC_PATH.exists():
        print(f"ERROR: Documentation file not found: {DOC_PATH}", file=sys.stderr)
        return 1

    try:
        json_entries = load_matrix_entries(MATRIX_PATH)
    except (OSError, ValueError) as exc:
        print(f"ERROR: unable to load matrix {MATRIX_PATH}: {exc}", file=sys.stderr)
        return 1
    doc_entries = parse_doc_matrix(DOC_PATH)

    if not doc_entries:
        print(
            "ERROR: No matrix entries found in INSTALLATION.md. "
            "Is the 'Platform Compatibility Matrix' section present?",
            file=sys.stderr,
        )
        return 1

    if diffs := compare_matrices(json_entries, doc_entries):
        print(
            "Sync check FAILED — INSTALLATION.md matrix is out of sync "
            "with release-matrix.json:",
            file=sys.stderr,
        )
        for i, diff in enumerate(diffs, 1):
            print(f"  {i}. {diff}", file=sys.stderr)
        print(
            f"\nJSON entries: {len(json_entries)}, Doc entries: {len(doc_entries)}",
            file=sys.stderr,
        )
        return 1

    print("Sync check PASSED.")
    print(f"  JSON entries: {len(json_entries)}")
    print(f"  Doc entries:  {len(doc_entries)}")
    print("  INSTALLATION.md matrix matches release-matrix.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
