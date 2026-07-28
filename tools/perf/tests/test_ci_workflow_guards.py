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


def test_release_gate_installs_and_preflights_pinned_dependencies() -> None:
    """Tag releases must install exact Python release-gate dependencies."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "release-packages.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "python3 -m pip install --requirement requirements-release.txt" in workflow
    assert "import brotli, yaml; print(brotli.__version__, yaml.__version__)" in workflow
    assert "Brotli==1.2.0" in (repo_root / "requirements-perf.txt").read_text(
        encoding="utf-8"
    )
    assert "PyYAML==6.0.2" in (repo_root / "requirements-release.txt").read_text(
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
        "rules/branches/${DEFAULT_BRANCH}?per_page=100",
        "gh api --paginate --slurp",
        "verify_tag_sha_checks.py",
        "commits/${TAG_SHA}/check-runs",
        "commits/${TAG_SHA}/status",
        "--statuses-file",
    ):
        assert snippet in workflow
    gate = (repo_root / "tools" / "release" / "gates" / "verify_tag_sha_checks.py").read_text(
        encoding="utf-8"
    )
    assert "has no active required status checks; refusing tag release" in gate
    assert "passed all required checks" in gate
    assert "/branches/${DEFAULT_BRANCH}/protection" not in workflow
    assert "rulesets?includes_parents=true" not in workflow


def test_release_gate_declares_statuses_read_permission() -> None:
    """The release-gate job must declare statuses: read for the Commit Status API."""
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "release-packages.yml").read_text(
        encoding="utf-8"
    )

    assert "statuses: read" in workflow


# ---------------------------------------------------------------------------
# Nightly perf workflow guards (canonical module baseline generation)
# ---------------------------------------------------------------------------


def _nightly_perf_text() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / ".github" / "workflows" / "nightly-perf.yml").read_text(
        encoding="utf-8"
    )


def test_nightly_perf_generates_raw_baseline() -> None:
    """The workflow must generate the raw canonical baseline report."""
    text = _nightly_perf_text()
    assert "module-baseline-091-raw.json" in text
    assert "run_module_benchmark.sh" in text


def test_nightly_perf_uses_finalizer_with_raw_input_and_output() -> None:
    """The finalizer must read --raw-input and write --output (not in-place)."""
    text = _nightly_perf_text()
    assert "finalize_module_baseline.py" in text
    assert "--raw-input" in text
    assert "--output" in text
    # Must not use the old in-place --input flag.
    assert "--input perf/baselines/module-baseline-091.json" not in text


def test_nightly_perf_records_raw_digest() -> None:
    """The finalizer computes source_artifact_sha256 from the raw file."""
    text = _nightly_perf_text()
    # The finalizer CLI does not take --source-artifact-sha256; it computes
    # the digest internally.  Guard against re-introducing an in-place copy
    # that bypasses the raw binding.
    assert "cp perf/baselines/module-baseline-091-raw.json" not in text


def test_nightly_perf_validates_finalized_baseline() -> None:
    """The workflow must run evidence-gate validation on the finalized baseline."""
    text = _nightly_perf_text()
    assert "_validate_benchmark_evidence" in text
    assert "canonical module baseline validation failed" in text


def test_nightly_perf_uploads_raw_and_finalized() -> None:
    """The workflow must upload both raw and finalized baseline files."""
    text = _nightly_perf_text()
    assert "module-baseline-091.json" in text
    assert "module-baseline-091-raw.json" in text
    assert "if-no-files-found: error" in text


def test_nightly_perf_uploads_debug_artifacts_on_failure() -> None:
    """The workflow must retain debug artifacts when benchmark or validation fails."""
    text = _nightly_perf_text()
    assert "module-baseline-091-debug-" in text
    assert "if: always()" in text


def test_nightly_perf_records_run_attempt_in_source_run() -> None:
    """The source_run URL must include the run attempt for traceability."""
    text = _nightly_perf_text()
    assert "github.run_attempt" in text
    assert "/attempts/" in text
