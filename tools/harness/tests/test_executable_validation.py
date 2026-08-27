"""Regression tests for executable PATH trust-root resolution."""

from pathlib import Path

from tools.lib import executable_validation as module


def test_trusted_roots_keep_configured_and_resolved_spellings() -> None:
    roots = module._trusted_roots()

    for configured_root in module._APPROVED_EXECUTABLE_DIRS:
        assert configured_root in roots
        assert configured_root.resolve() in roots


def test_literal_bin_entry_is_trusted_when_bin_is_a_symlink() -> None:
    roots = module._trusted_roots()
    literal_bin = Path("/bin") / "git"

    assert module._is_under(literal_bin, roots)


def test_rustup_dispatcher_returns_active_toolchain_executable(tmp_path, monkeypatch):
    """A Rustup dispatcher resolves to the selected tool, not the shim."""
    home = tmp_path
    cargo_bin = home / ".cargo" / "bin"
    dispatcher = cargo_bin / "rustup"
    shim = cargo_bin / "cargo"
    tool = (
        home
        / ".rustup"
        / "toolchains"
        / "stable-x86_64-unknown-linux-gnu"
        / "bin"
        / "cargo"
    )
    cargo_bin.mkdir(parents=True)
    tool.parent.mkdir(parents=True)
    dispatcher.write_text("dispatcher", encoding="utf-8")
    dispatcher.chmod(0o755)
    tool.write_text("cargo", encoding="utf-8")
    tool.chmod(0o755)
    shim.symlink_to(dispatcher)

    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module.shutil, "which", lambda _name: str(shim))
    monkeypatch.setattr(module, "_trusted_roots", set)
    monkeypatch.setattr(
        module,
        "_active_rustup_toolchain",
        lambda: "stable-x86_64-unknown-linux-gnu",
    )

    assert module.resolve_approved_executable("cargo") == str(tool)
