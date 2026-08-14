"""Validation helpers for fixed repository tooling executables."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_APPROVED_EXECUTABLES = frozenset(
    {"ab", "brotli", "cargo", "git", "ps", "rustfmt"}
)
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

# Executables that may be Rustup tool shims (resolved through ~/.cargo/bin).
_RUSTUP_SHIM_TOOLS = frozenset({"cargo", "rustfmt"})


def _trusted_roots() -> tuple[Path, ...]:
    """Return the fixed system directories allowed for tool executables."""
    return tuple(root.resolve() for root in _APPROVED_EXECUTABLE_DIRS)


def _is_under(path: Path, roots: tuple[Path, ...]) -> bool:
    """Return whether a resolved path is below one of the trusted roots.

    Containment compares the RESOLVED candidate path against the resolved
    trusted roots so symlinked or ``..``-containing candidates cannot escape
    the allowlist.
    """
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _is_rustup_tool_shim(candidate: Path, resolved: Path, name: str) -> bool:
    """Allow only a standard Rustup shim targeting its toolchain."""
    try:
        home = Path.home()
    except (RuntimeError, KeyError):
        return False
    tool_shim = home / ".cargo" / "bin" / name
    rustup_toolchains = home / ".rustup" / "toolchains"
    if candidate != tool_shim:
        return False
    if resolved.name == name and rustup_toolchains.resolve() in resolved.parents:
        return True

    # Current Rustup installs use a small ``.cargo/bin/rustup`` dispatcher
    # rather than a direct symlink into the selected toolchain.  Keep that
    # exact dispatcher path narrow, and require a matching executable under
    # the real Rustup toolchain root before accepting it.
    rustup_dispatcher = Path.home() / ".cargo" / "bin" / "rustup"
    try:
        if resolved != rustup_dispatcher.resolve(strict=True):
            return False
        toolchain_root = rustup_toolchains.resolve(strict=True)
        for toolchain in toolchain_root.iterdir():
            tool = toolchain / "bin" / name
            try:
                tool_resolved = tool.resolve(strict=True)
            except FileNotFoundError:
                # A partial toolchain is normal; keep looking for a usable one.
                continue
            except OSError:
                # An unreadable entry must not hide other toolchains.
                continue
            if (
                tool_resolved.name == name
                and toolchain_root in tool_resolved.parents
                and tool_resolved.is_file()
                and os.access(tool_resolved, os.X_OK)
            ):
                return True
    except OSError:
        return False
    return False


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
        trusted_roots = _trusted_roots()
    except OSError:
        return None

    if (
        candidate.name != name
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        return None

    candidate_is_trusted = _is_under(candidate, trusted_roots)
    resolved_is_trusted = _is_under(resolved, trusted_roots)
    is_rustup_shim = name in _RUSTUP_SHIM_TOOLS and _is_rustup_tool_shim(
        candidate, resolved, name
    )
    if not (candidate_is_trusted and resolved_is_trusted) and not is_rustup_shim:
        return None
    # Preserve a Rustup shim only after the dedicated shim check accepted it;
    # every other executable is returned at its canonical resolved path.
    if is_rustup_shim:
        return str(candidate)
    return str(resolved)
