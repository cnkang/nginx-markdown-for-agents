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
