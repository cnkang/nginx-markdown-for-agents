#!/usr/bin/env python3
"""detect_regex_safety.py — Detect regex patterns prone to catastrophic backtracking (ReDoS).

Rule 10 (parser-regex): Avoid regex patterns with overlapping quantifiers or
backtracking hotspots. Treat static-analysis ReDoS findings as design issues.

Detection strategy:
  Scan Python (.py) and Shell (.sh) source files for compiled regex patterns.
  For each regex literal found, apply heuristic checks for known dangerous
  patterns that can cause super-linear (O(n^2) or exponential) runtime:

  1. Adjacent unbounded repetitions of overlapping character classes:
     e.g. (a+)+ , (.*)(.*) , [^x]*[^y]* where x==y
  2. Nested quantifiers: (a+)+, (a*)*
  3. Overlapping alternatives with repetition: (a|a)+
  4. Backreference after lazy/greedy .* with DOTALL:
     e.g. (.*?)\\1 or (.*)\\1
  5. Adjacent identical unbounded quantifiers: \\s*\\s* , .*.*

This is a heuristic detector (no full NFA analysis). It flags known-bad
structural patterns. False positives should be suppressed with an inline
comment: # nosec:regex-safety or # noqa:regex-safety

Usage:
    python3 tools/harness/detect_regex_safety.py [directory] [--strict]
      directory defaults to tools/
      --strict  exit 1 on findings (default: advisory exit 0)

Exit codes:
    0 — no findings, or findings reported as warnings (advisory mode)
    1 — findings in --strict mode, or usage error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns to extract regex literals from Python source
# Matches: re.compile(r"..."), re.compile("..."), re.compile(r'...')
_PY_RE_COMPILE = re.compile(
    r"""re\.compile\(\s*r?(['"])(.*?)\1""",
    re.DOTALL,
)

# Also match raw regex assignment: pattern = r"..."
_PY_RE_ASSIGN = re.compile(
    r"""(?:_re_|_RE_|_PATTERN|pattern)\s*=\s*re\.compile\(\s*r?(['"])(.*?)\1""",
    re.DOTALL,
)

# Shell grep -E / grep -P patterns
# Two-step: find grep with flags, then extract the quoted pattern
_SH_GREP_CMD = re.compile(r"grep\s+-[a-zA-Z]*[EP]")
_SH_GREP_ARG = re.compile(r"""['"]([^'"]+)['"]""")

# Suppression comment
_SUPPRESSION_RE = re.compile(r"#\s*(?:nosec|noqa):regex-safety", re.IGNORECASE)


class RegexFinding:
    """A single regex safety finding."""

    def __init__(self, file_path: str, line: int, pattern: str, reason: str):
        self.file_path = file_path
        self.line = line
        self.pattern = pattern
        self.reason = reason

    def __str__(self) -> str:
        short_pattern = (
            f"{self.pattern[:60]}..." if len(self.pattern) > 60 else self.pattern
        )
        return (
            f"WARN: {self.file_path}:{self.line}: "
            f"potentially unsafe regex: {short_pattern}\n"
            f"      Reason: {self.reason}"
        )


def _group_has_literal_anchor(group_content: str) -> bool:
    """Check if a regex group starts with a required non-quantified literal.

    A literal anchor at the start of a group prevents overlap between
    iterations of an outer quantifier, making the pattern safe.
    """
    inner = group_content
    inner = inner.removeprefix("?:")
    if not inner:
        return False
    # Literal char not followed by a quantifier
    if inner[0] not in (".", "[", "\\", "(", "?", "*", "+", "|", "^", "$"):
        return len(inner) < 2 or inner[1] not in ("+", "*", "?", "{")
    # Literal escape (e.g., \_, \-) not followed by a quantifier
    if inner.startswith("\\") and len(inner) > 1 and inner[1] not in "wWdDsS.bB":
        return len(inner) < 3 or inner[2] not in ("+", "*", "?", "{")
    return False


def _check_nested_quantifier(pattern: str) -> str | None:
    """Check for nested quantifiers like (a+)+, (a*)+, (.+)+, (.*)+.

    Only flags when the outer quantifier is + or * (unbounded) AND the group
    content does not start with a required literal anchor that prevents
    overlap between iterations.

    Safe patterns (literal separator prevents overlap):
      (-[a-z]+)*  — the '-' is required at each iteration start
      (_\\d+)*    — the '_' is required at each iteration start

    Dangerous patterns:
      (a+)+       — inner and outer both consume 'a'
      (.*)+       — unbounded overlap
      ([^x]*)+    — inner can match empty, outer repeats
    """
    # Match group containing a quantifier followed by an outer quantifier.
    # Use [^)+*]* to match non-special chars, avoiding backtracking between
    # two [^)]* groups (S8786).
    nested_re = re.compile(r"\((\?:)?([^)+*]*[+*])[^)]*\)([+*])")
    for m in nested_re.finditer(pattern):
        full_group_content = pattern[m.start() + 1 : m.end() - 2]
        if _group_has_literal_anchor(full_group_content):
            continue
        return (
            f"nested quantifier '{m.group(0)}' — "
            "inner repetition inside outer repetition causes exponential backtracking"
        )
    return None


def _check_adjacent_overlapping_quantifiers(pattern: str) -> str | None:
    """Check for adjacent unbounded repetitions with overlapping character classes.

    Only flags when two quantified groups are directly adjacent with NO required
    literal separator between them (or the separator itself is optional/quantified).
    Patterns like \\s*:\\s* are safe because ':' is required between them.
    Patterns like [^"]*[^"]* or \\s*\\s* or .*.* or (.*)(.*) are dangerous.
    """
    # Two identical or overlapping unbounded quantifiers DIRECTLY adjacent,
    # possibly separated only by group boundaries )(
    quant_atom = r"(?:\.\*|\.\+|\\[sdwSDW][*+]|\[[^\]]+\][*+])"
    group_sep = r"(?:\)\(?(?:\?:)?)?"
    direct_adjacent_re = re.compile(f"({quant_atom}){group_sep}({quant_atom})")
    if m := direct_adjacent_re.search(pattern):
        g1, g2 = m[1], m[2]
        if g1 == g2:
            return f"adjacent identical quantifiers '{g1}...{g2}' — causes quadratic backtracking"
        if g1.startswith(".") or g2.startswith("."):
            return f"adjacent overlapping quantifiers '{g1}...{g2}' — .* overlaps with subsequent quantifier"
    return None


def _check_backreference_after_dotstar(pattern: str) -> str | None:
    r"""Check for backreference (\1, \2...) after .* or .*? with DOTALL risk.

    Pattern like: (.*?)\n\s*\1 — causes O(n^2) when backreference fails.
    """
    backref_re = re.compile(r"\.\*[?]?.*\\[1-9]")
    if backref_re.search(pattern):
        return "backreference after .* or .*? — causes quadratic backtracking when backreference fails to match"
    return None


def _check_star_in_repetition(pattern: str) -> str | None:
    """Check for quantifier applied to a group containing .* or .+.

    Pattern like: (.*|foo)+ or (?:.*,)+
    """
    # Look for groups followed by + or *, then check if .* or .+ is inside.
    # Use a simple find-group approach to avoid backtracking regex (S8786).
    group_re = re.compile(r"\(([^)]+)\)[+*]")
    for m in group_re.finditer(pattern):
        group_body = m.group(1)
        if ".*" in group_body or ".+" in group_body:
            return "unbounded repetition inside repeated group — causes exponential backtracking"
    return None


def _pair_overlaps(alt_a: str, alt_b: str) -> bool:
    """Check if two alternation branches could overlap."""
    if not alt_a or not alt_b:
        return False
    if alt_a[0] == alt_b[0]:
        return True
    return bool(re.match(r"\\[wdWD]", alt_a) and re.match(r"\\[wdWD]", alt_b))


def _branches_overlap(alternatives: list[str]) -> bool:
    """Check if any two alternation branches share prefix or character class."""
    from itertools import combinations

    return any(_pair_overlaps(a, b) for a, b in combinations(alternatives, 2))


def _check_overlapping_alternation(pattern: str) -> str | None:
    r"""Check for alternation where branches overlap and are quantified.

    Pattern like: (a|ab)+ or (\\w+|\\d+)+
    """
    # Quantified group with alternation where branches share prefix/class.
    # Use [^)|]+ to avoid backtracking between alternatives (S8786).
    alt_quant_re = re.compile(r"\(([^)|]+(?:\|[^)|]+)+)\)[+*]")
    m = alt_quant_re.search(pattern)
    if not m:
        return None
    alternatives = m[1].split("|")
    if len(alternatives) < 2:
        return None
    overlap_reason = _branches_overlap(alternatives)
    if overlap_reason:
        return f"overlapping alternation '{m[0]}' with quantifier — branches overlap"
    return None


# All checks in priority order
_CHECKS = [
    _check_nested_quantifier,
    _check_star_in_repetition,
    _check_adjacent_overlapping_quantifiers,
    _check_backreference_after_dotstar,
    _check_overlapping_alternation,
]


def _analyze_pattern(pattern: str) -> str | None:
    """Run all checks on a regex pattern. Return first finding or None."""
    for check in _CHECKS:
        if result := check(pattern):
            return result
    return None


def _extract_python_regexes(content: str) -> list[tuple[str, int]]:
    """Extract regex patterns from Python source with line numbers."""
    results = []
    for m in _PY_RE_COMPILE.finditer(content):
        pattern = m.group(2)
        line = content[: m.start()].count("\n") + 1
        results.append((pattern, line))
    return results


def _extract_shell_regexes(content: str) -> list[tuple[str, int]]:
    """Extract regex patterns from shell scripts (grep -E/-P)."""
    results = []
    for m in _SH_GREP_CMD.finditer(content):
        # Find the quoted pattern argument after the grep command
        rest = content[m.end():]
        arg_match = _SH_GREP_ARG.search(rest)
        if arg_match and arg_match.start() < 80:  # pattern should be nearby
            pattern = arg_match.group(1)
            line = content[: m.start()].count("\n") + 1
            results.append((pattern, line))
    return results


def scan_file(file_path: Path, repo_root: Path) -> list[RegexFinding]:
    """Scan a file for unsafe regex patterns."""
    findings: list[RegexFinding] = []
    rel_path = str(file_path.relative_to(repo_root))

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    # Quick check — skip files without regex usage
    if "re.compile" not in content and "grep" not in content:
        return findings

    lines = content.split("\n")
    suffix = file_path.suffix

    # Extract regex patterns
    if suffix == ".py":
        regex_list = _extract_python_regexes(content)
    elif suffix == ".sh":
        regex_list = _extract_shell_regexes(content)
    else:
        return findings

    for pattern, line_num in regex_list:
        # Check suppression on the same or preceding line
        context_start = max(0, line_num - 2)
        context_end = min(len(lines), line_num + 1)
        context_text = "\n".join(lines[context_start:context_end])
        if _SUPPRESSION_RE.search(context_text):
            continue

        if reason := _analyze_pattern(pattern):
            findings.append(RegexFinding(rel_path, line_num, pattern, reason))

    return findings


def _collect_findings(scan_dir: Path, repo_root: Path) -> list[RegexFinding]:
    """Scan all Python and shell files under scan_dir."""
    findings: list[RegexFinding] = []
    skip_parts = ("target/", "node_modules/", ".venv/", "__pycache__/", "tests/")
    for ext in ("*.py", "*.sh"):
        for file_path in sorted(scan_dir.rglob(ext)):
            # Skip build artifacts, virtual environments, and test fixtures
            rel = str(file_path.relative_to(scan_dir))
            if all(part not in rel for part in skip_parts):
                findings.extend(scan_file(file_path, repo_root))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect unsafe regex patterns prone to catastrophic backtracking (Rule 10)."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(REPO_ROOT / "tools"),
        help="Directory to scan (default: tools/)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on findings (default: advisory exit 0)",
    )
    args = parser.parse_args()

    scan_dir = validate_read_path(args.directory, must_exist=True, purpose="scan directory")
    if not scan_dir.is_dir():
        print(f"ERROR: not a directory: {scan_dir}", file=sys.stderr)
        return 1

    if findings := _collect_findings(scan_dir, scan_dir):
        for f in findings:
            print(f, file=sys.stderr)
        count = len(findings)
        if args.strict:
            print(f"FAIL: found {count} unsafe regex pattern(s)", file=sys.stderr)
            return 1
        print(f"WARN: found {count} unsafe regex pattern(s) (advisory)", file=sys.stderr)
    else:
        print("OK: no unsafe regex patterns found", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
