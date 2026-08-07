#!/usr/bin/env python3
"""Non-native-reader writing-style checks for maintained Markdown docs.

STE-inspired prose audit (see docs/development/WRITING_GUIDE.md):
- sentence length (descriptive > 25 words, instruction > 20 words)
- semicolons in prose
- Latin abbreviations (e.g., i.e., etc.)
- contractions (don't, isn't, can't, ...)
- multi-word noun chains (>= 4 consecutive capitalized words)
- passive-voice-ish patterns (is/was/... + past participle)

Design: WARNING-ONLY by default (exit 0) so it never blocks existing CI.
Pass --strict to fail (exit 1) when any warning is found. It never edits
files. Code blocks, fenced blocks, tables, headings, inline code, links,
and HTML comments are excluded from the prose scan.

Usage:
    python3 tools/docs/check_writing_style.py [--strict] [--limit N] [paths...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SEGMENT = "docs/archive/"
MAINTAINED_ROOT_DOCS = {"AGENTS.md", "README.md", "README_zh-CN.md"}

SENT_DESCRIPTIVE_MAX = 25
SENT_INSTRUCTION_MAX = 20
NOUN_CHAIN_MAX = 3

LATIN_RE = re.compile(r"\b(?:e\.g\.|i\.e\.|etc\.|vs\.|et al\.)\b")
CONTRACTION_RE = re.compile(
    r"\b(?:don't|doesn't|isn't|aren't|wasn't|weren't|won't|can't|couldn't|"
    r"shouldn't|wouldn't|it's|that's|we're|you're|they're|there's|let's)\b"
)
PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+\w+ed\b", re.IGNORECASE
)
NOUN_CHAIN_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){3,})\b"
)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"`(])")


def _is_ignored(path: Path) -> bool:
    if ARCHIVE_SEGMENT in path.as_posix():
        return True
    try:
        import subprocess

        out = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=ROOT,
            capture_output=True,
        )
        return out.returncode == 0
    except Exception:
        return False


def _is_maintained(rel: str) -> bool:
    """Mirror check_docs.py scope: root docs + docs/ minus archive."""
    if rel in MAINTAINED_ROOT_DOCS:
        return True
    return rel.startswith("docs/") and not rel.startswith(ARCHIVE_SEGMENT)


def _tracked_md_files() -> list[Path]:
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    candidates: list[Path] = []
    if out.returncode == 0:
        candidates = [ROOT / rel for rel in out.stdout.splitlines() if rel.strip()]
    else:
        candidates = list(ROOT.rglob("*.md"))
    return sorted(
        p
        for p in candidates
        if p.is_file() and _is_maintained(p.relative_to(ROOT).as_posix())
    )


def _prose_only(raw: str) -> str:
    t = re.sub(r"```.*?```", " ", raw, flags=re.S)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"\|[^\n]*\|", " ", t)
    t = re.sub(r"^\s*#{1,6}\s.*$", " ", t, flags=re.M)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`[^`]*`", " ", t)
    # Keep line breaks: each line is a candidate sentence unit, so list
    # items / blockquote lines don't merge into one giant pseudo-sentence.
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _sentences(prose: str) -> list[str]:
    """Split prose into sentence units: split lines first, then on enders."""
    out: list[str] = []
    for line in prose.split("\n"):
        line = line.strip()
        if not line:
            continue
        for s in SENT_SPLIT_RE.split(line):
            s = s.strip()
            if len(s) > 3:
                out.append(s)
    return out


def audit(text: str, path: Path, limit: int | None) -> list[str]:
    """Return warning lines for one file."""
    prose = _prose_only(text)
    warnings: list[str] = []

    for s in _sentences(prose):
        n = len(s.split())
        if n > SENT_DESCRIPTIVE_MAX:
            warnings.append(
                f"long sentence ({n}w > {SENT_DESCRIPTIVE_MAX}): {s[:100]}"
            )
        elif n > SENT_INSTRUCTION_MAX and re.match(
            r"^(?:[A-Z][a-z]+|[0-9]+[.)])\s", s
        ):
            warnings.append(
                f"long instruction ({n}w > {SENT_INSTRUCTION_MAX}): {s[:100]}"
            )

    for m in LATIN_RE.finditer(prose):
        warnings.append(f"Latin abbreviation '{m.group(0)}': spell it out")
    for m in CONTRACTION_RE.finditer(prose):
        warnings.append(f"contraction '{m.group(0)}': avoid in prose")
    for m in PASSIVE_RE.finditer(prose):
        warnings.append(f"passive-ish '{m.group(0)}': prefer active voice")
    for m in NOUN_CHAIN_RE.finditer(prose):
        warnings.append(
            f"multi-word noun chain '{m.group(0)}': expand with prepositions"
        )
    semi = prose.count(";")
    if semi:
        warnings.append(f"{semi} semicolon(s) in prose: split into sentences")

    return warnings[:limit] if limit else warnings


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[i + 1])
            except ValueError:
                pass

    files: list[Path]
    if args:
        files = [ROOT / a for a in args]
    else:
        files = _tracked_md_files()

    total = 0
    for f in files:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for w in audit(text, f, limit):
            rel = f.relative_to(ROOT)
            print(f"WARN {rel}: {w}")
            total += 1

    print(f"\nWriting-style warnings: {total} (file(s): {len(files)})")
    print("This check is advisory (STE-inspired, non-native-reader friendly).")
    if strict and total:
        print("FAIL: strict mode found warnings")
        return 1
    print("OK: advisory only (no exit-code failure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
