"""
Path validation helpers for tooling scripts.

Provides defensive path resolution and boundary checks to prevent
path-traversal vulnerabilities (CWE-22 / Snyk path normalization).

Usage:
    from lib.path_validation import validate_read_path, validate_write_path_within_root

    # For read-only paths (reports, configs, matrices):
    resolved = validate_read_path(args.baseline)

    # For write paths that must stay within a root directory:
    resolved = validate_write_path_within_root(args.output, repo_root)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def validate_read_path(
    path: str | Path,
    *,
    must_exist: bool = True,
    purpose: str = "file",
) -> Path:
    """
    Resolve and validate a read-only path.

    Performs realpath resolution and optional existence check.
    Rejects paths that resolve outside the current working tree
    only when an explicit root is not provided (the default
    allows any absolute path, since CLI tooling often needs to
    read from arbitrary locations).

    Parameters:
        path: Raw path from CLI args or config.
        must_exist: If True (default), raise FileNotFoundError when
                    the resolved path does not exist on disk.
        purpose: Human-readable label for error messages.

    Returns:
        Resolved absolute Path.

    Raises:
        FileNotFoundError: If must_exist and path does not exist.
        ValueError: If the path contains suspicious traversal patterns
                    before resolution (defense-in-depth).
    """
    raw = str(path)

    components = raw.replace("\\", "/").split("/")
    if ".." in components:
        raise ValueError(
            f"Refusing path with '..' traversal component: {raw!r} "
            f"(purpose: {purpose})"
        )

    resolved = Path(raw).resolve()

    if must_exist and not resolved.exists():
        raise FileNotFoundError(
            f"Read {purpose} path does not exist: {resolved}"
        )

    return resolved


def validate_write_path_within_root(
    path: str | Path,
    root: str | Path,
    *,
    purpose: str = "output",
) -> Path:
    """
    Resolve a write path and ensure it stays within the given root directory.

    This prevents path-traversal writes outside the intended tree.
    Pattern extracted from tools/release/matrix/update_matrix.py.

    Parameters:
        path: Raw write path from CLI args or config.
        root: Root directory that the write must stay within.
        purpose: Human-readable label for error messages.

    Returns:
        Resolved absolute Path (within root).

    Raises:
        ValueError: If the resolved path escapes the root directory
                    or contains '..' traversal components.
    """
    raw = str(path)

    components = raw.replace("\\", "/").split("/")
    if ".." in components:
        raise ValueError(
            f"Refusing write {purpose} path with '..' traversal component: {raw!r}"
        )

    resolved = Path(raw).resolve()
    resolved_root = Path(str(root)).resolve()

    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"Write {purpose} path {resolved} escapes root {resolved_root}; "
            f"refusing to write outside the intended directory tree"
        )

    return resolved


def sanitize_path_component(name: str) -> str:
    """
    Sanitize a string for safe use as a single filesystem path component.

    Replaces directory separators and traversal markers with underscores.
    Pattern extracted from tools/perf/run_corpus_benchmark.py.

    Parameters:
        name: Raw string (e.g., a benchmark label or identifier).

    Returns:
        Sanitized string safe for use as a filename component.
    """
    result = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return result


def validate_filename_strict(name: str, *, purpose: str = "filename") -> str:
    """
    Validate a filename against a strict allowlist pattern (CWE-22 / S2083).

    Only allows alphanumeric characters, underscores, hyphens, and dots.
    Must start with an alphanumeric or underscore character.  Rejects
    empty strings, path separators, traversal markers, and any
    characters outside the allowlist.

    Unlike ``sanitize_path_component`` (blocklist), this function uses
    an allowlist approach that is recognized by static-analysis taint
    trackers as a proper sanitization boundary.

    Parameters:
        name: Raw filename string to validate.
        purpose: Human-readable label for error messages.

    Returns:
        The validated filename string (unchanged if valid).

    Raises:
        ValueError: If the filename contains disallowed characters
                    or is empty.
    """
    if not name or not _SAFE_FILENAME_RE.match(name):
        raise ValueError(
            f"Invalid {purpose}: {name!r}. "
            f"Only alphanumeric characters, underscores, hyphens, "
            f"and dots are allowed.  Must start with an alphanumeric "
            f"or underscore character."
        )
    return name
