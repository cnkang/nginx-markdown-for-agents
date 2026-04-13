#!/usr/bin/env python3
"""Validate repo-owned harness truth surfaces and optional local adapters."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


PASS = "PASS"
FAIL = "FAIL"
SKIP_NOT_PRESENT = "SKIP_NOT_PRESENT"
WARN_NEEDS_AUTHOR_REVIEW = "WARN_NEEDS_AUTHOR_REVIEW"

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.json"
README_PATH = REPO_ROOT / "docs" / "harness" / "README.md"
CORE_PATH = REPO_ROOT / "docs" / "harness" / "core.md"
SUMMARY_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.md"
PACK_INDEX_PATH = REPO_ROOT / "docs" / "harness" / "risk-packs" / "README.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _required_text(path: Path, needles: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [needle for needle in needles if needle not in text]


def _result(name: str, status: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status=status, detail=detail)


def _check_manifest_structure(manifest: dict) -> CheckResult:
    required_keys = {
        "version",
        "truth_surfaces",
        "status_semantics",
        "spec_resolver",
        "verification_families",
        "risk_packs",
        "task_entrypoints",
    }
    missing = sorted(required_keys - set(manifest))
    if missing:
        return _result(
            "manifest-structure",
            FAIL,
            f"missing top-level keys: {', '.join(missing)}",
        )

    if manifest["status_semantics"] != [
        PASS,
        FAIL,
        SKIP_NOT_PRESENT,
        WARN_NEEDS_AUTHOR_REVIEW,
    ]:
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
    missing: list[str] = []
    for category in ("contract", "harness", "canonical_docs"):
        for rel in manifest["truth_surfaces"][category]:
            if not (REPO_ROOT / rel).exists():
                missing.append(rel)
    if missing:
        return _result(
            "truth-surfaces",
            FAIL,
            f"missing truth surface files: {', '.join(missing)}",
        )
    return _result("truth-surfaces", PASS, "repo-owned truth surfaces exist")


def _check_risk_pack_docs(manifest: dict) -> CheckResult:
    missing_docs: list[str] = []
    missing_families: list[str] = []
    families = set(manifest["verification_families"])
    for pack in manifest["risk_packs"]:
        doc_path = REPO_ROOT / pack["doc"]
        if not doc_path.exists():
            missing_docs.append(pack["doc"])
        for family in pack["verification_families"]:
            if family not in families:
                missing_families.append(f"{pack['id']}->{family}")
    if missing_docs or missing_families:
        parts = []
        if missing_docs:
            parts.append(f"missing docs: {', '.join(missing_docs)}")
        if missing_families:
            parts.append(f"unknown verification families: {', '.join(missing_families)}")
        return _result("risk-pack-contract", FAIL, "; ".join(parts))
    return _result("risk-pack-contract", PASS, "risk packs and verification families line up")


def _check_harness_docs(manifest: dict) -> CheckResult:
    missing: list[str] = []
    missing.extend(
        _required_text(
            README_PATH,
            [
                "core.md",
                "routing-manifest.json",
                "routing-manifest.md",
                "risk-packs/README.md",
            ],
        )
    )
    pack_ids = [pack["id"] for pack in manifest["risk_packs"]]
    missing.extend(_required_text(SUMMARY_PATH, pack_ids))
    missing.extend(
        _required_text(
            CORE_PATH,
            [
                PASS,
                FAIL,
                SKIP_NOT_PRESENT,
                WARN_NEEDS_AUTHOR_REVIEW,
                "outside voice",
                "state carrier",
                "stop and explain the mismatch",
                "tools/harness/resolve_spec.py",
            ],
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
    missing = _required_text(
        AGENTS_PATH,
        [
            "docs/harness/README.md",
            "docs/harness/core.md",
            "docs/harness/routing-manifest.json",
            "Codex-first",
        ],
    )
    if missing:
        return _result(
            "agents-map",
            FAIL,
            f"AGENTS.md is missing harness map references: {', '.join(missing)}",
        )
    return _result("agents-map", PASS, "AGENTS.md points at the harness entrypoints")


def _check_optional_kiro(manifest: dict, full: bool) -> CheckResult:
    adapters = manifest["truth_surfaces"]["optional_adapters"]
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
                    "docs/harness/README.md",
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


def collect_results(full: bool = False) -> list[CheckResult]:
    manifest = _load_manifest()
    return [
        _check_manifest_structure(manifest),
        _check_truth_surfaces(manifest),
        _check_risk_pack_docs(manifest),
        _check_harness_docs(manifest),
        _check_agents_map(),
        _check_optional_kiro(manifest, full=full),
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="treat optional local adapter drift as blocking",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
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
