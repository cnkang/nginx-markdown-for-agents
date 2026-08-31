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
import sys
from pathlib import Path
from typing import List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import validate_read_path
from tools.release.matrix.normalize_matrix import (  # noqa: E402
    MatrixNormalizationError,
    canonical_arch,
    normalize_compatibility_document,
    normalize_compatibility_entry,
)


# Artifact naming convention:
#   ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz
ARTIFACT_TEMPLATE = (
    "ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz"
)
REQUIRED_ENTRY_KEYS = ("nginx", "os_type", "arch")
RELEASE_BINARIES_WORKFLOW = ".github/workflows/release-binaries.yml"


def _normalize_arch(arch: str) -> str:
    """Normalize release-matrix architecture names to binary artifact names."""
    return canonical_arch(arch)


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


def _load_current_matrix_entries(entries: list[object]) -> List[dict]:
    """Select supported dynamic-module entries from the current schema."""
    qualifying: List[dict] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = normalize_compatibility_entry(raw_entry, require_fields=False)
        if raw_entry.get("owner_workflow") != RELEASE_BINARIES_WORKFLOW:
            continue
        if entry.get("support_tier") != "supported":
            continue
        if entry.get("artifact_type") != "dynamic-module":
            continue
        if entry.get("libc") not in {"glibc", "musl"}:
            continue

        nginx = entry.get("nginx_version")
        raw_arch = entry.get("target")
        arch = entry.get("target")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (nginx, entry.get("libc"), raw_arch, arch)
        ):
            continue
        qualifying.append(
            {
                "nginx": nginx,
                "os_type": entry["libc"],
                "arch": _normalize_arch(raw_arch),
                "support_tier": entry["support_tier"],
            }
        )
    return qualifying


def _load_legacy_matrix_entries(entries: list[object]) -> List[dict]:
    """Select full-tier entries from the legacy matrix schema."""
    qualifying: List[dict] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = normalize_compatibility_entry(raw_entry, require_fields=False)
        if entry.get("support_tier") != "supported":
            continue
        nginx = entry.get("nginx_version")
        os_type = entry.get("libc")
        raw_arch = entry.get("target", "")
        arch = entry.get("target", "")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (nginx, os_type, raw_arch, arch)
        ):
            continue
        qualifying.append(
            {
                "nginx": nginx,
                "os_type": os_type,
                "arch": _normalize_arch(raw_arch),
                "support_tier": entry["support_tier"],
            }
        )
    return qualifying


def load_matrix(matrix_path: str) -> List[dict]:
    """
    Load qualifying release-binary entries from a release matrix JSON file.

    Parameters:
        matrix_path (str): Filesystem path to the release matrix JSON file.

    Returns:
        List[dict]: Qualifying entries from the current or legacy matrix schema,
        or an empty list when the file has no supported matrix structure.

    Raises:
        MatrixNormalizationError: When the matrix document is not a JSON
            object, or when normalize_compatibility_document reports an
            unsupported or invalid matrix structure.
    """
    resolved = validate_read_path(matrix_path, purpose="release matrix")
    data = json.loads(resolved.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise MatrixNormalizationError("matrix document must be an object")

    current_schema = "entries" in data
    normalized = normalize_compatibility_document(data)
    entries = normalized["entries"]
    if current_schema:
        return _load_current_matrix_entries(entries)
    return _load_legacy_matrix_entries(entries)


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
    Check whether all expected release artifacts are present for qualifying matrix entries.
    
    Parameters:
        argv (List[str] | None): Command-line arguments to parse; uses sys.argv[1:] when None.
    
    Returns:
        int: 0 when all expected artifacts are present; 1 when no qualifying entries exist or artifacts are missing.
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

    try:
        matrix_entries = load_matrix(args.matrix)
    except (OSError, UnicodeError, json.JSONDecodeError,
            MatrixNormalizationError) as exc:
        print(f"ERROR: invalid release matrix: {exc}", file=sys.stderr)
        return 1
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
