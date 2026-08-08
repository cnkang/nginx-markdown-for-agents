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
                   since the explicitly supplied --base commit: working-tree
                   + staged diffs. The base is required and must resolve to a
                   valid commit. An empty diff is valid only when the caller
                   explicitly selected a valid base such as HEAD.
    --baseline [N] fail (exit 1) when the total warning count exceeds the
                   baseline N (default 0). Guards against regressions on
                   the retained-warning budget; lower N as docs improve.

Usage:
    python3 tools/docs/check_writing_style.py [--strict|--changed|--baseline [N]]
        [--base REF] [--limit N] [paths...]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SEGMENT = "docs/archive/"
# CHANGELOG.md keeps historical release prose. Changed-mode still audits it so
# an edited historical entry cannot add a new warning. Baseline mode excludes
# the retained historical warning set and covers current reader-facing docs.
MAINTAINED_ROOT_DOCS = {
    "AGENTS.md",
    "README.md",
    "README_zh-CN.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
}
HISTORICAL_ROOT_DOCS = {"CHANGELOG.md"}

# Retained-warning budget for --baseline mode. Lower this constant as docs
# improve; the Makefile target docs-style-check-baseline enforces it.
# 0 = full cleanup complete: the maintained docs pass the STE-inspired audit
# with zero warnings. The checker exempts structural surfaces (rule-checklist
# items, formal titles in the allowlist, reference lines, quoted source
# citations) so the remaining scan targets genuine prose violations only.
DEFAULT_BASELINE = 0

SENT_DESCRIPTIVE_MAX = 25
SENT_INSTRUCTION_MAX = 20
NOUN_CHAIN_MAX = 3

LATIN_RE = re.compile(r"\b(?:e\.g\.|i\.e\.|etc\.|vs\.|et al\.)(?=\s|[.,;:!?)]|$)", re.IGNORECASE)
CONTRACTION_RE = re.compile(
    r"\b(?:don't|doesn't|isn't|aren't|wasn't|weren't|won't|can't|couldn't|"
    r"shouldn't|wouldn't|it's|that's|we're|you're|they're|there's|let's)\b",
    re.IGNORECASE,
)
PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+\w+ed\b", re.IGNORECASE
)
NOUN_CHAIN_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){3,})\b"
)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"`(])")
SOURCE_CITATION_LINE_RE = re.compile(
    r"^(?:>\s*)?(?:[-*]\s*)?(?:\*\*)?"
    r"(?:source(?:\s+(?:comment|citation))?|citation|requirement|external citation|reference|[A-Z]{2,}-\d+(?:\.\d+)*)"
    r"(?:\*\*)?\s*:",
    re.IGNORECASE,
)
INSTRUCTION_RE = re.compile(
    r"^(?:always|avoid|call|check|compare|confirm|create|delete|do not|"
    r"ensure|follow|include|install|keep|make|never|preserve|read|record|"
    r"remove|run|set|store|test|use|verify|write)\b",
    re.IGNORECASE,
)


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
    """Return whether a path belongs to the current docs style scope."""
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
        if p.is_file()
        and _is_maintained(p.relative_to(ROOT).as_posix())
        and p.relative_to(ROOT).as_posix() not in HISTORICAL_ROOT_DOCS
    )


def _prose_only(raw: str) -> str:
    # Rust doc-comment examples use a fenced block whose backticks have a
    # leading ``///`` prefix. Remove that prefix before stripping fenced code.
    t = re.sub(
        r"^[ \t]*///[ \t]*```[^\n]*\n.*?^[ \t]*///[ \t]*```[ \t]*$",
        " ",
        raw,
        flags=re.M | re.S,
    )
    t = re.sub(r"^[ \t]*///[ \t]*```[^\n]*\n?", " ", t, flags=re.M)
    t = re.sub(r"^[ \t]*///[ \t]?", "", t, flags=re.M)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"\|[^\n]*\|", " ", t)
    t = re.sub(r"^\s*#{1,6}\s.*$", " ", t, flags=re.M)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`[^`]*`", " ", t)
    # Only an explicitly labelled citation may preserve quoted source text.
    # Ordinary prose in quotes remains part of the audit surface.
    lines = []
    for line in t.splitlines():
        if SOURCE_CITATION_LINE_RE.match(line.strip()):
            line = re.sub(r'"[^"\n]*"', " ", line)
        lines.append(line)
    t = "\n".join(lines)
    # Keep line breaks: each line is a candidate sentence unit, so list
    # items / blockquote lines don't merge into one giant pseudo-sentence.
    t = re.sub(r"[ \t]+", " ", t)
    return t


# Rule-checklist item line: "- **Title**: ..." or "N. **Title**: ...".
# Only this explicit structure is exempt from prose length checks.
RULE_ITEM_RE = re.compile(
    r"^(?:[-*]\s+|\d+[.)]\s+)\*\*[^*\n]+\*\*:\s+"
)
LIST_MARKER_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+")

# Rule/governance documents whose list items are atomic checkpoints even
# without a bold title (AGENTS.md required-list, harness rule docs).
RULE_DOC_PREFIXES = ("AGENTS.md", "docs/harness/rules/")

# Rule-format items may contain several atomic requirements. Keep the
# exemption narrow: ordinary guide prose still uses the length and semicolon
# checks, while explicit rule items, release-gate templates, and MUST clauses
# on repo-owned specification surfaces retain their structure.
RELEASE_GATE_TEMPLATE_RE = re.compile(
    r"(?:release[-_]gate|go[-_]no[-_]go)[^\n]*template", re.IGNORECASE
)
MUST_SPECIFICATION_RE = re.compile(r"\bMUST\b")
MUST_SPECIFICATION_PREFIXES = (
    "AGENTS.md",
    "docs/architecture/",
    "docs/harness/",
    "docs/project/release-gates/",
)

# Reference line carrying a formal title: "- ADR-0019: 0.9.0 Production
# Readiness Release Gate Framework", "- RFC 7932 (Brotli Compressed Data
# Format)", "- Rule 55: ...". Noun chains inside these are document names.
REF_LINE_RE = re.compile(r"^(?:-\s+)?(?:ADR-\d+|RFC\s+\d+|Rule\s+\d+)\s*[:,(]")

# Noun-chain tokens that are proper nouns / formal titles and must stay as
# written (standard names, ADR titles, product names, tool names).
NOUN_CHAIN_ALLOWLIST = {
    "An Architecture Decision Record",
    "Brotli Compressed Data Format",
    "Common Issues Quick Reference",
    "Deflate Streaming Decompression Routing",
    "Deterministic Markdown Output Constraints",
    "If Upstream Has Vary",
    "Large Response Path Optimization",
    "Latest Canonical Module Measurement",
    "Noise Region Early Pruning",
    "Normal Brotli Streaming Operation",
    "Page Types Not Recommended",
    "Performance Evidence Release Gate",
    "Production Readiness Breaking Release",
    "Production Readiness Release Gate Framework",
    "Production Readiness Release Gates",
    "Progress Guard No False Positives",
    "Prometheus Metrics Guide",
    "Simple Structure Fast Path",
    "Single Public Streaming Policy Before",
    "Streaming Bounded Memory Conversion",
    "True Streaming Contract",
    "Version Consistency Across All Artifacts",
    "Version Mismatch Error Troubleshooting",
    "Why These Defaults Matter",
    "Why These Page Types Are Risky",
    "Xcode Command Line Tools",
    "Changing Defaults During Rollout",
    "Performance Evidence Release Gate",
    "No-Progress Guard No False Positives",
}


def _is_structural_rule_line(line: str, rel: str) -> bool:
    """Return whether a line belongs to an exempt rule-format structure."""
    stripped = line.strip()
    if RULE_ITEM_RE.match(stripped):
        return True
    if rel.startswith(RULE_DOC_PREFIXES) and LIST_MARKER_RE.match(stripped):
        return True
    if RELEASE_GATE_TEMPLATE_RE.search(rel) and LIST_MARKER_RE.match(stripped):
        return True
    return rel.startswith(MUST_SPECIFICATION_PREFIXES) and bool(
        MUST_SPECIFICATION_RE.search(stripped)
    )


def _structural_rule_lines(prose: str, rel: str) -> Iterator[tuple[str, bool]]:
    """Yield prose lines and whether they belong to a structural rule item."""
    in_structural_item = False
    for line in prose.splitlines():
        if not line.strip():
            in_structural_item = False
            continue
        structural_line = _is_structural_rule_line(line, rel)
        continuation = in_structural_item and line.startswith((" ", "\t"))
        structural = structural_line or continuation
        if structural_line:
            in_structural_item = True
        elif not continuation:
            in_structural_item = False
        yield line, structural


def _sentences(prose: str, rel: str) -> list[tuple[str, bool, bool]]:
    """Split prose into (sentence, structural_line, ref_line) units.

    Each line is processed first; the line's flags decide which checks apply.
    Structural rule items and their continuations keep their formatting.
    Reference lines keep formal-title exemptions (see REF_LINE_RE).
    """
    out: list[tuple[str, bool, bool]] = []
    for raw_line, structural in _structural_rule_lines(prose, rel):
        line = raw_line
        line = line.strip()
        if not line:
            continue
        ref_line = bool(REF_LINE_RE.match(line))
        for s in SENT_SPLIT_RE.split(line):
            s = s.strip()
            if len(s) > 3:
                out.append((s, structural, ref_line))
    return out


def _prose_semicolon_count(prose: str, rel: str) -> int:
    """Count semicolons outside structural rule lines."""
    count = 0
    for line, structural in _structural_rule_lines(prose, rel):
        if ";" in line and not structural:
            count += line.count(";")
    return count


def audit(text: str, path: Path, limit: int | None) -> list[str]:
    """Return warning lines for one file."""
    prose = _prose_only(text)
    warnings: list[str] = []
    rel = str(path.relative_to(ROOT)) if path.is_absolute() else str(path)
    for s, rule_item, ref_line in _sentences(prose, rel):
        if rule_item:
            continue  # rule-checklist item: length is structural
        rule_text = LIST_MARKER_RE.sub("", s, count=1)
        n = len(rule_text.split())
        if n > SENT_DESCRIPTIVE_MAX:
            warnings.append(
                f"long sentence ({n}w > {SENT_DESCRIPTIVE_MAX}): {s[:100]}"
            )
        elif n > SENT_INSTRUCTION_MAX and INSTRUCTION_RE.match(rule_text):
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
        chain = m.group(0)
        # Cross-line merges may glue an allowlisted proper noun to
        # neighboring prose (e.g. "See\nPrometheus Metrics Guide\n\nCapture").
        # If the chain contains an allowlisted formal title, it is a proper
        # noun, not prose bloat.
        if any(al in chain for al in NOUN_CHAIN_ALLOWLIST):
            continue
        # skip noun chains on reference lines (document titles)
        line_start = prose.rfind("\n", 0, m.start()) + 1
        line = prose[line_start : prose.find("\n", m.end())]
        if REF_LINE_RE.match(line.strip()):
            continue
        warnings.append(
            f"multi-word noun chain '{chain}': expand with prepositions"
        )
    semi = _prose_semicolon_count(prose, rel)
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


def _resolve_base(ref: str) -> str | None:
    """Resolve a user-supplied base ref, or return None when it is invalid."""
    if not ref or ref.startswith("-"):
        return None
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit maintained Markdown prose for Rule 63 violations."
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--changed", action="store_true")
    parser.add_argument(
        "--baseline",
        nargs="?",
        type=int,
        const=DEFAULT_BASELINE,
        metavar="N",
    )
    parser.add_argument("--base", metavar="REF")
    parser.add_argument("--limit", type=int, metavar="N")
    parser.add_argument("paths", nargs="*")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.baseline is not None and args.baseline < 0:
        parser.error("--baseline must be non-negative")

    base = None
    if args.changed:
        if not args.base:
            parser.error("--base is required with --changed")
        base = _resolve_base(args.base)
        if base is None:
            parser.error(f"--base does not name a valid commit: {args.base}")

        files = _changed_md_files(base)
        if not files:
            print(
                "No changed maintained Markdown files since "
                f"{args.base}; nothing to check."
            )
            print("OK: --changed found no changed files")
            return 0
    elif args.paths:
        files = [ROOT / path for path in args.paths]
    else:
        files = _tracked_md_files()

    total = 0
    new_total = 0
    base_counts: dict[Path, Counter] = {}
    if args.changed:
        base_counts = _base_warning_counts(files, base)
    for f in files:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for w in audit(text, f, args.limit):
            rel = f.relative_to(ROOT)
            print(f"WARN {rel}: {w}")
            total += 1
            if args.changed:
                before = base_counts.get(f, Counter())
                if before.get(w, 0) <= 0:
                    print(f"  (NEW in {base} -> now)")
                    new_total += 1
                else:
                    before[w] -= 1

    print(f"\nWriting-style warnings: {total} (file(s): {len(files)})")
    print("This check is advisory (STE-inspired, non-native-reader friendly).")
    if args.strict and total:
        print("FAIL: strict mode found warnings")
        return 1
    if args.changed and new_total:
        print(
            f"FAIL: --changed found {new_total} new warning(s) in changed "
            f"files since {args.base}"
        )
        return 1
    if args.baseline is not None and total > args.baseline:
        print(
            f"FAIL: warning count {total} exceeds baseline {args.baseline}; "
            "reduce warnings, do not add new ones"
        )
        return 1
    if args.changed:
        print(f"OK: no new warnings in changed files since {args.base}")
    elif args.baseline is not None:
        print(f"OK: {total} warnings within baseline {args.baseline}")
    else:
        print("OK: advisory only (no exit-code failure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
