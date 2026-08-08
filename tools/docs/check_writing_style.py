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
It never edits files. Code blocks, fenced blocks, tables, headings, inline
code, links, and HTML comments are excluded from the prose scan.

Modes:
    (default)      advisory scan of all maintained Markdown docs (exit 0)
    --strict       fail (exit 1) when any warning is found
    --changed      fail (exit 1) when any warning appears in files changed
                   since --base (default HEAD): working-tree + staged diffs.
                   In CI where the PR is already committed and diff HEAD is
                   empty, the check reports "no changed files" and passes.
    --baseline [N] fail (exit 1) when the total warning count exceeds the
                   baseline N (default 295). Guards against regressions on
                   the retained-warning budget; lower N as docs improve.

Usage:
    python3 tools/docs/check_writing_style.py [--strict|--changed|--baseline [N]]
        [--base REF] [--limit N] [paths...]
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SEGMENT = "docs/archive/"
MAINTAINED_ROOT_DOCS = {"AGENTS.md", "README.md", "README_zh-CN.md"}

# Retained-warning budget for --baseline mode. Lower this constant as docs
# improve; the Makefile target docs-style-check-baseline enforces it.
# 295 = 192 previous + 103 Latin-abbreviation warnings now detected after
# fixing LATIN_RE (trailing \b never matched e.g./i.e./etc.). The Latin
# abbreviations are a known cleanup backlog: replace with for example /
# such as / and so on as docs are touched.
DEFAULT_BASELINE = 295

SENT_DESCRIPTIVE_MAX = 25
SENT_INSTRUCTION_MAX = 20
NOUN_CHAIN_MAX = 3

LATIN_RE = re.compile(r"\b(?:e\.g\.|i\.e\.|etc\.|vs\.|et al\.)(?=\s|[.,;:!?)]|$)", re.IGNORECASE)
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


def _changed_md_files(base: str) -> list[Path]:
    """Maintained .md files changed since base (working tree + staged)."""
    out = subprocess.run(
        ["git", "diff", "--name-only", base, "--", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    cached = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    rels: set[str] = set()
    if out.returncode == 0:
        rels.update(out.stdout.splitlines())
    if cached.returncode == 0:
        rels.update(cached.stdout.splitlines())
    return sorted(
        ROOT / rel
        for rel in rels
        if rel.strip() and _is_maintained(rel) and (ROOT / rel).is_file()
    )


def _base_warning_counts(files: list[Path], base: str) -> dict[Path, Counter]:
    """Warning counts for the base revision of each file (empty for new files)."""
    counts: dict[Path, Counter] = {}
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        out = subprocess.run(
            ["git", "show", f"{base}:{rel}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            counts[f] = Counter()
            continue
        counts[f] = Counter(audit(out.stdout, f, None))
    return counts


def main() -> int:
    strict = "--strict" in sys.argv
    changed = "--changed" in sys.argv
    baseline = None
    base = "HEAD"
    limit = None
    args: list[str] = []
    i = 0
    argv = sys.argv[1:]
    while i < len(argv):
        a = argv[i]
        if a == "--limit" and i + 1 < len(argv):
            try:
                limit = int(argv[i + 1])
            except ValueError:
                pass
            i += 1
        elif a == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            i += 1
        elif a == "--baseline":
            baseline = DEFAULT_BASELINE
            if i + 1 < len(argv) and argv[i + 1].isdigit():
                baseline = int(argv[i + 1])
                i += 1
        elif a.startswith("--"):
            pass  # unknown flag, ignore
        else:
            args.append(a)
        i += 1

    if changed:
        files = _changed_md_files(base)
        if not files:
            print(
                "No changed maintained Markdown files since "
                f"{base}; nothing to check."
            )
            print("OK: --changed found no changed files")
            return 0
        base_counts = _base_warning_counts(files, base)
    elif args:
        files = [ROOT / a for a in args]
    else:
        files = _tracked_md_files()

    total = 0
    new_total = 0
    base_counts: dict[Path, Counter] = {}
    if changed:
        base_counts = _base_warning_counts(files, base)
    for f in files:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for w in audit(text, f, limit):
            rel = f.relative_to(ROOT)
            print(f"WARN {rel}: {w}")
            total += 1
            if changed:
                before = base_counts.get(f, Counter())
                if before.get(w, 0) <= 0:
                    print(f"  (NEW in {base} -> now)")
                    new_total += 1
                else:
                    before[w] -= 1

    print(f"\nWriting-style warnings: {total} (file(s): {len(files)})")
    print("This check is advisory (STE-inspired, non-native-reader friendly).")
    if strict and total:
        print("FAIL: strict mode found warnings")
        return 1
    if changed and new_total:
        print(
            f"FAIL: --changed found {new_total} new warning(s) in changed "
            f"files since {base}"
        )
        return 1
    if baseline is not None and total > baseline:
        print(
            f"FAIL: warning count {total} exceeds baseline {baseline}; "
            "reduce warnings, do not add new ones"
        )
        return 1
    if changed:
        print(f"OK: no new warnings in changed files since {base}")
    elif baseline is not None:
        print(f"OK: {total} warnings within baseline {baseline}")
    else:
        print("OK: advisory only (no exit-code failure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
