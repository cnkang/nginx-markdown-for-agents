"""Validation helpers for fixed repository tooling executables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_APPROVED_EXECUTABLES = frozenset({"ab", "brotli", "cargo", "git", "ps"})
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


def _trusted_roots(name: str) -> tuple[Path, ...]:
    """Return system roots plus the user toolchain root for Cargo."""
    roots = _APPROVED_EXECUTABLE_DIRS
    if name == "cargo":
        roots += (Path.home() / ".cargo" / "bin", Path.home() / ".rustup")
    return tuple(root.resolve() for root in roots)


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
        candidate = Path(discovered)
        resolved = candidate.resolve(strict=True)
        trusted_roots = _trusted_roots(name)
    except OSError:
        return None

    if (
        candidate.name != name
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        return None

    if not any(
        candidate == root or root in candidate.parents
        or resolved == root or root in resolved.parents
        for root in trusted_roots
    ):
        return None
    # Preserve a Cargo rustup shim so ``cargo +nightly`` continues to select
    # the requested toolchain; system tools return their canonical path.
    return str(candidate if name == "cargo" else resolved)
