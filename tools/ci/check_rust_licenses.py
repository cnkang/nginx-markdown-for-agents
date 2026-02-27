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
RUST_MANIFEST_REL = "components/rust-converter/Cargo.toml"


@dataclass
class Token:
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
  | (?P<id>[A-Za-z0-9.\-+]+)
""",
    re.IGNORECASE | re.VERBOSE,
)


def tokenize(expr: str) -> list[Token]:
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
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.idx = 0

    def peek(self) -> Token | None:
        if self.idx >= len(self.tokens):
            return None
        return self.tokens[self.idx]

    def consume(self, kind: str) -> Token:
        tok = self.peek()
        if tok is None or tok.kind != kind:
            got = "EOF" if tok is None else tok.kind
            raise ValueError(f"Expected {kind}, got {got}")
        self.idx += 1
        return tok

    def parse_expression(self) -> bool:
        # OR: strong-copyleft is required only if all OR branches require it.
        value = self.parse_term()
        while self.peek() is not None and self.peek().kind == "OR":
            self.consume("OR")
            rhs = self.parse_term()
            value = value and rhs
        return value

    def parse_term(self) -> bool:
        # AND: strong-copyleft is required if any AND part requires it.
        value = self.parse_factor()
        while self.peek() is not None and self.peek().kind == "AND":
            self.consume("AND")
            rhs = self.parse_factor()
            value = value or rhs
        return value

    def parse_factor(self) -> bool:
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
    normalized = license_id.upper()
    return any(normalized.startswith(prefix) for prefix in STRONG_COPYLEFT_PREFIXES)


def requires_strong_copyleft(expr: str) -> bool:
    # Some crates still publish slash-separated legacy expressions like
    # "MIT/Apache-2.0". Treat "/" as OR.
    expr = re.sub(r"\s*/\s*", " OR ", expr)
    parser = Parser(tokenize(expr))
    result = parser.parse_expression()
    if parser.peek() is not None:
        raise ValueError("Unexpected trailing tokens in SPDX expression")
    return result


def run_metadata(locked: bool) -> dict:
    if locked:
        completed = subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version",
                "1",
                "--all-features",
                "--manifest-path",
                RUST_MANIFEST_REL,
                "--locked",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        completed = subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version",
                "1",
                "--all-features",
                "--manifest-path",
                RUST_MANIFEST_REL,
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    return json.loads(completed.stdout)


def parse_locked_flag(argv: list[str]) -> bool:
    valid = {"--locked"}
    unknown = [arg for arg in argv if arg not in valid]
    if unknown:
        print(f"Unknown arguments: {' '.join(unknown)}", file=sys.stderr)
        print("Usage: check_rust_licenses.py [--locked]", file=sys.stderr)
        raise SystemExit(2)
    return "--locked" in argv


def main() -> int:
    locked = parse_locked_flag(sys.argv[1:])

    manifest_path = (REPO_ROOT / RUST_MANIFEST_REL).resolve()
    if REPO_ROOT not in manifest_path.parents and manifest_path != REPO_ROOT:
        print(f"Refusing manifest path outside repository: {manifest_path}", file=sys.stderr)
        return 2
    if not manifest_path.is_file():
        print(f"Manifest path does not exist: {manifest_path}", file=sys.stderr)
        return 2

    metadata = run_metadata(locked=locked)

    violations: list[str] = []
    for pkg in metadata.get("packages", []):
        name = pkg.get("name", "<unknown>")
        version = pkg.get("version", "<unknown>")
        license_expr = pkg.get("license")

        if not license_expr:
            violations.append(f"{name} {version}: missing SPDX license expression")
            continue

        try:
            if requires_strong_copyleft(license_expr):
                violations.append(f"{name} {version}: {license_expr}")
        except ValueError as exc:
            violations.append(f"{name} {version}: unparsable license expression '{license_expr}' ({exc})")

    if violations:
        print("Rust license policy check failed. Found blocked dependencies:")
        for v in violations:
            print(f"  - {v}")
        print("")
        print("Policy: dependencies must not require GPL/AGPL/LGPL/SSPL terms.")
        print("Dual-licensed crates with a permissive option remain allowed.")
        return 1

    print("Rust license policy check passed: no dependency requires strong copyleft terms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
