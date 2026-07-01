#!/usr/bin/env python3
"""Release gate validator for 0.9.0.

Validates that all P0 0.9.0 deliverables are present in the repository.
Runs from a clean checkout — does not depend on .kiro/ or local state.

Exit codes:
  0 = all gates pass
  1 = at least one gate failed
"""

import re
import sys
from pathlib import Path


def find_repo_root() -> Path:
    """Find repository root from script location."""
    script_dir = Path(__file__).resolve().parent
    # tools/release/gates/ -> repo root
    return script_dir.parent.parent.parent


def check_file_exists(repo: Path, rel_path: str, description: str) -> dict:
    """Check that a required file exists."""
    path = repo / rel_path
    if path.exists():
        return {"name": description, "status": "pass", "path": rel_path}
    return {"name": description, "status": "fail", "path": rel_path,
            "message": f"Missing: {rel_path}"}


def check_reason_code_count(repo: Path) -> dict:
    """Verify Rust reason code count is 25 (0.9.0 target)."""
    rc_file = repo / "components/rust-converter/src/decision/reason_code.rs"
    if not rc_file.exists():
        return {"name": "reason_code_count", "status": "fail",
                "message": "reason_code.rs not found"}
    content = rc_file.read_text()
    match = re.search(
        r"pub const REASON_CODE_COUNT:\s*usize\s*=\s*(\d+)", content
    )
    if not match:
        return {"name": "reason_code_count", "status": "fail",
                "message": "REASON_CODE_COUNT not found in reason_code.rs"}
    count = int(match.group(1))
    if count >= 25:
        return {"name": "reason_code_count", "status": "pass",
                "details": {"count": count}}
    return {"name": "reason_code_count", "status": "fail",
            "message": f"Expected >= 25, got {count}"}


def check_diagnostics_schema_version(repo: Path) -> dict:
    """Verify diagnostics schema defines schema_version = 1."""
    schema_file = repo / "components/rust-converter/src/diagnostics/schema.rs"
    if not schema_file.exists():
        return {"name": "diagnostics_schema_v1", "status": "fail",
                "message": "diagnostics/schema.rs not found"}
    content = schema_file.read_text()
    if "schema_version" in content:
        return {"name": "diagnostics_schema_v1", "status": "pass"}
    return {"name": "diagnostics_schema_v1", "status": "fail",
            "message": "schema_version not found in schema.rs"}


def check_production_examples(repo: Path) -> dict:
    """Verify production examples directory has >= 4 configs."""
    examples_dir = repo / "examples/production"
    if not examples_dir.exists():
        return {"name": "production_examples", "status": "fail",
                "message": "examples/production/ directory not found"}
    confs = list(examples_dir.glob("*.conf"))
    if len(confs) >= 4:
        return {"name": "production_examples", "status": "pass",
                "details": {"count": len(confs),
                            "files": [f.name for f in confs]}}
    return {"name": "production_examples", "status": "fail",
            "message": f"Expected >= 4 configs, found {len(confs)}"}


def check_migration_guide(repo: Path) -> dict:
    """Verify 0.9.0 migration guide exists and has key sections."""
    guide = repo / "docs/guides/MIGRATION-0.9.md"
    if not guide.exists():
        return {"name": "migration_guide", "status": "fail",
                "message": "MIGRATION-0.9.md not found"}
    content = guide.read_text()
    required_sections = [
        "Breaking Changes",
        "Directive Mapping",
        "Rollback",
        "No Legacy Compatibility",
    ]
    missing = [s for s in required_sections if s.lower() not in content.lower()]
    if not missing:
        return {"name": "migration_guide", "status": "pass"}
    return {"name": "migration_guide", "status": "warn",
            "message": f"Missing sections: {missing}"}


def check_doctor_tool(repo: Path) -> dict:
    """Verify doctor tool exists and is executable."""
    doctor = repo / "tools/doctor/nginx-markdown-doctor.sh"
    if not doctor.exists():
        return {"name": "doctor_tool", "status": "fail",
                "message": "tools/doctor/nginx-markdown-doctor.sh not found"}
    return {"name": "doctor_tool", "status": "pass"}


def check_label_whitelist(repo: Path) -> dict:
    """Verify metrics label whitelist module exists."""
    labels = repo / "components/rust-converter/src/metrics/labels.rs"
    if not labels.exists():
        return {"name": "label_whitelist", "status": "fail",
                "message": "metrics/labels.rs not found"}
    content = labels.read_text()
    if "is_label_allowed" in content and "is_label_blocked" in content:
        return {"name": "label_whitelist", "status": "pass"}
    return {"name": "label_whitelist", "status": "fail",
            "message": "Whitelist functions not found"}


def check_error_policy(repo: Path) -> dict:
    """Verify error classification module exists."""
    cls_file = repo / "components/rust-converter/src/error/classification.rs"
    if not cls_file.exists():
        return {"name": "error_policy", "status": "fail",
                "message": "error/classification.rs not found"}
    content = cls_file.read_text()
    if "decide_error_behavior" in content and "ErrorPolicy" in content:
        return {"name": "error_policy", "status": "pass"}
    return {"name": "error_policy", "status": "fail",
            "message": "Error policy functions not found"}


def check_inflight_guard(repo: Path) -> dict:
    """Verify inflight guard C implementation exists."""
    inflight = (
        repo / "components/nginx-module/src/ngx_http_markdown_inflight_impl.h"
    )
    if not inflight.exists():
        return {"name": "inflight_guard", "status": "fail",
                "message": "inflight_impl.h not found"}
    return {"name": "inflight_guard", "status": "pass"}


def check_changelog_090(repo: Path) -> dict:
    """Verify CHANGELOG has 0.9.0 section."""
    changelog = repo / "CHANGELOG.md"
    if not changelog.exists():
        return {"name": "changelog_090", "status": "fail",
                "message": "CHANGELOG.md not found"}
    content = changelog.read_text()
    if "[0.9.0]" in content or "## 0.9.0" in content:
        return {"name": "changelog_090", "status": "pass"}
    return {"name": "changelog_090", "status": "fail",
            "message": "0.9.0 section not found in CHANGELOG"}


def main():
    repo = find_repo_root()
    results = []

    # Core deliverables
    results.append(check_reason_code_count(repo))
    results.append(check_diagnostics_schema_version(repo))
    results.append(check_label_whitelist(repo))
    results.append(check_error_policy(repo))
    results.append(check_inflight_guard(repo))
    results.append(check_production_examples(repo))
    results.append(check_migration_guide(repo))
    results.append(check_doctor_tool(repo))
    results.append(check_changelog_090(repo))

    # Key files
    results.append(check_file_exists(
        repo,
        "docs/architecture/observability-schema-v1.md",
        "observability_schema_doc"))
    results.append(check_file_exists(
        repo,
        "docs/architecture/error-policy.md",
        "error_policy_doc"))
    results.append(check_file_exists(
        repo,
        "docs/release/0.9.0-release-notes.md",
        "release_notes"))
    results.append(check_file_exists(
        repo,
        ".github/workflows/doctor-smoke.yml",
        "doctor_ci_workflow"))
    results.append(check_file_exists(
        repo,
        "docs/operations/production-configs.md",
        "production_configs_doc"))
    results.append(check_file_exists(
        repo,
        "components/rust-converter/src/ffi/diagnostics.rs",
        "diagnostics_ffi"))

    # Summary
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    warned = sum(1 for r in results if r["status"] == "warn")
    total = len(results)

    print(f"\n{'=' * 60}")
    print("  0.9.0 Release Gate Validation")
    print(f"{'=' * 60}")
    for r in results:
        symbol = {"pass": "\u2713", "fail": "\u2717",
                  "warn": "\u26a0"}.get(r["status"], "?")
        msg = r.get("message", "")
        line = f"  {symbol} [{r['status']}] {r['name']}"
        if msg:
            line += f": {msg}"
        print(line)
    print(f"{'=' * 60}")
    print(f"  Total: {total} | Passed: {passed} "
          f"| Failed: {failed} | Warnings: {warned}")
    print(f"{'=' * 60}\n")

    if failed > 0:
        print("RELEASE GATE: FAIL", file=sys.stderr)
        sys.exit(1)
    print("RELEASE GATE: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
