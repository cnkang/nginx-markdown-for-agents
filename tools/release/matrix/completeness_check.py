#!/usr/bin/env python3
"""Release matrix completeness check.

Compares the release matrix definition (release-matrix.json) against actual
build artifacts and reports any missing combinations. Exits with code 1 when
artifacts are missing, printing the missing combinations to stderr.

Usage:
    python3 tools/release/matrix/completeness_check.py \
        --matrix tools/release-matrix.json \
        --artifacts <artifact-dir-or-list>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Set, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.path_validation import validate_read_path


# Artifact naming convention:
#   ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz
ARTIFACT_TEMPLATE = (
    "ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz"
)
REQUIRED_ENTRY_KEYS = ("nginx", "os_type", "arch")
RELEASE_BINARIES_WORKFLOW = ".github/workflows/release-binaries.yml"


def _normalize_arch(arch: str) -> str:
    """Normalize release-matrix architecture names to binary artifact names."""
    return {
        "amd64": "x86_64",
        "arm64": "aarch64",
    }.get(arch, arch)


def _require_entry_keys(entry: dict, *, context: str) -> None:
    """
    Ensure a matrix entry contains all required keys.
    
    Parameters:
        entry (dict): Matrix entry to validate.
        context (str): Context label included in the KeyError message when keys are missing.
    
    Raises:
        KeyError: If any required keys are missing; the message includes the provided context and the missing key names.
    """
    if missing_keys := [
        key for key in REQUIRED_ENTRY_KEYS if key not in entry
    ]:
        raise KeyError(f"{context} missing required keys: {', '.join(missing_keys)}")


def load_matrix(matrix_path: str) -> List[dict]:
    """
    Load release-binary entries from a release matrix JSON file.
    
    Parameters:
        matrix_path (str): Filesystem path to release-matrix.json. The current
            schema uses top-level "entries"; the legacy schema used "matrix".
    
    Returns:
        List[dict]: Normalized entries with "nginx", "os_type", and "arch"
        keys matching release binary artifact filenames.
    """
    resolved = validate_read_path(matrix_path, purpose="release matrix")
    data = json.loads(resolved.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        return []

    if isinstance(data.get("entries"), list):
        return [
            {
                "nginx": entry["nginx_version"],
                "os_type": entry["libc"],
                "arch": _normalize_arch(entry["arch"]),
                "support_tier": entry["support_tier"],
            }
            for entry in data["entries"]
            if entry.get("owner_workflow") == RELEASE_BINARIES_WORKFLOW
            and entry.get("support_tier") == "supported"
            and entry.get("artifact_type") == "dynamic-module"
            and entry.get("libc") in {"glibc", "musl"}
        ]

    if not isinstance(data.get("matrix"), list):
        return []

    return [
        entry for entry in data["matrix"]
        if entry.get("support_tier") == "full"
    ]


def expected_artifact_name(entry: dict) -> str:
    """
    Compute the expected artifact filename for a release matrix entry.
    
    Parameters:
        entry (dict): A matrix entry mapping that must contain the keys `nginx`, `os_type`, and `arch`.
    
    Returns:
        str: The expected artifact filename, e.g. "ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz".
    
    Raises:
        KeyError: If any of `nginx`, `os_type`, or `arch` are missing from `entry`.
    """
    _require_entry_keys(entry, context="Entry")
    return ARTIFACT_TEMPLATE.format(
        nginx=entry["nginx"],
        os_type=entry["os_type"],
        arch=entry["arch"],
    )


def collect_artifacts(artifacts_path: str) -> Set[str]:
    """
    Gather artifact filenames from a directory or a newline-separated file list.
    
    If `artifacts_path` is a directory, returns the non-recursive set of filenames
    inside that end with `.tar.gz`. If it is a file, reads it as a UTF-8,
    newline-separated list; lines are trimmed and empty lines are ignored.
    
    Parameters:
        artifacts_path (str): Path to a directory containing `.tar.gz` files or to a
            text file listing artifact filenames, one per line.
    
    Returns:
        Set[str]: A set of artifact filenames found.
    """
    resolved = validate_read_path(str(Path(artifacts_path)), purpose="artifacts list")
    path = Path(resolved)

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
    """
    Identify which matrix entries do not have corresponding artifact files.
    
    Parameters:
        matrix_entries (List[dict]): Matrix entries to validate; each entry must contain the keys required by expected_artifact_name().
        actual_artifacts (Set[str]): Set of artifact filenames that are present.
    
    Returns:
        missing (List[Tuple[dict, str]]): List of (entry, expected_filename) pairs for entries whose expected artifact name is not found in `actual_artifacts`.
    """
    missing = []
    for entry in matrix_entries:
        name = expected_artifact_name(entry)
        if name not in actual_artifacts:
            missing.append((entry, name))
    return missing


def format_missing(missing: List[Tuple[dict, str]]) -> str:
    """
    Create a human-readable report of missing artifacts.
    
    Parameters:
        missing (List[Tuple[dict, str]]): List of (matrix_entry, expected_filename) pairs where
            matrix_entry contains at least the keys `nginx`, `os_type`, and `arch`.
    
    Returns:
        report (str): Multi-line string that begins with "Missing N artifact(s):" and includes one
        line per missing artifact formatted as:
        "  - {filename}  (nginx={nginx} os={os_type} arch={arch})".
    """
    lines = [f"Missing {len(missing)} artifact(s):"]
    lines.extend(
        f"  - {filename}  (nginx={entry['nginx']} os={entry['os_type']} arch={entry['arch']})"
        for entry, filename in missing
    )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    """
    Run the completeness check using the provided command-line arguments and return a process-style exit code.
    
    Parses --matrix and --artifacts from argv, loads the matrix entries with support_tier "full", compares expected artifact filenames against the actual artifacts found, and returns an exit code reflecting the result.
    
    Parameters:
        argv (List[str] | None): Command-line arguments to parse; if None, sys.argv[1:] is used.
    
    Returns:
        int: 0 if all expected artifacts are present, 1 if any artifacts are missing or if no qualifying matrix entries are found.
    """
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
    if not matrix_entries:
        print(
            "ERROR: No release-binaries entries found in matrix. The matrix "
            "may be empty, malformed, or missing supported dynamic-module "
            "entries owned by .github/workflows/release-binaries.yml.",
            file=sys.stderr,
        )
        return 1

    actual_artifacts = collect_artifacts(args.artifacts)
    if missing := check_completeness(matrix_entries, actual_artifacts):
        print(format_missing(missing), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
