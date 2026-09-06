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


TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


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


def _is_table_separator(line: str) -> bool:
    """Return whether a line contains only Markdown table separators."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False

    cells = stripped[1:-1].split("|")
    if not cells:
        return False
    for cell in cells:
        token = cell.strip()
        if token.startswith(":"):
            token = token[1:]
        if token.endswith(":"):
            token = token[:-1]
        if not token or any(character != "-" for character in token):
            return False
    return True


def _table_block_at(lines: list[str], index: int) -> tuple[int, str] | None:
    """Return the end index and normalized table at ``index``, if present."""
    if index + 1 >= len(lines):
        return None
    if not TABLE_ROW.match(lines[index]) or not _is_table_separator(lines[index + 1]):
        return None

    end = index + 2
    while end < len(lines) and TABLE_ROW.match(lines[end]):
        end += 1
    block = "\n".join(line.strip() for line in lines[index:end])
    return end, block


def _table_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """Return normalized Markdown table blocks with their one-based starts."""
    blocks: list[tuple[int, str]] = []
    index = 0
    section = ""
    while index < len(lines):
        if lines[index].lstrip().startswith("#"):
            section = lines[index].lstrip("# ").strip().lower()

        table = _table_block_at(lines, index)
        if table is None:
            index += 1
            continue

        start = index
        index, block = table
        # Document-update tables are intentionally repeated boilerplate, not
        # competing copies of a volatile contract.  Require a substantial
        # table so common two-row reference tables do not become noise.
        if (section != "document updates" and index - start >= 5
                and len(block) >= 300):
            blocks.append((start + 1, block))
    return blocks


def _find_duplicate_tables(root: Path) -> list[tuple[str, int, str, int]]:
    """Find identical substantial Markdown tables across tracked docs."""
    occurrences: dict[str, list[tuple[str, int]]] = {}
    for path in sorted(root.rglob("*.md")):
        relative_path = path.relative_to(root)
        if relative_path.parts and relative_path.parts[0] in {
            "harness",
            "project",
            "releases",
        }:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_number, block in _table_blocks(lines):
            occurrences.setdefault(block, []).append(
                (str(path.relative_to(root.parent)), line_number)
            )

    duplicates: list[tuple[str, int, str, int]] = []
    for locations in occurrences.values():
        paths = {path for path, _ in locations}
        if len(paths) < 2:
            continue
        first_path, first_line = locations[0]
        for other_path, other_line in locations[1:]:
            duplicates.append((first_path, first_line, other_path, other_line))
    return duplicates


def main() -> int:
    """Check all configured duplicate pairs and report drift."""
    root = Path(__file__).resolve().parents[2]
    failures = 0

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

    duplicate_tables = _find_duplicate_tables(root / "docs")
    for first_path, first_line, second_path, second_line in duplicate_tables:
        failures += 1
        print(
            "DUPLICATE TABLE: "
            f"{first_path}:{first_line} == {second_path}:{second_line}"
        )

    if failures:
        print(f"\nDuplicate documentation drift detected in {failures} finding(s).")
        return 1

    if PAIRS:
        print(f"\nAll {len(PAIRS)} duplicate documentation pairs are in sync.")
    else:
        print("\nNo explicit duplicate pairs; generic table duplication scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
