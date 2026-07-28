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


def test_release_gate_installs_and_preflights_pinned_brotli() -> None:
    """Tag releases must install the exact Python Brotli benchmark dependency."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "release-packages.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "python3 -m pip install --requirement requirements-perf.txt" in workflow
    assert "import brotli; print(brotli.__version__)" in workflow
    assert "Brotli==1.2.0" in (repo_root / "requirements-perf.txt").read_text(
        encoding="utf-8"
    )


def test_release_gate_requires_exact_tag_sha_checks() -> None:
    """Publication must bind the tag SHA to protected-main required checks."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "release-packages.yml").read_text(
        encoding="utf-8"
    )

    for snippet in (
        "Verify tag SHA is main CI-approved",
        "commits/${TAG_SHA}/check-runs",
        "has no active required status checks; refusing tag release",
        "passed all required checks",
    ):
        assert snippet in workflow
