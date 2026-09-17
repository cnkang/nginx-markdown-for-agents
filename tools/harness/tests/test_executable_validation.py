"""Regression tests for executable PATH trust-root resolution."""

from pathlib import Path

import pytest

from tools.lib import executable_validation as module


def test_trusted_roots_keep_configured_and_resolved_spellings() -> None:
    roots = module._trusted_roots()

    for configured_root in module._APPROVED_EXECUTABLE_DIRS:
        assert configured_root in roots
        assert configured_root.resolve() in roots


def test_literal_bin_entry_is_trusted_when_bin_is_a_symlink() -> None:
    # The test verifies that a literal /bin entry is trusted when /bin is a
    # symlink (macOS /bin -> /usr/bin) that resolves under an approved root.
    # On systems where /bin is a real directory (not a link), the literal
    # entry is still trusted directly, but the resolution path differs, so
    # only run the symlink-specific assertion when the precondition holds.
    if not Path("/bin").is_symlink():
        pytest.skip("/bin is not a symlink on this platform")
    roots = module._trusted_roots()
    literal_bin = Path("/bin") / "git"

    assert module._is_under(literal_bin, roots)


@pytest.mark.parametrize("name", ["cargo", "rustc", "rustfmt"])
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_rustup_shim_forms_resolve_to_active_toolchain(tmp_path, monkeypatch, name, link_kind):
    """Every approved Rustup shim, symlinked or hardlinked, resolves to the tool."""
    home = tmp_path
    cargo_bin = home / ".cargo" / "bin"
    dispatcher = cargo_bin / "rustup"
    shim = cargo_bin / name
    tool = (
        home
        / ".rustup"
        / "toolchains"
        / "stable-x86_64-unknown-linux-gnu"
        / "bin"
        / name
    )
    cargo_bin.mkdir(parents=True)
    tool.parent.mkdir(parents=True)
    dispatcher.write_text("dispatcher", encoding="utf-8")
    dispatcher.chmod(0o755)
    tool.write_text(name, encoding="utf-8")
    tool.chmod(0o755)
    if link_kind == "symlink":
        shim.symlink_to(dispatcher)
    else:
        shim.hardlink_to(dispatcher)

    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module.shutil, "which", lambda _name: str(shim))
    monkeypatch.setattr(module, "_trusted_roots", set)
    monkeypatch.setattr(
        module,
        "_active_rustup_toolchain",
        lambda: "stable-x86_64-unknown-linux-gnu",
    )

    assert module.resolve_approved_executable(name) == str(tool)
