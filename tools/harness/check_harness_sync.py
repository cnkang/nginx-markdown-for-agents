#!/usr/bin/env python3
"""Validate repo-owned harness truth surfaces and optional local adapters."""

from __future__ import annotations

import argparse
import json
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
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.json"
README_PATH = REPO_ROOT / "docs" / "harness" / "README.md"
CORE_PATH = REPO_ROOT / "docs" / "harness" / "core.md"
SUMMARY_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_manifest(path: Path | None = None) -> dict:
    path = path or MANIFEST_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to load harness manifest from {path}: {exc}") from exc


def _required_text(path: Path, needles: list[str]) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{_display_path(path)} unreadable"]
    return [needle for needle in needles if needle not in text]


def _required_patterns(path: Path, patterns: dict[str, str]) -> list[str]:
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
    if not isinstance(actual_statuses, list) or set(actual_statuses) != expected_statuses:
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
    missing: list[str] = []
    missing.extend(_required_patterns(
        README_PATH,
        {
            "core.md link": r"\[[^\]]+\]\(core\.md\)",
            "routing-manifest.json link": (
                r"\[[^\]]+\]\(routing-manifest\.json\)"
            ),
            "routing-manifest.md link": r"\[[^\]]+\]\(routing-manifest\.md\)",
            "risk-packs/README.md link": (
                r"\[[^\]]+\]\(risk-packs/README\.md\)"
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
    missing = _required_patterns(
        AGENTS_PATH,
        {
            "docs/harness/README.md": r"`docs/harness/README\.md`",
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


def _check_optional_kiro(manifest: dict, full: bool) -> CheckResult:
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
