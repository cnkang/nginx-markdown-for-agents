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


def test_homebrew_opt_alias_dirs_are_trusted() -> None:
    """Homebrew `opt` version-alias dirs join the trusted roots (Rule 33)."""
    roots = module._trusted_roots()

    assert Path("/opt/homebrew/opt") in roots
    assert Path("/usr/local/opt") in roots


def test_git_resolved_through_opt_alias_is_accepted(
    tmp_path: Path, monkeypatch
) -> None:
    """A git under an `opt`-style alias resolving into Cellar is trusted.

    Reproduces the pre-commit failure on Homebrew macOS: `git commit`
    prepends `GIT_EXEC_PATH` (an `opt/.../libexec/git-core` path) to PATH, so
    the hook's `shutil.which("git")` finds the executable at its literal `opt`
    location while it resolves into the `Cellar` install.  The literal `opt`
    directory must be trusted or the resolver rejects a legitimate git.
    """
    opt_root = tmp_path / "opt"
    cellar_root = tmp_path / "Cellar"
    opt_core = opt_root / "git" / "libexec" / "git-core"
    cellar_bin = cellar_root / "git" / "2.55.0" / "bin"
    opt_core.mkdir(parents=True)
    cellar_bin.mkdir(parents=True)
    real_git = cellar_bin / "git"
    real_git.write_text("git", encoding="utf-8")
    real_git.chmod(0o755)
    opt_git = opt_core / "git"
    opt_git.symlink_to(real_git)

    monkeypatch.setattr(module.shutil, "which", lambda _name: str(opt_git))
    monkeypatch.setattr(module, "_trusted_roots", lambda: (opt_root, cellar_root))

    assert module.resolve_approved_executable("git") == str(real_git.resolve())


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


def test_rustup_shim_resolver_returns_dispatcher(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path
    cargo_bin = home / ".cargo" / "bin"
    dispatcher = cargo_bin / "rustup"
    shim = cargo_bin / "cargo"
    cargo_bin.mkdir(parents=True)
    dispatcher.write_text("dispatcher", encoding="utf-8")
    dispatcher.chmod(0o755)
    shim.symlink_to(dispatcher)

    monkeypatch.setattr(module.Path, "home", lambda: home)

    assert module.resolve_rustup_tool_shim("cargo") == str(shim)


def test_rustup_shim_resolver_rejects_foreign_binary(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path
    cargo_bin = home / ".cargo" / "bin"
    cargo_bin.mkdir(parents=True)
    _extracted_from_test_rustup_shim_resolver_rejects_foreign_binary_7(
        cargo_bin, "rustup", "dispatcher"
    )
    _extracted_from_test_rustup_shim_resolver_rejects_foreign_binary_7(
        cargo_bin, "cargo", "concrete"
    )
    monkeypatch.setattr(module.Path, "home", lambda: home)

    assert module.resolve_rustup_tool_shim("cargo") is None


# TODO Rename this here and in `test_rustup_shim_resolver_rejects_foreign_binary`
def _extracted_from_test_rustup_shim_resolver_rejects_foreign_binary_7(cargo_bin, arg1, arg2):
    dispatcher = cargo_bin / arg1
    dispatcher.write_text(arg2, encoding="utf-8")
    dispatcher.chmod(0o755)


def test_rustup_shim_resolver_rejects_non_shim_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError):
        module.resolve_rustup_tool_shim("git")
