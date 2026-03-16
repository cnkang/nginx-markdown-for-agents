#!/usr/bin/env python3
"""Validate that the compatibility matrix in INSTALLATION.md is in sync with release-matrix.json.

Parses the "Platform Compatibility Matrix" markdown table from the installation
guide and compares it against the canonical release-matrix.json definition.

Exit code 0 = in sync, exit code 1 = out of sync or parse error.

Usage:
    python3 tools/release/validate_doc_matrix_sync.py
"""

import json
import re
import sys
from pathlib import Path

# Paths relative to the repository root
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
DOC_PATH = REPO_ROOT / "docs" / "guides" / "INSTALLATION.md"


def load_matrix_entries(path: Path) -> list[tuple[str, str, str, str]]:
    """Load release-matrix.json and return sorted list of (nginx, os_type, arch, tier) tuples.

    The support_tier value is normalized to lowercase for comparison.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = []
    for item in data.get("matrix", []):
        entries.append((
            item["nginx"],
            item["os_type"],
            item["arch"],
            item["support_tier"].lower(),
        ))
    return sorted(entries)


def _is_table_header_or_separator(nginx: str) -> bool:
    """Return True if *nginx* cell value indicates a header or separator row."""
    return nginx.lower() == "nginx version" or set(nginx.replace("-", "")) == set() or nginx.startswith("-")


def parse_doc_matrix(path: Path) -> list[tuple[str, str, str, str]]:
    """Parse the Platform Compatibility Matrix table from INSTALLATION.md.

    Looks for lines matching: | <version> | <os_type> | <arch> | <tier> |
    Skips the header row and separator lines.

    Returns sorted list of (nginx, os_type, arch, tier) tuples with tier lowercased.
    """
    content = path.read_text(encoding="utf-8")

    # Match table rows: | value | value | value | value |
    # Skip header and separator lines (containing dashes only in cells)
    # Uses greedy [^|]+ to avoid backtracking between lazy quantifier and \s*
    row_pattern = re.compile(
        r"^\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|$"
    )

    entries = []
    in_matrix_section = False

    for line in content.splitlines():
        # Detect the start of the Platform Compatibility Matrix section
        if "Platform Compatibility Matrix" in line and line.strip().startswith("#"):
            in_matrix_section = True
            continue

        # Stop at the next heading after the matrix section
        if in_matrix_section and line.strip().startswith("#") and "Platform Compatibility Matrix" not in line:
            break

        if not in_matrix_section:
            continue

        match = row_pattern.match(line.strip())
        if not match:
            continue

        nginx, os_type, arch, tier = (g.strip() for g in match.groups())

        if _is_table_header_or_separator(nginx):
            continue

        entries.append((nginx, os_type, arch, tier.lower()))

    return sorted(entries)


def compare_matrices(
    json_entries: list[tuple[str, str, str, str]],
    doc_entries: list[tuple[str, str, str, str]],
) -> list[str]:
    """Compare JSON and doc matrix entries. Returns a list of difference descriptions."""
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
    """Entry point. Returns 0 if in sync, 1 if out of sync."""
    if not MATRIX_PATH.exists():
        print(f"ERROR: Matrix file not found: {MATRIX_PATH}", file=sys.stderr)
        return 1

    if not DOC_PATH.exists():
        print(f"ERROR: Documentation file not found: {DOC_PATH}", file=sys.stderr)
        return 1

    json_entries = load_matrix_entries(MATRIX_PATH)
    doc_entries = parse_doc_matrix(DOC_PATH)

    if not doc_entries:
        print(
            "ERROR: No matrix entries found in INSTALLATION.md. "
            "Is the 'Platform Compatibility Matrix' section present?",
            file=sys.stderr,
        )
        return 1

    diffs = compare_matrices(json_entries, doc_entries)

    if diffs:
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
