"""Regression tests for the stale-symbol release gate."""

import subprocess

from tools.release.gates import check_stale_symbols


def test_stale_symbol_check_ignores_legacy_reference_docs(tmp_path):
    """Historical docs outside the 0.9 gate surface should not fail the gate."""
    repo = tmp_path
    (repo / "docs/guides").mkdir(parents=True)
    (repo / "docs/release").mkdir(parents=True)
    (repo / "docs/guides/legacy.md").write_text("markdown_timeout\n")
    (repo / "docs/release/current.md").write_text("markdown_limits timeout=1s\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(repo)

    assert exit_code == 0
    assert stdout == "No stale 0.8 symbols found."
    assert stderr == ""


def test_stale_symbol_check_fails_on_release_surface_leak(tmp_path):
    """Stale 0.8 symbols in checked 0.9 release surfaces should fail."""
    repo = tmp_path
    (repo / "docs/release").mkdir(parents=True)
    (repo / "docs/release/current.md").write_text("markdown_timeout\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(repo)

    assert exit_code == 1
    assert "STALE SYMBOLS DETECTED" in stdout
    assert "docs/release/current.md:1:markdown_timeout" in stdout
    assert stderr == ""


def test_stale_symbol_check_fails_when_git_is_missing(monkeypatch, tmp_path):
    """The gate should fail explicitly when git cannot be resolved."""
    def raise_missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(check_stale_symbols.subprocess, "run", raise_missing_git)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(tmp_path)

    assert exit_code == 1
    assert stdout == ""
    assert "Error listing tracked files with git:" in stderr
    assert "git" in stderr


def test_stale_symbol_check_fails_on_harness_rule_field_leak(tmp_path):
    """Naked old config field names in harness rules should fail."""
    repo = tmp_path
    (repo / "docs/harness/rules").mkdir(parents=True)
    (repo / "docs/harness/rules/dynconf-snapshot.md").write_text(
        "eligibility tests cover non-NULL eff memory_budget path\n"
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(repo)

    assert exit_code == 1
    assert "STALE SYMBOLS DETECTED" in stdout
    assert "docs/harness/rules/dynconf-snapshot.md:1:" in stdout
    assert "memory_budget" in stdout
    assert "(stale field)" in stdout
    assert stderr == ""


def test_stale_symbol_check_fails_when_git_times_out(monkeypatch, tmp_path):
    """The gate should fail explicitly when git ls-files hangs."""
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git", "ls-files"], timeout=15)

    monkeypatch.setattr(check_stale_symbols.subprocess, "run", raise_timeout)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(tmp_path)

    assert exit_code == 1
    assert stdout == ""
    assert stderr == "Error listing tracked files: git ls-files timed out after 15s"
