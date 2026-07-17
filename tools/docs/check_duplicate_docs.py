#!/usr/bin/env python3
"""Check canonical/mirror Markdown duplicates for drift.

This script compares known duplicate documentation pairs after removing
intentional mirror-copy notices and normalizing trivial whitespace so that
maintainers can catch real content drift quickly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import re
import sys


@dataclass(frozen=True)
class Pair:
    """A canonical/mirror documentation pair to compare."""

    canonical: str
    mirror: str


PAIRS: list[Pair] = []


MIRROR_NOTE_PATTERNS = [
    re.compile(r"^> Mirror copy\..*$"),
    re.compile(r"^This file is a local copy of the maintained testing documentation\..*$"),
]


def normalize_markdown(text: str) -> list[str]:
    """Normalize markdown text for comparison by removing mirror notes and extra blanks."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = _strip_mirror_notes(lines)
    collapsed = _collapse_blank_lines(kept)
    _trim_edge_blanks(collapsed)
    return collapsed


def _strip_mirror_notes(lines: list[str]) -> list[str]:
    """Remove intentional mirror-copy notice lines."""
    kept: list[str] = []
    for line in lines:
        if any(pat.match(line.strip()) for pat in MIRROR_NOTE_PATTERNS):
            continue
        kept.append(line.rstrip())
    return kept


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    """Collapse runs of multiple blank lines into a single blank line."""
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        collapsed.append(line)
    return collapsed


def _trim_edge_blanks(lines: list[str]) -> None:
    """Trim leading and trailing blank lines in-place for stable comparisons."""
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()


def main() -> int:
    """Check all configured duplicate pairs and report drift."""
    root = Path(__file__).resolve().parents[2]
    failures = 0

    if not PAIRS:
        print("No duplicate documentation pairs configured.")
        return 0

    for pair in PAIRS:
        canonical_path = root / pair.canonical
        mirror_path = root / pair.mirror

        if not canonical_path.exists() or not mirror_path.exists():
            print(f"MISSING: {pair.canonical} <-> {pair.mirror}")
            failures += 1
            continue

        canonical_lines = normalize_markdown(
            canonical_path.read_text(encoding="utf-8", errors="replace")
        )
        mirror_lines = normalize_markdown(
            mirror_path.read_text(encoding="utf-8", errors="replace")
        )

        if canonical_lines == mirror_lines:
            print(f"OK: {pair.canonical} == {pair.mirror}")
            continue

        failures += 1
        print(f"DIFF: {pair.canonical} != {pair.mirror}")
        diff = difflib.unified_diff(
            canonical_lines,
            mirror_lines,
            fromfile=pair.canonical,
            tofile=pair.mirror,
            n=3,
        )
        for i, line in enumerate(diff):
            if i >= 40:
                print("... (diff truncated)")
                break
            print(line)

    if failures:
        print(f"\nDuplicate documentation drift detected in {failures} pair(s).")
        return 1

    print(f"\nAll {len(PAIRS)} duplicate documentation pairs are in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
