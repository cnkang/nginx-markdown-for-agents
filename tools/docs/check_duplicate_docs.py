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
    canonical: str
    mirror: str


PAIRS: list[Pair] = []


MIRROR_NOTE_PATTERNS = [
    re.compile(r"^> Mirror copy\..*$"),
    re.compile(r"^This file is a local copy of the maintained testing documentation\..*$"),
]


def normalize_markdown(text: str) -> list[str]:
    # Normalize line endings and remove intentional mirror-copy notes.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        if any(pat.match(line.strip()) for pat in MIRROR_NOTE_PATTERNS):
            continue
        kept.append(line.rstrip())

    # Collapse multiple blank lines to minimize formatting-only noise.
    collapsed: list[str] = []
    blank_run = 0
    for line in kept:
        if line == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        collapsed.append(line)

    # Trim leading/trailing blank lines for stable comparisons.
    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return collapsed


def main() -> int:
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
