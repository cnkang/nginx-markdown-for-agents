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


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

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
    """Convert a version string like ``"X.Y.Z"`` to a tuple of ints."""
    return tuple(int(p) for p in v.split("."))


def classify_version(version: str) -> str:
    """Classify an nginx version as ``"stable"`` or ``"mainline"``.

    Even minor → stable/legacy, odd minor → mainline.
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
    """Return missing required keys for *entry* in declaration order."""
    return [key for key in required_keys if key not in entry]


def _resolve_repo_write_path(path: Path) -> Path:
    """Resolve *path* and ensure it stays within the repository root."""
    resolved_path = path.resolve(strict=False)
    repo_root = REPO_ROOT.resolve()
    try:
        resolved_path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Refusing to write outside repository root: {path}") from exc
    return resolved_path


def _write_repo_text(path: Path, content: str) -> None:
    """Write *content* to a repository-owned path using UTF-8 encoding."""
    safe_path = _resolve_repo_write_path(path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")


def read_min_version(install_script_path: Path) -> str:
    """Parse ``MIN_SUPPORTED_NGINX_VERSION`` from *install_script_path*.

    Looks for a line matching ``MIN_SUPPORTED_NGINX_VERSION="X.Y.Z"`` and
    returns the version string.  Raises ``RuntimeError`` if the variable
    cannot be found.
    """
    text = install_script_path.read_text(encoding="utf-8")
    match = re.search(r'MIN_SUPPORTED_NGINX_VERSION="([^"]+)"', text)
    if not match:
        raise RuntimeError(
            f"Could not find MIN_SUPPORTED_NGINX_VERSION in {install_script_path}"
        )
    return match.group(1)


def filter_versions(versions: list[str], min_version: str) -> list[str]:
    """Keep all nginx versions >= *min_version*.

    The release matrix now covers both stable/legacy and mainline releases.
    Returns a new list sorted by :func:`version_tuple` ascending.
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
    """Fetch the nginx download page and return the HTML as a string.

    Raises :class:`~urllib.error.URLError` on network errors or non-200
    responses.  The caller is responsible for handling the exception.
    """
    with urlopen(url) as resp:
        return resp.read().decode("utf-8")


def parse_nginx_versions(html: str) -> list[str]:
    """Extract nginx version numbers from download page HTML.

    Scans for download links matching ``/download/nginx-X.Y.Z.tar.gz`` and
    returns a deduplicated list of version strings.
    """
    pattern = re.compile(r"/download/nginx-(\d+\.\d+\.\d+)\.tar\.gz")
    versions = pattern.findall(html)
    return list(dict.fromkeys(versions))


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------

def load_matrix(path: Path) -> tuple[dict, list[dict], list[dict]]:
    """Load and validate ``release-matrix.json``, separating auto vs manual entries.

    Reads the JSON file at *path*, validates its structure, and partitions
    the ``matrix`` entries into auto-managed entries (no ``managed_by`` key
    or ``managed_by: "auto"``) and manual Pin_Entries (``managed_by: "manual"``).

    Validates that no two Pin_Entries share the same ``(nginx, os_type, arch)``
    key.  On any validation error, prints a descriptive message to stderr and
    calls ``sys.exit(1)``.

    Returns:
        A tuple of ``(data, auto_entries, manual_entries)`` where *data* is
        the full parsed JSON dict (preserving ``schema_version``, etc.),
        *auto_entries* is the list of auto-managed entry dicts, and
        *manual_entries* is the list of manual Pin_Entry dicts.
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

    for entry in data["matrix"]:
        managed_by = entry.get("managed_by")
        if managed_by == "manual":
            manual_entries.append(entry)
        else:
            # No managed_by or managed_by: "auto" → auto-managed
            auto_entries.append(entry)

    # Validate no duplicate (nginx, os_type, arch) keys among manual entries
    seen_keys: dict[tuple[str, str, str], int] = {}
    duplicates: list[tuple[str, str, str]] = []
    for entry in manual_entries:
        missing_keys = _missing_required_keys(entry, REQUIRED_MATRIX_ENTRY_KEYS)
        if missing_keys:
            print(
                (
                    f"Manual entry missing required keys in {path}: "
                    f"{', '.join(missing_keys)}"
                ),
                file=sys.stderr,
            )
            sys.exit(1)

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

    return data, auto_entries, manual_entries


# ---------------------------------------------------------------------------
# Matrix computation and merging
# ---------------------------------------------------------------------------


def _entry_sort_key(entry: dict) -> tuple[tuple[int, ...], str, str]:
    """Return a sort key for a matrix entry: ``(version_tuple, os_type, arch)``."""
    return (version_tuple(entry["nginx"]), entry["os_type"], entry["arch"])


def compute_matrix(
    versions: list[str], os_types: list[str], archs: list[str]
) -> list[dict]:
    """Generate full cross-product auto entries with ``support_tier = "full"``.

    Produces one entry per ``(version, os_type, arch)`` combination, sorted
    by version (ascending by :func:`version_tuple`), then *os_type*
    (alphabetical), then *arch* (alphabetical).
    """
    entries: list[dict] = []
    for v in versions:
        for os_type in os_types:
            for arch in archs:
                entries.append({
                    "nginx": v,
                    "os_type": os_type,
                    "arch": arch,
                    "support_tier": SUPPORT_TIER,
                })
    entries.sort(key=_entry_sort_key)
    return entries


def merge_matrix(
    auto_entries: list[dict], manual_entries: list[dict]
) -> list[dict]:
    """Combine auto entries with preserved manual Pin_Entries.

    On ``(nginx, os_type, arch)`` key collision, the manual entry takes
    precedence and the conflicting auto entry is dropped.  The result is
    sorted by :func:`version_tuple` of the nginx version, then *os_type*,
    then *arch*.
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
    """Compute added/removed version sets between current and desired auto entries.

    Performs entry-level comparison: any difference in the full set of
    auto-managed entries (not just version strings) triggers ``has_changes``.
    The ``added_versions`` and ``removed_versions`` fields report the
    version-level summary for PR titles and human consumption, but the
    ``has_changes`` flag is authoritative and may be ``True`` even when
    both version lists are empty (e.g., when ``os_type``/``arch``/``support_tier``
    changed for an existing version).

    Both ``added_versions`` and ``removed_versions`` are sorted by
    :func:`version_tuple`.

    Returns a :class:`MatrixDiff` with the results.
    """

    def _entry_key(e: dict) -> tuple[str, str, str, str]:
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
    """Write *data* to *path* as formatted JSON using crash-safe temp+rename.

    Serializes the full *data* dict (preserving ``schema_version``,
    ``updated_at``, and all other top-level fields) as JSON with 2-space
    indentation and a trailing newline.

    The write uses a temporary file (``{path}.tmp``) in the same directory,
    followed by :func:`os.replace` to atomically move it into place.  If
    the write or rename fails, the temporary file is cleaned up.

    The caller is responsible for updating ``updated_at`` (or any other
    field) before calling this function.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        safe_path = _resolve_repo_write_path(path)
        safe_tmp_path = _resolve_repo_write_path(tmp_path)
        _write_repo_text(safe_tmp_path, json.dumps(data, indent=2) + "\n")
        os.replace(safe_tmp_path, safe_path)
    except Exception:
        # Clean up the temp file on any failure
        try:
            _resolve_repo_write_path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        except ValueError:
            pass
        raise


def update_doc_table(doc_path: Path, matrix_entries: list[dict]) -> str:
    """Return updated document content with the matrix table replaced.

    Reads the document at *doc_path*, locates the ``<!-- BEGIN AUTO-GENERATED
    MATRIX -->`` and ``<!-- END AUTO-GENERATED MATRIX -->`` markers, and
    replaces everything between them (inclusive, re-emitting the markers)
    with a freshly generated Markdown table built from *matrix_entries*.

    Table rows are sorted consistently with the matrix (version ascending,
    os_type, arch) using :func:`_entry_sort_key`.  The ``support_tier``
    value is title-cased for display (e.g., ``"full"`` → ``"Full"``,
    ``"source_only"`` → ``"Source Only"``).

    Returns the full document content as a string.  The caller is
    responsible for writing it to disk.

    Calls ``sys.exit(1)`` if either marker is not found in the document.
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

    table_block = "\n".join(lines)

    # Replace from BEGIN marker through END marker (inclusive)
    end_marker_end = end_idx + len(DOC_MARKER_END)
    new_content = content[:begin_idx] + table_block + content[end_marker_end:]

    return new_content


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
        try:
            DIFF_PATH.unlink(missing_ok=True)
        except OSError:
            pass

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
    for v in diff.added_versions:
        print(f"  + adding version {v}")
    for v in diff.removed_versions:
        print(f"  - removing version {v}")
    if diff.has_changes and not diff.added_versions and not diff.removed_versions:
        print("  ~ entry-level changes detected (no version additions/removals)")

    # --- Handle based on mode ------------------------------------------------
    if not diff.has_changes:
        print("Matrix is up to date — no changes needed.")
        return 0

    # --check-only: report discrepancies to stderr, exit 2
    if args.check_only:
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

    # --dry-run: print changes to stdout, exit 0
    if args.dry_run:
        print("Dry-run mode — the following changes would be applied:")
        for v in diff.added_versions:
            print(f"  ADD {v} ({len(OS_TYPES) * len(ARCHS)} entries)")
        for v in diff.removed_versions:
            print(f"  REMOVE {v}")
        if not diff.added_versions and not diff.removed_versions:
            print("  Entry-level changes detected (no version additions/removals)")
        return 0

    # --- Normal write mode ---------------------------------------------------
    # Back up original file contents in memory for rollback
    try:
        matrix_backup = MATRIX_PATH.read_text()
    except OSError:
        matrix_backup = None

    try:
        doc_backup = DOC_PATH.read_text()
    except OSError:
        doc_backup = None

    # Update updated_at timestamp
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Update matrix entries
    data["matrix"] = merged

    # Write matrix via crash-safe temp+rename
    try:
        write_matrix(MATRIX_PATH, data)
    except Exception as exc:
        print(f"Error writing {MATRIX_PATH}: {exc}", file=sys.stderr)
        return 1

    # Generate new doc content and write via crash-safe temp+rename
    try:
        new_doc_content = update_doc_table(DOC_PATH, merged)
    except SystemExit:
        # update_doc_table calls sys.exit(1) on missing markers — restore matrix
        if matrix_backup is not None:
            try:
                _write_repo_text(MATRIX_PATH, matrix_backup)
            except OSError:
                pass
        return 1

    doc_tmp = DOC_PATH.with_suffix(DOC_PATH.suffix + ".tmp")
    try:
        safe_doc_path = _resolve_repo_write_path(DOC_PATH)
        safe_doc_tmp = _resolve_repo_write_path(doc_tmp)
        _write_repo_text(safe_doc_tmp, new_doc_content)
        os.replace(safe_doc_tmp, safe_doc_path)
    except Exception as exc:
        print(f"Error writing {DOC_PATH}: {exc}", file=sys.stderr)
        # Clean up temp file
        try:
            _resolve_repo_write_path(doc_tmp).unlink(missing_ok=True)
        except OSError:
            pass
        except ValueError:
            pass
        # Restore matrix from backup
        if matrix_backup is not None:
            try:
                _write_repo_text(MATRIX_PATH, matrix_backup)
            except OSError:
                pass
        return 1

    # Write matrix-diff.json
    try:
        write_diff_file(diff, DIFF_PATH)
    except Exception as exc:
        print(f"Warning: failed to write {DIFF_PATH}: {exc}", file=sys.stderr)
        # Non-fatal — the matrix and doc are already updated

    return 0


if __name__ == "__main__":
    sys.exit(main())
