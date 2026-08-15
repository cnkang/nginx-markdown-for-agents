"""Tests for the tag release blocking-qualification set.

The tag release path (``.github/workflows/release-packages.yml``) must run the
same candidate-bound qualification stages as the Makefile
``release-gates-check-092`` target's [8/13]-[12/13] stages.  These tests pin
the two surfaces together so they cannot drift.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"
MAKEFILE = REPO_ROOT / "Makefile"

# Makefile stage name -> validator module that the stage invokes.  Must stay
# in lockstep with release-gates-check-092 stages [8/13]-[12/13].
QUALIFICATION_STAGES = {
    "release-candidate-evidence-check": "validate_release_candidate_evidence.py",
    "artifact-registry-check": "validate_artifact_registry.py",
    "release-evidence-manifest-check": "validate_release_evidence_manifest.py",
    "test-rust-fuzz-qualification": "validate_fuzz_qualification.py",
    "test-e2e-rust-soak": "validate_soak_qualification.py",
}


def _workflow_release_gate_body() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("  release-gate:")
    end = text.index("  integrity-checksums:", start)
    return text[start:end]


def _workflow_publish_body() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("  publish:")
    rest = text[start:]
    # Stop at the next top-level job marker so a later job cannot leak
    # into the publish job's own configuration.
    next_job = re.search(r"\n  [a-z][a-z0-9-]*:$", rest)
    if next_job is not None:
        rest = rest[: next_job.start()]
    return rest


def _makefile_092_target() -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.index("release-gates-check-092: release-gates-check-091")
    end = text.index("\nrelease-matrix-check:", start)
    return text[start:end]


def test_release_gate_job_runs_all_qualification_validators() -> None:
    body = _workflow_release_gate_body()
    for stage, validator in QUALIFICATION_STAGES.items():
        assert validator in body, (
            f"release-gate job must run {validator} (Makefile stage "
            f"'{stage}'); the tag release qualification set has drifted "
            "from release-gates-check-092"
        )


def test_makefile_092_runs_all_qualification_stages() -> None:
    target = _makefile_092_target()
    for stage in QUALIFICATION_STAGES:
        assert f"$(MAKE) {stage}" in target, (
            f"release-gates-check-092 must invoke '{stage}' as a blocking stage"
        )


def test_publish_hard_depends_on_release_gate() -> None:
    body = _workflow_publish_body()
    # The qualification validators run inside release-gate, so publish's
    # success condition on release-gate carries the qualification.
    assert "needs.release-gate.result == 'success'" in body
