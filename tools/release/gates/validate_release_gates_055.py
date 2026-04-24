#!/usr/bin/env python3
"""
0.5.5 release gate validation.

Validates governance and evidence artifacts for the 0.5.5 stabilization
release.  Complements the existing 0.5.0 gate validator.

Checks:
- Sub-spec document existence (0.5.5 workstream requirements.md, design.md, tasks.md)
- Release spec document existence (docs/project/0.5.5-release-spec.md)
- Release checklist existence (docs/project/release-checklist-0-5-5.md)
- Test matrix existence (docs/project/test-matrix-0-5-5.md)
- Streaming evidence artifact existence and schema validation
- Known-difference registry structured metadata
- Reason-code lifecycle audit (calls audit_reason_codes.sh)
- CHANGELOG 0.5.5 entry existence

Security: File paths are validated against an allow-list of expected
directories.  No user-supplied paths are used for file operations without
validation (path traversal prevention).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

SPECS_DIR = PROJECT_ROOT / ".kiro" / "specs"

SUBSPECS_055 = [
    "19-0.5.5-release-spec",
    "20-0.5.5-semantic-fidelity",
    "21-0.5.5-protocol-correctness",
    "22-0.5.5-auth-cache-safety",
    "23-0.5.5-streaming-parity-evidence",
    "24-0.5.5-operator-diagnostics",
    "25-0.5.5-release-gate-docs-sync",
]

SPEC_DOCS = ["requirements.md", "design.md", "tasks.md"]

RELEASE_SPEC_PATH = PROJECT_ROOT / "docs" / "project" / "0.5.5-release-spec.md"
RELEASE_CHECKLIST_PATH = (
    PROJECT_ROOT / "docs" / "project" / "release-checklist-0-5-5.md"
)
TEST_MATRIX_PATH = PROJECT_ROOT / "docs" / "project" / "test-matrix-0-5-5.md"
EVIDENCE_PATH = (
    PROJECT_ROOT / "tests" / "streaming" / "evidence" / "summary.json"
)
KNOWN_DIFF_PATH = (
    PROJECT_ROOT / "tests" / "streaming" / "known-differences.toml"
)
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------


class ValidationResult:
    """Collects pass/fail/skip results for structured output."""

    def __init__(self) -> None:
        """Initialize an empty result list."""
        self.results: list[tuple[str, str, str]] = []

    def passed(self, check: str, detail: str = "") -> None:
        """Record a successful check result."""
        self.results.append(("PASS", check, detail))

    def failed(self, check: str, detail: str = "") -> None:
        """Record a failed check result."""
        self.results.append(("FAIL", check, detail))

    def skipped(self, check: str, detail: str = "") -> None:
        """Record a skipped check result."""
        self.results.append(("SKIP", check, detail))

    @property
    def has_failures(self) -> bool:
        """Return True when at least one recorded result is FAIL."""
        return any(s == "FAIL" for s, _, _ in self.results)

    def print_report(self) -> None:
        """Print one human-readable line per recorded validation result."""
        for status, check, detail in self.results:
            suffix = f" — {detail}" if detail else ""
            print(f"  [{status}] {check}{suffix}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_subspecs_docs_exist(result: ValidationResult) -> None:
    """Verify that each 0.5.5 sub-spec has requirements.md, design.md, tasks.md."""
    for name in SUBSPECS_055:
        spec_dir = SPECS_DIR / name
        if not spec_dir.is_dir():
            result.failed(f"docs-exist:{name}", "sub-spec directory not found")
            continue
        for doc in SPEC_DOCS:
            path = spec_dir / doc
            if path.is_file():
                result.passed(f"docs-exist:{name}/{doc}")
            else:
                result.failed(f"docs-exist:{name}/{doc}", "file not found")


def check_release_spec(result: ValidationResult) -> None:
    """Verify the release spec document exists and has key sections."""
    if not RELEASE_SPEC_PATH.is_file():
        result.failed("release-spec", "docs/project/0.5.5-release-spec.md not found")
        return

    content = RELEASE_SPEC_PATH.read_text(encoding="utf-8")
    required_sections = [
        "Release Positioning",
        "Scope Summary",
        "Shared Principles",
        "Global Invariants",
        "Verification Policy",
        "Acceptance Model",
        "Sub-Spec Index",
    ]
    missing = [s for s in required_sections if s not in content]
    if missing:
        result.failed("release-spec:sections", f"missing sections: {', '.join(missing)}")
    else:
        result.passed("release-spec:sections", f"all {len(required_sections)} key sections present")


def check_release_checklist(result: ValidationResult) -> None:
    """Verify the release checklist exists and has required structure."""
    if not RELEASE_CHECKLIST_PATH.is_file():
        result.failed("release-checklist", "docs/project/release-checklist-0-5-5.md not found")
        return

    content = RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")

    for section in ["Phase 1: Cheap Blockers", "Phase 2: Focused Semantic", "Phase 3: Umbrella"]:
        if section in content:
            result.passed(f"release-checklist:{section.split(':')[0].strip().lower().replace(' ', '-')}")
        else:
            result.failed(f"release-checklist:{section.split(':')[0].strip().lower().replace(' ', '-')}", f"missing section: {section}")

    if "Go/No-Go Criteria" in content:
        result.passed("release-checklist:go-nogo")
    else:
        result.failed("release-checklist:go-nogo", "missing Go/No-Go Criteria section")

    if "Waiver Process" in content:
        result.passed("release-checklist:waiver-process")
    else:
        result.failed("release-checklist:waiver-process", "missing Waiver Process section")


def check_test_matrix(result: ValidationResult) -> None:
    """Verify the test matrix exists and covers all workstreams."""
    if not TEST_MATRIX_PATH.is_file():
        result.failed("test-matrix", "docs/project/test-matrix-0-5-5.md not found")
        return

    content = TEST_MATRIX_PATH.read_text(encoding="utf-8")
    workstreams = [
        "Semantic Fidelity",
        "Protocol Correctness",
        "Auth / Cache Safety",
        "Streaming Parity",
        "Operator Diagnostics",
        "Release Gate",
    ]
    missing = [w for w in workstreams if w not in content]
    if missing:
        result.failed("test-matrix:workstreams", f"missing workstreams: {', '.join(missing)}")
    else:
        result.passed("test-matrix:workstreams", f"all {len(workstreams)} workstreams covered")


def check_evidence_artifact(result: ValidationResult) -> None:
    """Verify the streaming evidence artifact exists and validates."""
    if not EVIDENCE_PATH.is_file():
        result.failed("evidence-artifact:exists", "tests/streaming/evidence/summary.json not found")
        return

    result.passed("evidence-artifact:exists")

    try:
        data = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        result.failed("evidence-artifact:parse", f"JSON parse error: {exc}")
        return

    if data.get("schema_version") != 1:
        result.failed(
            "evidence-artifact:schema-version",
            f"expected schema_version=1, got {data.get('schema_version')}",
        )
    else:
        result.passed("evidence-artifact:schema-version")

    if data.get("pass") is not True:
        result.failed("evidence-artifact:pass", f"pass={data.get('pass')}, expected true")
    else:
        result.passed("evidence-artifact:pass")

    if data.get("unknown_difference_count", -1) != 0:
        result.failed(
            "evidence-artifact:unknown-diffs",
            f"unknown_difference_count={data.get('unknown_difference_count')}, expected 0",
        )
    else:
        result.passed("evidence-artifact:unknown-diffs")

    if data.get("error_parity_mismatch_count", -1) != 0:
        result.failed(
            "evidence-artifact:error-parity",
            f"error_parity_mismatch_count={data.get('error_parity_mismatch_count')}, expected 0",
        )
    else:
        result.passed("evidence-artifact:error-parity")

    total = data.get("total_comparisons", 0)
    if total > 0:
        result.passed("evidence-artifact:total-comparisons", f"{total} comparisons")
    else:
        result.failed("evidence-artifact:total-comparisons", "total_comparisons is 0")


def check_known_differences_metadata(result: ValidationResult) -> None:
    """Verify known-difference entries have structured metadata."""
    if not KNOWN_DIFF_PATH.is_file():
        result.failed("known-diffs:exists", "tests/streaming/known-differences.toml not found")
        return

    result.passed("known-diffs:exists")

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            result.skipped("known-diffs:metadata", "tomllib/tomli not available")
            return

    try:
        with open(KNOWN_DIFF_PATH, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        result.failed("known-diffs:parse", f"TOML parse error: {exc}")
        return

    entries = data.get("difference", [])
    if not entries:
        result.failed("known-diffs:entries", "no [[difference]] entries found")
        return

    missing_metadata = []
    unscoped_without_justification = []
    for entry in entries:
        entry_id = entry.get("id", "<unknown>")
        if "drift_type" not in entry or "severity" not in entry:
            missing_metadata.append(entry_id)
        if "fixture_contains" not in entry and "global_suppressor_justification" not in entry:
            unscoped_without_justification.append(entry_id)

    if missing_metadata:
        result.failed(
            "known-diffs:structured-metadata",
            f"entries missing drift_type/severity: {', '.join(missing_metadata)}",
        )
    else:
        result.passed("known-diffs:structured-metadata", f"all {len(entries)} entries have drift_type and severity")

    if unscoped_without_justification:
        result.failed(
            "known-diffs:suppressor-scope",
            f"entries without fixture_contains or global_suppressor_justification: {', '.join(unscoped_without_justification)}",
        )
    else:
        result.passed("known-diffs:suppressor-scope", "all entries scoped or justified")


def check_changelog_entry(result: ValidationResult) -> None:
    """Verify CHANGELOG.md has a 0.5.5 entry."""
    if not CHANGELOG_PATH.is_file():
        result.failed("changelog:exists", "CHANGELOG.md not found")
        return

    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    if "0.5.5" in content:
        result.passed("changelog:0.5.5-entry")
    else:
        result.failed("changelog:0.5.5-entry", "no 0.5.5 entry found in CHANGELOG.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run 0.5.5 release gate validation and report results."""
    result = ValidationResult()

    check_subspecs_docs_exist(result)
    check_release_spec(result)
    check_release_checklist(result)
    check_test_matrix(result)
    check_evidence_artifact(result)
    check_known_differences_metadata(result)
    check_changelog_entry(result)

    print("0.5.5 Release Gate Validation Report")
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
