#!/usr/bin/env python3
"""Release gate validator for 0.9.0.

Validates that all P0 0.9.0 deliverables are present in the repository.
Runs from a clean checkout — does not depend on .kiro/ or local state.

Exit codes:
  0 = all gates pass
  1 = at least one gate failed
"""

import re
import json
import subprocess
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
    """Verify Rust reason code count is 26 (0.9.0 frozen count).

    0.9.0 freezes at exactly 26 reason codes. After 1.0.0, only additive
    new codes are permitted. The gate uses == 26 to detect drift.
    """
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
    if count == 26:
        return {"name": "reason_code_count", "status": "pass",
                "details": {"count": count}}
    return {"name": "reason_code_count", "status": "fail",
            "message": f"Expected exactly 26, got {count}"}


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
    """Verify doctor tool exists and produces parseable JSON output."""
    doctor = repo / "tools/doctor/nginx-markdown-doctor.sh"
    if not doctor.exists():
        return {"name": "doctor_tool", "status": "fail",
                "message": "tools/doctor/nginx-markdown-doctor.sh not found"}
    try:
        completed = subprocess.run(
            ["bash", str(doctor), "--json", "--nginx-bin", ""],
            cwd=repo,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "doctor_tool", "status": "fail",
                "message": f"doctor smoke failed to run: {exc}"}
    if completed.returncode != 0:
        return {"name": "doctor_tool", "status": "fail",
                "message": "doctor smoke returned non-zero exit",
                "details": {"exit_code": completed.returncode,
                            "stderr": completed.stderr[-400:]}}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"name": "doctor_tool", "status": "fail",
                "message": f"doctor smoke emitted invalid JSON: {exc}"}
    if "checks" not in payload or "summary" not in payload:
        return {"name": "doctor_tool", "status": "fail",
                "message": "doctor JSON missing checks or summary"}
    return {"name": "doctor_tool", "status": "pass",
            "details": {"checks": len(payload["checks"])}}


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
    """Verify inflight guard C implementation exists and honors status."""
    inflight = (
        repo / "components/nginx-module/src/ngx_http_markdown_inflight_impl.h"
    )
    request_impl = (
        repo / "components/nginx-module/src/ngx_http_markdown_request_impl.h"
    )
    if not inflight.exists():
        return {"name": "inflight_guard", "status": "fail",
                "message": "inflight_impl.h not found"}
    if not request_impl.exists():
        return {"name": "inflight_guard", "status": "fail",
                "message": "request_impl.h not found"}
    content = request_impl.read_text()
    if "return conf->error_status;" not in content:
        return {"name": "inflight_guard", "status": "fail",
                "message": "inflight overload does not return error_status"}
    return {"name": "inflight_guard", "status": "pass"}


def check_config_v2_removed_directives(repo: Path) -> dict:
    """Verify removed Config V2 directives are reject-only stubs."""
    directives = (
        repo /
        "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
    )
    migration = repo / "docs/guides/MIGRATION-0.9.md"
    if not directives.exists():
        return {"name": "config_v2_removed_directives", "status": "fail",
                "message": "config_directives_impl.h not found"}
    content = directives.read_text()
    removed = [
        "markdown_max_size",
        "markdown_memory_budget",
        "markdown_timeout",
        "markdown_streaming_budget",
    ]
    missing = []
    for name in removed:
        idx = content.find(f'ngx_string("{name}")')
        if idx < 0:
            missing.append(f"{name}: directive missing")
            continue
        block = content[idx:idx + 700]
        if "ngx_http_markdown_reject_removed_directive" not in block:
            missing.append(f"{name}: not reject-only")
    if migration.exists():
        migration_text = migration.read_text()
        if "markdown_memory_budget" not in migration_text:
            missing.append("markdown_memory_budget: migration guide missing")
    else:
        missing.append("MIGRATION-0.9.md missing")
    if missing:
        return {"name": "config_v2_removed_directives", "status": "fail",
                "message": "; ".join(missing)}
    return {"name": "config_v2_removed_directives", "status": "pass"}


def check_conditional_runtime_path(repo: Path) -> dict:
    """Verify C conditional handling delegates to Rust decide_conditional."""
    conditional = (
        repo / "components/nginx-module/src/ngx_http_markdown_conditional.c"
    )
    if not conditional.exists():
        return {"name": "conditional_runtime_path", "status": "fail",
                "message": "conditional.c not found"}
    content = conditional.read_text()
    required = [
        "markdown_decide_conditional(&cond_input",
        "FFIConditionalInput",
        "cond_input.cache_validation",
        "cond_input.if_none_match",
        "cond_input.if_modified_since",
        "cond_input.has_range",
        "cond_input.last_modified",
    ]
    missing = [item for item in required if item not in content]
    if missing:
        return {"name": "conditional_runtime_path", "status": "fail",
                "message": f"missing runtime fields: {missing}"}
    # P0: Bypass outcome must be explicitly handled, not treated as Proceed.
    bypass_required = [
        "cond_decision.outcome == 2",
        "NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT",
    ]
    bypass_missing = [item for item in bypass_required if item not in content]
    if bypass_missing:
        return {"name": "conditional_runtime_path", "status": "fail",
                "message": f"Bypass outcome not handled: {bypass_missing}"}
    return {"name": "conditional_runtime_path", "status": "pass"}


def check_conditional_bypass_header_filter(repo: Path) -> dict:
    """Verify header filter checks Cache-Control: no-transform before conversion."""
    request_impl = (
        repo / "components/nginx-module/src/ngx_http_markdown_request_impl.h"
    )
    if not request_impl.exists():
        return {"name": "conditional_bypass_header_filter", "status": "fail",
                "message": "request_impl.h not found"}
    content = request_impl.read_text()
    if "ngx_http_markdown_has_no_transform" not in content:
        return {"name": "conditional_bypass_header_filter", "status": "fail",
                "message": "header filter does not call has_no_transform"}
    if "no-transform" not in content:
        return {"name": "conditional_bypass_header_filter", "status": "fail",
                "message": "header filter missing no-transform bypass logic"}
    if "ngx_http_markdown_reason_bypass_no_transform" not in content:
        return {"name": "conditional_bypass_header_filter", "status": "fail",
                "message": "header filter uses generic reason instead of bypass_no_transform"}
    return {"name": "conditional_bypass_header_filter", "status": "pass"}


def check_conditional_bypass_no_error_policy(repo: Path) -> dict:
    """Verify conditional bypass path does not go through error_policy."""
    conversion_impl = (
        repo / "components/nginx-module/src/ngx_http_markdown_conversion_impl.h"
    )
    if not conversion_impl.exists():
        return {"name": "conditional_bypass_no_error_policy", "status": "fail",
                "message": "conversion_impl.h not found"}
    content = conversion_impl.read_text()
    # Find the BYPASS_RESULT block and check it uses fail_open, not reject_or_fail_open
    bypass_idx = content.find("NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT")
    if bypass_idx < 0:
        return {"name": "conditional_bypass_no_error_policy", "status": "fail",
                "message": "BYPASS_RESULT handling not found"}
    # Look at the block around bypass handling (1500 chars for the full comment + code)
    block = content[bypass_idx:bypass_idx + 1500]
    # Check for actual function CALL (not comment references).
    # Filter out C comment lines: lines starting with *, /*, or ending with */
    code_lines = []
    in_comment = False
    for line in block.split('\n'):
        s = line.strip()
        if not s:
            continue
        if s.startswith('/*'):
            in_comment = True
            if s.endswith('*/'):
                in_comment = False
            continue
        if in_comment:
            if '*/' in s:
                in_comment = False
            continue
        if s.startswith('*'):
            continue
        code_lines.append(s)
    code_text = ' '.join(code_lines)
    if "reject_or_fail_open" in code_text:
        return {"name": "conditional_bypass_no_error_policy", "status": "fail",
                "message": "bypass path still calls reject_or_fail_open (error_policy)"}
    if "fail_open_buffered_response" not in code_text:
        return {"name": "conditional_bypass_no_error_policy", "status": "fail",
                "message": "bypass path does not call fail_open_buffered_response"}
    return {"name": "conditional_bypass_no_error_policy", "status": "pass"}


def check_conditional_bypass_tests(repo: Path) -> dict:
    """Verify unit tests cover Bypass outcome (Range + no-transform)."""
    test_file = (
        repo / "components/nginx-module/tests/unit/conditional_production_test.c"
    )
    if not test_file.exists():
        return {"name": "conditional_bypass_tests", "status": "fail",
                "message": "conditional_production_test.c not found"}
    content = test_file.read_text()
    required_tests = [
        "test_handle_bypass_range_request",
        "test_handle_bypass_no_transform",
        "test_has_no_transform",
        "NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT",
    ]
    missing = [t for t in required_tests if t not in content]
    if missing:
        return {"name": "conditional_bypass_tests", "status": "fail",
                "message": f"missing bypass tests: {missing}"}
    return {"name": "conditional_bypass_tests", "status": "pass"}


def check_last_modified_time_fallback(repo: Path) -> dict:
    """Verify IMS-only uses last_modified_time when no list header exists."""
    conditional = (
        repo / "components/nginx-module/src/ngx_http_markdown_conditional.c"
    )
    if not conditional.exists():
        return {"name": "last_modified_time_fallback", "status": "fail",
                "message": "conditional.c not found"}
    content = conditional.read_text()
    if "ngx_http_time" not in content:
        return {"name": "last_modified_time_fallback", "status": "fail",
                "message": "ngx_http_time not used for last_modified_time fallback"}
    if "last_modified_time" not in content:
        return {"name": "last_modified_time_fallback", "status": "fail",
                "message": "last_modified_time field not checked"}
    return {"name": "last_modified_time_fallback", "status": "pass"}


def check_profile_explicit_inheritance(repo: Path) -> dict:
    """Verify profile cache_validation_explicit is inherited across scopes."""
    merge_impl = (
        repo / "components/nginx-module/src/ngx_http_markdown_config_core_impl.h"
    )
    test_file = repo / "components/nginx-module/tests/unit/profile_test.c"
    if not merge_impl.exists() or not test_file.exists():
        return {"name": "profile_explicit_inheritance", "status": "fail",
                "message": "merge implementation or profile test missing"}
    merge_content = merge_impl.read_text()
    test_content = test_file.read_text()
    if ("prev->profile.cache_validation_explicit" not in merge_content or
            "test_cache_validation_explicit_inheritance" not in test_content):
        return {"name": "profile_explicit_inheritance", "status": "fail",
                "message": "cache_validation_explicit inheritance unguarded"}
    return {"name": "profile_explicit_inheritance", "status": "pass"}


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
    results.append(check_config_v2_removed_directives(repo))
    results.append(check_conditional_runtime_path(repo))
    results.append(check_conditional_bypass_header_filter(repo))
    results.append(check_conditional_bypass_no_error_policy(repo))
    results.append(check_conditional_bypass_tests(repo))
    results.append(check_last_modified_time_fallback(repo))
    results.append(check_profile_explicit_inheritance(repo))
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
