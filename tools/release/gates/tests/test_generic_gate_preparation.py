"""Regression tests for all five generic release gates in fixture mode.

Tests each gate against both valid and negative fixtures to confirm
fail-closed semantics and identifiable rejection reasons.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "release"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_gate(script: str, fixture: str, extra_args: list[str] | None = None
              ) -> subprocess.CompletedProcess:
    """Run a gate validator in fixture mode and return the result."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "release" / "gates" / script),
        "--mode", "fixture",
        "--record-input", str(FIXTURE_DIR / fixture),
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=30, check=False)


# The canonical candidate_sha used across all valid fixtures.
EXPECTED_SHA = "9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d"


# ---------------------------------------------------------------------------
# Release Candidate Evidence Gate
# ---------------------------------------------------------------------------

class TestReleaseCandidateEvidence:
    """Tests for validate_release_candidate_evidence.py."""

    SCRIPT = "validate_release_candidate_evidence.py"

    def test_valid_passes(self):
        result = _run_gate(self.SCRIPT, "candidate-valid.json")
        assert result.returncode == 0
        assert "PASS:" in result.stdout

    def test_malformed_fails(self):
        result = _run_gate(self.SCRIPT, "candidate-malformed.json")
        assert result.returncode == 1
        assert "malformed" in result.stderr

    def test_stale_digest_fails(self):
        result = _run_gate(
            self.SCRIPT, "candidate-stale-digest.json",
            ["--expected-sha", EXPECTED_SHA])
        assert result.returncode == 1
        assert "stale-digest" in result.stderr

    def test_blocking_pending_fails(self):
        result = _run_gate(self.SCRIPT, "candidate-blocking-pending.json")
        assert result.returncode == 1
        assert "blocking-pending" in result.stderr

    def test_below_threshold_fails(self):
        result = _run_gate(self.SCRIPT, "candidate-below-threshold.json")
        assert result.returncode == 1
        assert "below-threshold" in result.stderr

    def test_missing_observation_fails(self):
        result = _run_gate(self.SCRIPT, "candidate-missing-observation.json")
        assert result.returncode == 1
        assert "missing-observation" in result.stderr


# ---------------------------------------------------------------------------
# Artifact Registry Gate
# ---------------------------------------------------------------------------

class TestArtifactRegistry:
    """Tests for validate_artifact_registry.py."""

    SCRIPT = "validate_artifact_registry.py"

    def test_valid_passes(self):
        result = _run_gate(self.SCRIPT, "artifact-registry-valid.json")
        assert result.returncode == 0
        assert "PASS:" in result.stdout

    def test_malformed_fails(self):
        result = _run_gate(self.SCRIPT, "artifact-registry-malformed.json")
        assert result.returncode == 1
        assert "malformed" in result.stderr

    def test_stale_digest_fails(self):
        result = _run_gate(
            self.SCRIPT, "artifact-registry-stale-digest.json",
            ["--expected-sha", EXPECTED_SHA])
        assert result.returncode == 1
        assert "stale-digest" in result.stderr

    def test_duplicate_artifact_registry_id_fails(self):
        result = _run_gate(self.SCRIPT, "artifact-registry-duplicate-id.json")
        assert result.returncode == 1
        assert "duplicate" in result.stderr

    def test_invalid_digest_algorithm_fails(self):
        result = _run_gate(
            self.SCRIPT, "artifact-registry-invalid-digest-algorithm.json"
        )
        assert result.returncode == 1
        assert "valid sha256 format" in result.stderr

    def test_missing_observation_fails(self):
        result = _run_gate(self.SCRIPT, "artifact-registry-missing-observation.json")
        assert result.returncode == 1
        assert "missing-observation" in result.stderr


# ---------------------------------------------------------------------------
# Release Evidence Manifest Gate
# ---------------------------------------------------------------------------

class TestReleaseEvidenceManifest:
    """Tests for validate_release_evidence_manifest.py."""

    SCRIPT = "validate_release_evidence_manifest.py"

    def test_valid_passes(self):
        result = _run_gate(self.SCRIPT, "final-evidence-valid.json")
        assert result.returncode == 0
        assert "PASS:" in result.stdout

    def test_malformed_fails(self):
        result = _run_gate(self.SCRIPT, "final-evidence-malformed.json")
        assert result.returncode == 1
        assert "malformed" in result.stderr

    def test_stale_digest_fails(self):
        result = _run_gate(
            self.SCRIPT, "final-evidence-stale-digest.json",
            ["--expected-sha", EXPECTED_SHA])
        assert result.returncode == 1
        assert "stale-digest" in result.stderr

    def test_blocking_pending_fails(self):
        result = _run_gate(self.SCRIPT, "final-evidence-blocking-pending.json")
        assert result.returncode == 1
        assert "blocking-pending" in result.stderr

    def test_below_threshold_fails(self):
        result = _run_gate(self.SCRIPT, "final-evidence-below-threshold.json")
        assert result.returncode == 1
        assert "below-threshold" in result.stderr

    def test_missing_observation_fails(self):
        result = _run_gate(self.SCRIPT, "final-evidence-missing-observation.json")
        assert result.returncode == 1
        assert "missing-observation" in result.stderr


# ---------------------------------------------------------------------------
# Fuzz Qualification Gate (existing validator, fixture mode)
# ---------------------------------------------------------------------------

class TestFuzzQualification:
    """Tests for validate_fuzz_qualification.py in fixture mode."""

    SCRIPT = "validate_fuzz_qualification.py"
    MANIFEST_ARGS = [
        "--manifest", str(FIXTURE_DIR / "fuzz-qualification-manifest.json")
    ]

    def test_valid_passes(self):
        result = _run_gate(
            self.SCRIPT, "fuzz-qualification-valid.json", self.MANIFEST_ARGS)
        assert result.returncode == 0
        assert "PASS:" in result.stdout

    def test_malformed_fails(self):
        result = _run_gate(
            self.SCRIPT, "fuzz-qualification-malformed.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "malformed" in result.stderr

    def test_stale_digest_fails(self):
        result = _run_gate(
            self.SCRIPT, "fuzz-qualification-stale-digest.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "stale-digest" in result.stderr

    def test_blocking_pending_fails(self):
        result = _run_gate(
            self.SCRIPT, "fuzz-qualification-blocking-pending.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "blocking-pending" in result.stderr

    def test_below_threshold_fails(self):
        result = _run_gate(
            self.SCRIPT, "fuzz-qualification-below-threshold.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "below-threshold" in result.stderr

    def test_missing_observation_fails(self):
        result = _run_gate(
            self.SCRIPT, "fuzz-qualification-missing-observation.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "missing-observation" in result.stderr


# ---------------------------------------------------------------------------
# Soak Qualification Gate (existing validator, fixture mode)
# ---------------------------------------------------------------------------

class TestSoakQualification:
    """Tests for validate_soak_qualification.py in fixture mode."""

    SCRIPT = "validate_soak_qualification.py"
    MANIFEST_ARGS = [
        "--manifest", str(FIXTURE_DIR / "soak-qualification-manifest.json")
    ]

    def test_valid_passes(self):
        result = _run_gate(
            self.SCRIPT, "soak-qualification-valid.json", self.MANIFEST_ARGS)
        assert result.returncode == 0
        assert "PASS:" in result.stdout

    def test_malformed_fails(self):
        result = _run_gate(
            self.SCRIPT, "soak-qualification-schema-invalid.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "missing-observation" in result.stderr

    def test_stale_digest_fails(self):
        result = _run_gate(
            self.SCRIPT, "soak-qualification-stale-digest.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "stale-digest" in result.stderr

    def test_blocking_pending_fails(self):
        result = _run_gate(
            self.SCRIPT, "soak-qualification-blocking-pending.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "blocking-pending" in result.stderr

    def test_below_threshold_fails(self):
        result = _run_gate(
            self.SCRIPT, "soak-qualification-below-threshold.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "below-threshold" in result.stderr

    def test_missing_observation_fails(self):
        result = _run_gate(
            self.SCRIPT, "soak-qualification-missing-observation.json",
            self.MANIFEST_ARGS)
        assert result.returncode == 1
        assert "missing-observation" in result.stderr
