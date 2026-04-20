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

    Checks README.md for navigation links, routing-manifest.md for
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
    """Verify that AGENTS.md references the harness entrypoints and Codex-first semantics.

    Returns:
        CheckResult with PASS if all required references are present,
        or FAIL listing the missing items.
    """
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
