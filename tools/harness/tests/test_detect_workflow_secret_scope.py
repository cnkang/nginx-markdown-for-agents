"""Pytest tests for detect_workflow_secret_scope.py — secret-scope policy.

Validates that workflow secrets are scoped to the minimal consuming step,
not leaked into broad workflow-level or job-level env maps.

Rule 48 (security-static-analysis): workflow secrets are step-scoped to
their minimal consumer. Repository build, test, setup, and coverage steps
must not inherit unrelated credentials.

The sonarcloud fixtures mirror the real workflow text (the presence check
publishes a gate through $GITHUB_OUTPUT and every later scanner step carries
an if: gating on it), and check_sonar_gate_wiring() asserts that wiring so
the fixture cannot drift away from the workflow it stands for.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

import harness.detect_workflow_secret_scope as secret_scope_module  # noqa: E402
from harness.detect_workflow_secret_scope import (  # noqa: E402
    check_sonar_gate_wiring,
    check_sonar_token_steps,
    find_broad_env_secrets,
    scan_workflows,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_workflow_secret_scope.py"
WORKFLOW = TOOLS_DIR.parent / ".github" / "workflows" / "sonarcloud.yml"


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

    def test_workflow_level_env_bracket_secret_detected(self):
        """A ${{ secrets['X'] }} selector in top-level env is a finding too."""
        text = (
            "name: CI\n"
            "env:\n"
            "  TOKEN: ${{ secrets['SONAR_TOKEN'] }}\n"
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

    def test_job_level_env_bracket_secret_detected(self):
        """A bracket-form secret in a job-level env: block is a finding."""
        text = (
            "name: CI\n"
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    env:\n"
            "      API_KEY: ${{ secrets['API_KEY'] }}\n"
            "    steps:\n"
            "      - run: echo hi\n"
        )
        findings = find_broad_env_secrets(text, "ci.yml")
        assert len(findings) == 1
        assert findings[0].line == 7

    def test_step_level_env_bracket_secret_allowed(self):
        """A bracket-form secret scoped to a step's env stays allowed."""
        text = (
            "name: CI\n"
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Scan\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets['SONAR_TOKEN'] }}\n"
            "        run: sonar-scanner\n"
        )
        findings = find_broad_env_secrets(text, "ci.yml")
        assert findings == []


# ---------------------------------------------------------------------------
# check_sonar_token_steps — sonarcloud.yml-specific validation
# ---------------------------------------------------------------------------

class TestSonarTokenSteps:
    """check_sonar_token_steps: sonarcloud.yml scopes all Sonar tokens."""

    def test_valid_structure_passes(self):
        """Token check before checkout and both scans pass.

        Mirrors the real sonarcloud.yml: the presence check has an id and
        publishes the gate through $GITHUB_OUTPUT, and both scan steps carry
        the if: gating on it.
        """
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        id: token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        shell: bash\n"
            "        run: |\n"
            "          if [[ -n \"${SONAR_TOKEN:-}\" ]]; then\n"
            "            echo \"enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
            "          else\n"
            "            echo \"enabled=false\" >> \"$GITHUB_OUTPUT\"\n"
            "          fi\n"
            "      - name: Checkout repository\n"
            "        if: steps.token.outputs.enabled == 'true'\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        if: steps.token.outputs.enabled == 'true'\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
            "      - name: SonarCloud Branch Scan\n"
            "        if: steps.token.outputs.enabled == 'true'\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
        )
        findings = check_sonar_token_steps(text)
        assert findings == []
        assert check_sonar_gate_wiring(text) == []

    def test_gate_wiring_matches_real_workflow(self):
        """The fixture above and the shipped workflow share the same wiring.

        The old fixture carried an exit-only presence check that no
        longer matched sonarcloud.yml, so it could not catch the regression the
        detector is supposed to catch.  Both texts now publish the same gate
        and carry the same if: wiring; assert that directly.
        """
        real = WORKFLOW.read_text(encoding="utf-8")
        assert check_sonar_gate_wiring(real) == []
        # The real workflow publishes its gate through a step output and every
        # token-consuming scanner step is conditioned on it.
        assert "id: token" in real
        assert '"$GITHUB_OUTPUT"' in real or "$GITHUB_OUTPUT" in real
        scanner_gates = [
            line for line in real.splitlines()
            if "steps.token.outputs.enabled" in line
        ]
        assert len(scanner_gates) >= 3

    def test_gate_dropped_from_scanners_is_reported(self):
        """A scanner step without the if: gate is a finding.

        Regression shape: the presence check still publishes enabled=true, but
        a scan step lost its if: condition and would run with an unset token.
        """
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        id: token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: |\n"
            "          if [[ -n \"${SONAR_TOKEN:-}\" ]]; then\n"
            "            echo \"enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
            "          else\n"
            "            echo \"enabled=false\" >> \"$GITHUB_OUTPUT\"\n"
            "          fi\n"
            "      - name: Checkout repository\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "      - name: SonarCloud Branch Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
        )
        findings = check_sonar_gate_wiring(text)
        assert len(findings) == 2
        assert all("not gated" in finding.message for finding in findings)

    def test_exit_only_presence_check_is_reported(self):
        """An exit-only presence check cannot gate the scanners.

        This is the shape the old fixture used; nothing can reference it, so it
        is reported even though the scanner steps look correctly wired.
        """
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
        )
        findings = check_sonar_gate_wiring(text)
        assert len(findings) == 1
        assert "must publish a gating step output" in findings[0].message

    def test_block_scalar_if_gates_the_step(self):
        """A folded ``if: >-`` condition still counts as gating.

        The shipped workflow writes its conditions as block scalars, so the
        wiring check must fold the continuation lines before searching.
        """
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        id: token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: |\n"
            "          if [[ -n \"${SONAR_TOKEN:-}\" ]]; then\n"
            "            echo \"enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
            "          else\n"
            "            echo \"enabled=false\" >> \"$GITHUB_OUTPUT\"\n"
            "          fi\n"
            "      - name: Checkout repository\n"
            "        if: >-\n"
            "          steps.token.outputs.enabled == 'true' &&\n"
            "          github.event_name == 'push'\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        if: >-\n"
            "          steps.token.outputs.enabled == 'true'\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
        )
        findings = check_sonar_gate_wiring(text)
        assert findings == []

    def test_run_only_gate_reference_is_not_wiring(self):
        """A gate reference outside if: does not gate the step.

        Regression shape: the scanner's only reference to the presence-check
        output sits in its run body (for logging); the step has no if: and
        would run with an unset token.
        """
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        id: token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: |\n"
            "          if [[ -n \"${SONAR_TOKEN:-}\" ]]; then\n"
            "            echo \"enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
            "          else\n"
            "            echo \"enabled=false\" >> \"$GITHUB_OUTPUT\"\n"
            "          fi\n"
            "      - name: Checkout repository\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        run: echo \"gate=${{ steps.token.outputs.enabled }}\" && sonar-scanner\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
        )
        findings = check_sonar_gate_wiring(text)
        assert len(findings) == 1
        assert "not gated" in findings[0].message

    def test_gate_redirected_to_github_env_is_rejected(self):
        """Publishing the flag outside $GITHUB_OUTPUT is not a publishable gate.

        Regression shape: the presence check writes enabled=true to
        $GITHUB_ENV, so no step output exists for the scanners to gate on.
        """
        text = (
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        id: token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: |\n"
            "          echo \"enabled=true\" >> \"$GITHUB_ENV\"\n"
            "      - name: Checkout repository\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        if: steps.token.outputs.enabled == 'true'\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
        )
        findings = check_sonar_gate_wiring(text)
        assert len(findings) == 1
        assert "must publish a gating step output" in findings[0].message

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

    def test_scan_workflows_reports_ungated_scanner(self, tmp_path: Path, monkeypatch):
        """Gate wiring is part of the scan, not just an unused helper.

        Without this the gate check could be dropped from scan_workflows and
        the suite would still pass.
        """
        root = tmp_path / "workflows"
        root.mkdir()
        (root / "sonarcloud.yml").write_text(
            "name: SonarCloud\n"
            "on: push\n"
            "jobs:\n"
            "  scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Check Sonar token\n"
            "        id: token\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "        run: |\n"
            "          if [[ -n \"${SONAR_TOKEN:-}\" ]]; then\n"
            "            echo \"enabled=true\" >> \"$GITHUB_OUTPUT\"\n"
            "          else\n"
            "            echo \"enabled=false\" >> \"$GITHUB_OUTPUT\"\n"
            "          fi\n"
            "      - name: Checkout repository\n"
            "        uses: actions/checkout@abc123\n"
            "      - name: SonarCloud Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
            "      - name: SonarCloud Branch Scan\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(secret_scope_module, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(secret_scope_module, "WORKFLOW_ROOT", root)

        findings = scan_workflows(root)

        assert any("not gated" in f.message for f in findings)


class TestCLI:
    """CLI contract: the detector must exit 0 when the repository is clean."""

    def test_cli_runs_and_returns_clean_exit(self) -> None:
        # The detector derives its scan root from __file__, not the cwd, so
        # no fixture tree is needed; the repository must be clean today.
        result = subprocess.run(
            [sys.executable, str(DETECTOR)],
            capture_output=True, text=True,
            check=False,
        )
        assert result.returncode == 0, f"expected exit 0, got {result.returncode}; stderr:\n{result.stderr}"


class TestStepIfValueIndentation:
    """_step_if_value reads only direct step-child if: keys."""

    def test_nested_if_under_env_does_not_gate(self):
        lines = [
            "      - name: Scan",
            "        env:",
            "          if: steps.token.outputs.enabled == 'true'",
            "        run: echo scan",
        ]
        assert secret_scope_module._step_if_value(lines, 0, len(lines)) == ""

    def test_direct_if_wins_over_nested_if(self):
        lines = [
            "      - name: Scan",
            "        env:",
            "          if: steps.token.outputs.enabled == 'true'",
            "        if: steps.token.outputs.enabled == 'true'",
            "        run: echo scan",
        ]
        assert (
            secret_scope_module._step_if_value(lines, 0, len(lines))
            == "steps.token.outputs.enabled == 'true'"
        )


class TestReferencesGatePolarity:
    """_references_gate accepts only positive gate requirements."""

    def test_positive_comparison_gates(self):
        assert secret_scope_module._references_gate(
            "steps.token.outputs.enabled == 'true'", "token", {"enabled"}
        )

    def test_negated_comparison_does_not_gate(self):
        assert not secret_scope_module._references_gate(
            "steps.token.outputs.enabled != 'true'", "token", {"enabled"}
        )

    def test_false_equality_does_not_gate(self):
        assert not secret_scope_module._references_gate(
            "steps.token.outputs.enabled == false", "token", {"enabled"}
        )

    def test_negated_contains_does_not_gate(self):
        assert not secret_scope_module._references_gate(
            "!contains(steps.token.outputs.enabled, 'true')",
            "token",
            {"enabled"},
        )
