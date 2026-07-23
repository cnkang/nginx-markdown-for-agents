#!/usr/bin/env python3
"""Reject workflow secrets outside the minimal step that consumes them."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
SECRET_EXPRESSION = re.compile(r"\$\{\{\s*secrets\.\w+\s*\}\}")
SONAR_SECRET_EXPRESSION = re.compile(
    r"\$\{\{\s*secrets\.SONAR_TOKEN\s*\}\}"
)
SONAR_TOKEN_LINE = re.compile(r"^\s*SONAR_TOKEN:\s*\$\{\{\s*secrets\.SONAR_TOKEN\s*\}\}\s*$")


@dataclass(frozen=True)
class Finding:
    """One secret-scope policy violation."""

    path: str
    line: int
    message: str


def find_broad_env_secrets(text: str, path: str) -> list[Finding]:
    """Find secrets assigned in workflow-level or job-level env maps."""
    findings: list[Finding] = []
    env_indent: int | None = None

    for number, line in enumerate(text.splitlines(), 1):
        env_indent, finding = _inspect_env_line(
            line,
            number,
            path,
            env_indent,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _inspect_env_line(
    line: str,
    number: int,
    path: str,
    env_indent: int | None,
) -> tuple[int | None, Finding | None]:
    """Advance YAML env-map state and report a broad secret assignment."""
    stripped = line.lstrip()
    indent = len(line) - len(stripped)
    if _starts_env_map(stripped):
        finding = _broad_secret_finding(path, number) if (
            _is_broad_secret(indent, line)
        ) else None
        return (indent if stripped == "env:" else None), finding
    if env_indent is None:
        return None, None
    if stripped and indent <= env_indent:
        return None, None
    if env_indent <= 4 and SECRET_EXPRESSION.search(line):
        return env_indent, _broad_secret_finding(path, number)
    return env_indent, None


def _broad_secret_finding(path: str, number: int) -> Finding:
    """Build one deterministic broad-secret diagnostic."""
    return Finding(
        path,
        number,
        "secret expression is forbidden in workflow/job env; "
        "use the minimal consuming step",
    )


def _starts_env_map(stripped: str) -> bool:
    """Return whether a YAML line begins an ``env`` mapping."""
    return stripped == "env:" or stripped.startswith("env: ")


def _is_broad_secret(indent: int, line: str) -> bool:
    """Return whether a secret is assigned above step scope."""
    return indent <= 4 and SECRET_EXPRESSION.search(line) is not None


def _step_name(lines: list[str], token_index: int) -> str | None:
    """Return the step name owning a token entry."""
    for index in range(token_index - 1, -1, -1):
        if match := re.match(r"^\s{6}- name:\s+(\S.*)$", lines[index]):
            return match[1]
    return None


def check_sonar_token_steps(text: str) -> list[Finding]:
    """Require SONAR_TOKEN only in the presence check and pinned scanner."""
    path = ".github/workflows/sonarcloud.yml"
    lines = text.splitlines()
    secret_occurrences = sum(
        len(SONAR_SECRET_EXPRESSION.findall(line)) for line in lines
    )
    occurrences = [
        index for index, line in enumerate(lines) if SONAR_TOKEN_LINE.match(line)
    ]
    findings: list[Finding] = []
    names = [_step_name(lines, index) for index in occurrences]

    if (
        secret_occurrences != 2
        or names != ["Check Sonar token", "SonarCloud Scan"]
    ):
        findings.append(
            Finding(
                path,
                1,
                "SONAR_TOKEN must appear only in the presence-check and "
                "SonarCloud Scan step env maps",
            )
        )
    if occurrences and occurrences[0] > next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s{6}- name:\s*Checkout repository\s*$", line)
        ),
        len(lines),
    ):
        findings.append(
            Finding(
                path,
                occurrences[0] + 1,
                "the minimal token presence check must run before checkout",
            )
        )
    return findings


def scan_workflows(root: Path = WORKFLOW_ROOT) -> list[Finding]:
    """Scan all workflow files, failing closed on read errors."""
    findings: list[Finding] = []
    for path in sorted((*root.glob("*.yml"), *root.glob("*.yaml"))):
        relative = str(path.relative_to(REPO_ROOT))
        try:
            resolved = validate_read_path(path, purpose="workflow policy input")
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            findings.append(Finding(relative, 1, f"cannot read workflow: {exc}"))
            continue
        findings.extend(find_broad_env_secrets(text, relative))
        if path.name == "sonarcloud.yml":
            findings.extend(check_sonar_token_steps(text))
    return findings


def main() -> int:
    """Run the workflow secret-scope policy."""
    findings = scan_workflows()
    for finding in findings:
        print(
            f"ERROR: {finding.path}:{finding.line}: {finding.message}",
            file=sys.stderr,
        )
    if findings:
        return 1
    print("Workflow secret-scope contracts passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
