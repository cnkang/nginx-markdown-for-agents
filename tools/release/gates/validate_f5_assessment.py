#!/usr/bin/env python3
"""Validate the candidate-bound F5 Gate 4 assessment record."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import validate_read_path  # noqa: E402

REQUIRED_HEADINGS = (
    "Candidate",
    "Controller",
    "Module and ABI",
    "Injection",
    "Verification",
    "Rollback",
    "Decision",
)
DIGEST_RE = re.compile(r"\bsha256:[0-9a-f]{64}\b")
DATE_RE = re.compile(
    r"(?im)^\s*(?:[-*|]\s*)?(?:\*\*)?test date(?:\*\*)?\s*[:|]\s*(\d{4}-\d{2}-\d{2})"
)


def _read_assessment(path: str | Path) -> str:
    validated = validate_read_path(path, purpose="F5 assessment")
    try:
        return validated.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to read F5 assessment: {exc}") from exc


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD") from exc


def validate(text: str, candidate_digest: str, acceptance_date: date) -> list[str]:
    errors: list[str] = []
    if DIGEST_RE.fullmatch(candidate_digest) is None:
        errors.append("--candidate-digest must be a full sha256 digest")
    for heading in REQUIRED_HEADINGS:
        if not re.search(rf"(?im)^\s*#+\s+{re.escape(heading)}\b", text):
            errors.append(f"missing required heading: {heading}")

    candidate_matches = re.findall(
        r"(?im)^\s*(?:[-*|]\s*)?(?:\*\*)?candidate(?:\s+digest)?(?:\*\*)?\s*[:|]\s*([^\s|]+)",
        text,
    )
    candidate_values = [value.rstrip("`*\")'") for value in candidate_matches]
    if not candidate_values:
        errors.append("missing Candidate digest")
    elif candidate_digest not in candidate_values:
        errors.append("Candidate digest does not equal the selected digest")

    date_matches = DATE_RE.findall(text)
    if not date_matches:
        errors.append("missing Test date")
    else:
        test_date = _parse_date(date_matches[0], "Test date")
        age = (acceptance_date - test_date).days
        if age < 0 or age > 14:
            errors.append("Test date is not within 14 days before acceptance")

    if not re.search(r"(?im)^\s*(?:[-*|]\s*)?(?:\*\*)?decision(?:\*\*)?\s*[:|]\s*pass\b", text):
        errors.append("Decision must be pass")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assessment", help="path to f5-assessment.md")
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--acceptance-date", required=True)
    args = parser.parse_args(argv)
    try:
        acceptance_date = _parse_date(args.acceptance_date, "--acceptance-date")
        errors = validate(
            _read_assessment(args.assessment), args.candidate_digest, acceptance_date
        )
    except (ImportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: F5 assessment {args.assessment} validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
