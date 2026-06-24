#!/usr/bin/env python3
"""Validate repo-owned harness truth surfaces and optional local adapters."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from tools.harness.constants import (
        FAIL,
        PASS,
        SKIP_NOT_PRESENT,
        WARN_NEEDS_AUTHOR_REVIEW,
    )
except ModuleNotFoundError:
    from constants import (  # type: ignore[no-redef]  # noqa: F401
        FAIL,
        PASS,
        SKIP_NOT_PRESENT,
        WARN_NEEDS_AUTHOR_REVIEW,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
README_FILENAME = "README.md"
DOCS_HARNESS_README = f"docs/harness/{README_FILENAME}"
RISK_PACKS_README = f"risk-packs/{README_FILENAME}"
FUZZ_README_REL = f"fuzz/{README_FILENAME}"
COMPONENT_FUZZ_README_REL = f"components/rust-converter/fuzz/{README_FILENAME}"
GITHUB_WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CFLITE_PR_WORKFLOW = "cflite_pr.yml"
CFLITE_BATCH_WORKFLOW = "cflite_batch.yml"
CFLITE_CRON_WORKFLOW = "cflite_cron.yml"
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.json"
E2E_HARNESS_DIR = REPO_ROOT / "tools" / "e2e-harness"
E2E_HARNESS_CARGO = E2E_HARNESS_DIR / "Cargo.toml"
README_PATH = REPO_ROOT / "docs" / "harness" / README_FILENAME
CORE_PATH = REPO_ROOT / "docs" / "harness" / "core.md"
SUMMARY_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
RECENT_ANALYSIS_REPORT_GLOB = "docs/project/recent-git-harness-steering-analysis-*.md"
REMEDIATION_STATUSES = {
    "fixed",
    "intentionally deferred",
    "not applicable after review",
}
E2E_PYTHON_DIR = REPO_ROOT / "components" / "nginx-module" / "tests" / "e2e"
REMOVED_PYTHON_E2E_FILES = (
    "test_streaming_e2e.py",
    "test_streaming_failure_cache_e2e.py",
)
MIGRATED_SCENARIO_WRAPPERS = {
    "tools/e2e/verify_accept_negotiation_e2e.sh": "accept-negotiation",
    "tools/e2e/verify_metrics_endpoint_e2e.sh": "metrics-endpoint",
    "tools/e2e/verify_conditional_requests_e2e.sh": "conditional-requests",
    "tools/e2e/verify_auth_cache_e2e.sh": "auth-cache",
    "tools/e2e/verify_status_codes_e2e.sh": "status-codes",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for *path*, falling back to the full path.

    Args:
        path: Absolute or relative path to format.

    Returns:
        Path string relative to REPO_ROOT if possible, otherwise the full path.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_manifest(path: Path | None = None) -> dict:
    """Load and parse the routing manifest JSON file.

    Args:
        path: Path to the manifest file. Defaults to MANIFEST_PATH.

    Returns:
        Parsed manifest as a dictionary.

    Raises:
        ValueError: If the file cannot be read or contains invalid JSON.
    """
    path = path or MANIFEST_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load harness manifest from {path}: {exc}") from exc


def _required_text(path: Path, needles: list[str]) -> list[str]:
    """Return the subset of *needles* not found in the text at *path*.

    Args:
        path: File to search.
        needles: Exact strings that must appear in the file.

    Returns:
        List of needles absent from the file, or an unreadability marker
        if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{_display_path(path)} unreadable"]
    return [needle for needle in needles if needle not in text]


def _required_patterns(path: Path, patterns: dict[str, str]) -> list[str]:
    """Return the labels whose regex patterns are not matched in the file at *path*.

    Args:
        path: File to search.
        patterns: Mapping of label to regex pattern; each pattern is
            searched with the MULTILINE flag.

    Returns:
        List of labels whose patterns were not found, or an unreadability
        marker if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{_display_path(path)} unreadable"]
    missing: list[str] = []
    missing.extend(
        label
        for label, pattern in patterns.items()
        if not re.search(pattern, text, flags=re.MULTILINE)
    )
    return missing


def _result(name: str, status: str, detail: str) -> CheckResult:
    """Build a CheckResult with the given fields.

    Args:
        name: Check identifier.
        status: One of PASS, FAIL, SKIP_NOT_PRESENT, or WARN_NEEDS_AUTHOR_REVIEW.
        detail: Human-readable explanation of the outcome.

    Returns:
        A frozen CheckResult dataclass instance.
    """
    return CheckResult(name=name, status=status, detail=detail)


def _check_manifest_structure(manifest: dict) -> CheckResult:
    """Validate that the manifest contains all required top-level and nested keys.

    Checks for required top-level keys, truth-surface sub-keys, the
    canonical four-state status semantics, and spec-resolver keys.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if the schema is complete, or FAIL with
        details of the first missing key set.
    """
    required_keys = {
        "version",
        "truth_surfaces",
        "status_semantics",
        "spec_resolver",
        "verification_families",
        "risk_packs",
        "task_entrypoints",
    }
    if missing := sorted(required_keys - set(manifest)):
        return _result(
            "manifest-structure",
            FAIL,
            f"missing top-level keys: {', '.join(missing)}",
        )

    truth_surfaces = manifest["truth_surfaces"]
    required_truth_surface_keys = {
        "contract",
        "harness",
        "canonical_docs",
        "optional_adapters",
    }
    if missing_truth_surface_keys := sorted(
        required_truth_surface_keys - set(truth_surfaces)
    ):
        return _result(
            "manifest-structure",
            FAIL,
            "missing truth surface keys: "
            + ", ".join(missing_truth_surface_keys),
        )

    expected_statuses = {
        PASS,
        FAIL,
        SKIP_NOT_PRESENT,
        WARN_NEEDS_AUTHOR_REVIEW,
    }
    actual_statuses = manifest["status_semantics"]
    if (
        not isinstance(actual_statuses, list)
        or len(actual_statuses) != len(expected_statuses)
        or set(actual_statuses) != expected_statuses
    ):
        return _result(
            "manifest-status-semantics",
            FAIL,
            "status semantics do not match the canonical four-state contract",
        )

    spec_resolver = manifest["spec_resolver"]
    required_resolver_keys = {
        "priority",
        "pointer_candidates",
        "multiple_spec_policy",
        "conflict_policy",
    }
    missing_resolver = sorted(required_resolver_keys - set(spec_resolver))
    if missing_resolver:
        return _result(
            "manifest-spec-resolver",
            FAIL,
            f"missing spec resolver keys: {', '.join(missing_resolver)}",
        )

    return _result("manifest-structure", PASS, "manifest schema looks complete")


def _check_truth_surfaces(manifest: dict) -> CheckResult:
    """Verify that all repo-owned truth-surface files exist on disk.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if every contract, harness, and
        canonical_docs file exists, or FAIL listing missing paths.
    """
    missing: list[str] = []
    for category in ("contract", "harness", "canonical_docs"):
        missing.extend(
            rel
            for rel in manifest["truth_surfaces"][category]
            if not (REPO_ROOT / rel).exists()
        )
    if missing:
        return _result(
            "truth-surfaces",
            FAIL,
            f"missing truth surface files: {', '.join(missing)}",
        )
    return _result("truth-surfaces", PASS, "repo-owned truth surfaces exist")


def _check_risk_pack_docs(manifest: dict) -> CheckResult:
    """Verify that every risk pack has its doc file and references known verification families.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if all risk-pack docs exist and their
        verification families are defined, or FAIL with details.
    """
    missing_docs: list[str] = []
    missing_families: list[str] = []
    families = set(manifest["verification_families"])
    for pack in manifest["risk_packs"]:
        doc_path = REPO_ROOT / pack["doc"]
        if not doc_path.exists():
            missing_docs.append(pack["doc"])
        missing_families.extend(
            f"{pack['id']}->{family}"
            for family in pack["verification_families"]
            if family not in families
        )
    if missing_docs or missing_families:
        parts = []
        if missing_docs:
            parts.append(f"missing docs: {', '.join(missing_docs)}")
        if missing_families:
            parts.append(f"unknown verification families: {', '.join(missing_families)}")
        return _result("risk-pack-contract", FAIL, "; ".join(parts))
    return _result("risk-pack-contract", PASS, "risk packs and verification families line up")


def _check_harness_docs(manifest: dict) -> CheckResult:
    """Verify that harness documentation files contain required links, phrases, and patterns.

    Checks the harness entrypoint for navigation links, routing-manifest.md for
    risk-pack identifiers, and core.md for status labels and key phrases.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if all required references are present,
        or FAIL listing the missing items.
    """
    missing: list[str] = []
    missing.extend(_required_patterns(
        README_PATH,
        {
            "core.md link": r"\[[^\]]+\]\(core\.md\)",
            "routing-manifest.json link": (
                r"\[[^\]]+\]\(routing-manifest\.json\)"
            ),
            "routing-manifest.md link": r"\[[^\]]+\]\(routing-manifest\.md\)",
            "risk-pack index link": (
                rf"\[[^\]]+\]\({re.escape(RISK_PACKS_README)}\)"
            ),
        },
    ))
    missing.extend(
        _required_patterns(
            SUMMARY_PATH,
            {
                pack_id: rf"\b{re.escape(pack_id)}\b"
                for pack_id in [pack["id"] for pack in manifest["risk_packs"]]
            },
        )
    )
    missing.extend(
        _required_patterns(
            CORE_PATH,
            {
                PASS: rf"`{re.escape(PASS)}`",
                FAIL: rf"`{re.escape(FAIL)}`",
                SKIP_NOT_PRESENT: rf"`{re.escape(SKIP_NOT_PRESENT)}`",
                WARN_NEEDS_AUTHOR_REVIEW: (
                    rf"`{re.escape(WARN_NEEDS_AUTHOR_REVIEW)}`"
                ),
                "outside voice": r"\boutside voice\b",
                "state carrier": r"\bstate carrier\b",
                "stop and explain the mismatch": (
                    r"\bstop and explain the mismatch\b"
                ),
                "tools/harness/resolve_spec.py": (
                    r"python3\s+tools/harness/resolve_spec\.py"
                ),
            },
        )
    )
    if missing:
        unique_missing = ", ".join(sorted(set(missing)))
        return _result(
            "harness-docs",
            FAIL,
            f"harness docs are missing required references or phrases: {unique_missing}",
        )
    return _result("harness-docs", PASS, "README, core, and summary expose the manifest contract")


def _check_agents_map() -> CheckResult:
    """Verify that AGENTS.md references the harness entrypoints and Codex-first semantics.

    Returns:
        CheckResult with PASS if all required references are present,
        or FAIL listing the missing items.
    """
    missing = _required_patterns(
        AGENTS_PATH,
        {
            DOCS_HARNESS_README: rf"`{re.escape(DOCS_HARNESS_README)}`",
            "docs/harness/core.md": r"`docs/harness/core\.md`",
            "docs/harness/routing-manifest.json": (
                r"`docs/harness/routing-manifest\.json`"
            ),
            "Codex-first": r"\bCodex-first\b",
        },
    )
    if missing:
        return _result(
            "agents-map",
            FAIL,
            f"AGENTS.md is missing harness map references: {', '.join(missing)}",
        )
    return _result("agents-map", PASS, "AGENTS.md points at the harness entrypoints")


def _check_e2e_harness_contract() -> CheckResult:
    """Validate the Rust E2E harness presence and binary contract."""
    missing: list[str] = []
    if not E2E_HARNESS_DIR.exists():
        missing.append(_display_path(E2E_HARNESS_DIR))
    if not E2E_HARNESS_CARGO.exists():
        missing.append(_display_path(E2E_HARNESS_CARGO))
    if missing:
        return _result(
            "e2e-harness-contract",
            FAIL,
            f"missing required harness paths: {', '.join(missing)}",
        )

    cargo_text = E2E_HARNESS_CARGO.read_text(encoding="utf-8")
    has_bin_decl = bool(
        re.search(
            r"\[\[bin\]\]\s*name\s*=\s*\"e2e-harness\"",
            cargo_text,
            flags=re.MULTILINE | re.DOTALL,
        )
    )
    if not has_bin_decl:
        return _result(
            "e2e-harness-contract",
            FAIL,
            "Cargo.toml missing [[bin]] name = \"e2e-harness\" declaration",
        )

    main_rs = E2E_HARNESS_DIR / "src" / "main.rs"
    if not main_rs.exists():
        return _result(
            "e2e-harness-contract",
            FAIL,
            f"missing harness entrypoint: {_display_path(main_rs)}",
        )

    return _result(
        "e2e-harness-contract",
        PASS,
        "Rust e2e-harness directory and binary contract are present",
    )


def _wrapper_invokes_expected_scenario(text: str, scenario_name: str) -> bool:
    """Return True when a migrated shell wrapper calls the expected scenario."""
    literal_call_pattern = rf"\bscenario\s+{re.escape(scenario_name)}\b"
    scenario_decl_pattern = rf"SCENARIO_NAME=\"{re.escape(scenario_name)}\""
    has_literal_call = re.search(literal_call_pattern, text) is not None
    has_variable_call = (
        "args=(scenario \"${SCENARIO_NAME}\")" in text
        and re.search(scenario_decl_pattern, text) is not None
    )
    return has_literal_call or has_variable_call


def _wrapper_contains_stale_runtime_logic(text: str) -> bool:
    """Return True when wrapper text still contains migrated assertion/runtime logic."""
    forbidden_tokens = (
        "markdown_build_with_nginx",
        "markdown_prepare_runtime_reuse",
        "curl -sS",
        "curl -fsS",
        "curl -X",
    )
    return any(token in text for token in forbidden_tokens)


def _collect_migrated_wrapper_findings() -> tuple[list[str], list[str]]:
    """Collect missing wrappers and stale wrapper logic violations."""
    missing_wrappers: list[str] = []
    stale_shell_logic: list[str] = []
    for rel_path, scenario_name in MIGRATED_SCENARIO_WRAPPERS.items():
        script_path = REPO_ROOT / rel_path
        if not script_path.exists():
            missing_wrappers.append(rel_path)
            continue
        text = script_path.read_text(encoding="utf-8")
        if not _wrapper_invokes_expected_scenario(text, scenario_name):
            stale_shell_logic.append(f"{rel_path}:missing scenario wrapper call")
        if _wrapper_contains_stale_runtime_logic(text):
            stale_shell_logic.append(f"{rel_path}:contains scenario assertion/runtime logic")
    return missing_wrappers, stale_shell_logic


def _collect_removed_python_e2e_paths() -> list[str]:
    """Return removed Python E2E file paths that still exist on disk."""
    return [
        str(E2E_PYTHON_DIR / filename)
        for filename in REMOVED_PYTHON_E2E_FILES
        if (E2E_PYTHON_DIR / filename).exists()
    ]


def _collect_stale_execution_surface_refs() -> list[str]:
    """Return execution-surface references that still mention removed E2E files."""
    refs: list[str] = []
    execution_surfaces = (
        REPO_ROOT / "Makefile",
        REPO_ROOT / "tools" / "e2e" / "run_e2e_suite.sh",
        GITHUB_WORKFLOWS_DIR / "ci.yml",
        REPO_ROOT / "docs" / "testing" / "E2E_TESTS.md",
    )
    for surface in execution_surfaces:
        if not surface.is_file():
            continue
        text = surface.read_text(encoding="utf-8")
        refs.extend(
            f"{_display_path(surface)}::{filename}"
            for filename in REMOVED_PYTHON_E2E_FILES
            if filename in text
        )
    return refs


def _check_e2e_migration_policy() -> CheckResult:
    """Validate Rust-first E2E migration policy for Python and shell paths."""
    missing_wrappers, stale_shell_logic = _collect_migrated_wrapper_findings()
    removed_python_present = _collect_removed_python_e2e_paths()
    stale_execution_refs = _collect_stale_execution_surface_refs()

    if missing_wrappers or stale_shell_logic or removed_python_present or stale_execution_refs:
        return _format_e2e_migration_failure(
            missing_wrappers,
            stale_shell_logic,
            removed_python_present,
            stale_execution_refs,
        )
    return _result(
        "e2e-migration-policy",
        PASS,
        "migrated shell paths are thin wrappers and removed Python E2E files are absent",
    )


def _format_e2e_migration_failure(
    missing_wrappers,
    stale_shell_logic,
    removed_python_present,
    stale_execution_refs,
) -> CheckResult:
    details: list[str] = []
    if missing_wrappers:
        details.append(f"missing migrated wrappers: {', '.join(missing_wrappers)}")
    if stale_shell_logic:
        details.append(f"non-wrapper migrated shell logic: {', '.join(stale_shell_logic)}")
    if removed_python_present:
        display = ", ".join(_display_path(Path(p)) for p in removed_python_present)
        details.append(f"removed Python E2E files still present: {display}")
    if stale_execution_refs:
        details.append(f"stale execution-surface refs: {', '.join(stale_execution_refs)}")
    return _result("e2e-migration-policy", FAIL, "; ".join(details))


def check_batch_prune_pairing() -> CheckResult:
    """Verify batch → prune workflow pairing (FUZZ-005).

    If the batch fuzzing workflow exists, the corresponding corpus pruning
    workflow must also exist.  If batch is absent, this check passes
    unconditionally (no requirement for prune without batch).

    Returns:
        CheckResult with PASS if pairing is satisfied or batch is absent,
        or FAIL if batch exists without a corresponding prune workflow.
    """
    batch_path = GITHUB_WORKFLOWS_DIR / CFLITE_BATCH_WORKFLOW
    prune_path = GITHUB_WORKFLOWS_DIR / CFLITE_CRON_WORKFLOW

    if not batch_path.exists():
        return _result(
            "batch-prune-pairing",
            PASS,
            "batch workflow absent; pairing check not applicable",
        )

    if not prune_path.exists():
        return _result(
            "batch-prune-pairing",
            FAIL,
            "FUZZ-005: batch workflow exists but prune workflow "
            f"({_display_path(prune_path)}) is missing",
        )

    return _result(
        "batch-prune-pairing",
        PASS,
        "batch and prune workflows both present (FUZZ-005 satisfied)",
    )


def check_cfl_workflows() -> CheckResult:
    """Verify ClusterFuzzLite CI workflow files exist and are correctly configured.

    Checks:
    - ClusterFuzzLite PR, batch, and cron workflows all exist
    - PR workflow uses address sanitizer
    - PR workflow has path filters under pull_request trigger
    - Batch workflow has corpus storage-repo configuration

    Returns:
        CheckResult with PASS if all checks pass, or FAIL listing issues.
    """
    required_workflows = [
        str((GITHUB_WORKFLOWS_DIR / CFLITE_PR_WORKFLOW).relative_to(REPO_ROOT)),
        str((GITHUB_WORKFLOWS_DIR / CFLITE_BATCH_WORKFLOW).relative_to(REPO_ROOT)),
        str((GITHUB_WORKFLOWS_DIR / CFLITE_CRON_WORKFLOW).relative_to(REPO_ROOT)),
    ]

    missing: list[str] = []
    missing.extend(
        rel for rel in required_workflows if not (REPO_ROOT / rel).exists()
    )
    if missing:
        return _result(
            "cfl-workflows",
            FAIL,
            f"missing ClusterFuzzLite workflow files: {', '.join(missing)}",
        )

    issues: list[str] = []

    # Verify PR workflow uses address sanitizer
    pr_workflow = GITHUB_WORKFLOWS_DIR / CFLITE_PR_WORKFLOW
    pr_missing = _required_patterns(
        pr_workflow,
        {"sanitizer: address": r"sanitizer:\s*address"},
    )
    if pr_missing:
        issues.append(f"{CFLITE_PR_WORKFLOW} missing address sanitizer configuration")

    # Verify PR workflow has path filters under pull_request trigger
    pr_path_missing = _required_patterns(
        pr_workflow,
        {"pull_request paths filter": r"pull_request:\s*\n\s+paths:"},
    )
    if pr_path_missing:
        issues.append(f"{CFLITE_PR_WORKFLOW} missing path filter for pull_request trigger")

    # Verify batch workflow has corpus storage-repo configuration
    batch_workflow = GITHUB_WORKFLOWS_DIR / CFLITE_BATCH_WORKFLOW
    batch_missing = _required_patterns(
        batch_workflow,
        {"storage-repo": r"storage-repo:"},
    )
    if batch_missing:
        issues.append(f"{CFLITE_BATCH_WORKFLOW} missing storage-repo configuration")

    if issues:
        return _result("cfl-workflows", FAIL, "; ".join(issues))

    return _result(
        "cfl-workflows",
        PASS,
        "ClusterFuzzLite workflows present and correctly configured",
    )


def _check_optional_kiro(manifest: dict, full: bool) -> CheckResult:
    """Check optional .kiro/steering adapters for drift against harness truth surfaces.

    If no adapters are present, returns SKIP_NOT_PRESENT.  If adapters
    exist but lack required links, returns WARN_NEEDS_AUTHOR_REVIEW in
    quick mode or FAIL in full mode.

    Args:
        manifest: Parsed routing manifest dictionary.
        full: If True, treat missing links as a blocking failure.

    Returns:
        CheckResult indicating whether optional adapters are in sync,
        absent, or need author review.
    """
    adapters = manifest.get("truth_surfaces", {}).get("optional_adapters", [])
    present = [REPO_ROOT / rel for rel in adapters if (REPO_ROOT / rel).exists()]
    if not present:
        return _result(
            "kiro-adapters",
            SKIP_NOT_PRESENT,
            ".kiro/steering is absent, skipping optional adapter checks",
        )

    missing_links: list[str] = []
    for path in present:
        missing_links.extend(
            f"{path.relative_to(REPO_ROOT)}::{needle}"
            for needle in _required_text(
                path,
                [
                    DOCS_HARNESS_README,
                    "docs/harness/core.md",
                ],
            )
        )
    if missing_links:
        status = FAIL if full else WARN_NEEDS_AUTHOR_REVIEW
        return _result(
            "kiro-adapters",
            status,
            f"local adapter docs need refresh: {', '.join(missing_links)}",
        )
    return _result("kiro-adapters", PASS, "optional local Kiro adapters point at harness truth")


def _check_recent_analysis_reports() -> CheckResult:
    """Validate recent-change analysis reports when they are present.

    These reports are optional project evidence, but once one exists it must
    carry enough structure to make findings traceable through remediation and
    verification.  This keeps post-hoc harness updates from becoming prose-only
    recommendations with no closeout state.

    Returns:
        CheckResult indicating skip, pass, or missing report evidence.
    """
    reports = sorted(REPO_ROOT.glob(RECENT_ANALYSIS_REPORT_GLOB))
    if not reports:
        return _result(
            "recent-analysis-report",
            SKIP_NOT_PRESENT,
            "no recent Git harness/steering analysis reports found",
        )

    missing: list[str] = []
    for report in reports:
        missing.extend(_missing_recent_report_evidence(report))

    if missing:
        return _result(
            "recent-analysis-report",
            FAIL,
            "analysis reports are missing traceable closeout evidence: "
            + ", ".join(missing),
        )
    return _result(
        "recent-analysis-report",
        PASS,
        "recent analysis reports include findings, remediation, and verification",
    )


def _missing_recent_report_evidence(report: Path) -> list[str]:
    """Return closeout evidence gaps for one recent-analysis report."""
    try:
        text = report.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{_display_path(report)}::unreadable ({exc})"]

    missing = _missing_recent_report_sections(report, text)
    finding_ids = sorted(set(re.findall(r"\|\s*(P[0-3]-\d{3})\s*\|", text)))
    if not finding_ids:
        missing.append(f"{_display_path(report)}::finding ids")
        return missing

    remediation_start = text.find("## Remediation Results")
    remediation_text = text[remediation_start:] if remediation_start >= 0 else ""
    for finding_id in finding_ids:
        missing.extend(_missing_recent_finding_closeout(report, remediation_text, finding_id))
    return missing


def _missing_recent_report_sections(report: Path, text: str) -> list[str]:
    """Return required section headings absent from a report."""
    required = (
        "## Phase 1 Analysis",
        "## Findings",
        "## Remediation Results",
        "## Verification",
    )
    return [f"{_display_path(report)}::{section}" for section in required if section not in text]


def _missing_recent_finding_closeout(
    report: Path,
    remediation_text: str,
    finding_id: str,
) -> list[str]:
    """Return remediation row/final-status gaps for one finding."""
    row_match = re.search(
        rf"\|\s*{re.escape(finding_id)}\s*\|([^\n]+)\|",
        remediation_text,
        re.IGNORECASE,
    )
    if not row_match:
        return [f"{_display_path(report)}::{finding_id} remediation row"]

    row = row_match[0].lower()
    if all(status not in row for status in REMEDIATION_STATUSES):
        return [f"{_display_path(report)}::{finding_id} final status"]
    return []


def check_clusterfuzzlite_build_config() -> CheckResult:
    """Verify .clusterfuzzlite/ directory completeness and content correctness.

    Checks that the three required ClusterFuzzLite build configuration files
    exist (project.yaml, Dockerfile, build.sh), that project.yaml declares
    language as rust with address sanitizer, and that build.sh has executable
    permission.

    Returns:
        CheckResult with PASS if all checks pass, or FAIL with details of
        the first failing condition.
    """
    required_files = [
        ".clusterfuzzlite/project.yaml",
        ".clusterfuzzlite/Dockerfile",
        ".clusterfuzzlite/build.sh",
    ]
    if missing := [
        rel for rel in required_files if not (REPO_ROOT / rel).exists()
    ]:
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            f"missing ClusterFuzzLite build files: {', '.join(missing)}",
        )

    # Validate project.yaml content
    project_yaml_path = REPO_ROOT / ".clusterfuzzlite" / "project.yaml"
    try:
        project_yaml_text = project_yaml_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            f".clusterfuzzlite/project.yaml unreadable: {exc}",
        )

    content_issues: list[str] = []
    if "language: rust" not in project_yaml_text:
        content_issues.append("project.yaml missing 'language: rust'")
    if "address" not in project_yaml_text:
        content_issues.append("project.yaml missing address sanitizer")
    if content_issues:
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            "; ".join(content_issues),
        )

    # Validate build.sh has executable permission
    build_sh_path = REPO_ROOT / ".clusterfuzzlite" / "build.sh"
    if not os.access(build_sh_path, os.X_OK):
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            ".clusterfuzzlite/build.sh is not executable",
        )

    return _result(
        "clusterfuzzlite-build-config",
        PASS,
        "ClusterFuzzLite build config is complete and valid",
    )


def check_fuzz_target_determinism() -> CheckResult:
    """Verify fuzz targets do not use non-deterministic APIs (FUZZ-003).

    Scans all fuzz target source files for forbidden API patterns that would
    violate the deterministic execution requirement: network I/O, filesystem
    writes, system time for logic branches, external random sources, environment
    variable reads, and process/thread creation.

    Returns:
        CheckResult with PASS if no forbidden patterns are found, or FAIL
        listing the violations.
    """
    fuzz_targets_dir = (
        REPO_ROOT / "components" / "rust-converter" / "fuzz" / "fuzz_targets"
    )
    if not fuzz_targets_dir.exists():
        return _result(
            "fuzz-target-determinism",
            FAIL,
            "FUZZ-003: fuzz targets directory not found",
        )

    # Forbidden API patterns that indicate non-deterministic behavior.
    # Each tuple is (pattern_regex, description).
    forbidden_patterns: list[tuple[str, str]] = [
        (r"\bstd::net\b", "network I/O (std::net)"),
        (r"\bTcpStream\b", "network I/O (TcpStream)"),
        (r"\bUdpSocket\b", "network I/O (UdpSocket)"),
        (r"\bstd::fs::(?:write|create_dir|remove)", "filesystem write"),
        (r"\bFile::create\b", "filesystem write (File::create)"),
        (r"\bSystemTime::now\b", "system time"),
        (r"\bInstant::now\b", "system time (Instant::now)"),
        (r"\brand::thread_rng\b", "external random (thread_rng)"),
        (r"\bOsRng\b", "external random (OsRng)"),
        (r"\bstd::env::var\b", "environment variable read"),
        (r"\bstd::process::Command\b", "process creation"),
        (r"\bstd::thread::spawn\b", "thread creation"),
    ]

    violations: list[str] = []
    for target_file in sorted(fuzz_targets_dir.glob("*.rs")):
        try:
            text = target_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(f"{target_file.name}:unreadable")
            continue

        violations.extend(
            f"{target_file.name}:{desc}"
            for pattern, desc in forbidden_patterns
            if re.search(pattern, text)
        )
    if violations:
        return _result(
            "fuzz-target-determinism",
            FAIL,
            f"FUZZ-003 violations: {'; '.join(violations)}",
        )

    return _result(
        "fuzz-target-determinism",
        PASS,
        "fuzz targets are free of non-deterministic API usage (FUZZ-003 satisfied)",
    )


def check_fuzz_gitignore() -> CheckResult:
    """Verify that .gitignore excludes fuzz artifact and corpus paths.

    Checks that the root .gitignore contains entries for fuzz artifacts
    and corpus directories to prevent generated fuzzing outputs from
    being accidentally committed.

    Returns:
        CheckResult with PASS if exclusion entries are found, or
        WARN_NEEDS_AUTHOR_REVIEW if expected patterns are missing.
    """
    gitignore_path = REPO_ROOT / ".gitignore"
    if not gitignore_path.exists():
        return _result(
            "fuzz-gitignore",
            WARN_NEEDS_AUTHOR_REVIEW,
            ".gitignore not found; cannot verify fuzz exclusion entries",
        )

    try:
        text = gitignore_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _result(
            "fuzz-gitignore",
            WARN_NEEDS_AUTHOR_REVIEW,
            ".gitignore unreadable",
        )

    # Check for fuzz artifact/corpus exclusion patterns
    fuzz_patterns = [
        r"fuzz/artifacts",
        r"fuzz/corpus",
    ]
    if any(pat in text for pat in fuzz_patterns):
        return _result(
            "fuzz-gitignore",
            PASS,
            "fuzz artifact/corpus paths excluded in .gitignore",
        )
    else:
        return _result(
            "fuzz-gitignore",
            WARN_NEEDS_AUTHOR_REVIEW,
            ".gitignore missing fuzz artifact/corpus exclusion entries",
        )


def check_fuzz_guide() -> CheckResult:
    """Verify that a fuzz README guide exists.

    Checks for the fuzz guide at both the top-level and component-level paths.
    At least one must exist.

    Returns:
        CheckResult with PASS if at least one fuzz guide exists, or
        FAIL if neither location has a README.
    """
    candidates = [
        REPO_ROOT / FUZZ_README_REL,
        REPO_ROOT / COMPONENT_FUZZ_README_REL,
    ]
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return _result(
            "fuzz-guide",
            FAIL,
            f"{FUZZ_README_REL} not found (checked both fuzz guide locations)",
        )

    locations = ", ".join(_display_path(p) for p in existing)
    return _result(
        "fuzz-guide",
        PASS,
        f"fuzz guide present: {locations}",
    )


def collect_results(full: bool = False) -> list[CheckResult]:
    """Run all harness-sync checks and return their results.

    Loads the manifest first; if that fails or the manifest structure is
    invalid, returns early with a single failure result.  Otherwise
    returns results for manifest structure, truth surfaces, risk-pack
    docs, harness docs, AGENTS.md map, and optional Kiro adapters.

    Args:
        full: If True, treat optional adapter drift as a blocking failure.

    Returns:
        List of CheckResult instances, one per check.
    """
    try:
        manifest = _load_manifest()
    except ValueError as exc:
        return [_result("manifest-load", FAIL, str(exc))]

    manifest_structure = _check_manifest_structure(manifest)
    if manifest_structure.status == FAIL:
        return [manifest_structure]

    return [
        manifest_structure,
        _check_truth_surfaces(manifest),
        _check_risk_pack_docs(manifest),
        _check_harness_docs(manifest),
        _check_agents_map(),
        _check_e2e_harness_contract(),
        _check_e2e_migration_policy(),
        _check_recent_analysis_reports(),
        check_clusterfuzzlite_build_config(),
        check_cfl_workflows(),
        check_batch_prune_pairing(),
        check_fuzz_target_determinism(),
        check_fuzz_gitignore(),
        check_fuzz_guide(),
        _check_optional_kiro(manifest, full=full),
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the harness-sync checker.

    Args:
        argv: Argument strings to parse (typically sys.argv[1:]).

    Returns:
        Parsed namespace with the ``full`` boolean flag.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="treat optional local adapter drift as blocking",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the harness-sync CLI.

    Runs all checks, prints each result, and returns an exit code of 1
    if any result is blocking (FAIL, or WARN in full mode), otherwise 0.

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    args = parse_args(argv or sys.argv[1:])
    results = collect_results(full=args.full)
    failing_statuses = {FAIL}
    if args.full:
        failing_statuses.add(WARN_NEEDS_AUTHOR_REVIEW)

    for result in results:
        print(f"{result.status:<24} {result.name:<20} {result.detail}")

    return 1 if any(result.status in failing_statuses for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
