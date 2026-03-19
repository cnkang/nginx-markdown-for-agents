#!/usr/bin/env python3
"""Automated nginx release matrix updater.

Scrapes nginx.org for current supported versions, computes the desired
matrix state (respecting ``managed_by: manual`` Pin_Entries), and updates
both ``tools/release-matrix.json`` and the Platform Compatibility Matrix
table in ``docs/guides/INSTALLATION.md``.  A machine-readable diff summary
is written to ``matrix-diff.json`` when changes are detected, for
consumption by the GitHub Actions workflow.

Exit codes:
    0 — Success (no changes needed, or changes written successfully)
    1 — Error (network failure, parse failure, invalid JSON, missing markers)
    2 — Stale matrix detected (only in ``--check-only`` mode)

Usage:
    python3 tools/release/update_matrix.py [--dry-run] [--check-only]
"""



import argparse
import contextlib
import itertools
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Path constants (same pattern as validate_doc_matrix_sync.py)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
INSTALL_SCRIPT_PATH = REPO_ROOT / "tools" / "install.sh"
DOC_PATH = REPO_ROOT / "docs" / "guides" / "INSTALLATION.md"
DIFF_PATH = MATRIX_PATH.parent / "matrix-diff.json"

# ---------------------------------------------------------------------------
# Scraping / version constants
# ---------------------------------------------------------------------------
NGINX_DOWNLOAD_URL = "https://nginx.org/en/download.html"

# Supported platform combinations
OS_TYPES = ["glibc", "musl"]
ARCHS = ["x86_64", "aarch64"]
SUPPORT_TIER = "full"

# ---------------------------------------------------------------------------
# Documentation markers
# ---------------------------------------------------------------------------
DOC_MARKER_BEGIN = "<!-- BEGIN AUTO-GENERATED MATRIX -->"
DOC_MARKER_END = "<!-- END AUTO-GENERATED MATRIX -->"
REQUIRED_MATRIX_ENTRY_KEYS = ("nginx", "os_type", "arch")


@dataclass
class MatrixDiff:
    """Result of comparing current vs desired auto-managed matrix entries."""

    added_versions: list[str]
    removed_versions: list[str]
    has_changes: bool


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def version_tuple(v: str) -> tuple[int, ...]:
    """
    Convert a dot-separated version string into a tuple of integer components.
    
    Parameters:
        v (str): Version string like "X.Y.Z" (e.g., "1.22.0").
    
    Returns:
        tuple[int, ...]: Integer components of the version suitable for numeric sorting/comparison.
    """
    return tuple(int(p) for p in v.split("."))


def classify_version(version: str) -> str:
    """
    Classify an NGINX dotted version string as a release track.
    
    Parameters:
        version (str): NGINX version in dotted form (e.g. "1.22.1").
    
    Returns:
        str: `"stable"` if the minor version is even, `"mainline"` if the minor version is odd.
    
    Raises:
        ValueError: If `version` is not in the expected dotted format or the minor component is not an integer.
    """
    parts = version.split(".")
    if len(parts) < 2:
        raise ValueError(f"Invalid nginx version format: {version}")

    try:
        minor = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"Invalid nginx version format: {version}") from exc

    return "stable" if minor % 2 == 0 else "mainline"


def _missing_required_keys(entry: dict, required_keys: tuple[str, ...]) -> list[str]:
    """
    List required keys that are missing from the given mapping, preserving the order of `required_keys`.
    
    Parameters:
        entry (dict): Mapping to check for required keys.
        required_keys (tuple[str, ...]): Sequence of keys to verify, in declaration order.
    
    Returns:
        list[str]: Missing keys from `required_keys`, in declaration order.
    """
    return [key for key in required_keys if key not in entry]


def _resolve_repo_write_path(path: Path) -> Path:
    """
    Resolve a filesystem path to its canonical absolute Path and ensure it is inside the repository root.
    
    Returns:
        Path: The resolved absolute path guaranteed to reside within the repository root.
    
    Raises:
        ValueError: If the resolved path would be outside the repository root.
    """
    resolved_path = os.path.realpath(path)
    repo_root = os.path.realpath(REPO_ROOT)
    if not resolved_path.startswith(repo_root + os.sep) and resolved_path != repo_root:
        raise ValueError(f"Refusing to write outside repository root: {path}")
    return Path(resolved_path)


def _write_repo_text(path: Path, content: str) -> None:
    """
    Write `content` to `path` within the repository using UTF-8 encoding.
    
    The target `path` is first validated to ensure it lies under the repository root; parent directories will be created if missing and the file will be written with mode 0o644.
    
    Parameters:
        path (Path): Destination path inside the repository.
        content (str): UTF-8 text to write to the file.
    
    Raises:
        ValueError: If `path` resolves outside the repository root.
    """
    safe_path = _resolve_repo_write_path(path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)

    # Anchor the final open to a trusted parent directory instead of passing
    # an arbitrary path string into a convenience helper.
    parent_fd = os.open(safe_path.parent, os.O_RDONLY)
    try:
        file_fd = os.open(
            safe_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o644,
            dir_fd=parent_fd,
        )
        with os.fdopen(file_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        os.close(parent_fd)


def read_min_version(install_script_path: Path) -> str:
    """
    Return the MIN_SUPPORTED_NGINX_VERSION value defined in the given install script.
    
    Parameters:
        install_script_path (Path): Path to the install.sh file to read.
    
    Returns:
        str: The version string found (e.g. "1.22.1").
    
    Raises:
        RuntimeError: If `MIN_SUPPORTED_NGINX_VERSION` cannot be found in the file.
    """
    text = install_script_path.read_text(encoding="utf-8")
    if match := re.search(r'MIN_SUPPORTED_NGINX_VERSION="([^"]+)"', text):
        return match[1]
    else:
        raise RuntimeError(
            f"Could not find MIN_SUPPORTED_NGINX_VERSION in {install_script_path}"
        )


def filter_versions(versions: list[str], min_version: str) -> list[str]:
    """
    Filter a list of nginx version strings to those greater than or equal to the provided minimum version.
    
    Parameters:
        versions (list[str]): Version strings (e.g., "1.22.1").
        min_version (str): Minimum allowed version string (inclusive).
    
    Returns:
        list[str]: Versions >= min_version, sorted in ascending order.
    """
    min_t = version_tuple(min_version)
    return sorted(
        {v for v in versions if version_tuple(v) >= min_t},
        key=version_tuple,
    )


# ---------------------------------------------------------------------------
# HTML fetching and parsing
# ---------------------------------------------------------------------------

def fetch_download_page(url: str) -> str:
    """
    Fetch the nginx download page HTML.
    
    @returns
        The page HTML as a string.
    
    @raises urllib.error.URLError
        On network errors or non-200 HTTP responses.
    """
    with urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_nginx_versions(html: str) -> list[str]:
    """
    Extract nginx version numbers from the provided download-page HTML.
    
    Parameters:
        html (str): HTML content of the nginx download page to scan.
    
    Returns:
        list[str]: Deduplicated list of version strings (format "X.Y.Z") found in download links,
        preserving the order of first appearance.
    """
    pattern = re.compile(r"/download/nginx-(\d+\.\d+\.\d+)\.tar\.gz")
    versions = pattern.findall(html)
    return list(dict.fromkeys(versions))


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------

def _validate_manual_entries(manual_entries: list[dict], path: Path) -> None:
    """
    Ensure no duplicate manual matrix entries with the same (nginx, os_type, arch) key.
    Also validates that each manual entry has a ``support_tier`` key.
    
    If one or more duplicate keys are present, prints an error message to stderr referencing the provided matrix file path and terminates the process with exit code 1.
    
    Parameters:
        manual_entries (list[dict]): Manual matrix entries (each must include 'nginx', 'os_type', and 'arch').
        path (Path): Path to the source matrix file; included in the error message when duplicates are found.
    """
    for i, entry in enumerate(manual_entries):
        if "support_tier" not in entry:
            print(
                f"Manual matrix entry at index {i} in {path} missing required key: support_tier",
                file=sys.stderr,
            )
            sys.exit(1)

    seen_keys: dict[tuple[str, str, str], int] = {}
    duplicates: list[tuple[str, str, str]] = []
    for entry in manual_entries:
        key = (entry["nginx"], entry["os_type"], entry["arch"])
        if key in seen_keys:
            duplicates.append(key)
        else:
            seen_keys[key] = 1

    if duplicates:
        dup_strs = [f"({n}, {o}, {a})" for n, o, a in duplicates]
        print(
            f"Duplicate manual entry keys in {path}: {', '.join(dup_strs)}",
            file=sys.stderr,
        )
        sys.exit(1)


def load_matrix(path: Path) -> tuple[dict, list[dict], list[dict]]:
    """
    Load and validate release-matrix.json and split its entries into auto-managed and manual lists.
    
    Reads the JSON at the provided path, verifies it is a dict containing a top-level "matrix" list, and validates each entry contains the required keys. On validation or I/O errors the function prints a descriptive message to stderr and exits with code 1.
    
    Returns:
        tuple[data, auto_entries, manual_entries]: 
            - data: The full parsed JSON dictionary (preserving keys such as "schema_version").
            - auto_entries: List of entry dicts where `managed_by` is absent or "auto".
            - manual_entries: List of entry dicts where `managed_by` == "manual".
    """
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict) or "matrix" not in data:
        print(f"Invalid matrix structure in {path}: missing 'matrix' key", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data["matrix"], list):
        print(f"Invalid matrix structure in {path}: 'matrix' must be a list", file=sys.stderr)
        sys.exit(1)

    auto_entries: list[dict] = []
    manual_entries: list[dict] = []

    for i, entry in enumerate(data["matrix"]):
        if not isinstance(entry, dict):
            print(
                f"Invalid matrix entry at index {i} in {path}: expected dict, got {type(entry).__name__}",
                file=sys.stderr,
            )
            sys.exit(1)
        if missing := _missing_required_keys(
            entry, REQUIRED_MATRIX_ENTRY_KEYS
        ):
            print(
                f"Matrix entry at index {i} in {path} missing required keys: {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(1)
        managed_by = entry.get("managed_by")
        if managed_by == "manual":
            manual_entries.append(entry)
        elif managed_by is None or managed_by == "auto":
            auto_entries.append(entry)
        else:
            print(
                f"Matrix entry at index {i} in {path} has unknown managed_by value: {managed_by!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    _validate_manual_entries(manual_entries, path)

    return data, auto_entries, manual_entries


# ---------------------------------------------------------------------------
# Matrix computation and merging
# ---------------------------------------------------------------------------


def _entry_sort_key(entry: dict) -> tuple[tuple[int, ...], str, str]:
    """
    Produce a sort key for a release matrix entry.
    
    Parameters:
        entry (dict): Matrix entry with keys "nginx" (version string), "os_type", and "arch".
    
    Returns:
        tuple: A three-element tuple (version_tuple, os_type, arch) where `version_tuple` is the numeric version tuple derived from `entry["nginx"]`, used to sort versions naturally, and `os_type` and `arch` are the entry's OS type and architecture strings.
    """
    return (version_tuple(entry["nginx"]), entry["os_type"], entry["arch"])


def compute_matrix(
    versions: list[str], os_types: list[str], archs: list[str]
) -> list[dict]:
    """
    Generate the full cross-product of auto-managed matrix entries with support_tier set to "full".
    
    Each returned entry is a dict containing the keys "nginx", "os_type", "arch", and "support_tier". The list is sorted by version (ascending), then by os_type (alphabetical), then by arch (alphabetical).
    
    Returns:
        list[dict]: A list of matrix entry dictionaries for every combination of the provided `versions`, `os_types`, and `archs`.
    """
    entries: list[dict] = []
    for v, os_type in itertools.product(versions, os_types):
        entries.extend(
            {
                "nginx": v,
                "os_type": os_type,
                "arch": arch,
                "support_tier": SUPPORT_TIER,
            }
            for arch in archs
        )
    entries.sort(key=_entry_sort_key)
    return entries


def merge_matrix(
    auto_entries: list[dict], manual_entries: list[dict]
) -> list[dict]:
    """
    Merge auto-generated matrix entries with manual Pin_Entries, giving manual entries precedence on key collisions.
    
    On a collision of the (nginx, os_type, arch) key, the manual entry is kept and the conflicting auto entry is dropped. The returned list is sorted by nginx version (ascending), then by os_type, then by arch.
    
    Returns:
        list[dict]: Merged matrix entries sorted as described.
    """
    manual_keys: set[tuple[str, str, str]] = {
        (e["nginx"], e["os_type"], e["arch"]) for e in manual_entries
    }

    # Keep only auto entries whose key does not collide with a manual entry
    merged: list[dict] = [
        e for e in auto_entries
        if (e["nginx"], e["os_type"], e["arch"]) not in manual_keys
    ]
    merged.extend(manual_entries)
    merged.sort(key=_entry_sort_key)
    return merged


def diff_matrix(
    current_auto: list[dict], desired_auto: list[dict]
) -> MatrixDiff:
    """
    Compute which NGINX versions were added or removed and whether any auto-managed entries changed.
    
    Produces version-level lists (sorted) suitable for human consumption and pull-request titles, and detects entry-level differences across `nginx`, `os_type`, `arch`, and `support_tier`.
    
    Returns:
        MatrixDiff: `added_versions` (list[str]) are versions present in `desired_auto` but not `current_auto`, `removed_versions` (list[str]) are versions present in `current_auto` but not `desired_auto`, and `has_changes` (bool) is `True` if any auto-managed entry differs between the two sets, `False` otherwise.
    """

    def _entry_key(e: dict) -> tuple[str, str, str, str]:
        """
        Compute a stable sort key for a matrix entry.
        
        Parameters:
            e (dict): Matrix entry containing "nginx", "os_type", "arch", and optionally "support_tier".
        
        Returns:
            tuple: (nginx, os_type, arch, support_tier) where `support_tier` is an empty string if not present.
        """
        return (e["nginx"], e["os_type"], e["arch"], e.get("support_tier", ""))

    current_set = {_entry_key(e) for e in current_auto}
    desired_set = {_entry_key(e) for e in desired_auto}

    # Version-level summary for PR titles
    current_versions = {e["nginx"] for e in current_auto}
    desired_versions = {e["nginx"] for e in desired_auto}

    added = sorted(desired_versions - current_versions, key=version_tuple)
    removed = sorted(current_versions - desired_versions, key=version_tuple)

    # Entry-level change detection: any entry added or removed
    has_changes = current_set != desired_set

    return MatrixDiff(
        added_versions=added,
        removed_versions=removed,
        has_changes=has_changes,
    )


def write_diff_file(diff: MatrixDiff, path: Path) -> None:
    """Write ``matrix-diff.json`` with added/removed version arrays.

    The caller is responsible for only invoking this when
    ``diff.has_changes`` is true.  File absence signals no changes to the
    workflow, so this function is intentionally *not* called in the
    no-change case.

    The output is formatted JSON with 2-space indentation and a trailing
    newline, matching the project convention for machine-readable artifacts.
    """
    payload = {
        "added_versions": diff.added_versions,
        "removed_versions": diff.removed_versions,
    }
    _write_repo_text(path, json.dumps(payload, indent=2) + "\n")


def write_matrix(path: Path, data: dict) -> None:
    """
    Atomically write the given matrix mapping as formatted JSON to a repository file.
    
    Serializes `data` with 2-space indentation and a trailing newline, preserving all top-level fields (for example `schema_version` and `updated_at`). The write is performed atomically (temporary file in the same directory replaced into place) and the temporary file is removed on failure. The target `path` is validated to remain inside the repository.
    
    Parameters:
        path (Path): Destination path inside the repository where the matrix JSON will be written.
        data (dict): Full matrix data to serialize and write; the caller must update fields like `updated_at` before calling.
    """
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        safe_path = _resolve_repo_write_path(path)
        safe_tmp_path = _resolve_repo_write_path(tmp_path)
        _write_repo_text(safe_tmp_path, json.dumps(data, indent=2) + "\n")
        os.replace(safe_tmp_path, safe_path)
    except Exception:
        # Clean up the temp file on any failure
        with contextlib.suppress(OSError, ValueError):
            _resolve_repo_write_path(tmp_path).unlink(missing_ok=True)
        raise


def update_doc_table(doc_path: Path, matrix_entries: list[dict]) -> str:
    """
    Replace the auto-generated matrix section in a documentation file with a Markdown table built from `matrix_entries`.
    
    Reads the file at `doc_path`, finds the `DOC_MARKER_BEGIN` and `DOC_MARKER_END` markers, and replaces the inclusive block between them with a generated Markdown table. Rows are sorted by version (ascending), then `os_type`, then `arch` using the module's entry sort key. The `support_tier` value is title-cased (underscores become spaces) for display.
    
    Parameters:
        doc_path (Path): Path to the documentation file that contains the BEGIN/END markers.
        matrix_entries (list[dict]): List of matrix entry mappings. Each entry is expected to include the keys `nginx`, `os_type`, `arch`, and `support_tier`.
    
    Returns:
        str: The full document content with the auto-generated matrix block replaced.
    
    Raises:
        SystemExit: Exits with status 1 if either the begin or end marker is missing from the document.
    """
    content = doc_path.read_text(encoding="utf-8")

    begin_idx = content.find(DOC_MARKER_BEGIN)
    end_idx = content.find(DOC_MARKER_END)

    if begin_idx == -1 or end_idx == -1:
        missing = []
        if begin_idx == -1:
            missing.append(DOC_MARKER_BEGIN)
        if end_idx == -1:
            missing.append(DOC_MARKER_END)
        print(
            f"Missing marker(s) in {doc_path}: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build the replacement table
    sorted_entries = sorted(matrix_entries, key=_entry_sort_key)

    lines: list[str] = [
        DOC_MARKER_BEGIN,
        "| NGINX Version | OS Type | Architecture | Support Tier |",
        "|---------------|---------|--------------|--------------|",
    ]
    for entry in sorted_entries:
        tier = entry["support_tier"].replace("_", " ").title()
        lines.append(
            f"| {entry['nginx']} | {entry['os_type']} | {entry['arch']} | {tier} |"
        )
    lines.append(DOC_MARKER_END)

    # Replace from BEGIN marker through END marker (inclusive)
    end_marker_end = end_idx + len(DOC_MARKER_END)
    return content[:begin_idx] + "\n".join(lines) + content[end_marker_end:]


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the matrix updater.

    Supports three mutually exclusive modes:

    - *Normal* (no flags): scrape, compute, write files via crash-safe
      temp+rename.
    - ``--dry-run``: scrape, compute, print changes to stdout — no file
      writes.
    - ``--check-only``: scrape, compare, exit 0 (fresh) / 2 (stale) /
      1 (error) — no file writes.

    Parameters
    ----------
    argv:
        Optional argument list for testability.  When ``None``,
        ``argparse`` reads from ``sys.argv[1:]`` as usual.

    Returns
    -------
    argparse.Namespace
        Parsed arguments with boolean attributes ``dry_run`` and
        ``check_only``.
    """
    parser = argparse.ArgumentParser(
        description="Automated nginx release matrix updater.",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Scrape and compute changes, print to stdout without writing files.",
    )
    group.add_argument(
        "--check-only",
        action="store_true",
        default=False,
        help=(
            "Scrape and compare against current matrix. "
            "Exit 0 if fresh, 2 if stale, 1 on error."
        ),
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _log_diff_summary(diff: MatrixDiff) -> None:
    """
    Print a concise summary of matrix version additions, removals, and entry-level changes to stdout.
    
    The output lists added versions prefixed with "  +", removed versions prefixed with "  -", and, if there are changes to entries without any version-level additions or removals, a single line indicating entry-level changes.
    """
    for v in diff.added_versions:
        print(f"  + adding version {v}")
    for v in diff.removed_versions:
        print(f"  - removing version {v}")
    if diff.has_changes and not diff.added_versions and not diff.removed_versions:
        print("  ~ entry-level changes detected (no version additions/removals)")


def _handle_check_only(diff: MatrixDiff) -> int:
    """
    Report matrix discrepancies to stderr for the `--check-only` mode.
    
    Parameters:
        diff (MatrixDiff): Diff summary comparing current and desired auto-managed entries.
    
    Returns:
        int: Exit code `2` indicating the matrix is stale (differences were found).
    """
    if diff.added_versions:
        print(
            f"Stale matrix: versions to add: {', '.join(diff.added_versions)}",
            file=sys.stderr,
        )
    if diff.removed_versions:
        print(
            f"Stale matrix: versions to remove: {', '.join(diff.removed_versions)}",
            file=sys.stderr,
        )
    return 2


def _handle_dry_run(diff: MatrixDiff) -> int:
    """
    Print a summary of matrix changes that would be applied in dry-run mode.
    
    Prints added versions with the number of auto-generated entries per version and removed versions. If no version-level additions or removals are present, notes that entry-level changes were detected.
    
    Returns:
        int: 0 on success.
    """
    print("Dry-run mode — the following changes would be applied:")
    for v in diff.added_versions:
        print(f"  ADD {v} ({len(OS_TYPES) * len(ARCHS)} entries)")
    for v in diff.removed_versions:
        print(f"  REMOVE {v}")
    if not diff.added_versions and not diff.removed_versions:
        print("  Entry-level changes detected (no version additions/removals)")
    return 0


def _restore_matrix_backup(matrix_backup: str | None) -> None:
    """
    Restore the repository matrix file from the provided backup content.
    
    If `matrix_backup` is a string, overwrite MATRIX_PATH with that content; if `matrix_backup` is `None`, do nothing. I/O errors encountered while writing the backup are suppressed.
     
    Parameters:
        matrix_backup (str | None): The text content to restore to MATRIX_PATH, or `None` to skip restoration.
    """
    if matrix_backup is not None:
        with contextlib.suppress(OSError):
            _write_repo_text(MATRIX_PATH, matrix_backup)


def _write_doc_with_rollback(
    merged: list[dict],
    matrix_backup: str | None,
) -> str:
    """
    Build the updated INSTALLATION.md content from merged matrix entries and restore the previous matrix on failure.
    
    Parameters:
        merged (list[dict]): Matrix entries to insert into the auto-generated documentation block.
        matrix_backup (str | None): Contents of the previous matrix file to restore on failure, or None if no backup is available.
    
    Returns:
        new_doc_content (str): Complete document text with the auto-generated matrix block replaced.
    
    Raises:
        SystemExit: Re-raised after restoring the matrix backup when the documentation update triggers an exit.
        Exception: Any exception raised while generating the documentation is re-raised after restoring the matrix backup.
    """
    try:
        return update_doc_table(DOC_PATH, merged)
    except BaseException:
        _restore_matrix_backup(matrix_backup)
        raise


def _atomic_doc_write(new_doc_content: str, matrix_backup: str | None) -> int:
    """
    Atomically replace the installation guide content and restore the previous matrix on failure.
    
    Parameters:
        new_doc_content (str): Full text to write into the installation guide document.
        matrix_backup (str | None): Serialized backup of the matrix to restore if the write fails; pass `None` to skip restoration.
    
    Returns:
        int: `0` on successful write, `1` if an error occurred and restoration (if provided) was attempted.
    """
    doc_tmp = DOC_PATH.with_suffix(f"{DOC_PATH.suffix}.tmp")
    try:
        safe_doc_path = _resolve_repo_write_path(DOC_PATH)
        safe_doc_tmp = _resolve_repo_write_path(doc_tmp)
        _write_repo_text(safe_doc_tmp, new_doc_content)
        os.replace(safe_doc_tmp, safe_doc_path)
    except Exception as exc:
        print(f"Error writing {DOC_PATH}: {exc}", file=sys.stderr)
        with contextlib.suppress(OSError, ValueError):
            _resolve_repo_write_path(doc_tmp).unlink(missing_ok=True)
        _restore_matrix_backup(matrix_backup)
        return 1
    return 0


def _run_write_mode(
    data: dict,
    merged: list[dict],
    diff: MatrixDiff,
) -> int:
    """
    Execute the write-mode workflow to persist the updated release matrix, replace the generated documentation table, and emit a machine-readable matrix-diff.
    
    If any step fails the function attempts to restore the previous matrix content. Returns 0 on success and 1 on error.
    Returns:
        int: 0 on success, 1 on error.
    """
    # Back up original matrix contents in memory for rollback
    try:
        matrix_backup = MATRIX_PATH.read_text()
    except OSError:
        matrix_backup = None

    # Update updated_at timestamp
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["matrix"] = merged

    # Write matrix via crash-safe temp+rename
    try:
        write_matrix(MATRIX_PATH, data)
    except Exception as exc:
        print(f"Error writing {MATRIX_PATH}: {exc}", file=sys.stderr)
        return 1

    # Generate new doc content (restores matrix on SystemExit)
    new_doc_content = _write_doc_with_rollback(merged, matrix_backup)

    # Write doc atomically (restores matrix on failure)
    result = _atomic_doc_write(new_doc_content, matrix_backup)
    if result != 0:
        return result

    # Write matrix-diff.json (non-fatal)
    try:
        write_diff_file(diff, DIFF_PATH)
    except Exception as exc:
        print(f"Warning: failed to write {DIFF_PATH}: {exc}", file=sys.stderr)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the matrix updater end-to-end.

    Orchestrates the full fetch → parse → filter → load → compute → merge →
    diff → write pipeline, respecting ``--dry-run`` and ``--check-only``
    modes.

    Parameters
    ----------
    argv:
        Optional argument list for testability.  When ``None``,
        ``argparse`` reads from ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code: 0 on success, 1 on error, 2 on stale matrix
        (``--check-only`` only).
    """
    args = parse_args(argv)

    # In normal write mode, remove any pre-existing matrix-diff.json so that
    # a stale file from a previous run cannot falsely signal changes.
    if not args.dry_run and not args.check_only:
        with contextlib.suppress(OSError):
            DIFF_PATH.unlink(missing_ok=True)
    # --- Fetch and parse nginx.org -------------------------------------------
    try:
        html = fetch_download_page(NGINX_DOWNLOAD_URL)
    except URLError as exc:
        print(f"Error fetching {NGINX_DOWNLOAD_URL}: {exc}", file=sys.stderr)
        return 1

    all_versions = parse_nginx_versions(html)
    if not all_versions:
        print(
            "Error: zero nginx versions parsed from download page",
            file=sys.stderr,
        )
        return 1

    # --- Filter to supported versions >= MIN_SUPPORTED -----------------------
    min_version = read_min_version(INSTALL_SCRIPT_PATH)
    versions = filter_versions(all_versions, min_version)

    # --- Load existing matrix ------------------------------------------------
    data, current_auto, manual_entries = load_matrix(MATRIX_PATH)

    # --- Compute desired auto entries and merge ------------------------------
    desired_auto = compute_matrix(versions, OS_TYPES, ARCHS)
    merged = merge_matrix(desired_auto, manual_entries)

    # --- Diff current auto vs desired auto -----------------------------------
    diff = diff_matrix(current_auto, desired_auto)

    # --- Log additions / removals to stdout ----------------------------------
    _log_diff_summary(diff)

    # --- Handle based on mode ------------------------------------------------
    if not diff.has_changes:
        print("Matrix is up to date — no changes needed.")
        return 0

    if args.check_only:
        return _handle_check_only(diff)

    if args.dry_run:
        return _handle_dry_run(diff)

    # --- Normal write mode ---------------------------------------------------
    return _run_write_mode(data, merged, diff)


if __name__ == "__main__":
    sys.exit(main())
