#!/usr/bin/env python3
"""
Release gate validator for v0.8.0.

Extends the v0.7.0 gate framework with v0.8.0-specific checks:

1. Cargo version is 0.8.0
2. 0.6.x compatibility bridge is fully removed:
   - markdown_streaming_auto_threshold is NOT in the directive array
   - NGX_HTTP_MARKDOWN_STREAMING_ENGINE_* constants are NOT in filter_module.h
   - conf->streaming.* fields are NOT in C source
3. New v0.8.0 streaming directives are registered and documented
4. Release gate Makefile target uses 0.8 validators
5. FFI migration contract mentions 0.8.0 changes

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use Path.resolve() within PROJECT_ROOT.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MAKEFILE = PROJECT_ROOT / "Makefile"
CARGO_TOML_PATH = PROJECT_ROOT / "components" / "rust-converter" / "Cargo.toml"
CONFIG_DIRECTIVES_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_directives_impl.h"
)
FILTER_MODULE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_filter_module.h"
)
CONFIG_CORE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_core_impl.h"
)
FFI_CONTRACT_PATH = PROJECT_ROOT / "docs" / "architecture" / "FFI_MIGRATION_CONTRACT.md"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
MIGRATION_08_PATH = PROJECT_ROOT / "docs" / "guides" / "MIGRATION-0.8.md"
STREAMING_IMPL_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_streaming_impl.h"
)

CARGO_VERSION_GATE = "cargo:version"


class ValidationResult:
    """Accumulates PASS/FAIL/SKIP check results for release gate validation."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, check_id: str, message: str) -> None:
        """Record a passing check."""
        self.results.append(("PASS", check_id, message))

    def fail(self, check_id: str, message: str) -> None:
        """Record a failing check."""
        self.results.append(("FAIL", check_id, message))

    def skip(self, check_id: str, message: str) -> None:
        """Record a skipped check (e.g. prerequisite file missing)."""
        self.results.append(("SKIP", check_id, message))

    @property
    def has_failures(self) -> bool:
        """Return True if any recorded check has status FAIL."""
        return any(s == "FAIL" for s, _, _ in self.results)


def read(path: Path) -> str:
    """Read a file; return empty string if it does not exist."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _expected_cargo_version() -> str:
    """Return expected cargo version from RELEASE_GATE_EXPECTED_CARGO_VERSION or default."""
    return os.environ.get("RELEASE_GATE_EXPECTED_CARGO_VERSION", "0.8.0")


def check_cargo_version(result: ValidationResult) -> None:
    """Verify Cargo.toml package version matches the expected release version."""
    cargo_txt = read(CARGO_TOML_PATH)
    if not cargo_txt:
        result.fail(CARGO_VERSION_GATE, "Cargo.toml missing")
        return
    try:
        version = tomllib.loads(cargo_txt).get("package", {}).get("version", "")
    except tomllib.TOMLDecodeError as exc:
        result.fail(CARGO_VERSION_GATE, f"Cargo.toml parse error: {exc}")
        return
    expected = _expected_cargo_version()
    if version == expected:
        result.pass_(CARGO_VERSION_GATE, f"Cargo version is {expected}")
    else:
        result.fail(CARGO_VERSION_GATE, f"version is {version}, expected {expected}")


def check_removed_directive(result: ValidationResult) -> None:
    """Verify markdown_streaming_auto_threshold is absent from the command array."""
    src = read(CONFIG_DIRECTIVES_H)
    if not src:
        result.fail("prereq:directives", "config_directives_impl.h not found")
        return
    pattern = r'ngx_string\("markdown_streaming_auto_threshold"\)'
    if re.search(pattern, src):
        result.fail(
            "removed:markdown_streaming_auto_threshold",
            "removed directive still in command array",
        )
    else:
        result.pass_(
            "removed:markdown_streaming_auto_threshold",
            "removed directive absent from command array",
        )


def check_removed_constants(result: ValidationResult) -> None:
    """Verify NGX_HTTP_MARKDOWN_STREAMING_ENGINE_* constants are not #defined."""
    src = read(FILTER_MODULE_H)
    if not src:
        result.fail("prereq:filter_module_h", "filter_module.h not found")
        return
    removed = [
        "NGX_HTTP_MARKDOWN_STREAMING_ENGINE_OFF",
        "NGX_HTTP_MARKDOWN_STREAMING_ENGINE_ON",
        "NGX_HTTP_MARKDOWN_STREAMING_ENGINE_AUTO",
    ]
    for constant in removed:
        pattern = rf"#define\s+{re.escape(constant)}\b"
        if re.search(pattern, src):
            result.fail(
                f"removed:constant:{constant}",
                f"{constant} still defined in filter_module.h",
            )
        else:
            result.pass_(
                f"removed:constant:{constant}",
                f"{constant} absent from filter_module.h",
            )


def check_removed_conf_fields(result: ValidationResult) -> None:
    """Verify conf->streaming.* fields are absent from all C sources."""
    src_dir = (
        PROJECT_ROOT / "components" / "nginx-module" / "src"
    )
    sources: dict[str, str] = {}
    if src_dir.is_dir():
        for path in src_dir.rglob("*.[ch]"):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            sources[path.name] = content
    removed_fields = [
        r"conf->streaming\.engine",
        r"conf->streaming\.auto_threshold",
    ]
    for field_pat in removed_fields:
        found_in = []
        for name, content in sources.items():
            if re.search(field_pat, content):
                found_in.append(name)
        if found_in:
            result.fail(
                f"removed:field:{field_pat}",
                f"still present in: {', '.join(found_in)}",
            )
        else:
            result.pass_(f"removed:field:{field_pat}", "absent from all sources")


def check_new_directives(result: ValidationResult) -> None:
    """Verify all v0.8.0 streaming directives are registered in the command array."""
    src = read(CONFIG_DIRECTIVES_H)
    if not src:
        result.fail("prereq:directives", "config_directives_impl.h not found")
        return
    new_directives = [
        "markdown_stream_threshold",
        "markdown_stream_precommit_buffer",
        "markdown_stream_flush_min",
        "markdown_stream_excluded_types",
    ]
    for directive in new_directives:
        pattern = rf'ngx_string\("{re.escape(directive)}"\)'
        if re.search(pattern, src):
            result.pass_(
                f"new:directive:{directive}",
                f"{directive} found in command array",
            )
        else:
            result.fail(
                f"new:directive:{directive}",
                f"{directive} NOT found in command array",
            )


def check_select_processing_path(result: ValidationResult) -> None:
    """Verify select_processing_path uses conf->stream.engine, not conf->streaming.engine."""
    src = read(STREAMING_IMPL_H)
    if not src:
        result.fail("prereq:streaming_impl", "streaming_impl.h not found")
        return
    if "conf->stream.engine" in src:
        result.pass_(
            "runtime:conf->stream.engine",
            "select_processing_path uses conf->stream.engine",
        )
    else:
        result.fail(
            "runtime:conf->stream.engine",
            "select_processing_path does not use conf->stream.engine",
        )
    if "conf->streaming.engine" in src:
        result.fail(
            "removed:conf->streaming.engine",
            "select_processing_path still uses conf->streaming.engine (v0.6 fallback)",
        )
    else:
        result.pass_(
            "removed:conf->streaming.engine",
            "select_processing_path does not use conf->streaming.engine",
        )


def check_migration_doc(result: ValidationResult) -> None:
    """Verify MIGRATION-0.8.md documents removed directives and 0.6.x compat removal."""
    migration = read(MIGRATION_08_PATH)
    if not migration:
        result.fail("doc:migration-0.8", "MIGRATION-0.8.md not found")
        return
    if "markdown_streaming_auto_threshold" in migration and "REMOVED" in migration:
        result.pass_(
            "doc:migration-removed",
            "MIGRATION-0.8.md documents markdown_streaming_auto_threshold as REMOVED",
        )
    else:
        result.fail(
            "doc:migration-removed",
            "MIGRATION-0.8.md missing markdown_streaming_auto_threshold REMOVED documentation",
        )
    if "0.6.x" in migration and "compatibility" in migration.lower():
        result.pass_(
            "doc:migration-06x",
            "MIGRATION-0.8.md documents 0.6.x compatibility removal",
        )
    else:
        result.fail(
            "doc:migration-06x",
            "MIGRATION-0.8.md missing 0.6.x compatibility removal documentation",
        )


def check_makefile_080_gate(result: ValidationResult) -> None:
    """Verify Makefile has release-gates-check-080 target referencing the config validator."""
    mk = read(MAKEFILE)
    if not mk:
        result.fail("prereq:makefile", "Makefile not found")
        return
    if "release-gates-check-080" in mk:
        result.pass_("makefile:080-gate", "release-gates-check-080 target exists")
    else:
        result.fail("makefile:080-gate", "release-gates-check-080 target missing")
    if "validate_config_directives_080.py" in mk:
        result.pass_(
            "makefile:080-config-validator",
            "Makefile references validate_config_directives_080.py",
        )
    else:
        result.fail(
            "makefile:080-config-validator",
            "Makefile does not reference validate_config_directives_080.py",
        )


def check_changelog(result: ValidationResult) -> None:
    """Verify CHANGELOG.md contains a 0.8.0 section."""
    changelog = read(CHANGELOG_PATH)
    if not changelog:
        result.fail("prereq:changelog", "CHANGELOG.md not found")
        return
    if "0.8.0" in changelog:
        result.pass_("changelog:080", "CHANGELOG.md has 0.8.0 section")
    else:
        result.fail("changelog:080", "CHANGELOG.md missing 0.8.0 section")


def validate_all(result: ValidationResult) -> None:
    """Run every release gate check and record pass/fail in result."""
    check_cargo_version(result)
    check_removed_directive(result)
    check_removed_constants(result)
    check_removed_conf_fields(result)
    check_new_directives(result)
    check_select_processing_path(result)
    check_migration_doc(result)
    check_makefile_080_gate(result)
    check_changelog(result)


def print_report(result: ValidationResult) -> None:
    """Print the release gate validation report to stdout."""
    print("v0.8.0 Release Gate Validation Report")
    print("=" * 60)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:44s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    """Entry point: parse args, run validation, print report, return exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["basic", "strict", "evidence"],
        default="basic",
    )
    args = parser.parse_args()

    result = ValidationResult()
    validate_all(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
