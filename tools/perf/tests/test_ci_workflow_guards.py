"""Regression guards for CI workflow trigger coverage.

These tests ensure corpus benchmark automation keeps running when benchmark
tooling under tools/corpus changes.
"""

from __future__ import annotations

from pathlib import Path


def _ci_workflow_text() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    ci_path = repo_root / ".github" / "workflows" / "ci.yml"
    return ci_path.read_text(encoding="utf-8")


def test_changes_job_exposes_corpus_tools_output() -> None:
    """changes.outputs must include corpus_tools."""
    text = _ci_workflow_text()
    assert "corpus_tools: ${{ steps.filter.outputs.corpus_tools }}" in text


def test_paths_filter_tracks_tools_corpus_tree() -> None:
    """paths-filter rules must track tools/corpus changes."""
    text = _ci_workflow_text()
    assert "corpus_tools:" in text
    assert "- 'tools/corpus/**'" in text


def test_corpus_benchmark_job_triggers_on_corpus_tools_changes() -> None:
    """Corpus benchmark gate must run when corpus_tools output is true."""
    text = _ci_workflow_text()
    assert "needs.changes.outputs.corpus_tools == 'true'" in text
