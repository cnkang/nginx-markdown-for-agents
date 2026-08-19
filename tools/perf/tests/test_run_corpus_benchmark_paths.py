"""Path-validation regression tests for run_corpus_benchmark fixture discovery."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_corpus_benchmark as rcb  # noqa: E402
from tools.lib import executable_validation  # noqa: E402
from run_corpus_benchmark import discover_fixtures, write_examples  # noqa: E402


def test_run_converter_rejects_repo_symlink_to_external_command():
    link_dir = rcb.REPO_ROOT / ".test-tmp"
    link_dir.mkdir(exist_ok=True)
    converter_link = link_dir / "external-converter"
    html_path = link_dir / "external-converter-input.html"
    converter_link.unlink(missing_ok=True)
    converter_link.symlink_to("/bin/echo")
    html_path.write_text("<p>test</p>", encoding="utf-8")

    try:
        output, exit_code, latency_ms = rcb.run_converter(
            str(converter_link), str(html_path)
        )
    finally:
        converter_link.unlink(missing_ok=True)
        html_path.unlink(missing_ok=True)

    assert output == ""
    assert exit_code == 1
    assert latency_ms == 0.0


def test_approved_executable_rejects_writable_path_entry(monkeypatch, tmp_path):
    """A PATH entry outside trusted system directories is not executable input."""
    fake_git = tmp_path / "git"
    fake_git.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setattr(
        executable_validation.shutil,
        "which",
        lambda _name: str(fake_git),
    )

    assert executable_validation.resolve_approved_executable("git") is None


def test_approved_ab_rejects_path_shadowing(monkeypatch, tmp_path):
    """A PATH-first fake ab must not become the soak load generator."""
    fake_ab = tmp_path / "ab"
    fake_ab.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_ab.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(tmp_path), os.environ.get("PATH", ""))),
    )

    assert executable_validation.resolve_approved_executable("ab") is None


def test_approved_executable_rejects_symlink_escape(monkeypatch, tmp_path):
    """A trusted-looking PATH entry must not resolve outside its root."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    escaped = tmp_path / "escaped-git"
    escaped.write_text("#!/bin/sh\n", encoding="utf-8")
    escaped.chmod(0o755)
    link = fake_bin / "git"
    link.symlink_to(escaped)

    monkeypatch.setattr(
        executable_validation.shutil,
        "which",
        lambda _name: str(link),
    )
    monkeypatch.setattr(
        executable_validation,
        "_APPROVED_EXECUTABLE_DIRS",
        (fake_bin,),
    )

    assert executable_validation.resolve_approved_executable("git") is None


def test_approved_cargo_accepts_only_rustup_toolchain_shim(
    monkeypatch, tmp_path,
):
    """The CI Rustup shim is allowed only when its target is toolchain Cargo."""
    cargo_home = tmp_path / ".cargo" / "bin"
    cargo_home.mkdir(parents=True)
    toolchain_bin = tmp_path / ".rustup" / "toolchains" / "stable" / "bin"
    toolchain_bin.mkdir(parents=True)
    cargo = toolchain_bin / "cargo"
    cargo.write_text("#!/bin/sh\n", encoding="utf-8")
    cargo.chmod(0o755)
    shim = cargo_home / "cargo"
    shim.symlink_to(cargo)

    monkeypatch.setattr(executable_validation.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        executable_validation.shutil,
        "which",
        lambda _name: str(shim),
    )

    assert executable_validation.resolve_approved_executable("cargo") == str(shim)


def test_approved_cargo_rejects_user_home_fake(monkeypatch, tmp_path):
    """A regular user-home Cargo binary is not an approved toolchain shim."""
    cargo_home = tmp_path / ".cargo" / "bin"
    cargo_home.mkdir(parents=True)
    cargo = cargo_home / "cargo"
    cargo.write_text("#!/bin/sh\n", encoding="utf-8")
    cargo.chmod(0o755)

    monkeypatch.setattr(executable_validation.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        executable_validation.shutil,
        "which",
        lambda _name: str(cargo),
    )

    assert executable_validation.resolve_approved_executable("cargo") is None


def test_approved_rustfmt_accepts_rustup_toolchain_shim(monkeypatch, tmp_path):
    toolchain_bin = tmp_path / ".rustup" / "toolchains" / "stable" / "bin"
    toolchain_bin.mkdir(parents=True)
    rustfmt = toolchain_bin / "rustfmt"
    rustfmt.write_text("#!/bin/sh\n", encoding="utf-8")
    rustfmt.chmod(0o755)
    shim = tmp_path / ".cargo" / "bin" / "rustfmt"
    shim.parent.mkdir(parents=True)
    shim.symlink_to(rustfmt)

    monkeypatch.setattr(executable_validation.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        executable_validation.shutil,
        "which",
        lambda _name: str(shim),
    )

    assert executable_validation.resolve_approved_executable("rustfmt") == str(shim)


def test_approved_rustfmt_accepts_standard_rustup_dispatcher(
    monkeypatch, tmp_path
):
    """The standard Rustup dispatcher is accepted only with a real toolchain.

    The resolved executable must come from the *active* toolchain per
    Rustup's selection rules (RUSTUP_TOOLCHAIN env, then
    rust-toolchain.toml from cwd upward, then settings.toml
    default_toolchain).  The fixture pins the active toolchain via
    settings.toml so the dispatcher resolves to that toolchain's rustfmt.
    """
    rustup_bin = tmp_path / ".cargo" / "bin"
    rustup_bin.mkdir(parents=True)
    dispatcher = rustup_bin / "rustup"
    dispatcher.write_text("#!/bin/sh\n", encoding="utf-8")
    dispatcher.chmod(0o755)
    shim = rustup_bin / "rustfmt"
    shim.symlink_to(dispatcher)
    toolchain_rustfmt = (
        tmp_path / ".rustup" / "toolchains" / "stable" / "bin" / "rustfmt"
    )
    toolchain_rustfmt.parent.mkdir(parents=True)
    toolchain_rustfmt.write_text("#!/bin/sh\n", encoding="utf-8")
    toolchain_rustfmt.chmod(0o755)

    # Pin the active toolchain to `stable` so the dispatcher resolves to the
    # toolchain's rustfmt (Rustup selection rule 3: settings default).  Run
    # from the fixture directory so no repository rust-toolchain.toml can
    # shadow the settings default via the directory-scoped override.
    settings_path = tmp_path / ".rustup" / "settings.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        'default_toolchain = "stable"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Clear the runner-environment Rustup overrides so the settings.toml
    # default is the sole active-toolchain input: a leaked RUSTUP_TOOLCHAIN
    # (or RUSTUP_HOME pointing elsewhere) would otherwise redirect the
    # dispatcher to a different toolchain than the fixture pins.
    monkeypatch.delenv("RUSTUP_TOOLCHAIN", raising=False)
    monkeypatch.delenv("RUSTUP_HOME", raising=False)

    monkeypatch.setattr(executable_validation.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        executable_validation.shutil,
        "which",
        lambda _name: str(shim),
    )

    assert executable_validation.resolve_approved_executable("rustfmt") == str(shim)


def test_discover_fixtures_persists_validated_html_path(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    html_path = corpus_dir / "sample.html"
    html_path.write_text("<html><body>ok</body></html>", encoding="utf-8")

    meta_path = corpus_dir / "sample.meta.json"
    meta_path.write_text(json.dumps({"fixture-id": "sample"}), encoding="utf-8")

    fixtures = discover_fixtures(corpus_dir)

    assert len(fixtures) == 1
    assert fixtures[0]["_html_path"] == str(html_path.resolve())


def test_write_examples_uses_non_metadata_filenames(tmp_path, monkeypatch):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    meta_path = corpus_dir / "fixture.meta.json"
    html_path = corpus_dir / "fixture.html"
    meta_path.write_text(
        json.dumps({"fixture-id": "../../evil", "failure-corpus": False}),
        encoding="utf-8",
    )
    html_path.write_text("<html><body>ok</body></html>", encoding="utf-8")

    examples = [{"fixture-id": "../../evil", "page-type": "bad/type"}]
    fixtures_meta = [{
        "fixture-id": "../../evil",
        "failure-corpus": False,
        "_meta_path": str(meta_path),
    }]
    out_dir = tmp_path / "examples"

    monkeypatch.setattr(
        rcb, "run_converter", lambda _bin, _html: ("# converted\n", 0, 0.1),
    )
    write_examples(examples, fixtures_meta, "/bin/echo", out_dir)

    generated = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    assert generated == ["example-001.html", "example-001.md"]
