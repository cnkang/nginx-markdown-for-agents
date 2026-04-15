"""Regression guards for release-related GitHub workflows."""

from __future__ import annotations

from pathlib import Path


def _workflow_text(name: str) -> str:
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / ".github" / "workflows" / name
    return path.read_text(encoding="utf-8")


def test_release_binaries_only_checks_matrix_freshness_on_manual_dispatch() -> None:
    """Release-triggered binary builds must not run workflow-dispatch freshness logic."""
    text = _workflow_text("release-binaries.yml")
    assert "if: github.event_name == 'workflow_dispatch' && inputs.matrix_freshness != 'off'" in text


def test_update_matrix_pr_creation_is_non_blocking_when_repo_disallows_actions_prs() -> None:
    """Scheduled matrix refreshes should succeed even if PR creation is policy-blocked."""
    text = _workflow_text("update-matrix.yml")
    assert "continue-on-error: true" in text
    assert "Matrix update branch pushed, but automatic PR creation is blocked." in text
