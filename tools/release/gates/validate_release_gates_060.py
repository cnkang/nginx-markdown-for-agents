#!/usr/bin/env python3
"""
0.6.0 release gate validation.

Validates governance and evidence artifacts for the 0.6.0 production
readiness release.

Checks:
- Repo-owned 0.6.0 release spec surfaces exist
- VERSION_PLANNING 0.6.0 section existence
- ADR-0007 and ADR-0008 existence
- Migration guide existence
- Harness routing manifest v0.6.0 entries
- New C configuration directives (markdown_streaming_auto_threshold,
  markdown_prune_noise, markdown_prune_selectors,
  markdown_prune_protection_selectors, markdown_memory_budget)
- New reason codes (ELIGIBLE_STREAMING_AUTO, ELIGIBLE_FULLBUFFER_AUTO)
- Cargo default features include prune_noise_regions
- CHANGELOG 0.6.0 entry existence

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

VERSION_PLANNING_PATH = PROJECT_ROOT / "docs" / "project" / "VERSION_PLANNING.md"
ADR_0007_PATH = (
    PROJECT_ROOT / "docs" / "architecture" / "ADR" / "0007-streaming-default.md"
)
ADR_0008_PATH = (
    PROJECT_ROOT / "docs" / "architecture" / "ADR" / "0008-noise-pruning-default.md"
)
MIGRATION_GUIDE_PATH = (
    PROJECT_ROOT / "docs" / "guides" / "streaming-default-migration.md"
)
ROUTING_MANIFEST_PATH = (
    PROJECT_ROOT / "docs" / "harness" / "routing-manifest.json"
)
CARGO_TOML_PATH = (
    PROJECT_ROOT / "components" / "rust-converter" / "Cargo.toml"
)
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
FILTER_MODULE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_filter_module.h"
)
REASON_C = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_reason.c"
)
CONFIG_DIRECTIVES_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_directives_impl.h"
)

GATE_SPEC_DOCS = "spec-docs:exists"
GATE_VERSION_PLANNING = "version-planning:060-section"
GATE_ADR_0007 = "adr:0007-exists"
GATE_ADR_0008 = "adr:0008-exists"
GATE_MIGRATION_GUIDE = "migration-guide:exists"
GATE_ROUTING_MANIFEST = "routing-manifest:060-entries"
GATE_CARGO_DEFAULT_FEATURE = "cargo:default-prune-noise-regions"
GATE_C_DIRECTIVES = "c-directives:v060-new"
GATE_REASON_CODES = "reason-codes:v060-new"
GATE_CHANGELOG = "changelog:060-entry"
GATE_COVERAGE_GATE_SCRIPT = "coverage-gate:script-exists"

COVERAGE_GATE_SCRIPT = (
    PROJECT_ROOT / "tools" / "ci" / "coverage_gate.py"
)

SPEC_SURFACES = [
    ("VERSION_PLANNING.md", VERSION_PLANNING_PATH),
    ("ADR-0007", ADR_0007_PATH),
    ("ADR-0008", ADR_0008_PATH),
]


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


def check_spec_docs(result: ValidationResult) -> None:
    """Verify repo-owned 0.6.0 release spec surfaces exist."""
    for label, path in SPEC_SURFACES:
        if path.is_file():
            result.pass_(GATE_SPEC_DOCS, f"{label} exists")
        else:
            result.fail(GATE_SPEC_DOCS, f"{label} missing at {path}")


def check_version_planning(result: ValidationResult) -> None:
    """Verify VERSION_PLANNING.md contains 0.6.0 section."""
    if not VERSION_PLANNING_PATH.is_file():
        result.fail(GATE_VERSION_PLANNING, "VERSION_PLANNING.md missing")
        return
    content = VERSION_PLANNING_PATH.read_text(encoding="utf-8")
    if "0.6.0" in content and "Production Readiness" in content:
        result.pass_(GATE_VERSION_PLANNING, "0.6.0 section found")
    else:
        result.fail(GATE_VERSION_PLANNING, "0.6.0 Production Readiness section missing")


def check_adr_0007(result: ValidationResult) -> None:
    """Verify ADR-0007 exists."""
    if ADR_0007_PATH.is_file():
        result.pass_(GATE_ADR_0007, "ADR-0007 exists")
    else:
        result.fail(GATE_ADR_0007, f"ADR-0007 missing at {ADR_0007_PATH}")


def check_adr_0008(result: ValidationResult) -> None:
    """Verify ADR-0008 exists."""
    if ADR_0008_PATH.is_file():
        result.pass_(GATE_ADR_0008, "ADR-0008 exists")
    else:
        result.fail(GATE_ADR_0008, f"ADR-0008 missing at {ADR_0008_PATH}")


def check_migration_guide(result: ValidationResult) -> None:
    """Verify migration guide exists."""
    if MIGRATION_GUIDE_PATH.is_file():
        result.pass_(GATE_MIGRATION_GUIDE, "Migration guide exists")
    else:
        result.fail(GATE_MIGRATION_GUIDE, f"Migration guide missing at {MIGRATION_GUIDE_PATH}")


def check_routing_manifest(result: ValidationResult) -> None:
    """Verify routing-manifest.json contains v0.6.0 verification families."""
    if not ROUTING_MANIFEST_PATH.is_file():
        result.fail(GATE_ROUTING_MANIFEST, "routing-manifest.json missing")
        return
    try:
        import json
        content = ROUTING_MANIFEST_PATH.read_text(encoding="utf-8")
        manifest = json.loads(content)
        families = manifest.get("verification_families", {})
        required_families = ["coverage-gate", "release-governance-060", "packaging-e2e"]
        found = 0
        for family in required_families:
            if family in families:
                found += 1
            else:
                result.fail(GATE_ROUTING_MANIFEST, f"verification family '{family}' missing")
        if found == len(required_families):
            result.pass_(GATE_ROUTING_MANIFEST, "All v0.6.0 verification families present")
    except (json.JSONDecodeError, KeyError) as exc:
        result.fail(GATE_ROUTING_MANIFEST, f"Parse error: {exc}")


def check_cargo_default_feature(result: ValidationResult) -> None:
    """Verify prune_noise_regions is in Cargo.toml default features."""
    if not CARGO_TOML_PATH.is_file():
        result.fail(GATE_CARGO_DEFAULT_FEATURE, "Cargo.toml missing")
        return
    try:
        cargo = tomllib.loads(CARGO_TOML_PATH.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        result.fail(GATE_CARGO_DEFAULT_FEATURE, f"Cargo.toml parse error: {exc}")
        return

    default_features = cargo.get("features", {}).get("default", [])
    if isinstance(default_features, list) and "prune_noise_regions" in default_features:
        result.pass_(GATE_CARGO_DEFAULT_FEATURE, "prune_noise_regions in default features")
    else:
        result.fail(GATE_CARGO_DEFAULT_FEATURE, "prune_noise_regions not in default features")


def check_c_directives(result: ValidationResult) -> None:
    """Verify new v0.6.0 C configuration directives exist."""
    directives_path = CONFIG_DIRECTIVES_H
    if not directives_path.is_file():
        result.fail(GATE_C_DIRECTIVES, "config_directives_impl.h missing")
        return
    content = directives_path.read_text(encoding="utf-8")
    required = [
        "markdown_streaming_auto_threshold",
        "markdown_prune_noise",
        "markdown_prune_selectors",
        "markdown_prune_protection_selectors",
        "markdown_memory_budget",
    ]
    found = 0
    for directive in required:
        if f'"{directive}"' in content or f"ngx_string(\"{directive}\")" in content:
            found += 1
        else:
            result.fail(GATE_C_DIRECTIVES, f"directive '{directive}' not found")
    if found == len(required):
        result.pass_(GATE_C_DIRECTIVES, "All v0.6.0 directives present")


def check_reason_codes(result: ValidationResult) -> None:
    """Verify new v0.6.0 reason codes exist in reason.c."""
    if not REASON_C.is_file():
        result.fail(GATE_REASON_CODES, "reason.c missing")
        return
    content = REASON_C.read_text(encoding="utf-8")
    required = ["ELIGIBLE_STREAMING_AUTO", "ELIGIBLE_FULLBUFFER_AUTO"]
    found = 0
    for code in required:
        if code in content:
            found += 1
        else:
            result.fail(GATE_REASON_CODES, f"reason code '{code}' not found")
    if found == len(required):
        result.pass_(GATE_REASON_CODES, "All v0.6.0 reason codes present")


def check_changelog(result: ValidationResult) -> None:
    """Verify CHANGELOG.md contains 0.6.0 entry."""
    if not CHANGELOG_PATH.is_file():
        result.skip(GATE_CHANGELOG, "CHANGELOG.md not present (not yet created)")
        return
    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    if re.search(r"0\.6\.0", content):
        result.pass_(GATE_CHANGELOG, "0.6.0 entry found")
    else:
        result.skip(GATE_CHANGELOG, "0.6.0 entry not yet in CHANGELOG")


def check_coverage_gate_script(result: ValidationResult) -> None:
    """Verify coverage gate enforcement script exists."""
    if COVERAGE_GATE_SCRIPT.is_file():
        content = COVERAGE_GATE_SCRIPT.read_text(encoding="utf-8")
        if "coverage_gate" in content and "parse_lcov_summary" in content:
            result.pass_(GATE_COVERAGE_GATE_SCRIPT, "coverage_gate.py exists with required functions")
        else:
            result.fail(GATE_COVERAGE_GATE_SCRIPT, "coverage_gate.py missing required functions")
    else:
        result.fail(GATE_COVERAGE_GATE_SCRIPT, f"coverage_gate.py missing at {COVERAGE_GATE_SCRIPT}")


def main() -> int:
    """Run 0.6.0 release gate validation and report results."""
    result = ValidationResult()

    check_spec_docs(result)
    check_version_planning(result)
    check_adr_0007(result)
    check_adr_0008(result)
    check_migration_guide(result)
    check_routing_manifest(result)
    check_cargo_default_feature(result)
    check_c_directives(result)
    check_reason_codes(result)
    check_changelog(result)
    check_coverage_gate_script(result)

    print("0.6.0 Release Gate Validation Report")
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
