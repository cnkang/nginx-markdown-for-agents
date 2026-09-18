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

    def test_negated_gate_is_reported_as_unwired(self):
        """A scanner gated on the negation of the output is not wired.

        ``! steps.token.outputs.enabled`` runs the step precisely when the
        token is absent, so the wiring check must not accept it.
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
            "          fi\n"
            "      - name: SonarCloud Scan\n"
            "        if: ${{ ! steps.token.outputs.enabled }}\n"
            "        uses: SonarSource/sonarcloud-github-action@abc123\n"
            "        env:\n"
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}\n"
        )
        findings = check_sonar_gate_wiring(text)
        assert len(findings) == 1
        assert "not gated on steps.token.outputs" in findings[0].message

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

    def test_dash_line_block_scalar_body_is_not_yaml(self):
        """A ``key:``-looking line inside ``- run: |`` must not gate the step.

        Adversarial and accidental heredoc content both hit this shape: the
        body line matches the child-key regex yet lives inside the scalar,
        so reading it as the step gate would fabricate wiring.
        """
        lines = [
            "      - run: |",
            "          if: steps.token.outputs.enabled",
            "        env:",
            "          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}",
        ]
        assert secret_scope_module._step_if_value(lines, 0, len(lines)) == ""

    def test_gate_after_dash_line_block_scalar_still_gates(self):
        """A real ``if:`` after a ``- run: |`` body is still read."""
        lines = [
            "      - run: |",
            "          echo scan",
            "        if: steps.token.outputs.enabled == 'true'",
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

    def test_unary_negation_does_not_gate(self):
        assert not secret_scope_module._references_gate(
            "! steps.token.outputs.enabled", "token", {"enabled"}
        )

    def test_negated_parenthesized_gate_does_not_gate(self):
        assert not secret_scope_module._references_gate(
            "! (steps.token.outputs.enabled == 'true')",
            "token",
            {"enabled"},
        )

    def test_unrelated_negation_still_gates(self):
        assert secret_scope_module._references_gate(
            "!cancelled() && steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_disjunction_does_not_gate(self):
        """A gate reachable through a top-level ``||`` is optional."""
        assert not secret_scope_module._references_gate(
            "steps.token.outputs.enabled == 'true' || github.event_name == 'push'",
            "token",
            {"enabled"},
        )

    def test_nested_disjunction_still_gates(self):
        """``<gate> && (a || b)`` still requires the gate."""
        assert secret_scope_module._references_gate(
            "steps.token.outputs.enabled == 'true' && "
            "(github.event_name == 'pull_request' || "
            "github.event_name == 'workflow_dispatch')",
            "token",
            {"enabled"},
        )

    def test_always_conjunction_still_gates(self):
        """``always() && <gate>`` still requires the gate."""
        assert secret_scope_module._references_gate(
            "always() && steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_always_disjunction_does_not_gate(self):
        """``always() || <gate>`` leaves the gate optional."""
        assert not secret_scope_module._references_gate(
            "always() || steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_quoted_disjunction_is_not_a_disjunction(self):
        """A ``||`` inside a quoted literal is not a top-level disjunction."""
        assert secret_scope_module._references_gate(
            "github.event_name == 'x||y' && steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_backslash_literal_does_not_swallow_quote(self):
        """A backslash in a literal is not an escape; the quote still closes.

        GitHub expressions escape quotes by doubling, so a trailing
        backslash must not hide the closing quote — otherwise a following
        top-level ``||`` would be missed.
        """
        assert not secret_scope_module._references_gate(
            "github.event_name == 'a\\' || steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_doubled_single_quote_stays_in_literal(self):
        """A doubled quote inside a literal does not end it early."""
        assert secret_scope_module._references_gate(
            "github.event_name == 'it''s' && steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_quoted_parenthesis_does_not_make_gate_nested(self):
        """An unbalanced ``(`` inside a literal is not expression grouping."""
        assert secret_scope_module._references_gate(
            "github.event_name == '(x||y' && steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_quoted_reference_is_not_wiring(self):
        """A reference that only exists inside a literal is not a gate."""
        assert not secret_scope_module._references_gate(
            "github.event_name == 'steps.token.outputs.enabled'",
            "token",
            {"enabled"},
        )

    def test_quoted_fake_negation_does_not_override_real_gate(self):
        """A literal shaped like a negation cannot neutralise a real gate."""
        assert secret_scope_module._references_gate(
            "github.event_name == 'steps.token.outputs.enabled != true' && "
            "steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_quoted_fake_unary_negation_does_not_override_real_gate(self):
        """A literal shaped like ``! <gate>`` cannot neutralise a real gate."""
        assert secret_scope_module._references_gate(
            "github.event_name == '! steps.token.outputs.enabled' && "
            "steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_distant_unary_negation_is_recognized(self):
        """Folded spacing does not hide a unary negation of the reference."""
        assert not secret_scope_module._references_gate(
            "!" + " " * 30 + "steps.token.outputs.enabled",
            "token",
            {"enabled"},
        )

    def test_distant_false_comparison_is_recognized(self):
        """Folded spacing does not hide an ``== 'false'`` comparison."""
        assert not secret_scope_module._references_gate(
            "steps.token.outputs.enabled" + " " * 30 + "== 'false'",
            "token",
            {"enabled"},
        )

    def test_repeated_reference_positions_are_independent(self):
        """An occurrence nested under a disjunction must not disqualify a
        separate top-level occurrence."""
        assert secret_scope_module._references_gate(
            "(a || steps.token.outputs.enabled) && "
            "steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )

    def test_a_double_quoted_reference_is_not_wiring(self):
        """A reference inside a double-quoted literal cannot gate the step."""
        assert not secret_scope_module._references_gate(
            'github.event_name == "steps.token.outputs.enabled"',
            "token",
            {"enabled"},
        )

    def test_an_operator_before_the_reference_is_not_wiring(self):
        """A comparison whose left operand precedes the reference is not a
        positive requirement."""
        assert not secret_scope_module._references_gate(
            "'true' != steps.token.outputs.enabled", "token", {"enabled"}
        )
        assert not secret_scope_module._references_gate(
            "false == steps.token.outputs.enabled", "token", {"enabled"}
        )

    def test_a_dotted_false_property_is_not_wiring(self):
        """`inputs.false ==` compares the gate against a property
        reference, not against the literal true, so the reference does
        not count as a positive requirement."""
        assert not secret_scope_module._references_gate(
            "inputs.false == steps.token.outputs.enabled", "token", {"enabled"}
        )

    def test_a_literal_true_operand_keeps_the_equality_positive(self):
        """Only a literal true on the left keeps `== ref` a positive
        requirement."""
        assert secret_scope_module._references_gate(
            "true == steps.token.outputs.enabled", "token", {"enabled"}
        )
        assert secret_scope_module._references_gate(
            "'true' == steps.token.outputs.enabled", "token", {"enabled"}
        )

    def test_a_double_quoted_reference_beside_a_real_one_still_gates(self):
        """Masking a double-quoted literal must not neutralise a real
        unquoted reference on the same line."""
        assert secret_scope_module._references_gate(
            '"steps.token.outputs.enabled" == github.event_name && '
            "steps.token.outputs.enabled == 'true'",
            "token",
            {"enabled"},
        )


    def test_a_yaml_quoted_condition_keeps_its_reference(self) -> None:
        """Wrapping the whole condition in a YAML flow scalar must not hide
        its gate reference from polarity analysis."""
        value = secret_scope_module._step_if_value(
            [
                "      - name: probe\n",
                "        if: \"steps.token.outputs.enabled == 'true'\"\n",
            ],
            0,
            2,
        )
        assert value == "steps.token.outputs.enabled == 'true'"
        assert secret_scope_module._references_gate(value, "token", {"enabled"})


class TestGateNamePattern:
    """GATE_NAME_RE accepts quoted values (spaces allowed) and bare tokens."""

    def test_a_quoted_value_with_spaces_is_accepted(self) -> None:
        match = secret_scope_module.GATE_NAME_RE.match(
            'echo "name=$(python3 tools/perf/report_utils.py detect-platform)"'
            ' >> "$GITHUB_OUTPUT"'
        )
        assert match is not None
        assert match.group(1) == "name"

    def test_bare_and_simple_quoted_values_are_accepted(self) -> None:
        bare = secret_scope_module.GATE_NAME_RE.match(
            'echo gate=true >> "$GITHUB_OUTPUT"'
        )
        assert bare is not None and bare.group(1) == "gate"
        quoted = secret_scope_module.GATE_NAME_RE.match(
            'echo "enabled=true" >> "$GITHUB_OUTPUT"'
        )
        assert quoted is not None and quoted.group(1) == "enabled"

    def test_a_chained_debug_echo_does_not_mask_the_ready_gate(self) -> None:
        """A debug echo chained before the gate echo must not supply the
        gate name: only the echo that owns the redirect names the gate."""
        lines = [
            '            echo "debug=1"; echo "ready=go" >> "$GITHUB_OUTPUT"\n'
        ]
        assert secret_scope_module._published_gates(lines, 0, 1) == {"ready"}

    def test_each_segment_of_a_chain_contributes_only_its_own_gate(self) -> None:
        lines = [
            'echo "first=1" >> "$GITHUB_OUTPUT"; echo "second=2"\n'
        ]
        assert secret_scope_module._published_gates(lines, 0, 1) == {"first"}

    def test_quoted_separators_do_not_fabricate_gates(self) -> None:
        """A separator inside a quoted value is content: the line stays one
        segment and only the echo's own assignment names the gate."""
        lines = [
            'echo "debug=1; ready=go" >> "$GITHUB_OUTPUT"\n'
        ]
        assert secret_scope_module._published_gates(lines, 0, 1) == {"debug"}

    def test_a_quoted_fake_redirection_does_not_publish_a_gate(self) -> None:
        """A `>>` inside a quoted string is literal text: the shell performs
        no redirection, so no gate may be reported."""
        lines = [
            'echo "enabled=true; ready=go >> $GITHUB_OUTPUT"\n'
        ]
        assert secret_scope_module._published_gates(lines, 0, 1) == set()

    def test_gate_equality_needs_the_literal_true_on_the_other_side(self) -> None:
        """`ref == <anything but literal true>` compares the value without
        requiring it, so it must not count as positive wiring."""
        assert not secret_scope_module._references_gate(
            "steps.gate.outputs.enabled == inputs.mode", "gate", {"enabled"}
        )
        assert not secret_scope_module._references_gate(
            'steps.gate.outputs.enabled == "true_or_more"',
            "gate",
            {"enabled"},
        )
        assert secret_scope_module._references_gate(
            "steps.gate.outputs.enabled == 'true'", "gate", {"enabled"}
        )

    def test_descriptor_and_name_prefixed_redirects_do_not_count(self) -> None:
        """`2>>` and `$GITHUB_OUTPUT_BACKUP` are not stdout appends to the
        gate output, so the wrapped gate must not be certified."""
        for line in (
            'if [[ "${{ steps.gate.outputs.enabled }}" == \'true\' ]]; then\n',
        ):
            pass
        lines = [
            'if [[ "${{ steps.gate.outputs.enabled }}" == \'true\' ]]; then\n',
            '  echo text=1 2>> "$GITHUB_OUTPUT"\n',
            'fi\n',
        ]
        assert secret_scope_module._published_gates(lines, 0, 3) == set()
        lines = [
            'if [[ "${{ steps.gate.outputs.enabled }}" == \'true\' ]]; then\n',
            '  echo text=1 >> "$GITHUB_OUTPUT_BACKUP"\n',
            'fi\n',
        ]
        assert secret_scope_module._published_gates(lines, 0, 3) == set()

    def test_escaped_separators_are_literal_arguments(self) -> None:
        """`\\;` outside quotes is an argument, not a command separator:
        the gate must come from the echo that owns the redirection."""
        lines = [
            'echo gate=true \\; echo ready=go >> "$GITHUB_OUTPUT"\n'
        ]
        assert secret_scope_module._published_gates(lines, 0, 1) == {"gate"}

    def test_separators_inside_a_comment_cannot_fabricate_gates(self) -> None:
        """Text after an unquoted `#` is not executable: a chained fake gate
        hidden in the comment must not be reported."""
        lines = [
            'echo "gate=1" >> "$GITHUB_OUTPUT" # tail; echo fake=2 >> "$GITHUB_OUTPUT"\n'
        ]
        assert secret_scope_module._published_gates(lines, 0, 1) == {"gate"}

    def test_a_comment_fake_redirection_does_not_publish_a_gate(self) -> None:
        lines = ['echo debug=1 # note >> "$GITHUB_OUTPUT"\n']
        assert secret_scope_module._published_gates(lines, 0, 1) == set()

    def test_quoted_ampersands_stay_inside_the_value(self) -> None:
        lines = ['echo "note=a&&b" >> "$GITHUB_OUTPUT"\n']
        assert secret_scope_module._published_gates(lines, 0, 1) == {"note"}
