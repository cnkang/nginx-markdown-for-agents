#!/usr/bin/env python3
"""Fail CI when Rust dependency licenses require strong copyleft terms.

Policy goal:
- Block dependencies whose SPDX expression *requires* strong copyleft licenses
  (GPL/AGPL/LGPL/SSPL).
- Allow dual-licensed crates when a permissive option exists
  (for example: "MIT OR LGPL-2.1-or-later").
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


STRONG_COPYLEFT_PREFIXES = (
    "GPL-",
    "AGPL-",
    "LGPL-",
    "SSPL-",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_MANIFEST_RELS = (
    "components/rust-converter/Cargo.toml",
    "components/rust-converter/fuzz/Cargo.toml",
    "tools/corpus/test-corpus-conversion/Cargo.toml",
    "tools/e2e-harness/Cargo.toml",
)


class ManifestPathError(Exception):
    """Raised when a configured Cargo manifest path is invalid."""


@dataclass
class Token:
    """A single SPDX expression token with kind and text value."""

    kind: str
    value: str


TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<lpar>\()
  | (?P<rpar>\))
  | (?P<and>\bAND\b)
  | (?P<or>\bOR\b)
  | (?P<with>\bWITH\b)
  | (?P<id>[-+.0-9A-Z]+)
""",
    re.IGNORECASE | re.VERBOSE,
)


def tokenize(expr: str) -> list[Token]:
    """Tokenize an SPDX license expression into a list of tokens."""
    tokens: list[Token] = []
    pos = 0
    while pos < len(expr):
        match = TOKEN_RE.match(expr, pos)
        if not match:
            raise ValueError(f"Cannot parse SPDX expression near: {expr[pos:pos+30]!r}")
        pos = match.end()
        kind = match.lastgroup
        if kind == "ws":
            continue
        if kind == "lpar":
            tokens.append(Token("LPAR", "("))
        elif kind == "rpar":
            tokens.append(Token("RPAR", ")"))
        elif kind == "and":
            tokens.append(Token("AND", "AND"))
        elif kind == "or":
            tokens.append(Token("OR", "OR"))
        elif kind == "with":
            tokens.append(Token("WITH", "WITH"))
        elif kind == "id":
            tokens.append(Token("ID", match.group(kind)))
        else:
            raise AssertionError(f"Unexpected token kind: {kind}")
    return tokens


class Parser:
    """Recursive descent parser for SPDX license expressions."""

    def __init__(self, tokens: list[Token]) -> None:
        """Initialize parser with a token list."""
        self.tokens = tokens
        self.idx = 0

    def peek(self) -> Token | None:
        """Return the current token without consuming it."""
        return None if self.idx >= len(self.tokens) else self.tokens[self.idx]

    def consume(self, kind: str) -> Token:
        """Consume and return the current token, validating its kind."""
        tok = self.peek()
        if tok is None or tok.kind != kind:
            got = "EOF" if tok is None else tok.kind
            raise ValueError(f"Expected {kind}, got {got}")
        self.idx += 1
        return tok

    def parse_expression(self) -> bool:
        """Parse an OR-separated expression; strong-copyleft required if all branches require it."""
        # OR: strong-copyleft is required only if all OR branches require it.
        value = self.parse_term()
        while self.peek() is not None and self.peek().kind == "OR":
            self.consume("OR")
            rhs = self.parse_term()
            value = value and rhs
        return value

    def parse_term(self) -> bool:
        """Parse an AND-separated term; strong-copyleft required if any part requires it."""
        # AND: strong-copyleft is required if any AND part requires it.
        value = self.parse_factor()
        while self.peek() is not None and self.peek().kind == "AND":
            self.consume("AND")
            rhs = self.parse_factor()
            value = value or rhs
        return value

    def parse_factor(self) -> bool:
        """Parse a single license identifier or parenthesized expression."""
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression")
        if tok.kind == "LPAR":
            self.consume("LPAR")
            value = self.parse_expression()
            self.consume("RPAR")
            return value
        if tok.kind == "ID":
            lic = self.consume("ID").value
            # SPDX "WITH exception" does not remove copyleft requirement.
            if self.peek() is not None and self.peek().kind == "WITH":
                self.consume("WITH")
                self.consume("ID")
            return is_strong_copyleft_license(lic)
        raise ValueError(f"Unexpected token: {tok.kind}")


def is_strong_copyleft_license(license_id: str) -> bool:
    """Return True if the license ID indicates strong copyleft terms."""
    normalized = license_id.upper()
    return any(normalized.startswith(prefix) for prefix in STRONG_COPYLEFT_PREFIXES)


def requires_strong_copyleft(expr: str) -> bool:
    """Return True if the SPDX expression requires strong copyleft terms."""
    # Some crates still publish slash-separated legacy expressions like
    # "MIT/Apache-2.0". Treat "/" as OR.
    if "/" in expr:
        pieces = [piece.strip() for piece in expr.split("/") if piece.strip()]
        expr = " OR ".join(pieces)
    parser = Parser(tokenize(expr))
    result = parser.parse_expression()
    if parser.peek() is not None:
        raise ValueError("Unexpected trailing tokens in SPDX expression")
    return result


def run_metadata(manifest_rel: str, locked: bool) -> dict:
    """Run cargo metadata and return parsed JSON output."""
    cmd = [
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--all-features",
        "--manifest-path",
        manifest_rel,
    ]
    if locked:
        cmd.append("--locked")
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def parse_locked_flag(argv: list[str]) -> bool:
    """Parse the --locked flag from CLI arguments."""
    valid = {"--locked"}
    if unknown := [arg for arg in argv if arg not in valid]:
        print(f"Unknown arguments: {' '.join(unknown)}", file=sys.stderr)
        print("Usage: check_rust_licenses.py [--locked]", file=sys.stderr)
        raise SystemExit(2)
    return "--locked" in argv


def validate_manifest_path(manifest_rel: str) -> Path:
    """Resolve and validate a configured manifest path inside the repository."""
    manifest_path = (REPO_ROOT / manifest_rel).resolve()
    if REPO_ROOT not in manifest_path.parents and manifest_path != REPO_ROOT:
        raise ManifestPathError(f"Refusing manifest path outside repository: {manifest_path}")
    if not manifest_path.is_file():
        raise ManifestPathError(f"Manifest path does not exist: {manifest_path}")
    return manifest_path


def package_license_violation(manifest_rel: str, pkg: dict) -> str | None:
    """Return a policy violation for one package, or None when it is allowed."""
    name = pkg.get("name", "<unknown>")
    version = pkg.get("version", "<unknown>")
    license_expr = pkg.get("license")

    if not license_expr:
        return f"{manifest_rel}: {name} {version}: missing SPDX license expression"

    try:
        if requires_strong_copyleft(license_expr):
            return f"{manifest_rel}: {name} {version}: {license_expr}"
    except ValueError as exc:
        return (
            f"{manifest_rel}: {name} {version}: unparsable license expression "
            f"'{license_expr}' ({exc})"
        )
    return None


def collect_manifest_violations(manifest_rel: str, locked: bool) -> list[str]:
    """Collect license policy violations for a configured Cargo manifest."""
    validate_manifest_path(manifest_rel)
    metadata = run_metadata(manifest_rel=manifest_rel, locked=locked)
    return [
        violation
        for pkg in metadata.get("packages", [])
        if (violation := package_license_violation(manifest_rel, pkg)) is not None
    ]


def main() -> int:
    """Run Rust license policy check and report results."""
    locked = parse_locked_flag(sys.argv[1:])

    try:
        violations = [
            violation
            for manifest_rel in RUST_MANIFEST_RELS
            for violation in collect_manifest_violations(manifest_rel, locked=locked)
        ]
    except ManifestPathError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if violations:
        return report_violations_and_fail(violations)
    print("Rust license policy check passed: no dependency requires strong copyleft terms.")
    return 0


def report_violations_and_fail(violations: list[str]) -> int:
    """Print policy violations and return a non-zero exit code."""
    print("Rust license policy check failed. Found blocked dependencies:")
    for v in violations:
        print(f"  - {v}")
    print("")
    print("Policy: dependencies must not require GPL/AGPL/LGPL/SSPL terms.")
    print("Dual-licensed crates with a permissive option remain allowed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
