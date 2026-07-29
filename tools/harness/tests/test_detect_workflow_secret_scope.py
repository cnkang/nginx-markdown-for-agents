"""Pytest tests for detect_workflow_secret_scope.py — secret-scope policy.

Validates that workflow secrets are scoped to the minimal consuming step,
not leaked into broad workflow-level or job-level env maps.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

from harness.detect_workflow_secret_scope import (  # noqa: E402
    Finding,
    check_sonar_token_steps,
    find_broad_env_secrets,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_workflow_secret_scope.py"


# ---------------------------------------------------------------------------
# find_broad_env_secrets — workflow/job-level env maps
# ---------------------------------------------------------------------------

class TestFindBroadEnvSecrets:
    def test_workflow_level_env_secret_detected(self):
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
    def test_valid_structure_passes(self):
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
        )
        findings = check_sonar_token_steps(text)
        assert findings == []

    def test_missing_scan_step_fails(self):
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

class TestCLI:
    def test_cli_runs_and_returns_valid_exit_code(self, tmp_path: Path):
        result = subprocess.run(
            [sys.executable, str(DETECTOR)],
            capture_output=True, text=True,
            cwd=tmp_path,
        )
        assert result.returncode in (0, 1)
