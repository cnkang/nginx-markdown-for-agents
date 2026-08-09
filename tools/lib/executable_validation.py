"""Validation helpers for fixed repository tooling executables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_APPROVED_EXECUTABLES = frozenset({"ab", "brotli", "git"})
_APPROVED_EXECUTABLE_DIRS = (
    Path("/bin"),
    Path("/usr/bin"),
    Path("/usr/sbin"),
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/opt/homebrew/Cellar"),
    Path("/opt/local/bin"),
    Path("/opt/local/libexec"),
    Path("/usr/local/Cellar"),
)


def resolve_approved_executable(name: str) -> str | None:
    """Resolve an approved CLI to a canonical executable path.

    PATH lookup is retained for portability, but the resolved target must be a
    regular executable with the expected basename under a trusted system
    executable directory.  This prevents a writable PATH entry or symlink from
    selecting an unintended binary.
    """
    if name not in _APPROVED_EXECUTABLES:
        raise ValueError(f"executable is not approved: {name!r}")

    discovered = shutil.which(name)
    if discovered is None:
        return None

    try:
        resolved = Path(discovered).resolve(strict=True)
        trusted_roots = tuple(root.resolve() for root in _APPROVED_EXECUTABLE_DIRS)
    except OSError:
        return None

    if (
        resolved.name != name
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        return None

    if not any(
        resolved == root or root in resolved.parents for root in trusted_roots
    ):
        return None
    return str(resolved)
