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
import re
import shutil
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

RELEASE_SPEC_SECTIONS = "release-spec:sections"
RELEASE_CHECKLIST_GO_NOGO = "release-checklist:go-nogo"
RELEASE_CHECKLIST_WAIVER_PROCESS = "release-checklist:waiver-process"
TEST_MATRIX_WORKSTREAMS = "test-matrix:workstreams"
EVIDENCE_ARTIFACT_EXISTS = "evidence-artifact:exists"
EVIDENCE_ARTIFACT_PARSE = "evidence-artifact:parse"
EVIDENCE_ARTIFACT_REQUIRED_FIELDS = "evidence-artifact:required-fields"
EVIDENCE_ARTIFACT_NON_NEGATIVE = "evidence-artifact:non-negative"
EVIDENCE_ARTIFACT_SCHEMA_VERSION = "evidence-artifact:schema-version"
EVIDENCE_ARTIFACT_PASS = "evidence-artifact:pass"
EVIDENCE_ARTIFACT_UNKNOWN_DIFFS = "evidence-artifact:unknown-diffs"
EVIDENCE_ARTIFACT_ERROR_PARITY = "evidence-artifact:error-parity"
EVIDENCE_ARTIFACT_TOTAL_COMPARISONS = "evidence-artifact:total-comparisons"
EVIDENCE_ARTIFACT_COUNT_CONSISTENCY = "evidence-artifact:count-consistency"
EVIDENCE_ARTIFACT_BREAKDOWN_VALUES = "evidence-artifact:breakdown-values"
EVIDENCE_ARTIFACT_MATCH_BREAKDOWN = "evidence-artifact:matched-breakdown"
EVIDENCE_ARTIFACT_ENTRIES_CONSISTENCY = "evidence-artifact:entries-consistency"
EVIDENCE_ARTIFACT_VERIFICATION_RESULT = "evidence-artifact:verification-result"
KNOWN_DIFFS_EXISTS = "known-diffs:exists"
KNOWN_DIFFS_METADATA = "known-diffs:metadata"
KNOWN_DIFFS_STRUCTURED_METADATA = "known-diffs:structured-metadata"
KNOWN_DIFFS_SUPPRESSOR_SCOPE = "known-diffs:suppressor-scope"
CHANGELOG_0_5_5_ENTRY = "changelog:0.5.5-entry"
CHANGELOG_RELEASE_PATTERN = re.compile(
    r"(?m)^(?:#{1,6}\s+(?:\[0\.5\.5\]|0\.5\.5\b)|\[0\.5\.5\])"
)
REASON_CODE_AUDIT_SCRIPT = "reason-code:audit-script"
REASON_CODES_AUDIT = "reason-codes:audit"


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
        result.failed(RELEASE_SPEC_SECTIONS, f"missing sections: {', '.join(missing)}")
    else:
        result.passed(
            RELEASE_SPEC_SECTIONS,
            f"all {len(required_sections)} key sections present",
        )


def check_release_checklist(result: ValidationResult) -> None:
    """Verify the release checklist exists and has required structure."""
    if not RELEASE_CHECKLIST_PATH.is_file():
        result.failed("release-checklist", "docs/project/release-checklist-0-5-5.md not found")
        return

    content = RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")

    for section in ["Phase 1: Cheap Blockers", "Phase 2: Focused Semantic", "Phase 3: Umbrella"]:
        check_id = section.split(':')[0].strip().lower().replace(' ', '-')
        if section in content:
            result.passed(f"release-checklist:{check_id}")
        else:
            result.failed(f"release-checklist:{check_id}", f"missing section: {section}")

    if "Go/No-Go Criteria" in content:
        result.passed(RELEASE_CHECKLIST_GO_NOGO)
    else:
        result.failed(RELEASE_CHECKLIST_GO_NOGO, "missing Go/No-Go Criteria section")

    if "Waiver Process" in content:
        result.passed(RELEASE_CHECKLIST_WAIVER_PROCESS)
    else:
        result.failed(RELEASE_CHECKLIST_WAIVER_PROCESS, "missing Waiver Process section")


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
        result.failed(TEST_MATRIX_WORKSTREAMS, f"missing workstreams: {', '.join(missing)}")
    else:
        result.passed(TEST_MATRIX_WORKSTREAMS, f"all {len(workstreams)} workstreams covered")


def check_evidence_artifact(result: ValidationResult) -> None:
    """Verify the streaming evidence artifact exists and validates."""
    if not EVIDENCE_PATH.is_file():
        result.failed(EVIDENCE_ARTIFACT_EXISTS, "tests/streaming/evidence/summary.json not found")
        return

    result.passed(EVIDENCE_ARTIFACT_EXISTS)

    try:
        data = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        result.failed(EVIDENCE_ARTIFACT_PARSE, f"JSON parse error: {exc}")
        return

    if not isinstance(data, dict):
        result.failed(
            EVIDENCE_ARTIFACT_PARSE,
            f"Expected JSON object at top-level, got {type(data).__name__}",
        )
        return

    validate_required_fields(data, result)
    validate_non_negative_counts(data, result)
    validate_schema_version(data, result)
    validate_pass_flag(data, result)
    validate_unknown_diffs(data, result)
    validate_error_parity(data, result)
    validate_total_comparisons(data, result)
    validate_count_consistency(data, result)
    validate_breakdown_values(data, result)
    validate_matched_breakdown_consistency(data, result)
    validate_entries_consistency(data, result)
    validate_verification_result(data, result)


def validate_required_fields(data: dict[str, object], result: ValidationResult) -> None:
    """Validate required evidence fields and their JSON types."""
    required_fields = [
        ("schema_version", int),
        ("verified_by", str),
        ("verified_at", str),
        ("total_comparisons", int),
        ("identical_count", int),
        ("known_difference_count", int),
        ("known_difference_by_drift_type", dict),
        ("known_difference_by_severity", dict),
        ("known_differences_registry_total_entries", int),
        ("known_difference_registry_by_drift_type", dict),
        ("known_difference_registry_by_severity", dict),
        ("known_differences_registry", str),
        ("corpus_root", str),
        ("unknown_difference_count", int),
        ("error_parity_mismatch_count", int),
        ("pass", bool),
        ("verification_command", str),
        ("verification_result", str),
    ]
    missing_fields = [
        field_name
        for field_name, _expected_type in required_fields
        if field_name not in data
    ]
    wrong_type_fields = [
        type_mismatch_message(data, field_name, expected_type)
        for field_name, expected_type in required_fields
        if field_name in data
        and not evidence_field_has_type(data[field_name], expected_type)
    ]

    if missing_fields:
        result.failed(
            EVIDENCE_ARTIFACT_REQUIRED_FIELDS,
            f"missing: {', '.join(missing_fields)}",
        )
    elif wrong_type_fields:
        result.failed(
            EVIDENCE_ARTIFACT_REQUIRED_FIELDS,
            f"type mismatches: {'; '.join(wrong_type_fields)}",
        )
    else:
        result.passed(
            EVIDENCE_ARTIFACT_REQUIRED_FIELDS,
            "all required fields present with correct types",
        )


def evidence_field_has_type(value: object, expected_type: type) -> bool:
    """Return whether an evidence field value has the expected JSON type."""
    if expected_type is int and isinstance(value, bool):
        return False
    return isinstance(value, expected_type)


def type_mismatch_message(
    data: dict[str, object],
    field_name: str,
    expected_type: type,
) -> str:
    """Format a field type mismatch for release gate diagnostics."""
    return (
        f"{field_name}: expected {expected_type.__name__}, "
        f"got {type(data[field_name]).__name__}"
    )


def validate_non_negative_counts(data: dict[str, object], result: ValidationResult) -> None:
    """Validate count fields are non-negative integers when present."""
    count_fields = [
        "total_comparisons", "identical_count", "known_difference_count",
        "unknown_difference_count", "error_parity_mismatch_count",
    ]
    negative_fields = [
        f for f in count_fields
        if f in data and (isinstance(data[f], bool) or (isinstance(data[f], int) and data[f] < 0))
    ]
    if negative_fields:
        result.failed(
            EVIDENCE_ARTIFACT_NON_NEGATIVE,
            f"negative values: {', '.join(negative_fields)}",
        )
    else:
        result.passed(EVIDENCE_ARTIFACT_NON_NEGATIVE, "all count fields non-negative")


def validate_schema_version(data: dict[str, object], result: ValidationResult) -> None:
    """Validate evidence schema version."""
    if data.get("schema_version") != 1:
        result.failed(
            EVIDENCE_ARTIFACT_SCHEMA_VERSION,
            f"expected schema_version=1, got {data.get('schema_version')}",
        )
    else:
        result.passed(EVIDENCE_ARTIFACT_SCHEMA_VERSION)


def validate_pass_flag(data: dict[str, object], result: ValidationResult) -> None:
    """Validate the evidence pass flag."""
    if data.get("pass") is not True:
        result.failed(EVIDENCE_ARTIFACT_PASS, f"pass={data.get('pass')}, expected true")
    else:
        result.passed(EVIDENCE_ARTIFACT_PASS)


def validate_unknown_diffs(data: dict[str, object], result: ValidationResult) -> None:
    """Validate that no unknown differences were reported."""
    if data.get("unknown_difference_count", -1) != 0:
        result.failed(
            EVIDENCE_ARTIFACT_UNKNOWN_DIFFS,
            f"unknown_difference_count={data.get('unknown_difference_count')}, expected 0",
        )
    else:
        result.passed(EVIDENCE_ARTIFACT_UNKNOWN_DIFFS)


def validate_error_parity(data: dict[str, object], result: ValidationResult) -> None:
    """Validate that no error-parity mismatches were reported."""
    if data.get("error_parity_mismatch_count", -1) != 0:
        result.failed(
            EVIDENCE_ARTIFACT_ERROR_PARITY,
            f"error_parity_mismatch_count={data.get('error_parity_mismatch_count')}, expected 0",
        )
    else:
        result.passed(EVIDENCE_ARTIFACT_ERROR_PARITY)


def validate_total_comparisons(data: dict[str, object], result: ValidationResult) -> None:
    """Validate that evidence includes at least one comparison."""
    total = data.get("total_comparisons", 0)
    if total > 0:
        result.passed(EVIDENCE_ARTIFACT_TOTAL_COMPARISONS, f"{total} comparisons")
    else:
        result.failed(EVIDENCE_ARTIFACT_TOTAL_COMPARISONS, "total_comparisons is 0")


def validate_count_consistency(data: dict[str, object], result: ValidationResult) -> None:
    """Validate identical plus known differences equals total comparisons."""
    total = data.get("total_comparisons", 0)
    identical = data.get("identical_count", 0)
    known_diffs = data.get("known_difference_count", 0)
    if identical + known_diffs == total:
        result.passed(
            EVIDENCE_ARTIFACT_COUNT_CONSISTENCY,
            f"identical({identical}) + known_diffs({known_diffs}) = total({total})",
        )
    else:
        result.failed(
            EVIDENCE_ARTIFACT_COUNT_CONSISTENCY,
            f"identical({identical}) + known_diffs({known_diffs}) = "
            f"{identical + known_diffs}, expected total({total})",
        )


def validate_breakdown_values(data: dict[str, object], result: ValidationResult) -> None:
    """Validate known-difference breakdown values are non-negative integers."""
    breakdowns = {
        "known_difference_by_drift_type": data.get("known_difference_by_drift_type", {}),
        "known_difference_by_severity": data.get("known_difference_by_severity", {}),
        "known_difference_registry_by_drift_type": data.get(
            "known_difference_registry_by_drift_type", {}
        ),
        "known_difference_registry_by_severity": data.get(
            "known_difference_registry_by_severity", {}
        ),
    }
    bad_values = []
    for field_name, value in breakdowns.items():
        bad_values.extend(invalid_breakdown_values(field_name, value))

    if not bad_values:
        result.passed(
            EVIDENCE_ARTIFACT_BREAKDOWN_VALUES,
            "all breakdown dict values are non-negative integers",
        )
    else:
        result.failed(EVIDENCE_ARTIFACT_BREAKDOWN_VALUES, "; ".join(bad_values))


def invalid_breakdown_values(field_name: str, value: object) -> list[str]:
    """Return invalid value diagnostics for a breakdown dict."""
    if not isinstance(value, dict):
        return [f"{field_name}: not a dict"]
    return [
        f"{field_name}.{k}={v}"
        for k, v in value.items()
        if isinstance(v, bool) or not isinstance(v, int) or v < 0
    ]


def int_breakdown_sum(value: object) -> int:
    """Sum a previously type-checked integer breakdown dict."""
    if not isinstance(value, dict):
        return 0
    return sum(
        v for v in value.values()
        if isinstance(v, int) and not isinstance(v, bool)
    )


def validate_matched_breakdown_consistency(
    data: dict[str, object],
    result: ValidationResult,
) -> None:
    """Validate matched known-difference breakdowns partition matched count."""
    known_diffs = data.get("known_difference_count", 0)
    drift_sum = int_breakdown_sum(data.get("known_difference_by_drift_type", {}))
    severity_sum = int_breakdown_sum(data.get("known_difference_by_severity", {}))

    if drift_sum == known_diffs and severity_sum == known_diffs:
        result.passed(
            EVIDENCE_ARTIFACT_MATCH_BREAKDOWN,
            f"drift_type({drift_sum}) = severity({severity_sum}) = "
            f"known_diffs({known_diffs})",
        )
    else:
        result.failed(
            EVIDENCE_ARTIFACT_MATCH_BREAKDOWN,
            f"drift_type sum={drift_sum}, severity sum={severity_sum}, "
            f"expected known_difference_count={known_diffs}",
        )


def validate_entries_consistency(data: dict[str, object], result: ValidationResult) -> None:
    """Validate registry breakdown sums match total registry entries."""
    entries_total = data.get("known_differences_registry_total_entries", 0)
    entries_by_drift = data.get("known_difference_registry_by_drift_type", {})
    entries_by_severity = data.get("known_difference_registry_by_severity", {})
    drift_sum = int_breakdown_sum(entries_by_drift)
    severity_sum = int_breakdown_sum(entries_by_severity)
    if drift_sum == entries_total and severity_sum == entries_total:
        result.passed(
            EVIDENCE_ARTIFACT_ENTRIES_CONSISTENCY,
            f"drift_type({drift_sum}) = severity({severity_sum}) = registry_entries({entries_total})",
        )
    else:
        result.failed(
            EVIDENCE_ARTIFACT_ENTRIES_CONSISTENCY,
            f"drift_type sum={drift_sum}, severity sum={severity_sum}, "
            f"expected entries_total={entries_total}",
        )


def validate_verification_result(data: dict[str, object], result: ValidationResult) -> None:
    """Validate the evidence verification command reported PASS."""
    if data.get("verification_result") == "PASS":
        result.passed(EVIDENCE_ARTIFACT_VERIFICATION_RESULT)
    else:
        result.failed(
            EVIDENCE_ARTIFACT_VERIFICATION_RESULT,
            f"verification_result={data.get('verification_result')}, expected PASS",
        )


def check_known_differences_metadata(result: ValidationResult) -> None:
    """Verify known-difference entries have structured metadata."""
    entries = _load_known_difference_entries(result)
    if entries is None:
        return

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
            KNOWN_DIFFS_STRUCTURED_METADATA,
            f"entries missing drift_type/severity: {', '.join(missing_metadata)}",
        )
    else:
        result.passed(
            KNOWN_DIFFS_STRUCTURED_METADATA,
            f"all {len(entries)} entries have drift_type and severity",
        )

    if unscoped_without_justification:
        result.failed(
            KNOWN_DIFFS_SUPPRESSOR_SCOPE,
            f"entries without fixture_contains or global_suppressor_justification: {', '.join(unscoped_without_justification)}",
        )
    else:
        result.passed(KNOWN_DIFFS_SUPPRESSOR_SCOPE, "all entries scoped or justified")


def _load_known_difference_entries(result: ValidationResult) -> list[dict[str, object]] | None:
    """Load known-difference entries, reporting gate status on failure."""
    if not KNOWN_DIFF_PATH.is_file():
        result.failed(KNOWN_DIFFS_EXISTS, "tests/streaming/known-differences.toml not found")
        return None

    result.passed(KNOWN_DIFFS_EXISTS)

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            result.failed(
                KNOWN_DIFFS_METADATA,
                "tomllib/tomli not available; install tomli via pip",
            )
            return None

    try:
        with open(KNOWN_DIFF_PATH, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        result.failed("known-diffs:parse", f"TOML parse error: {exc}")
        return None

    return data.get("difference", [])


def check_changelog_entry(result: ValidationResult) -> None:
    """Verify CHANGELOG.md has a 0.5.5 entry."""
    if not CHANGELOG_PATH.is_file():
        result.failed("changelog:exists", "CHANGELOG.md not found")
        return

    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    if CHANGELOG_RELEASE_PATTERN.search(content):
        result.passed(CHANGELOG_0_5_5_ENTRY)
    else:
        result.failed(CHANGELOG_0_5_5_ENTRY, "no 0.5.5 entry found in CHANGELOG.md")

def check_reason_code_audit(result: ValidationResult) -> None:
    """Run the reason-code lifecycle audit script and report its status."""
    import subprocess

    audit_script = PROJECT_ROOT / "tools" / "harness" / "audit_reason_codes.sh"
    if not audit_script.is_file():
        result.failed(REASON_CODE_AUDIT_SCRIPT, "tools/harness/audit_reason_codes.sh not found")
        return

    bash_path = shutil.which("bash")
    if bash_path is None:
        result.failed(REASON_CODE_AUDIT_SCRIPT, "bash not found on PATH")
        return

    try:
        proc = subprocess.run(
            [bash_path, str(audit_script)],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        result.failed(REASON_CODES_AUDIT, f"audit_reason_codes.sh execution error: {exc}")
        return

    if proc.returncode == 0:
        result.passed(REASON_CODES_AUDIT, "all reason codes have complete lifecycles")
    else:
        detail = proc.stderr.strip().split("\n")[-1] if proc.stderr.strip() else "exit code non-zero"
        result.failed(REASON_CODES_AUDIT, detail)


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
    check_reason_code_audit(result)

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
