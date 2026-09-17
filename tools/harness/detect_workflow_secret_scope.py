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
SECRET_EXPRESSION = re.compile(r"\$\{\{\s*secrets(\.\w+|\[[^]]*\])\s*\}\}")
SONAR_SECRET_EXPRESSION = re.compile(
    r"\$\{\{\s*secrets\.SONAR_TOKEN\s*\}\}"
)
SONAR_TOKEN_LINE = re.compile(r"^\s*SONAR_TOKEN:\s*\$\{\{\s*secrets\.SONAR_TOKEN\s*\}\}\s*$")
# A run body publishes a gating value to the step output file.
GITHUB_OUTPUT_RE = re.compile(r">>\s*\"?\$\{?GITHUB_OUTPUT\}?\"?")
GATE_NAME_RE = re.compile(r"^\s*echo\s+\"?([A-Za-z_][A-Za-z0-9_-]*)=[^\"]*\"?\s*>>")
STEP_KEY_RE = re.compile(r"^\s*- ([A-Za-z0-9_-]+):\s*(.*)$")
STEP_CHILD_KEY_RE = re.compile(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$")


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
    """Require SONAR_TOKEN only in the presence check and pinned scanners."""
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

    if secret_occurrences != 3 or names != [
        "Check Sonar token",
        "SonarCloud Scan",
        "SonarCloud Branch Scan",
    ]:
        findings.append(
            Finding(
                path,
                1,
                "SONAR_TOKEN must appear only in the presence-check and "
                "SonarCloud scan step env maps",
            )
        )
    checkout_index = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s{6}- name:\s*Checkout repository\s*$", line)
        ),
        None,
    )
    if occurrences and (
        checkout_index is None or occurrences[0] > checkout_index
    ):
        findings.append(
            Finding(
                path,
                occurrences[0] + 1,
                "the minimal token presence check must run before checkout",
            )
        )
    return findings


def _step_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Return (start, end) index pairs for each YAML step list item."""
    starts = [
        (index, len(match.group(1)))
        for index, line in enumerate(lines)
        if (match := re.match(r"^(\s*)-\s+\S", line))
    ]
    blocks: list[tuple[int, int]] = []
    for start, indent in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if not lines[index].strip():
                continue
            current = len(lines[index]) - len(lines[index].lstrip())
            if current <= indent:
                end = index
                break
        blocks.append((start, end))
    return blocks


def _step_id(lines: list[str], start: int, end: int) -> str | None:
    """Return a step's ``id:`` value, or None when the step has no id."""
    for index in range(start, end):
        match = re.match(r"^\s+id:\s*(\S+)\s*$", lines[index])
        if match:
            return match.group(1)
    return None


def _step_structural_lines(
    lines: list[str], start: int, end: int
) -> list[tuple[int, int]]:
    """Return (index, indent) pairs for the step's YAML structure lines.

    A block scalar (``run: |``, ``if: >-``, and their chomping variants)
    owns every following line that is more indented than its key, so those
    lines are skipped: a heredoc or script line that happens to look like
    ``key: value`` must never be read as YAML.  The dash line itself can
    open a scalar (``- run: |``); its body ends at the first line that is
    not deeper than the key.
    """
    body_indent: int | None = None
    leading = re.match(r"^(\s*)-[^\n]*?:\s*[|>]", lines[start])
    if leading is not None:
        body_indent = len(leading.group(1)) + 3

    structural: list[tuple[int, int]] = []
    for index in range(start + 1, end):
        line = lines[index]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if body_indent is not None:
            if indent >= body_indent:
                continue
            body_indent = None
        match = STEP_CHILD_KEY_RE.match(line)
        if match is None:
            continue
        if match.group(2).strip()[:1] in (">", "|"):
            body_indent = indent + 1
        structural.append((index, indent))
    return structural


def _step_child_indent(lines: list[str], start: int, end: int) -> int | None:
    """Indentation of the step's direct child keys, from the first key line."""
    structural = _step_structural_lines(lines, start, end)
    return structural[0][1] if structural else None


def _fold_block_scalar(lines: list[str], index: int, end: int) -> str:
    """Join a block-scalar value that continues on more-indented lines."""
    key_indent = len(lines[index]) - len(lines[index].lstrip())
    collected: list[str] = []
    for follower in range(index + 1, end):
        if not lines[follower].strip():
            continue
        indent = len(lines[follower]) - len(lines[follower].lstrip())
        if indent <= key_indent:
            break
        collected.append(lines[follower].strip())
    return " ".join(collected)


def _step_if_value(lines: list[str], start: int, end: int) -> str:
    """Return the step's own ``if:`` value from its block, else ``""``.

    Only a key at the step's direct-child indentation counts, and lines
    owned by a block scalar are never treated as YAML: an ``if:`` nested
    under ``env:``/``with:`` or a script line inside ``run: |`` does not
    gate the step.  Block scalars (``if: >-``) are folded into one string
    because the condition then continues on the following lines.
    """
    structural = _step_structural_lines(lines, start, end)
    if not structural:
        return ""
    child_indent = structural[0][1]
    for index, indent in structural:
        match = STEP_CHILD_KEY_RE.match(lines[index])
        if match is None or match.group(1) != "if" or indent != child_indent:
            continue
        value = match.group(2).strip()
        if value and value[0] not in ">|":
            return value
        return _fold_block_scalar(lines, index, end)
    return ""


def _presence_block(
    blocks: list[tuple[int, int]],
    lines: list[str],
) -> tuple[int, int] | None:
    """Return the step block that carries the token presence check."""
    return next(
        (
            (start, end)
            for start, end in blocks
            if any(SONAR_TOKEN_LINE.match(lines[i]) for i in range(start, end))
        ),
        None,
    )


def _published_gates(lines: list[str], start: int, end: int) -> set[str]:
    """Return the step outputs the presence check publishes for gating."""
    return {
        match.group(1)
        for index in range(start, end)
        if GITHUB_OUTPUT_RE.search(lines[index])
        and (match := GATE_NAME_RE.match(lines[index]))
    }


def _mask_quoted(if_value: str) -> str:
    """Blot out quoted-literal content, keeping positions (GitHub syntax).

    Quoted strings cannot contain operators that matter for the disjunction
    scan, and GitHub escapes a quote by doubling it — so both quote runs
    and literal content become spaces.
    """
    out: list[str] = []
    in_quote = False
    index = 0
    length = len(if_value)
    while index < length:
        char = if_value[index]
        if in_quote:
            if char == "'":
                if index + 1 < length and if_value[index + 1] == "'":
                    out.append("  ")
                    index += 2
                    continue
                in_quote = False
            out.append(" ")
        else:
            if char == "'":
                in_quote = True
            out.append(char)
        index += 1
    return "".join(out)


def _has_top_level_disjunction(if_value: str) -> bool:
    """Return whether a ``||`` sits outside parentheses (quotes masked)."""
    depth = 0
    for char in _mask_quoted(if_value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "|" and depth == 0:
            return True
    return False


def _optional_in_disjunction(if_value: str, match: re.Match[str]) -> bool:
    """Return whether a disjunction can bypass this reference occurrence.

    The occurrence counts only as a member of the top-level ``&&`` chain:
    a ``||`` outside every parenthesized group (``ref || a``) runs the step
    without the gate, and an occurrence that only appears inside parentheses
    cannot be proven required.  A disjunction nested inside parentheses
    under a top-level ``&&`` (``<gate> && (a || b)``) leaves the gate
    required.  Conditions without any ``||`` are never bypassed here; the
    parenthesis depth is measured at the occurrence's own position, so
    repeated references are classified independently.
    """
    if "||" not in if_value:
        return False
    if _has_top_level_disjunction(if_value):
        return True

    masked = _mask_quoted(if_value)
    prefix = masked[: match.start()]
    return prefix.count("(") > prefix.count(")")


def _reference_negated(if_value: str, match: re.Match[str]) -> bool:
    """Return whether this unquoted reference occurrence is negated.

    The text right after the reference covers ``!=`` and ``== false``
    comparisons (including quoted ``'false'``); the text right before
    covers unary ``!`` and negated ``contains(`` forms.  Both run over the
    full remaining text, so distant operators (long folded spacing) still
    count, and they read at mask-verified positions — a literal shaped like
    a negation cannot negative a separate, real occurrence.
    """
    after = if_value[match.end():]
    stripped = after.lstrip()
    if stripped.startswith("!="):
        return True
    if (
        stripped.startswith("==")
        and stripped[2:].lstrip().lstrip("'\"").startswith("false")
    ):
        return True

    before = if_value[: match.start()]
    if re.search(r"!\s*\(*\s*$", before):
        return True
    if re.search(r"!\s*contains\(\s*$", before):
        return True
    return False


def _references_gate(if_value: str, step_id: str, gates: set[str]) -> bool:
    """Return whether an ``if:`` value requires a published step output.

    Only a positive, required check counts as wiring.  A negated comparison
    of the gate (``!= 'true'``, an equality against false, a negated
    ``contains`` call, or a unary ``!`` on the reference itself) runs it
    when the gate did NOT pass, and a disjunction that can bypass the
    reference (``<gate> || a``) makes the gate optional — none of those
    count.  A negation of some *other* predicate in a conjunction
    (``!cancelled() && <gate>``) or a disjunction nested under it
    (``<gate> && (a || b)``) leaves the gate required and still counts, as
    does ``always()`` combined with the gate (it only overrides the
    cancellation default; the conjunction still requires the gate).

    References are matched on the quote-masked value and each unquoted
    occurrence is judged on its own polarity, so text inside a literal can
    neither fabricate wiring nor neutralise a real reference.
    """
    masked = _mask_quoted(if_value)
    for gate in gates:
        ref = rf"steps\.{re.escape(step_id)}\.outputs\.{re.escape(gate)}\b"
        for match in re.finditer(ref, masked):
            if _reference_negated(if_value, match):
                continue
            if _optional_in_disjunction(if_value, match):
                continue
            return True
    return False


def _scanner_blocks(
    blocks: list[tuple[int, int]],
    lines: list[str],
    presence_start: int,
) -> list[tuple[int, int]]:
    """Return the token-consuming step blocks other than the presence check."""
    return [
        (start, end)
        for start, end in blocks
        if start != presence_start
        and any(SONAR_TOKEN_LINE.match(lines[i]) for i in range(start, end))
    ]


def _ungated_scanner_findings(
    lines: list[str],
    scanners: list[tuple[int, int]],
    step_id: str,
    gates: set[str],
) -> list[Finding]:
    """Return one finding per token-consuming step not gated on the output."""
    findings: list[Finding] = []
    for block_start, block_end in scanners:
        if _references_gate(
            _step_if_value(lines, block_start, block_end), step_id, gates,
        ):
            continue
        findings.append(
            Finding(
                ".github/workflows/sonarcloud.yml",
                block_start + 1,
                f"token-consuming step is not gated on "
                f"steps.{step_id}.outputs.*: an unset token must skip the "
                f"scan, not run it unguarded",
            )
        )
    return findings


def check_sonar_gate_wiring(text: str) -> list[Finding]:
    """Require the presence check to gate both scanner steps.

    The documented skip contract (BUILD_INSTRUCTIONS.md: an unset token skips
    the scan) depends on the presence check publishing a step output and every
    later token-consuming scanner step being gated on it.  A presence check
    that merely exits — the drifted `if [ -z "$SONAR_TOKEN" ]; then exit 0; fi`
    shape — leaves the scanners ungated because nothing references the check.

    This reads the run body and the ``if:`` conditions the token-scope check
    itself does not judge, so the fixture cannot drift silently.
    """
    lines = text.splitlines()
    if not any(SONAR_TOKEN_LINE.match(line) for line in lines):
        return []

    blocks = _step_blocks(lines)
    presence = _presence_block(blocks, lines)
    if presence is None:
        return []

    start, end = presence
    step_id = _step_id(lines, start, end)
    gates = _published_gates(lines, start, end)

    if not gates or step_id is None:
        return [
            Finding(
                ".github/workflows/sonarcloud.yml",
                start + 1,
                "the token presence check must publish a gating step output "
                "(id: plus a $GITHUB_OUTPUT assignment); an exit-only check "
                "leaves the scanner steps ungated",
            )
        ]

    return _ungated_scanner_findings(
        lines,
        _scanner_blocks(blocks, lines, start),
        step_id,
        gates,
    )


def scan_workflows(root: Path = WORKFLOW_ROOT) -> list[Finding]:
    """Scan all workflow files, failing closed on read errors."""
    findings: list[Finding] = []
    seen_sonarcloud = False
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
            seen_sonarcloud = True
            findings.extend(check_sonar_token_steps(text))
            findings.extend(check_sonar_gate_wiring(text))
    if not seen_sonarcloud:
        findings.append(
            Finding(
                str(root / "sonarcloud.yml"),
                1,
                "required sonarcloud.yml workflow is missing",
            )
        )
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
