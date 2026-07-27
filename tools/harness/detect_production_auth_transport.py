#!/usr/bin/env python3
"""Require secure transport for Basic Auth in production examples."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = REPO_ROOT / "examples" / "production"
TLS_CONTRACT = "A co-located TLS terminator is mandatory"
ACTIVE_AUTH = re.compile(r"^[ \t]*auth_basic[ \t]++(?!off[ \t]*;)[^;]++;", re.MULTILINE)
LISTEN = re.compile(r"^[ \t]*listen[ \t]++([^;]++);", re.MULTILINE)
LOOPBACK = re.compile(r"^127\.0\.0\.1:\d+$")


@dataclass(frozen=True)
class Finding:
    """One authenticated-listener transport violation."""

    path: str
    line: int
    message: str


def _without_comments(text: str) -> str:
    """Remove NGINX line comments while preserving line count."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _server_blocks(text: str) -> tuple[list[tuple[int, str]], bool]:
    """Extract server blocks using comment-masked brace depth."""
    masked_lines = _without_comments(text).splitlines()
    original_lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    start: int | None = None
    depth = 0

    for index, line in enumerate(masked_lines):
        if start is None:
            if re.search(r"\bserver\s*\{", line):
                start = index
                depth = line.count("{") - line.count("}")
            continue
        depth += line.count("{") - line.count("}")
        if depth == 0:
            blocks.append((start + 1, "\n".join(original_lines[start : index + 1])))
            start = None
    return blocks, start is not None


def _check_auth_block(
    block: str,
    full_text: str,
    path: str,
    line: int,
) -> Finding | None:
    """Validate one server block that may enable Basic Auth."""
    active = _without_comments(block)
    if ACTIVE_AUTH.search(active) is None:
        return None
    listeners = LISTEN.findall(active)
    if not listeners:
        return Finding(path, line, "Basic Auth server has no listener")
    direct_tls = all("ssl" in listener.split() for listener in listeners)
    loopback_only = all(
        LOOPBACK.fullmatch(listener.split()[0]) is not None
        for listener in listeners
    )
    if direct_tls or (loopback_only and TLS_CONTRACT in full_text):
        return None
    return Finding(
        path,
        line,
        "Basic Auth requires an SSL listener or a loopback-only "
        "backend with the mandatory co-located TLS contract",
    )


def _check_client_guidance(text: str, path: str) -> list[Finding]:
    """Reject credential-bearing curl examples that target plain HTTP."""
    findings: list[Finding] = []
    findings.extend(
        Finding(
            path,
            number,
            "authenticated client guidance must use HTTPS",
        )
        for number, line in enumerate(text.splitlines(), 1)
        if "curl -u " in line and "https://" not in line
    )
    return findings


def check_config(text: str, path: str) -> list[Finding]:
    """Return transport findings for one NGINX configuration."""
    blocks, unterminated = _server_blocks(text)
    if unterminated:
        return [Finding(path, 1, "unterminated server block")]
    findings: list[Finding] = []
    for line, block in blocks:
        finding = _check_auth_block(block, text, path, line)
        if finding is not None:
            findings.append(finding)
    findings.extend(_check_client_guidance(text, path))
    return findings


def scan_examples(root: Path = EXAMPLES_ROOT) -> list[Finding]:
    """Scan tracked production example configurations."""
    findings: list[Finding] = []
    for path in sorted(root.glob("*.conf")):
        relative = str(path.relative_to(REPO_ROOT))
        try:
            resolved = validate_read_path(
                path,
                purpose="production NGINX example",
            )
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            findings.append(Finding(relative, 1, f"cannot read config: {exc}"))
            continue
        findings.extend(check_config(text, relative))
    return findings


def main() -> int:
    """Run the production Basic Auth transport policy."""
    findings = scan_examples()
    for finding in findings:
        print(
            f"ERROR: {finding.path}:{finding.line}: {finding.message}",
            file=sys.stderr,
        )
    if findings:
        return 1
    print("Production Basic Auth transport contracts passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
