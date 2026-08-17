"""Validation helpers for fixed repository tooling executables."""

from __future__ import annotations

import os
import re
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
_RUSTUP_DIR_NAME = ".rustup"


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


def _channel_from_toolchain_file(
    content: str, *, is_toml: bool = False
) -> str | None:
    """Extract the channel from a rust-toolchain(.toml) file body.

    TOML form: `channel = "1.97.1"`; legacy form: a bare channel on the
    first non-empty line.  The bare-line fallback only applies to the
    legacy `rust-toolchain` file: a `rust-toolchain.toml` without a
    channel key must not fall back to its first line (e.g. a
    `[toolchain]` header).
    """
    match = re.search(
        r"^\s*channel\s*=\s*[\"']([^\"']+)[\"']",
        content, re.MULTILINE)
    if match:
        return match.group(1)
    if is_toml:
        return None
    first = content.strip().splitlines()
    if first and first[0].strip():
        return first[0].strip()
    return None


def _toolchain_from_directory(cwd: Path) -> str | None:
    """Find a rust-toolchain(.toml) override from cwd upward.

    Rustup checks ``rust-toolchain`` before ``rust-toolchain.toml``, so the
    bare file takes precedence within each directory.
    """
    for directory in (cwd, *cwd.parents):
        for name in ("rust-toolchain", "rust-toolchain.toml"):
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                content = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            channel = _channel_from_toolchain_file(
                content, is_toml=(name == "rust-toolchain.toml")
            )
            if channel is not None:
                return channel
            # A candidate file that yields no channel does not end the
            # search; keep looking at the remaining candidates.
    return None


def _active_rustup_toolchain() -> str | None:
    """Resolve the active Rustup toolchain name.

    Priority follows Rustup's own selection rules:
    1. the RUSTUP_TOOLCHAIN environment variable;
    2. a `rust-toolchain.toml` / `rust-toolchain` file found from the
       current working directory upward (directory-scoped override);
    3. the `default_toolchain` recorded in ``~/.rustup/settings.toml``.
    Returns None when none is available.  A bare channel (e.g. ``1.97.1``)
    is expanded to its host-triple toolchain name when that toolchain is
    installed (``1.97.1-aarch64-apple-darwin``), matching Rustup's own
    installed-toolchain naming.
    """
    env_toolchain = os.environ.get("RUSTUP_TOOLCHAIN")
    if env_toolchain:
        return env_toolchain

    try:
        cwd = Path.cwd()
    except OSError:
        cwd = None
    if cwd is not None:
        directory_toolchain = _toolchain_from_directory(cwd)
        if directory_toolchain:
            return _expand_toolchain_name(directory_toolchain)

    settings = Path.home() / _RUSTUP_DIR_NAME / "settings.toml"
    try:
        content = settings.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(
        r"^\s*default_toolchain\s*=\s*[\"']([^\"']+)[\"']",
        content, re.MULTILINE)
    if not match:
        return None
    return _expand_toolchain_name(match.group(1))


def _expand_toolchain_name(channel: str) -> str:
    """Map a bare channel to its installed host-triple toolchain name.

    Rustup names installed toolchains ``<channel>-<host-triple>`` (e.g.
    ``1.97.1-aarch64-apple-darwin``).  If a toolchain directory with that
    suffix exists, return the full name; otherwise return the channel as-is
    so callers fail closed instead of resolving the wrong binary.
    """
    try:
        toolchain_root = (
            Path.home() / _RUSTUP_DIR_NAME / "toolchains").resolve(strict=True)
    except (OSError, RuntimeError, KeyError):
        return channel
    if (toolchain_root / channel).is_dir():
        return channel
    for entry in toolchain_root.iterdir():
        if entry.is_dir() and entry.name.startswith(channel + "-"):
            return entry.name
    return channel


def _is_rustup_tool_shim(candidate: Path, resolved: Path, name: str) -> bool:
    """Allow only a standard Rustup shim targeting its toolchain.

    The concrete executable must come from the *active* toolchain per
    Rustup's own selection rules (RUSTUP_TOOLCHAIN env, then
    settings.toml default_toolchain) — never from an arbitrary toolchain
    the dispatcher happens to have installed, which could resolve the
    wrong version for the current invocation.
    """
    try:
        home = Path.home()
    except (RuntimeError, KeyError):
        return False
    tool_shim = home / ".cargo" / "bin" / name
    rustup_toolchains = home / _RUSTUP_DIR_NAME / "toolchains"
    if candidate != tool_shim:
        return False
    if resolved.name == name and rustup_toolchains.resolve() in resolved.parents:
        return True

    # Current Rustup installs use a small ``.cargo/bin/rustup`` dispatcher
    # rather than a direct symlink into the selected toolchain.  Resolve the
    # active toolchain name and require the matching executable under that
    # specific toolchain root, not any installed toolchain.
    rustup_dispatcher = home / ".cargo" / "bin" / "rustup"
    try:
        if resolved != rustup_dispatcher.resolve(strict=True):
            return False
        toolchain_root = rustup_toolchains.resolve(strict=True)
        active = _active_rustup_toolchain()
        if not active:
            return False
        toolchain = toolchain_root / active
        tool = toolchain / "bin" / name
        try:
            tool_resolved = tool.resolve(strict=True)
        except OSError:
            return False
        return (
            tool_resolved.name == name
            and toolchain_root in tool_resolved.parents
            and tool_resolved.is_file()
            and os.access(tool_resolved, os.X_OK)
        )
    except OSError:
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
