#!/usr/bin/env python3
"""Verify that release tags are protected by an immutable ref ruleset.

A published release tag must not be movable or deletable after the fact: the
tag is the anchor that binds source archives, provenance documents, and
signatures to the exact approved candidate.  This gate asserts that the
repository has an active tag-target ruleset covering ``v*`` release tags with
both deletion and non-fast-forward (update) restrictions.

The check is intentionally semantic: it reads the ruleset list through the
GitHub API and verifies the contract (active enforcement, tag target, ``v*``
inclusion, both protection rule types) instead of matching configuration
text.

Usage:
    python3 tools/release/gates/verify_tag_ref_protection.py [--repo OWNER/NAME]

Exit codes: 0 = protected, 1 = unprotected or unverifiable (fail closed).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

REQUIRED_INCLUDE_PATTERN = "refs/tags/v*"
REQUIRED_RULE_TYPES = frozenset({"deletion", "non_fast_forward"})


def _flatten_pages(payload: list) -> list[dict]:
    """Flatten ``gh api --slurp`` pages into a list of JSON objects."""
    summaries: list[dict] = []
    for page in payload:
        if isinstance(page, list):
            summaries.extend(item for item in page if isinstance(item, dict))
        elif isinstance(page, dict):
            summaries.append(page)
    return summaries


def _fetch_ruleset_detail(repo: str, ruleset_id: int) -> dict | None:
    """Fetch one ruleset's full definition, or None when unavailable."""
    detail = subprocess.run(
        ["gh", "api", f"repos/{repo}/rulesets/{ruleset_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if detail.returncode != 0:
        return None
    try:
        parsed = json.loads(detail.stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _list_rulesets(repo: str) -> list[dict]:
    """Fetch all repository rulesets with their full rule/condition details.

    The rulesets *list* endpoint returns only metadata (id, name, target,
    enforcement) — the ``rules`` and ``conditions`` fields are present only on
    the per-ruleset GET endpoint, so each listed ruleset is fetched
    individually.  Rulesets whose detail fetch fails are skipped; a repository
    where no ruleset can be verified fails closed in :func:`main`.
    """
    payload = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/rulesets",
            "--paginate",
            "--slurp",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    summaries = _flatten_pages(json.loads(payload))
    detailed = []
    for summary in summaries:
        ruleset_id = summary.get("id")
        if not isinstance(ruleset_id, int):
            continue
        detail = _fetch_ruleset_detail(repo, ruleset_id)
        if detail is not None:
            detailed.append(detail)
    return detailed


def _ruleset_protects_release_tags(ruleset: dict) -> bool:
    """Return True when the ruleset actively restricts v* tag updates."""
    if ruleset.get("target") != "tag":
        return False
    if ruleset.get("enforcement") != "active":
        return False
    conditions = ruleset.get("conditions") or {}
    ref_name = conditions.get("ref_name") or {}
    include = ref_name.get("include") or []
    if REQUIRED_INCLUDE_PATTERN not in include:
        return False
    rule_types = {rule.get("type") for rule in ruleset.get("rules") or []}
    return REQUIRED_RULE_TYPES.issubset(rule_types)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assert that release tags are protected against "
        "deletion and update by an active tag ruleset."
    )
    parser.add_argument(
        "--repo",
        default="cnkang/nginx-markdown-for-agents",
        help="OWNER/REPOSITORY to check (default: cnkang/nginx-markdown-for-agents)",
    )
    args = parser.parse_args()

    try:
        rulesets = _list_rulesets(args.repo)
    except subprocess.CalledProcessError as exc:
        print(
            "FAIL: could not list repository rulesets "
            f"(exit {exc.returncode}): {exc.stderr.strip()}",
            file=sys.stderr,
        )
        print(
            "The release gate fails closed when ref protection cannot be "
            "verified; run this from an environment with GitHub API access.",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL: malformed ruleset payload: {exc}", file=sys.stderr)
        return 1

    matching = [
        ruleset
        for ruleset in rulesets
        if _ruleset_protects_release_tags(ruleset)
    ]
    if not matching:
        print(
            "FAIL: no active tag ruleset protects "
            f"'{REQUIRED_INCLUDE_PATTERN}' against deletion and updates. "
            "Create one via the repository rulesets API (see the release "
            "checklist) before tagging a release.",
            file=sys.stderr,
        )
        return 1

    for ruleset in matching:
        print(
            f"OK: ruleset {ruleset.get('name')!r} (id {ruleset.get('id')}) "
            f"protects {REQUIRED_INCLUDE_PATTERN} tags (deletion + non-fast-forward)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())