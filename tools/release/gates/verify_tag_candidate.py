#!/usr/bin/env python3
"""Verify that a release tag is the protected, approved candidate commit."""

from __future__ import annotations

import argparse
import os
import re
import sys


SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
TAG_PATTERN = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")


def verify_tag_candidate(
    *,
    tag_sha: str,
    approved_sha: str,
    ref_type: str,
    ref_name: str,
    default_branch: str,
    branch_relation: str,
) -> None:
    """Raise ``ValueError`` unless all release promotion inputs are valid."""
    if ref_type != "tag":
        raise ValueError("release promotion requires a tag ref")
    if not TAG_PATTERN.fullmatch(ref_name):
        raise ValueError("release ref name must be a version tag")
    if default_branch != "main":
        raise ValueError(
            "release promotion requires the protected main branch as default"
        )
    if not SHA_PATTERN.fullmatch(tag_sha):
        raise ValueError("tag SHA must be 40 lowercase hexadecimal characters")
    if not SHA_PATTERN.fullmatch(approved_sha):
        raise ValueError(
            "approved candidate SHA must be 40 lowercase hexadecimal characters"
        )
    if tag_sha != approved_sha:
        raise ValueError("tag SHA does not match the protected approved candidate")
    if branch_relation not in {"ahead", "identical"}:
        raise ValueError(
            "tag SHA is not contained in the protected main branch history"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag-sha", required=True)
    parser.add_argument("--ref-type", required=True)
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--default-branch", required=True)
    parser.add_argument("--branch-relation", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate command-line inputs against the protected environment secret."""
    args = _parser().parse_args(argv)
    approved_sha = os.environ.get("APPROVED_CANDIDATE_SHA", "")
    try:
        verify_tag_candidate(
            tag_sha=args.tag_sha,
            approved_sha=approved_sha,
            ref_type=args.ref_type,
            ref_name=args.ref_name,
            default_branch=args.default_branch,
            branch_relation=args.branch_relation,
        )
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Tag SHA matches the protected approved release candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
