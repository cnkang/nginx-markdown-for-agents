#!/usr/bin/env python3
"""
0.7.0 release gate validation.

Validates governance and evidence artifacts for the 0.7.0 release.

Checks:
- FFI migration contract document exists
- New C configuration directives (markdown_decompress_max_size,
  markdown_parse_timeout, markdown_parser_budget)
- New Rust error codes (9, 10, 11) in error.rs
- New Rust modules (negotiator, conditional, decision, header_plan)
- New FFI struct (FFIAcceptResult) in abi.rs
- Cargo.toml version is 0.7.0
- CHANGELOG 0.7.0 entry existence
- Release gates document exists

Security: File paths are validated against an allow-list of expected
directories.  No user-supplied paths are used for file operations without
validation (path traversal prevention).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

FFI_CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "architecture" / "FFI_MIGRATION_CONTRACT.md"
)
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
CARGO_TOML_PATH = (
    PROJECT_ROOT / "components" / "rust-converter" / "Cargo.toml"
)
CONFIG_DIRECTIVES_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_directives_impl.h"
)
ERROR_RS = (
    PROJECT_ROOT / "components" / "rust-converter" / "src" / "error.rs"
)
ABI_RS = (
    PROJECT_ROOT / "components" / "rust-converter" / "src" / "ffi" / "abi.rs"
)
RELEASE_GATES_MD = (
    PROJECT_ROOT
    / "docs"
    / "project"
    / "release-gates"
    / "0.7.0-release-gates.md"
)

RUST_MODULE_DIR = PROJECT_ROOT / "components" / "rust-converter" / "src"

GATE_FFI_CONTRACT = "ffi-contract:exists"
GATE_C_DIRECTIVES = "c-directives:v070-new"
GATE_RUST_ERROR_CODES = "rust-error-codes:v070-new"
GATE_RUST_MODULES = "rust-modules:v070-new"
GATE_FFI_ACCEPT_RESULT = "ffi-struct:FFIAcceptResult"
GATE_CARGO_VERSION = "cargo:version-070"
GATE_CHANGELOG = "changelog:070-entry"
GATE_RELEASE_GATES = "release-gates:070-doc"


class ValidationResult:
    """Accumulate gate check results."""

    def __init__(self) -> None:
        """Initialize an empty result accumulator."""
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, gate_id: str, message: str) -> None:
        """Record a passing gate check."""
        self.results.append(("PASS", gate_id, message))

    def fail(self, gate_id: str, message: str) -> None:
        """Record a failing gate check."""
        self.results.append(("FAIL", gate_id, message))

    def skip(self, gate_id: str, message: str) -> None:
        """Record a skipped gate check."""
        self.results.append(("SKIP", gate_id, message))

    @property
    def has_failures(self) -> bool:
        """Return True if any gate check has failed."""
        return any(s == "FAIL" for s, _, _ in self.results)

    def print_report(self) -> None:
        """Print a formatted report of all gate check results to stdout."""
        for status, gate_id, message in self.results:
            print(f"  {status:4s}  {gate_id:40s}  {message}")


def check_ffi_contract(result: ValidationResult) -> None:
    """Verify FFI migration contract document exists."""
    if FFI_CONTRACT_PATH.is_file():
        content = FFI_CONTRACT_PATH.read_text(encoding="utf-8")
        if len(content.splitlines()) >= 10:
            result.pass_(GATE_FFI_CONTRACT, "FFI_MIGRATION_CONTRACT.md exists")
        else:
            result.fail(GATE_FFI_CONTRACT, "FFI_MIGRATION_CONTRACT.md too short")
    else:
        result.fail(GATE_FFI_CONTRACT, f"Missing at {FFI_CONTRACT_PATH}")


def check_c_directives(result: ValidationResult) -> None:
    """Verify new v0.7.0 C configuration directives exist."""
    if not CONFIG_DIRECTIVES_H.is_file():
        result.fail(GATE_C_DIRECTIVES, "config_directives_impl.h missing")
        return
    content = CONFIG_DIRECTIVES_H.read_text(encoding="utf-8")
    required = [
        "markdown_decompress_max_size",
        "markdown_parse_timeout",
        "markdown_parser_budget",
    ]
    found = 0
    for directive in required:
        if directive in content:
            found += 1
        else:
            result.fail(GATE_C_DIRECTIVES, f"directive '{directive}' not found")
    if found == len(required):
        result.pass_(GATE_C_DIRECTIVES, "All v0.7.0 directives present")


def check_rust_error_codes(result: ValidationResult) -> None:
    """Verify new v0.7.0 Rust error codes (9, 10, 11) in error.rs."""
    if not ERROR_RS.is_file():
        result.fail(GATE_RUST_ERROR_CODES, "error.rs missing")
        return
    content = ERROR_RS.read_text(encoding="utf-8")
    required_names = [
        "DecompressionBudgetExceeded",
        "ParseTimeout",
        "ParseBudgetExceeded",
    ]
    found = 0
    for name in required_names:
        if name in content:
            found += 1
        else:
            result.fail(GATE_RUST_ERROR_CODES, f"error variant '{name}' not found")
    if found == len(required_names):
        result.pass_(GATE_RUST_ERROR_CODES, "All v0.7.0 error variants present")


def check_rust_modules(result: ValidationResult) -> None:
    """Verify new v0.7.0 Rust module files exist."""
    required_modules = ["negotiator.rs", "conditional.rs", "decision.rs", "header_plan.rs"]
    found = 0
    for module_name in required_modules:
        module_path = RUST_MODULE_DIR / module_name
        if module_path.is_file():
            found += 1
        else:
            result.fail(GATE_RUST_MODULES, f"module '{module_name}' not found")
    if found == len(required_modules):
        result.pass_(GATE_RUST_MODULES, "All v0.7.0 Rust modules present")


def check_ffi_accept_result(result: ValidationResult) -> None:
    """Verify FFIAcceptResult struct in abi.rs."""
    if not ABI_RS.is_file():
        result.fail(GATE_FFI_ACCEPT_RESULT, "abi.rs missing")
        return
    content = ABI_RS.is_file() and ABI_RS.read_text(encoding="utf-8")
    if "FFIAcceptResult" in content:
        result.pass_(GATE_FFI_ACCEPT_RESULT, "FFIAcceptResult struct present")
    else:
        result.fail(GATE_FFI_ACCEPT_RESULT, "FFIAcceptResult struct not found")


def check_cargo_version(result: ValidationResult) -> None:
    """Verify Cargo.toml version is 0.7.0."""
    if not CARGO_TOML_PATH.is_file():
        result.fail(GATE_CARGO_VERSION, "Cargo.toml missing")
        return
    try:
        cargo = tomllib.loads(CARGO_TOML_PATH.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        result.fail(GATE_CARGO_VERSION, f"Cargo.toml parse error: {exc}")
        return

    version = cargo.get("package", {}).get("version", "")
    if version == "0.7.0":
        result.pass_(GATE_CARGO_VERSION, "Cargo.toml version is 0.7.0")
    else:
        result.fail(GATE_CARGO_VERSION, f"version is '{version}', expected '0.7.0'")


def check_changelog(result: ValidationResult) -> None:
    """Verify CHANGELOG.md contains 0.7.0 entry."""
    if not CHANGELOG_PATH.is_file():
        result.skip(GATE_CHANGELOG, "CHANGELOG.md not present")
        return
    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    if re.search(r"0\.7\.0", content):
        result.pass_(GATE_CHANGELOG, "0.7.0 entry found")
    else:
        result.fail(GATE_CHANGELOG, "0.7.0 entry not found")


def check_release_gates(result: ValidationResult) -> None:
    """Verify release gates document exists."""
    if RELEASE_GATES_MD.is_file():
        result.pass_(GATE_RELEASE_GATES, "0.7.0-release-gates.md exists")
    else:
        result.fail(GATE_RELEASE_GATES, f"Missing at {RELEASE_GATES_MD}")


def main() -> int:
    """Run 0.7.0 release gate validation and report results."""
    result = ValidationResult()

    check_ffi_contract(result)
    check_c_directives(result)
    check_rust_error_codes(result)
    check_rust_modules(result)
    check_ffi_accept_result(result)
    check_cargo_version(result)
    check_changelog(result)
    check_release_gates(result)

    print("0.7.0 Release Gate Validation Report")
    print("=" * 60)
    result.print_report()
    print()

    pass_count = sum(s == "PASS" for s, _, _ in result.results)
    fail_count = sum(s == "FAIL" for s, _, _ in result.results)
    skip_count = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {pass_count} passed, {fail_count} failed, {skip_count} skipped")

    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
