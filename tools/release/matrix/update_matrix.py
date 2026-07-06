#!/usr/bin/env python3
"""Automated nginx release matrix updater.

Scrapes the nginx.org download page, computes the desired matrix state
(respecting ``managed_by: manual`` Pin_Entries), and updates both
``tools/release-matrix.json`` and the Platform Compatibility Matrix table
in ``docs/guides/INSTALLATION.md``.  A machine-readable diff summary is
written to ``matrix-diff.json`` when changes are detected, for consumption
by the GitHub Actions workflow.

Exit codes:
    0 — Success (no changes needed, or changes written successfully)
    1 — Error (network failure, parse failure, invalid JSON, missing markers)
    2 — Stale matrix detected (only in ``--check-only`` mode)

Usage:
    python3 tools/release/matrix/update_matrix.py [--dry-run] [--check-only]
"""

from __future__ import annotations


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
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Path constants (same pattern as validate_doc_matrix_sync.py)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
INSTALL_SCRIPT_PATH = REPO_ROOT / "tools" / "install.sh"
DOC_PATH = REPO_ROOT / "docs" / "guides" / "INSTALLATION.md"
DIFF_PATH = MATRIX_PATH.parent / "matrix-diff.json"

# ---------------------------------------------------------------------------
# Scraping / version constants
# ---------------------------------------------------------------------------
NGINX_DOWNLOAD_URL = "https://nginx.org/en/download.html"
REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "cnkang/nginx-markdown-for-agents")
GITHUB_API_ACCEPT = "application/vnd.github+json"
NGINX_DOWNLOAD_ALLOWED_HOSTS = {"nginx.org"}
GITHUB_RELEASE_ALLOWED_HOSTS = {"api.github.com"}

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
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f]|%(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7[fF])"
)


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
    """Convert a dot-separated version string into a tuple of integer components.

    Parameters:
        v (str): Version string like "X.Y.Z" (e.g., "1.22.0").

    Returns:
        tuple[int, ...]: Integer components of the version suitable for numeric sorting/comparison.
    """
    return tuple(int(p) for p in v.split("."))


def classify_version(version: str) -> str:
    """Classify an NGINX dotted version string as a release track.

    Parameters:
        version (str): NGINX version in dotted form (e.g. "1.22.1").

    Returns:
        str: `"stable"` if the minor version is even, `"mainline"` if the minor version is odd.

    Raises:
        ValueError: If `version` is not dotted as expected or its minor
            component is not an integer.
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
    """List required keys that are missing from the given mapping, preserving the order
    of `required_keys`.

    Handles canonical schema (nginx_version, libc) vs legacy (nginx, os_type).
    """
    missing = []
    for key in required_keys:
        # Normalize check: nginx -> nginx_version, os_type -> libc/os
        if key == "nginx" and ("nginx" not in entry and "nginx_version" not in entry):
            missing.append(key)
        elif key == "os_type" and ("os_type" not in entry and "libc" not in entry and "os" not in entry):
            missing.append(key)
        elif key == "arch" and "arch" not in entry:
            missing.append(key)
        elif key not in entry:
            missing.append(key)
    return missing


def _resolve_repo_write_path(path: Path) -> Path:
    """Resolve a filesystem path to its canonical absolute Path and ensure it is inside
    the repository root.

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
    """Write `content` to `path` within the repository using UTF-8 encoding.

    The path is validated to stay under the repository root, missing parent
    directories are created, and file mode 0o644 is used.

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
    """Return the MIN_SUPPORTED_NGINX_VERSION value defined in the given install script.

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
    raise RuntimeError(
        f"Could not find MIN_SUPPORTED_NGINX_VERSION in {install_script_path}"
    )


def filter_versions(versions: list[str], min_version: str) -> list[str]:
    """Filter a list of nginx version strings to those greater than or equal to the
    provided minimum version.

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
    """Fetch the nginx download page HTML.

    @returns
        The page HTML as a string.

    @raises urllib.error.URLError
        On network errors or non-200 HTTP responses.
    """
    safe_url = _validate_https_url(url, allowed_hosts=NGINX_DOWNLOAD_ALLOWED_HOSTS)
    with urlopen(safe_url, timeout=30) as resp:  # nosec B310
        return resp.read().decode("utf-8")


def fetch_release_json(url: str | None = None) -> str:
    """Fetch the latest GitHub release JSON for this repository.

    Legacy helper retained for tests and historical tooling.
    """
    release_url = url or f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
    safe_release_url = _validate_https_url(
        release_url,
        allowed_hosts=GITHUB_RELEASE_ALLOWED_HOSTS,
    )
    request = Request(
        safe_release_url,
        headers={
            "Accept": GITHUB_API_ACCEPT,
            "User-Agent": "nginx-markdown-for-agents-matrix-updater",
        },
    )
    with urlopen(request, timeout=30) as resp:  # nosec B310
        return resp.read().decode("utf-8")


def _validate_https_url(url: str, *, allowed_hosts: set[str]) -> str:
    """Allow only https URLs targeting explicit hosts."""
    if _CONTROL_CHARS_RE.search(url):
        raise ValueError(f"URL contains control characters: {url!r}")

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()

    if scheme != "https":
        raise ValueError(f"unsupported URL scheme: {scheme or '<missing>'}")
    if host not in allowed_hosts:
        allowed = ", ".join(sorted(allowed_hosts))
        raise ValueError(
            f"untrusted URL host: {host or '<missing>'}; allowed hosts: {allowed}"
        )

    return url


def parse_nginx_versions(html: str) -> list[str]:
    """Extract nginx version numbers from the provided download-page HTML.

    Parameters:
        html (str): HTML content of the nginx download page to scan.

    Returns:
        list[str]: Deduplicated list of version strings (format "X.Y.Z") found in download links,
        preserving the order of first appearance.
    """
    pattern = re.compile(r"/download/nginx-(\d+\.\d+\.\d+)\.tar\.gz")
    versions = pattern.findall(html)
    return list(dict.fromkeys(versions))


def _release_asset_version(name: object) -> str | None:
    """Extract the nginx version from a release asset name."""
    if not isinstance(name, str):
        return None

    prefix = "ngx_http_markdown_filter_module-"
    suffix = ".tar.gz"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None

    core = name[len(prefix) : -len(suffix)]
    parts = core.split("-")
    if len(parts) != 3:
        return None

    version, os_type, arch = parts
    if not _is_dotted_version(version):
        return None
    return None if not os_type or not arch else version


def _is_dotted_version(version: str) -> bool:
    """Return True when version is strictly X.Y.Z dotted numeric form."""
    parts = version.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def parse_release_module_versions(release_json: str) -> set[str]:
    """Extract module versions from a GitHub release asset payload.

    Legacy helper retained for tests and historical tooling.
    """
    try:
        release_data = json.loads(release_json)
    except json.JSONDecodeError:
        return set()

    assets = release_data.get("assets", [])
    if not isinstance(assets, list):
        return set()

    versions: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        version = _release_asset_version(asset.get("name"))
        if version is not None:
            versions.add(version)
    return versions


# ---------------------------------------------------------------------------
# Matrix loading
# ---------------------------------------------------------------------------


def _validate_manual_entries(manual_entries: list[dict], path: Path) -> None:
    """Ensure no duplicate manual matrix entries with the same (nginx, os_type, arch)
    key. Also validates that each manual entry has a ``support_tier`` key.

    If duplicates exist, print an error mentioning ``path`` and exit with code 1.

    Parameters:
        manual_entries (list[dict]): Manual entries that must include "nginx",
            "os_type", and "arch".
        path (Path): Source matrix file path shown in validation errors.
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
    """Load and validate release-matrix.json and split its entries into auto-managed and
    manual lists.

    Read JSON from ``path``, require a top-level ``matrix`` list, and validate
    required entry keys. On I/O or validation errors, print to stderr and exit 1.

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

    if not isinstance(data, dict) or ("matrix" not in data and "entries" not in data):
        print(
            f"Invalid matrix structure in {path}: missing 'matrix' or 'entries' key", file=sys.stderr
        )
        sys.exit(1)

    matrix_entries = data.get("entries") or data.get("matrix")
    if not isinstance(matrix_entries, list):
        print(
            f"Invalid matrix structure in {path}: matrix must be a list",
            file=sys.stderr,
        )
        sys.exit(1)

    auto_entries: list[dict] = []
    manual_entries: list[dict] = []

    for i, entry in enumerate(matrix_entries):
        if not isinstance(entry, dict):
            print(
                (
                    f"Invalid matrix entry at index {i} in {path}: "
                    f"expected dict, got {type(entry).__name__}"
                ),
                file=sys.stderr,
            )
            sys.exit(1)
        if missing := _missing_required_keys(entry, REQUIRED_MATRIX_ENTRY_KEYS):
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
    """Produce a sort key for a release matrix entry.

    Parameters:
        entry (dict): Matrix entry with keys "nginx" (version string), "os_type", and "arch".

    Returns:
        tuple: ``(version_tuple, os_type, arch)`` where ``version_tuple``
            comes from ``entry["nginx"]``.
    """
    version = entry.get("nginx_version") or entry.get("nginx")
    os_type = entry.get("os_type") or entry.get("os") or entry.get("libc")
    arch = entry.get("arch")
    return (version_tuple(version), os_type, arch)


def compute_matrix(
    versions: list[str], os_types: list[str], archs: list[str]
) -> list[dict]:
    """Generate the full cross-product of auto-managed matrix entries with support_tier
    set to "full".

    Each entry has ``nginx``, ``os_type``, ``arch``, and ``support_tier``.
    Output is sorted by version, then os_type, then arch.

    Returns:
        list[dict]: Matrix entries for every combination of ``versions``,
            ``os_types``, and ``archs``.
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


def merge_matrix(auto_entries: list[dict], manual_entries: list[dict]) -> list[dict]:
    """Merge auto-generated matrix entries with manual Pin_Entries, giving manual
    entries precedence on key collisions.

    On key collisions ``(nginx, os_type, arch)``, keep manual entries and drop
    conflicting auto entries. Output is sorted by nginx/os/arch.

    Returns:
        list[dict]: Merged matrix entries sorted as described.
    """
    manual_keys: set[tuple[str, str, str]] = {
        (e["nginx"], e["os_type"], e["arch"]) for e in manual_entries
    }

    # Keep only auto entries whose key does not collide with a manual entry
    merged: list[dict] = [
        e
        for e in auto_entries
        if (e["nginx"], e["os_type"], e["arch"]) not in manual_keys
    ]
    merged.extend(manual_entries)
    merged.sort(key=_entry_sort_key)
    return merged


def diff_matrix(current_auto: list[dict], desired_auto: list[dict]) -> MatrixDiff:
    """Compute which NGINX versions were added or removed and whether any auto-managed
    entries changed.

    Produce sorted version-level additions/removals and detect entry-level
    differences across nginx/os/arch/support_tier.

    Returns:
        MatrixDiff: Added versions, removed versions, and a boolean flag for
            any entry-level change.
    """

    def _entry_key(e: dict) -> tuple[str, str, str, str]:
        """Compute a stable sort key for a matrix entry.

        Parameters:
            e (dict): Matrix entry with nginx/os_type/arch and optional
                support_tier.

        Returns:
            tuple: ``(nginx, os_type, arch, support_tier)`` where missing
                support_tier is represented as ``""``.
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
    """Atomically write the given matrix mapping as formatted JSON to a repository file.

    Serialize ``data`` with 2-space indentation plus trailing newline while
    preserving all top-level keys. Write via temp+rename and remove temp files
    on failure. The path is validated to stay in-repo.

    Parameters:
        path (Path): Destination path inside the repository where the matrix JSON will be written.
        data (dict): Full matrix data to write. Callers update fields such as
            ``updated_at`` before invoking this function.
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
    """Replace the auto-generated matrix section in a documentation file with a Markdown
    table built from `matrix_entries`.

    Read ``doc_path``, find ``DOC_MARKER_BEGIN`` and ``DOC_MARKER_END``, and
    replace the inclusive block with a generated Markdown table. Rows are
    sorted by version, then ``os_type``, then ``arch``.

    Parameters:
        doc_path (Path): Path to the documentation file that contains the BEGIN/END markers.
        matrix_entries (list[dict]): Matrix entry mappings that include
            ``nginx``, ``os_type``, ``arch``, and ``support_tier``.

    Returns:
        str: The full document content with the auto-generated matrix block replaced.

    Raises:
        SystemExit: Exit 1 if either marker is missing.
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
    """Print a concise summary of matrix version additions, removals, and entry-level
    changes to stdout.

    The output lists added versions prefixed with "  +", removed versions prefixed with
    "  -", and, if there are changes to entries without any version-level additions or
    removals, a single line indicating entry-level changes.
    """
    for v in diff.added_versions:
        print(f"  + adding version {v}")
    for v in diff.removed_versions:
        print(f"  - removing version {v}")
    if diff.has_changes and not diff.added_versions and not diff.removed_versions:
        print("  ~ entry-level changes detected (no version additions/removals)")


def _handle_check_only(diff: MatrixDiff) -> int:
    """Report matrix discrepancies to stderr for the `--check-only` mode.

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
    """Print a summary of matrix changes that would be applied in dry-run mode.

    Print additions/removals. If only entry-level changes exist, print a
    separate notice.

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
    """Restore the repository matrix file from the provided backup content.

    If ``matrix_backup`` is set, restore MATRIX_PATH with it. Ignore I/O
    errors during restore.

    Parameters:
        matrix_backup (str | None): Matrix text to restore, or ``None`` to
            skip.
    """
    if matrix_backup is not None:
        with contextlib.suppress(OSError):
            _write_repo_text(MATRIX_PATH, matrix_backup)


def _write_doc_with_rollback(
    merged: list[dict],
    matrix_backup: str | None,
) -> str:
    """Build the updated INSTALLATION.md content from merged matrix entries and restore
    the previous matrix on failure.

    Parameters:
        merged (list[dict]): Matrix entries to insert into the auto-generated documentation block.
        matrix_backup (str | None): Previous matrix contents used for rollback,
            or ``None`` when unavailable.

    Returns:
        new_doc_content (str): Complete document text with the auto-generated matrix block replaced.

    Raises:
        SystemExit: Re-raised after matrix restoration.
        Exception: Re-raised after matrix restoration.
    """
    try:
        return update_doc_table(DOC_PATH, merged)
    except BaseException:
        _restore_matrix_backup(matrix_backup)
        raise


def _atomic_doc_write(new_doc_content: str, matrix_backup: str | None) -> int:
    """Atomically replace the installation guide content and restore the previous matrix
    on failure.

    Parameters:
        new_doc_content (str): Full text to write into the installation guide document.
        matrix_backup (str | None): Matrix backup to restore on failure, or
            ``None`` to skip restoration.

    Returns:
        int: ``0`` on success, ``1`` on write failure after attempting rollback.
    """
    doc_tmp = DOC_PATH.with_suffix(f"{DOC_PATH.suffix}.tmp")
    try:
        safe_doc_path = _resolve_repo_write_path(DOC_PATH)
        safe_doc_tmp = _resolve_repo_write_path(doc_tmp)
        _write_repo_text(safe_doc_tmp, new_doc_content)
        os.replace(safe_doc_tmp, safe_doc_path)
    except (OSError, ValueError) as exc:
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
    """Execute the write-mode workflow to persist the updated release matrix, replace
    the generated documentation table, and emit a machine-readable matrix-diff.

    If a doc-generation or doc-write step fails, attempt matrix rollback.

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
    except (OSError, ValueError, TypeError) as exc:
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
    except (OSError, ValueError, TypeError) as exc:
        print(f"Warning: failed to write {DIFF_PATH}: {exc}", file=sys.stderr)

    return 0


def _collect_supported_versions() -> list[str]:
    """Fetch and validate supported nginx versions from nginx.org."""
    try:
        download_html = fetch_download_page(NGINX_DOWNLOAD_URL)
    except URLError as exc:
        raise RuntimeError(
            f"Error fetching nginx download page from {NGINX_DOWNLOAD_URL}: {exc}"
        ) from exc

    download_versions = parse_nginx_versions(download_html)
    if not download_versions:
        raise RuntimeError(
            "Error: zero nginx versions parsed from the nginx.org download page"
        )

    min_version = read_min_version(INSTALL_SCRIPT_PATH)
    if versions := filter_versions(sorted(download_versions), min_version):
        return versions
    else:
        raise RuntimeError(
            "Error: no nginx.org versions satisfy the minimum supported NGINX version"
        )


def main(
    argv: list[str] | None = None,
) -> int:
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
    try:
        versions = _collect_supported_versions()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

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
    elif args.check_only:
        return _handle_check_only(diff)
    elif args.dry_run:
        return _handle_dry_run(diff)
    else:
        # --- Normal write mode -----------------------------------------------
        return _run_write_mode(data, merged, diff)


if __name__ == "__main__":
    sys.exit(main())
