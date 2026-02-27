#!/usr/bin/env python3
"""Fail CI when Rust dependency licenses require strong copyleft terms.

Policy goal:
- Block dependencies whose SPDX expression *requires* strong copyleft licenses
  (GPL/AGPL/LGPL/SSPL).
- Allow dual-licensed crates when a permissive option exists
  (for example: "MIT OR LGPL-2.1-or-later").
"""

from __future__ import annotations

import argparse
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


def run_metadata(manifest_path: Path, locked: bool) -> dict:
    cmd = [
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--all-features",
        "--manifest-path",
        str(manifest_path),
    ]
    if locked:
        cmd.append("--locked")
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest-path",
        default="components/rust-converter/Cargo.toml",
        help="Path to Cargo.toml for the Rust workspace/project to evaluate.",
    )
    parser.add_argument(
        "--locked",
        action="store_true",
        help="Require Cargo.lock to be present and up-to-date.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest_path).resolve()
    metadata = run_metadata(manifest_path, locked=args.locked)

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
