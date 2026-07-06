"""Regression tests for the stale-symbol release gate."""

import subprocess

from tools.release.gates import check_stale_symbols
from tools.release.gates.check_stale_symbols import _git_cmd


def test_stale_symbol_check_ignores_whitelisted_migration_docs(tmp_path):
    """Whitelisted migration/reference docs should not fail the gate."""
    repo = tmp_path
    (repo / "docs/guides").mkdir(parents=True)
    (repo / "docs/guides/MIGRATION-0.9.md").write_text("markdown_timeout\n")
    subprocess.run(_git_cmd() + ["init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(_git_cmd() + ["add", "."], cwd=repo, check=True)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(repo)

    assert exit_code == 0
    assert stdout == "No stale 0.8 symbols found."
    assert stderr == ""


def test_stale_symbol_check_fails_on_non_whitelisted_guide(tmp_path):
    """Non-whitelisted guide docs with stale symbols should fail."""
    repo = tmp_path
    (repo / "docs/guides").mkdir(parents=True)
    (repo / "docs/guides/legacy.md").write_text("markdown_timeout\n")
    subprocess.run(_git_cmd() + ["init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(_git_cmd() + ["add", "."], cwd=repo, check=True)

    exit_code, stdout, stderr = check_stale_symbols.run_stale_symbol_check(repo)

    assert exit_code == 1
    assert "STALE SYMBOLS DETECTED" in stdout
    assert "docs/guides/legacy.md:1:markdown_timeout" in stdout
    assert stderr == ""


def test_stale_symbol_check_fails_on_release_surface_leak(tmp_path):
    """Stale 0.8 symbols in checked 0.9 release surfaces should fail."""
    repo = tmp_path
    (repo / "docs/release").mkdir(parents=True)
    (repo / "docs/release/current.md").write_text("markdown_timeout\n")
    subprocess.run(_git_cmd() + ["init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(_git_cmd() + ["add", "."], cwd=repo, check=True)

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
    subprocess.run(_git_cmd() + ["init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(_git_cmd() + ["add", "."], cwd=repo, check=True)

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
