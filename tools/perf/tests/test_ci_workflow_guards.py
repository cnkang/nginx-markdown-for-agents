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


def _module_baseline_job(text: str) -> str:
    """Return only the canonical module-baseline job block."""
    start = text.index("\n  module-baseline-091:")
    return text[start:]


def test_nightly_perf_generates_raw_baseline() -> None:
    """The workflow must generate the raw canonical baseline report."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "--output perf/baselines/module-baseline-091-raw.json" in block
    assert "run_module_benchmark.sh" in block


def test_nightly_perf_uses_finalizer_with_raw_input_and_output() -> None:
    """The finalizer must read --raw-input and write --output (not in-place)."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "finalize_module_baseline.py" in block
    assert "--raw-input" in block
    assert "--output" in block
    # Must not use the old in-place --input flag.
    assert "--input perf/baselines/module-baseline-091.json" not in block


def test_nightly_perf_records_raw_digest() -> None:
    """The finalizer computes source_artifact_sha256 from the raw file."""
    block = _module_baseline_job(_nightly_perf_text())
    # The finalizer CLI does not take --source-artifact-sha256; it computes
    # the digest internally.  Guard against re-introducing an in-place copy
    # that bypasses the raw binding.
    assert "cp perf/baselines/module-baseline-091-raw.json" not in block


def test_nightly_perf_validates_finalized_baseline() -> None:
    """The workflow must run evidence-gate validation on the finalized baseline."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "_validate_benchmark_evidence" in block
    assert "canonical module baseline validation failed" in block


def test_nightly_perf_uploads_raw_and_finalized() -> None:
    """The workflow must upload both raw and finalized baseline files."""
    block = _module_baseline_job(_nightly_perf_text())
    upload_start = block.index("- name: Upload canonical module baseline")
    debug_start = block.index("- name: Upload debug artifacts on failure")
    upload_block = block[upload_start:debug_start]
    assert "perf/baselines/module-baseline-091.json" in upload_block
    assert "perf/baselines/module-baseline-091-raw.json" in upload_block
    assert "perf/baselines/module-baseline-091-raw-probes/" in upload_block
    assert "if-no-files-found: error" in upload_block
    assert "retention-days: 30" in upload_block
    assert "retention-days: 14" not in upload_block
    assert "perf/baselines/module-baseline-091-probes/" not in block


def test_nightly_perf_uploads_debug_artifacts_on_failure() -> None:
    """The workflow must retain debug artifacts when benchmark or validation fails."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "module-baseline-091-debug-" in block
    debug_start = block.index("- name: Upload debug artifacts on failure")
    debug_block = block[debug_start:]
    assert "if: failure()" in debug_block
    assert "perf/baselines/module-baseline-091-raw-probes/" in debug_block
    assert "perf/baselines/module-baseline-091-probes/" not in debug_block


def test_nightly_perf_uploads_canonical_only_after_all_release_gates() -> None:
    """A canonical artifact must not survive a later gate failure."""
    block = _module_baseline_job(_nightly_perf_text())
    upload = block.index("- name: Upload canonical module baseline")
    evidence_gate = block.index("make perf-evidence-check")
    release_gates = block.index("make release-gates-check-091")

    assert evidence_gate < upload
    assert release_gates < upload


def test_nightly_perf_verifies_probe_completeness_before_finalization() -> None:
    """Deleting the raw probe gate must make this workflow guard fail."""
    block = _module_baseline_job(_nightly_perf_text())
    verify = block.index("- name: Verify raw module probe artifacts")
    finalize = block.index("- name: Finalize canonical module baseline")
    verify_block = block[verify:finalize]

    assert "validate_module_probe_artifacts.py" in verify_block
    assert "--probe-dir perf/baselines/module-baseline-091-raw-probes" in verify_block
    assert verify < finalize


def test_nightly_perf_cross_checks_probes_against_finalized_baseline() -> None:
    """Finalized response correctness must be checked before baseline gates."""
    block = _module_baseline_job(_nightly_perf_text())
    cross_check = block.index("- name: Cross-check finalized baseline response probes")
    validate = block.index("- name: Validate canonical module baseline")
    evidence = block.index("- name: Run canonical performance evidence check")
    cross_check_block = block[cross_check:validate]

    assert "validate_module_probe_artifacts.py" in cross_check_block
    assert "--baseline perf/baselines/module-baseline-091.json" in cross_check_block
    assert cross_check < validate < evidence


def test_nightly_perf_records_run_attempt_in_source_run() -> None:
    """The source_run URL must include the run attempt for traceability."""
    text = _nightly_perf_text()
    assert "github.run_attempt" in text
    assert "/attempts/" in text
