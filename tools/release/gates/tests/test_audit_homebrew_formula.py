"""Regression tests for the Homebrew formula audit script's temporary state."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def test_audit_uses_an_exclusive_temporary_config_directory() -> None:
    """Per-run Homebrew state is created by mktemp, not a predictable PID path."""
    repo_root = Path(__file__).resolve().parents[4]
    audit_script = repo_root / "packaging/scripts/audit_homebrew_formula.sh"
    source = audit_script.read_text(encoding="utf-8")
    config_assignment = next(
        line for line in source.splitlines()
        if line.startswith("AUDIT_CONFIG=") and "mktemp" in line
    )
    assert "mktemp -d" in config_assignment
    assert "XXXXXX" in config_assignment
    assert audit_script.stat().st_mode & 0o111


def test_homebrew_target_skips_when_brew_is_unavailable() -> None:
    """The target skips with its documented message, never a hard error.

    A macOS host without Homebrew is possible (minimal images, CI mac
    containers).  The audit script exits 1 in that case, so the target
    gate itself checks `brew` and keeps the documented skip message.
    """
    repo_root = Path(__file__).resolve().parents[4]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    recipe = next(
        line for line in makefile.splitlines()
        if "Homebrew formula audit runs on macOS" in line
    )
    condition = makefile.split("homebrew-formula-check:", 1)[1].split(
        recipe, 1
    )[0]
    assert 'command -v brew' in condition, (
        "the target must check for brew itself, not only the OS"
    )
    assert "SKIP: Homebrew formula audit runs on macOS with Homebrew" in recipe


def test_homebrew_gate_path_filters_cover_script_and_makefile() -> None:
    """PR and push filters both run when the audit gate changes."""
    repo_root = Path(__file__).resolve().parents[4]
    workflow = (
        repo_root / ".github/workflows/homebrew-formula-gate.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count('"packaging/scripts/audit_homebrew_formula.sh"') == 2
    assert workflow.count('"Makefile"') == 2


def test_audit_fails_clearly_when_homebrew_is_missing(tmp_path: Path) -> None:
    """A Linux-like PATH without brew fails before creating temporary state."""
    repo_root = Path(__file__).resolve().parents[4]
    script = repo_root / "packaging/scripts/audit_homebrew_formula.sh"
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    dirname = shutil.which("dirname")
    assert dirname is not None
    (mock_bin / "dirname").symlink_to(dirname)
    env = os.environ.copy()
    env.update({"PATH": str(mock_bin), "TMPDIR": str(tmp_path)})

    completed = subprocess.run(
        ["/bin/bash", str(script)],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 1
    assert "Homebrew is required to audit the formula" in completed.stderr
    assert not list(tmp_path.glob("homebrew-formula-check.*"))


def test_audit_removes_trust_lock_on_success(tmp_path: Path) -> None:
    """A successful mocked audit removes its private trust lock and config."""
    repo_root = Path(__file__).resolve().parents[4]
    script = repo_root / "packaging/scripts/audit_homebrew_formula.sh"
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    brew = mock_bin / "brew"
    brew.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "case \"${1:-}\" in\n"
        "  tap-new) exit 0 ;;\n"
        "  --repository) printf '%s\\n' \"${TAP_PATH:?}\" ;;\n"
        "  help) exit 0 ;;\n"
        "  trust)\n"
        "    mkdir -p \"${XDG_CONFIG_HOME}/homebrew\"\n"
        "    : > \"${XDG_CONFIG_HOME}/homebrew/trust.json\"\n"
        "    : > \"${XDG_CONFIG_HOME}/homebrew/trust.json.lock\"\n"
        "    ;;\n"
        "  audit|untap) exit 0 ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    brew.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{mock_bin}{os.pathsep}{env['PATH']}",
            "TMPDIR": str(tmp_path),
            "TAP_PATH": str(tmp_path / "mock-tap"),
        }
    )

    completed = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert not list(tmp_path.glob("homebrew-formula-check.*"))


def test_formula_recognizes_tap_qualified_openssl_dependencies() -> None:
    """A tap-qualified openssl dependency is still recognized.

    Homebrew's ``Dependency#name`` keeps a tap-qualified declaration whole
    (``Dependency.new("homebrew/core/openssl@3").name`` is that exact
    string - verified with ``brew ruby``), so a match anchored to the
    first character refuses it and the build dies with "Unable to detect
    Homebrew nginx OpenSSL dependency".  The match reads the final
    slash-separated component instead; the returned spelling stays as
    declared because the opt-prefix helpers strip the tap themselves
    (``Utils.name_from_full_name``).
    """
    if shutil.which("brew") is None:
        import pytest

        pytest.skip("Homebrew is not available")

    repo_root = Path(__file__).resolve().parents[4]
    formula = repo_root / "packaging/homebrew/nginx-markdown-module.rb"
    source = formula.read_text(encoding="utf-8")

    # The matching line resolves the bare name through Homebrew's own
    # helper, which the formula audit requires over manual splitting.
    match_line = next(
        line for line in source.splitlines()
        if "dependency.name" in line and "match?" in line
    )
    assert "Utils.name_from_full_name(dependency.name).match?" in match_line, (
        match_line
    )
    # Neither the first-anchored form nor a manual split may remain.
    assert "dependency.name.match?(/\\Aopenssl" not in source
    assert "dependency.name.split" not in source

    # The behavior runs against real Dependency values.
    probe = (
        'require "dependency"\n'
        'm = ->(n) { Utils.name_from_full_name(n).match?(/\\Aopenssl(?:@\\d+)?\\z/) }\n'
        'puts m.call(Dependency.new("openssl@3").name)\n'
        'puts m.call(Dependency.new("homebrew/core/openssl@3").name)\n'
        'puts m.call(Dependency.new("pcre2").name)\n'
    )
    result = subprocess.run(
        ["brew", "ruby", "-e", probe],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["true", "true", "false"], result.stdout
