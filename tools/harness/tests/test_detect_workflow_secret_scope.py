"""Pytest tests for detect_workflow_secret_scope.py — secret-scope policy.

Validates that workflow secrets are scoped to the minimal consuming step,
not leaked into broad workflow-level or job-level env maps.

Rule 48 (security-static-analysis): workflow secrets are step-scoped to
their minimal consumer. Repository build, test, setup, and coverage steps
must not inherit unrelated credentials.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

import harness.detect_workflow_secret_scope as secret_scope_module
from harness.detect_workflow_secret_scope import (  # noqa: E402
    check_sonar_token_steps,
    find_broad_env_secrets,
    scan_workflows,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_workflow_secret_scope.py"


# ---------------------------------------------------------------------------
# find_broad_env_secrets — workflow/job-level env maps
# ---------------------------------------------------------------------------

class TestFindBroadEnvSecrets:
    """find_broad_env_secrets: secrets must not appear in workflow/job env."""

    def test_workflow_level_env_secret_detected(self):
        """A secret in the top-level env: block is a finding."""
        text = (
            "name: CI\n"
            "env:\n"
            "  SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo hi\n"
        )
        findings = find_broad_env_secrets(text, "ci.yml")
        assert len(findings) == 1
        assert findings[0].line == 3
        assert "forbidden in workflow/job env" in findings[0].message

    def test_job_level_env_secret_detected(self):
        """A secret in a job-level env: block is a finding."""
        text = (
            "name: CI\n"
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    env:\n"
            "      API_KEY: ${{ secrets.API_KEY }}\n"
            "    steps:\n"
            "      - run: echo hi\n"
        )
        findings = find_broad_env_secrets(text, "ci.yml")
        assert len(findings) == 1
        assert findings[0].line == 7

    def test_step_level_env_secret_allowed(self):
        """A secret scoped to a single step's env: is the correct pattern."""
        text = (
            "name: CI\n"
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Scan\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: sonar-scanner\n"
        )
        findings = find_broad_env_secrets(text, "ci.yml")
        assert findings == []

    def test_plain_env_no_secret_ignored(self):
        """A non-secret env var (no ${{ secrets.* }}) is not a finding."""
        text = (
            "name: CI\n"
            "env:\n"
            "  DEBUG: 'true'\n"
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo hi\n"
        )
        findings = find_broad_env_secrets(text, "ci.yml")
        assert findings == []


# ---------------------------------------------------------------------------
# check_sonar_token_steps — sonarcloud.yml-specific validation
# ---------------------------------------------------------------------------

class TestSonarTokenSteps:
    """check_sonar_token_steps: sonarcloud.yml scopes all Sonar tokens."""

    def test_valid_structure_passes(self):
        """Token check before checkout and both scans pass."""
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: |\n"
            "          if [ -z \"$SONAR_TOKEN\" ]; then exit 0; fi\n"
            "      - name: Checkout repository\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
            "      - name: SonarCloud Branch Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
        )
        findings = check_sonar_token_steps(text)
        assert findings == []

    def test_missing_scan_step_fails(self):
        """Missing the SonarCloud Scan step is a structural finding."""
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: SonarCloud Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
        )
        findings = check_sonar_token_steps(text)
        assert len(findings) >= 1

    def test_token_after_checkout_fails(self):
        """Token-check step must appear before checkout to avoid premature repo access."""
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Checkout repository\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: Check Sonar token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: echo check\n"
            "      - name: SonarCloud Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
        )
        findings = check_sonar_token_steps(text)
        assert any("before checkout" in f.message for f in findings)


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

class TestRequiredWorkflowPresence:
    """Missing sonarcloud.yml must fail closed, not pass silently."""

    def test_missing_sonarcloud_workflow_is_reported(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "workflows"
        root.mkdir()
        (root / "ci.yml").write_text("name: ci\non: [push]\n", encoding="utf-8")
        monkeypatch.setattr(secret_scope_module, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(secret_scope_module, "WORKFLOW_ROOT", root)

        findings = scan_workflows(root)

        assert any(
            "sonarcloud.yml" in f.path and "missing" in f.message
            for f in findings
        )


class TestCLI:
    """CLI contract: the detector must exit 0 or 1."""

    def test_cli_runs_and_returns_valid_exit_code(self, tmp_path: Path):
        result = subprocess.run(
            [sys.executable, str(DETECTOR)],
            capture_output=True, text=True,
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode == 0, f"expected exit 0, got {result.returncode}; stderr:\n{result.stderr}"
