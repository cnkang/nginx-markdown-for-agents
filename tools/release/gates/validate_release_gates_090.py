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

# Bootstrap repo root so sibling modules resolve under bare `python3` invocation
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from tools.release.gates.check_stale_symbols import run_stale_symbol_check
    from tools.release.gates._directive_macros import expand_directive_macros
except ModuleNotFoundError:
    from check_stale_symbols import run_stale_symbol_check
    from _directive_macros import expand_directive_macros


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
    """Verify the 0.9.x reason-code floor and generated-boundary parity.

    The 0.9.0 baseline contains 26 codes.  The current 0.9.2 freeze adds
    the distinct ``encoding_header_invalid`` code, so a prior-version
    regression gate must not reject the newer release solely because its
    registry has grown.  It still verifies that Rust and the production C
    reason constants expose the same count and that the baseline floor
    remains present.
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
    if count < 26:
        return {"name": "reason_code_count", "status": "fail",
                "message": f"Expected at least 26, got {count}"}

    c_file = repo / "components/nginx-module/src/ngx_http_markdown_reason.c"
    if not c_file.exists():
        return {"name": "reason_code_count", "status": "fail",
                "message": "ngx_http_markdown_reason.c not found"}
    c_content = c_file.read_text(encoding="utf-8")
    c_count = len(re.findall(
        r"^#define\s+REASON_[A-Z0-9_]+\s+\d+",
        c_content,
        re.MULTILINE,
    ))
    if c_count != count:
        return {
            "name": "reason_code_count",
            "status": "fail",
            "message": f"Rust/C reason-code count mismatch: Rust={count}, C={c_count}",
        }

    return {"name": "reason_code_count", "status": "pass",
            "details": {"count": count}}


def check_diagnostics_schema_version(repo: Path) -> dict:
    """Verify the documented and emitted diagnostics schema agree.

    The 0.9.0 baseline introduced schema version 1.  The active 0.9.2
    release intentionally evolves that response to version 2, so this
    historical regression gate accepts either supported version but never
    allows the documentation and production renderer to drift apart.
    """
    gate_name = "diagnostics_schema_v2"
    supported_versions = {1, 2}
    schema_file = repo / "docs/architecture/observability-schema-v2.md"
    if not schema_file.exists():
        return {"name": gate_name, "status": "fail",
                "message": "observability-schema-v2.md not found"}
    documentation = schema_file.read_text(encoding="utf-8")
    documentation_match = re.search(
        r"^\s*(?:-\s*)?`schema_version`\s*:\s*"
        r"integer\s+constant\s*`([12])`\s*\.?\s*$",
        documentation,
        flags=re.MULTILINE,
    )
    if documentation_match is None:
        return {"name": gate_name, "status": "fail",
                "message": "supported diagnostics schema version not documented"}
    documented_version = int(documentation_match.group(1))

    renderer_file = (
        repo
        / "components/nginx-module/src/ngx_http_markdown_diagnostics.c"
    )
    if not renderer_file.exists():
        return {"name": gate_name, "status": "fail",
                "message": "production C renderer not found"}

    renderer = renderer_file.read_text(encoding="utf-8")
    renderer_without_comments = re.sub(
        r"/\*.*?\*/", "", renderer, flags=re.DOTALL
    )
    emission = re.compile(
        r'ngx_slprintf\s*\(\s*p\s*,\s*last\s*,\s*'
        r'"(?:\\.|[^"\\])*\\"schema_version\\"\s*:\s*([12])\s*[,}]'
    )
    emission_match = emission.search(renderer_without_comments)
    if emission_match is None:
        return {"name": gate_name, "status": "fail",
                "message": (
                    "production C renderer does not emit supported "
                    "schema_version 1 or 2"
                )}
    emitted_version = int(emission_match.group(1))
    if documented_version != emitted_version:
        return {"name": gate_name, "status": "fail",
                "message": (
                    "production C renderer emits schema_version "
                    f"{emitted_version}, documentation declares "
                    f"schema_version {documented_version}"
                )}
    if emitted_version not in supported_versions:
        return {"name": gate_name, "status": "fail",
                "message": f"unsupported diagnostics schema version {emitted_version}"}

    return {"name": gate_name, "status": "pass"}


def check_production_examples(repo: Path) -> dict:
    """Verify production examples directory has >= 4 configs."""
    examples_dir = repo / "examples/production"
    if not examples_dir.exists():
        return {"name": "production_examples", "status": "fail",
                "message": "examples/production/ directory not found"}
    confs = list(examples_dir.glob("*.conf"))
    if len(confs) < 4:
        return {"name": "production_examples", "status": "fail",
                "message": f"Expected >= 4 configs, found {len(confs)}"}

    duplicate_default_type = []
    for conf in confs:
        # Strip comments to avoid matching commented-out directives
        uncommented = "\n".join(
            line.split("#", 1)[0] for line in conf.read_text().splitlines()
        )
        for statement in uncommented.split(";"):
            tokens = statement.split()
            # NGINX implicitly includes text/html in gzip_types; redeclaring it
            # is a configuration error that produces a duplicate warning.
            if tokens and tokens[0] == "gzip_types" and "text/html" in tokens[1:]:
                duplicate_default_type.append(conf.name)
                break
    if duplicate_default_type:
        return {
            "name": "production_examples",
            "status": "fail",
            "message": (
                "gzip_types must not redeclare NGINX's default text/html type: "
                + ", ".join(sorted(duplicate_default_type))
            ),
        }

    return {"name": "production_examples", "status": "pass",
            "details": {"count": len(confs),
                        "files": [f.name for f in confs]}}


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
    has_status_return = (
        "return conf->error_status;" in content
        or re.search(
            r"return\s+ngx_http_markdown_effective_error_status\s*\(",
            content,
        ) is not None
    )
    if not has_status_return:
        return {"name": "inflight_guard", "status": "fail",
                "message": "inflight overload does not return error_status"}
    return {"name": "inflight_guard", "status": "pass"}


def _check_removed_directives(content: str, removed: list) -> list:
    """Verify removed directives are absent from the live command registry.

    The 0.9.2 reset deliberately removes these names from ``ngx_command_t``.
    NGINX then provides the standard unknown-directive error.  Older versions
    of this validator required reject-only stubs, which contradicted the
    frozen command table and also matched migration comments rather
    than the live registry.
    """
    missing = []
    registry_marker = "static ngx_command_t ngx_http_markdown_filter_commands[] = {"
    registry_start = content.find(registry_marker)
    registry_end = content.find("\n};", registry_start)
    if registry_start < 0 or registry_end < 0:
        return ["live ngx_command_t registry missing or unterminated"]
    registry = content[registry_start:registry_end]
    for name in removed:
        if f'ngx_string("{name}")' in registry:
            missing.append(f"{name}: still present in live command registry")
    return missing


def _read_directive_registry(repo: Path) -> str | None:
    """Read the live command registry with canonical names expanded."""
    directives = repo / (
        "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
    )
    names = repo / (
        "components/nginx-module/src/ngx_http_markdown_directive_names.h"
    )
    if not directives.exists() or not names.exists():
        return None
    return expand_directive_macros(
        directives.read_text(), names.read_text()
    )


def _check_migration_guide(migration: Path) -> list:
    """Verify the migration guide documents the memory-budget rename.

    Return a list of problem descriptions (empty if the guide exists and
    mentions markdown_memory_budget).
    """
    missing = []
    if migration.exists():
        migration_text = migration.read_text()
        if "markdown_memory_budget" not in migration_text:
            missing.append("markdown_memory_budget: migration guide missing")
    else:
        missing.append("MIGRATION-0.9.md missing")
    return missing


def check_config_v2_removed_directives(repo: Path) -> dict:
    """Verify removed Config V2 directives are not active commands."""
    directives = (
        repo /
        "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
    )
    migration = repo / "docs/guides/MIGRATION-0.9.md"
    if not directives.exists():
        return {"name": "config_v2_removed_directives", "status": "fail",
                "message": "config_directives_impl.h not found"}
    content = _read_directive_registry(repo)
    if content is None:
        return {"name": "config_v2_removed_directives", "status": "fail",
                "message": "directive command registry or names header missing"}
    removed = [
        "markdown_max_size",
        "markdown_memory_budget",
        "markdown_timeout",
        "markdown_streaming_budget",
        "markdown_on_error",
        "markdown_streaming_on_error",
        "markdown_on_wildcard",
        "markdown_etag",
        "markdown_etag_policy",
        "markdown_conditional_requests",
        "markdown_trust_forwarded_headers",
        "markdown_forwarded_headers",
        "markdown_large_body_threshold",
    ]
    missing = _check_removed_directives(content, removed)
    missing.extend(_check_migration_guide(migration))
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


def _strip_block_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove /* ... */ spans from *line*, carrying *in_comment* state."""
    parts: list[str] = []
    i = 0
    while i < len(line):
        if in_comment:
            end = line.find('*/', i)
            if end == -1:
                break
            i = end + 2
            in_comment = False
        else:
            start = line.find('/*', i)
            if start == -1:
                parts.append(line[i:])
                break
            parts.append(line[i:start])
            i = start + 2
            in_comment = True
    return ''.join(parts), in_comment


def iter_c_code_lines(block: str) -> list[str]:
    """Return non-comment, non-empty C source lines from a short snippet.

    Strips block comments (/* ... */) including inline and multi-line spans,
    then skips blank lines so that subsequent string searches operate on
    actual code, not comment text.  Used to verify that a code path calls a
    specific function rather than merely mentioning it in a comment.
    """
    code_lines: list[str] = []
    in_comment = False
    for line in block.split('\n'):
        cleaned, in_comment = _strip_block_comments(line, in_comment)
        cleaned = cleaned.strip()
        if cleaned:
            code_lines.append(cleaned)
    return code_lines


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
    # (bypass is a deliberate pass-through, not an error — applying error_policy
    # would incorrectly reject or fail-open on a valid bypass decision)
    bypass_idx = content.find("NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT")
    if bypass_idx < 0:
        return {"name": "conditional_bypass_no_error_policy", "status": "fail",
                "message": "BYPASS_RESULT handling not found"}
    # Look at the block around bypass handling (1500 chars for the full comment + code)
    block = content[bypass_idx:bypass_idx + 1500]
    # Check for actual function CALL (not comment references).
    # Filter out C comment lines so we only match real code, not doc comments.
    code_text = ' '.join(iter_c_code_lines(block))
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
    """Check profile inheritance only when the profile surface still exists.

    The 0.9.2 reset removes ``markdown_profile`` and its configuration fields.
    A newer release gate must not fail solely because that prior-version
    contract no longer exists.
    """
    merge_impl = (
        repo / "components/nginx-module/src/ngx_http_markdown_config_core_impl.h"
    )
    test_file = repo / "components/nginx-module/tests/unit/profile_test.c"
    directive_text = _read_directive_registry(repo)
    if directive_text is None:
        return {"name": "profile_explicit_inheritance", "status": "fail",
                "message": "directive command registry or names header missing"}
    if 'ngx_string("markdown_profile")' not in directive_text:
        return {"name": "profile_explicit_inheritance", "status": "pass",
                "message": "profile surface removed; no inheritance contract"}
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


def check_no_stale_symbols(repo: Path) -> dict:
    """Verify removed 0.8 symbols do not reappear in tracked 0.9 sources."""
    try:
        exit_code, stdout, stderr = run_stale_symbol_check(repo)
    except RuntimeError as e:
        return {"name": "no_stale_symbols", "status": "fail",
                "message": str(e)}

    diag = (stdout + "\n" + stderr).strip()
    return {
        "name": "no_stale_symbols",
        "status": "pass" if exit_code == 0 else "fail",
        "message": "\n".join(diag.splitlines()[-5:]) if exit_code != 0 else "",
    }


def main():
    repo = find_repo_root()
    results = []

    # Core deliverables
    results.append(check_no_stale_symbols(repo))
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
        "docs/architecture/observability-schema-v2.md",
        "observability_schema_doc"))
    results.append(check_file_exists(
        repo,
        "docs/architecture/error-policy.md",
        "error_policy_doc"))
    results.append(check_file_exists(
        repo,
        "docs/releases/0.9.0-release-notes.md",
        "release_notes"))
    results.append(check_file_exists(
        repo,
        ".github/workflows/doctor-smoke.yml",
        "doctor_ci_workflow"))
    results.append(check_file_exists(
        repo,
        "docs/operations/production-configs.md",
        "production_configs_doc"))
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
