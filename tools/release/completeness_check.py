#!/usr/bin/env python3
"""Release matrix completeness check.

Compares the release matrix definition (release-matrix.json) against actual
build artifacts and reports any missing combinations. Exits with code 1 when
artifacts are missing, printing the missing combinations to stderr.

Usage:
    python3 tools/release/completeness_check.py \
        --matrix tools/release-matrix.json \
        --artifacts <artifact-dir-or-list>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Set, Tuple


# Artifact naming convention:
#   ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz
ARTIFACT_TEMPLATE = (
    "ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz"
)
REQUIRED_ENTRY_KEYS = ("nginx", "os_type", "arch")


def _require_entry_keys(entry: dict, *, context: str) -> None:
    """Raise ``KeyError`` when *entry* omits required matrix keys."""
    missing_keys = [key for key in REQUIRED_ENTRY_KEYS if key not in entry]
    if missing_keys:
        raise KeyError(f"{context} missing required keys: {', '.join(missing_keys)}")


def load_matrix(matrix_path: str) -> List[dict]:
    """Load the release matrix JSON and return entries with support_tier 'full'."""
    with open(matrix_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return [
        entry for entry in data.get("matrix", [])
        if entry.get("support_tier") == "full"
    ]


def expected_artifact_name(entry: dict) -> str:
    """Build the expected artifact filename from a matrix entry."""
    _require_entry_keys(entry, context="Entry")
    return ARTIFACT_TEMPLATE.format(
        nginx=entry["nginx"],
        os_type=entry["os_type"],
        arch=entry["arch"],
    )


def collect_artifacts(artifacts_path: str) -> Set[str]:
    """Collect artifact filenames from a directory or a file list.

    If *artifacts_path* is a directory, all ``*.tar.gz`` filenames inside it
    (non-recursive) are returned.  Otherwise the file is read as a text list
    with one filename per line.
    """
    path = Path(artifacts_path)

    if path.is_dir():
        return {
            f.name for f in path.iterdir()
            if f.is_file() and f.name.endswith(".tar.gz")
        }

    # Treat as a file list (one filename per line)
    with open(path, "r", encoding="utf-8") as f:
        return {
            line.strip() for line in f if line.strip()
        }


def check_completeness(
    matrix_entries: List[dict],
    actual_artifacts: Set[str],
) -> List[Tuple[dict, str]]:
    """Return a list of (entry, expected_filename) for missing artifacts."""
    missing = []
    for entry in matrix_entries:
        name = expected_artifact_name(entry)
        if name not in actual_artifacts:
            missing.append((entry, name))
    return missing


def format_missing(missing: List[Tuple[dict, str]]) -> str:
    """Format missing artifact details for stderr output."""
    lines = [f"Missing {len(missing)} artifact(s):"]
    for entry, filename in missing:
        lines.append(
            f"  - {filename}  "
            f"(nginx={entry['nginx']} os={entry['os_type']} arch={entry['arch']})"
        )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    """Entry point. Returns 0 when complete, 1 when artifacts are missing."""
    parser = argparse.ArgumentParser(
        description="Check release artifact completeness against the matrix."
    )
    parser.add_argument(
        "--matrix",
        required=True,
        help="Path to release-matrix.json",
    )
    parser.add_argument(
        "--artifacts",
        required=True,
        help="Path to artifact directory or file list (one filename per line)",
    )
    args = parser.parse_args(argv)

    matrix_entries = load_matrix(args.matrix)
    actual_artifacts = collect_artifacts(args.artifacts)
    missing = check_completeness(matrix_entries, actual_artifacts)

    if missing:
        print(format_missing(missing), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
