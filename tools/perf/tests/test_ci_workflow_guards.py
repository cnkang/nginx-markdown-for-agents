"""Regression guards for CI workflow trigger coverage.

These tests ensure corpus benchmark automation keeps running when benchmark
tooling under tools/corpus changes.
"""

from __future__ import annotations

import re
import subprocess
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
    start = text.index("\n  module-baseline-092:")
    return text[start:]


def test_nightly_perf_generates_raw_baseline() -> None:
    """The workflow must generate the raw canonical baseline report."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "--output perf/baselines/module-baseline-092-raw.json" in block
    assert "run_module_benchmark.sh" in block


def test_nightly_perf_uses_finalizer_with_raw_input_and_output() -> None:
    """The finalizer must read --raw-input and write --output (not in-place)."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "finalize_module_baseline.py" in block
    assert "--raw-input" in block
    assert "--output" in block
    # Must not use the old in-place --input flag.
    assert "--input perf/baselines/module-baseline-092.json" not in block


def test_nightly_perf_records_raw_digest() -> None:
    """The finalizer computes source_artifact_sha256 from the raw file."""
    block = _module_baseline_job(_nightly_perf_text())
    # The finalizer CLI does not take --source-artifact-sha256; it computes
    # the digest internally.  Guard against re-introducing an in-place copy
    # that bypasses the raw binding.
    assert "cp perf/baselines/module-baseline-092-raw.json" not in block


def test_nightly_perf_validates_finalized_baseline() -> None:
    """The workflow must run evidence-gate validation on the finalized baseline."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "_validate_benchmark_evidence" in block
    assert "canonical module baseline validation failed" in block


def test_nightly_perf_selects_matching_baseline_version() -> None:
    """The 0.9.2 workflow must make the evidence gate read its 0.9.2 file."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "MODULE_BASELINE_VERSION=092" in block


def test_nightly_perf_uploads_raw_and_finalized() -> None:
    """The workflow must upload both raw and finalized baseline files."""
    block = _module_baseline_job(_nightly_perf_text())
    upload_start = block.index("- name: Upload canonical module baseline")
    debug_start = block.index("- name: Upload debug artifacts on failure")
    upload_block = block[upload_start:debug_start]
    assert "perf/baselines/module-baseline-092.json" in upload_block
    assert "perf/baselines/module-baseline-092-raw.json" in upload_block
    assert "perf/baselines/module-baseline-092-raw-probes/" in upload_block
    assert "if-no-files-found: error" in upload_block
    assert "retention-days: 30" in upload_block
    assert "retention-days: 14" not in upload_block
    assert "perf/baselines/module-baseline-091-probes/" not in block


def test_nightly_perf_uploads_debug_artifacts_on_failure() -> None:
    """The workflow must retain debug artifacts when benchmark or validation fails."""
    block = _module_baseline_job(_nightly_perf_text())
    assert "module-baseline-092-debug-" in block
    debug_start = block.index("- name: Upload debug artifacts on failure")
    debug_block = block[debug_start:]
    assert "if: failure()" in debug_block
    assert "perf/baselines/module-baseline-092-raw-probes/" in debug_block
    assert "perf/baselines/module-baseline-091-probes/" not in debug_block


def test_nightly_perf_uploads_canonical_only_after_all_release_gates() -> None:
    """A canonical artifact must not survive a later gate failure."""
    block = _module_baseline_job(_nightly_perf_text())
    upload = block.index("- name: Upload canonical module baseline")
    evidence_gate = block.index("make perf-evidence-check")
    release_gates = block.index("make release-gates-check-092")

    assert evidence_gate < upload
    assert release_gates < upload


def test_nightly_perf_verifies_probe_completeness_before_finalization() -> None:
    """Deleting the raw probe gate must make this workflow guard fail."""
    block = _module_baseline_job(_nightly_perf_text())
    verify = block.index("- name: Verify raw module probe artifacts")
    finalize = block.index("- name: Finalize canonical module baseline")
    verify_block = block[verify:finalize]

    assert "validate_module_probe_artifacts.py" in verify_block
    assert "--probe-dir perf/baselines/module-baseline-092-raw-probes" in verify_block
    assert verify < finalize


def test_nightly_perf_cross_checks_probes_against_finalized_baseline() -> None:
    """Finalized response correctness must be checked before baseline gates."""
    block = _module_baseline_job(_nightly_perf_text())
    cross_check = block.index("- name: Cross-check finalized baseline response probes")
    validate = block.index("- name: Validate canonical module baseline")
    evidence = block.index("- name: Run canonical performance evidence check")
    cross_check_block = block[cross_check:validate]

    assert "validate_module_probe_artifacts.py" in cross_check_block
    assert "--baseline perf/baselines/module-baseline-092.json" in cross_check_block
    assert cross_check < validate < evidence


def test_nightly_perf_records_run_attempt_in_source_run() -> None:
    """The source_run URL must include the run attempt for traceability."""
    text = _nightly_perf_text()
    assert "github.run_attempt" in text
    assert "/attempts/" in text


# ---------------------------------------------------------------------------
# Makefile release-gate baseline execution guards
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _make_dry_run(target: str) -> str:
    """Return the dry-run recipe text for a make target.

    Runs ``make -n`` so the guards reflect the actual commands make would
    execute without needing NGINX_BIN or other environment prerequisites.
    """
    result = subprocess.run(
        ["make", "-n", target],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _blocking_evidence_invocations(text: str) -> list[tuple[int, str]]:
    """Return (line_index, baseline_version) for each distinct blocking
    evidence recipe invocation.

    A blocking evidence recipe is identified by a
    ``release-perf-evidence-blocking BASELINE_VERSION=0NN`` make call.  Each
    such call represents exactly one blocking evidence execution against the
    named baseline (the helper's internal if/else branches select one path at
    run time).  This avoids double-counting the sibling NGINX_BIN and
    --allow-skip-module lines that share a single baseline within one recipe.
    """
    invocations: list[tuple[int, str]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.search(
            r"release-perf-evidence-blocking\s+BASELINE_VERSION=([0-9]+)", line
        )
        if m:
            invocations.append((i, m.group(1)))
    return invocations


def test_make_091_gate_runs_blocking_evidence_with_baseline_091() -> None:
    """``make -n release-gates-check-091`` must invoke blocking evidence
    against baseline 091 exactly once."""
    invocations = _blocking_evidence_invocations(
        _make_dry_run("release-gates-check-091")
    )
    baselines = [b for _, b in invocations]
    assert baselines == ["091"], (
        f"expected exactly one blocking evidence run with baseline 091, "
        f"got {baselines}"
    )


def test_make_091_gate_does_not_reference_baseline_092() -> None:
    """The 091 gate must not leak the 092 baseline into its recipe."""
    text = _make_dry_run("release-gates-check-091")
    assert "MODULE_BASELINE_VERSION=\"092\"" not in text, (
        "091 gate recipe must not set MODULE_BASELINE_VERSION=092"
    )


def test_make_092_gate_runs_blocking_evidence_with_both_baselines() -> None:
    """``make -n release-gates-check-092`` must run blocking evidence for
    baseline 091 (via the 091 prerequisite) then baseline 092, each exactly
    once."""
    invocations = _blocking_evidence_invocations(
        _make_dry_run("release-gates-check-092")
    )
    baselines = [b for _, b in invocations]
    assert baselines.count("091") == 1, (
        f"expected baseline 091 exactly once, got {baselines}"
    )
    assert baselines.count("092") == 1, (
        f"expected baseline 092 exactly once, got {baselines}"
    )


def test_make_092_gate_runs_091_before_092() -> None:
    """Within the 092 gate dry-run, the 091 baseline invocation must occur
    before the 092 baseline invocation."""
    invocations = _blocking_evidence_invocations(
        _make_dry_run("release-gates-check-092")
    )
    positions = {b: i for i, b in invocations}
    assert "091" in positions, (
        f"expected baseline 091 in positions, got {list(positions)}"
    )
    assert "092" in positions, (
        f"expected baseline 092 in positions, got {list(positions)}"
    )
    assert positions["091"] < positions["092"], (
        "092 blocking evidence must run after the 091 prerequisite"
    )


def test_make_092_gate_runs_092_evidence_before_static_checks() -> None:
    """The 092 blocking evidence must run before the 092 static/contract
    checks so a NO_GO verdict blocks the rest of the gate."""
    text = _make_dry_run("release-gates-check-092")
    invocations = _blocking_evidence_invocations(text)
    pos_092 = next(i for i, b in invocations if b == "092")
    static_markers = [
        "public-surface-drift-check",
        "validate_release_gates_092.py",
        "detect_version_consistency.sh",
    ]
    for marker in static_markers:
        marker_pos = text.find(marker)
        assert marker_pos != -1, f"missing static check marker: {marker}"
        # Compare line indices for a stable ordering assertion.
        marker_line = text[:marker_pos].count("\n")
        assert marker_line > pos_092, (
            f"{marker} (line {marker_line}) must run after 092 evidence "
            f"(line {pos_092})"
        )


def test_make_release_perf_evidence_blocking_requires_baseline() -> None:
    """The shared helper must fail when BASELINE_VERSION is omitted."""
    result = subprocess.run(
        ["make", "-n", "release-perf-evidence-blocking"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
    )
    # The dry-run still emits the guard; the actual exit code is only
    # observable at execution time, but the recipe must contain the
    # fail-closed branch for a missing BASELINE_VERSION.
    assert "requires BASELINE_VERSION" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# PR CI blocking 0.9.2 contract gate coverage
# ---------------------------------------------------------------------------

def test_pr_ci_has_blocking_092_contract_checks() -> None:
    """PR CI must block on the 0.9.2 contract checks that do not require a
    real module binary: public-surface drift, version consistency, reason
    code registry validator, and the OTel request-scoped unit test."""
    text = _ci_workflow_text()
    # The 092 contract checks must appear in the PR CI file.
    assert "make public-surface-drift-check" in text, (
        "PR CI must block on public-surface drift check"
    )
    assert (
        "PYTHONPATH=. python3 tools/release/gates/validate_release_gates_092.py"
        in text
    ), "PR CI must block on the 0.9.2 release gate validator"
    assert "bash tools/harness/detect_version_consistency.sh" in text, (
        "PR CI must block on version consistency"
    )
    assert "make -C components/nginx-module/tests unit-otel_impl" in text, (
        "PR CI must block on the OTel request-scoped unit test"
    )


def test_pr_ci_092_contract_checks_are_blocking() -> None:
    """The 0.9.2 contract checks in PR CI must not be marked
    continue-on-error."""
    text = _ci_workflow_text()
    # The nginx-c-tests job hosts the 092 contract checks.  Find the job
    # block and ensure none of the 092 contract step lines are guarded by
    # continue-on-error.
    job_start = text.index("  nginx-c-tests:")
    job_block = text[job_start:]
    # Truncate at the next top-level job definition.
    next_job = job_block.find("\n  ", 4)
    # Find the end of the nginx-c-tests job by locating the next job at the
    # same indentation level (two-space indent).
    remaining = text[job_start + len("  nginx-c-tests:"):]
    next_job_match = re.search(r"\n  [a-z][a-z0-9-]*:", remaining)
    if next_job_match:
        job_block = job_block[:next_job_match.start() + len("  nginx-c-tests:")]
    assert "continue-on-error: true" not in job_block, (
        "nginx-c-tests job (hosting 092 contract checks) must not set "
        "continue-on-error: true"
    )


def test_tag_workflow_uses_092_blocking_evidence() -> None:
    """The tag-only release-gate job must run blocking evidence against
    baseline 092 (not the 091 default) and must not default-skip module
    evidence."""
    repo_root = _repo_root()
    workflow = (
        repo_root / ".github" / "workflows" / "release-packages.yml"
    ).read_text(encoding="utf-8")
    gate_start = workflow.index("  release-gate:")
    gate_block = workflow[gate_start:]
    assert "MODULE_BASELINE_VERSION=092" in gate_block, (
        "tag release-gate job must set MODULE_BASELINE_VERSION=092 for "
        "blocking evidence"
    )
    assert "MODULE_BASELINE_VERSION=091" not in gate_block, (
        "tag release-gate job must not fall back to baseline 091"
    )
    assert "evidence_gate.py --mode blocking" in gate_block
    assert "RELEASE_GATE_ALLOW_SKIP_MODULE=1" not in gate_block, (
        "tag release-gate job must not default-skip module evidence"
    )
