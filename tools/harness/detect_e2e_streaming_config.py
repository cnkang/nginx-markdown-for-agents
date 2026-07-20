#!/usr/bin/env python3
"""detect_e2e_streaming_config.py — Block-aware detection of contradictory streaming config.

Rule 60 (e2e-runner): E2E nginx.conf location blocks that use
markdown_cache_validation full must have an explicit markdown_streaming
directive. Using the implicit default (auto) with a blocking directive
generates a startup warning and obscures the test's intent.

Detection strategy:
  1. Parse nginx config content (from shell heredocs or Rust string literals)
     into logical location blocks using brace-depth tracking.
  2. Strip comments (both # line comments in nginx config and /* */ in Rust).
  3. Within each location block, check whether markdown_cache_validation full
     appears alongside an explicit markdown_streaming directive.
  4. Flag blocks missing the directive (implicit auto) or having explicit auto
     without a documented-intent comment.

This replaces the shell-based fixed-window approach with true block-aware
parsing that avoids false negatives from adjacent blocks and false positives
from commented-out directives.

Usage:
    python3 tools/harness/detect_e2e_streaming_config.py [directory] [--strict]
      directory defaults to the repository root
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

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns for detecting directives in nginx config text
_RE_CACHE_VALIDATION_FULL = re.compile(
    r"^\s*markdown_cache_validation\s+full\s*;", re.MULTILINE
)
_RE_STREAMING_DIRECTIVE = re.compile(
    r"^\s*markdown_streaming\s+(\w+)\s*;", re.MULTILINE
)
# Intentional-use comment pattern (case-insensitive)
# Matches comments that document why auto + full is deliberately used
_RE_INTENTIONAL_COMMENT = re.compile(
    r"#.*(?:intentional|deliberately|runtime[.\-_]block|"
    r"forces?\s+full-buffer|selects?\s+the\s+full-buffer|"
    r"out\s+of\s+the\s+streaming\s+path|"
    r"validates?\s+(?:the\s+)?runtime[.\-_]block|"
    r"full.buffer\s+path\s+in\s+auto|"
    r"keeps?\s+.*out\s+of\s+the\s+streaming|"
    r"bypass(?:es?)?\s+streaming)",
    re.IGNORECASE,
)
# Location block start
_RE_LOCATION_START = re.compile(
    r"^\s*location\s+([^\s{]+)\s*\{", re.MULTILINE
)


def _strip_comment_from_line(line: str) -> str:
    """Strip trailing # comment from a single nginx config line.

    Respects quoted strings so # inside quotes is not treated as comment start.
    """
    in_quote = False
    quote_char = None
    for i, ch in enumerate(line):
        if in_quote:
            if ch == quote_char and (i == 0 or line[i - 1] != "\\"):
                in_quote = False
        elif ch in ('"', "'"):
            in_quote = True
            quote_char = ch
        elif ch == "#":
            return line[:i]
    return line


def _strip_nginx_comments(text: str) -> str:
    """Remove # line comments from nginx config text, preserving line structure."""
    return "\n".join(_strip_comment_from_line(line) for line in text.split("\n"))


def _gather_preamble(lines: list[str], location_line_num: int) -> str:
    """Gather comment/blank lines immediately before a location directive.

    Args:
        lines: All lines of the config text.
        location_line_num: 1-based line number of the location directive.

    Returns:
        Preamble text (joined comment/blank lines preceding the location).
    """
    preamble_lines: list[str] = []
    idx = location_line_num - 2  # 0-based index of line before location
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped.startswith("#") or stripped == "":
            preamble_lines.insert(0, lines[idx])
            idx -= 1
        else:
            break
    return "\n".join(preamble_lines)


def _skip_quoted_string(text: str, pos: int) -> int:
    """Advance past a quoted string starting at pos (the quote character).

    Returns the index after the closing quote.
    """
    quote_char = text[pos]
    i = pos + 1
    while i < len(text) and text[i] != quote_char:
        if text[i] == "\\":
            i += 1
        i += 1
    return i + 1  # past closing quote


def _find_matching_brace(text: str, start_pos: int) -> int:
    """Find the position after the matching closing brace.

    Args:
        text: Full text to scan.
        start_pos: Position after the opening brace.

    Returns:
        Position after the closing brace, or -1 if unmatched.
    """
    depth = 1
    i = start_pos
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch in ('"', "'"):
            i = _skip_quoted_string(text, i)
            continue
        i += 1
    return i if depth == 0 else -1


def _extract_location_blocks(text: str) -> list[tuple[str, str, int, str]]:
    """Extract location blocks with brace-depth tracking.

    Returns list of (location_path, block_content, line_number, preamble) tuples.
    block_content includes everything between the opening { and closing }.
    line_number is 1-based line of the location directive.
    preamble is comment lines immediately preceding the location directive
    (for intentional-use comment detection).
    """
    blocks = []
    lines = text.split("\n")

    for match in _RE_LOCATION_START.finditer(text):
        loc_path = match.group(1)
        line_num = text[: match.start()].count("\n") + 1
        preamble = _gather_preamble(lines, line_num)

        end_pos = _find_matching_brace(text, match.end())
        if end_pos > 0:
            block_content = text[match.end() : end_pos - 1]
            blocks.append((loc_path, block_content, line_num, preamble))

    return blocks


def _extract_nginx_from_rust(content: str) -> list[tuple[str, int]]:
    """Extract nginx config from Rust raw strings or escaped string literals.

    Returns list of (config_text, base_line_offset) tuples.
    """
    configs = []
    # Match Rust raw strings: r#"..."#, r##"..."##, etc.
    # nosec:regex-safety — backreference is bounded by Rust raw-string syntax (short fence)
    raw_string_re = re.compile(r'r(#+)"(.*?)\1"', re.DOTALL)
    for m in raw_string_re.finditer(content):
        base_line = content[: m.start()].count("\n") + 1
        configs.append((m.group(2), base_line))

    # Match regular string literals (quoted with ") that contain escaped newlines.
    # Use a simple [^"]* pattern and filter for \\n in Python to avoid
    # super-linear backtracking (S8786).
    simple_str_re = re.compile(r'"([^"]*)"')
    for m in simple_str_re.finditer(content):
        raw = m.group(1)
        # Only process strings containing escaped newlines
        if "\\n" not in raw:
            continue
        # Unescape common sequences
        unescaped = (
            raw.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
        # Only consider if it looks like nginx config
        if "location" in unescaped and "markdown_" in unescaped:
            base_line = content[: m.start()].count("\n") + 1
            configs.append((unescaped, base_line))

    return configs


def _extract_nginx_from_shell(content: str) -> list[tuple[str, int]]:
    """Extract nginx config from shell heredocs and inline strings.

    Returns list of (config_text, base_line_offset) tuples.
    """
    configs = []
    # Two-phase heredoc extraction to avoid super-linear backtracking (S8786):
    # Phase 1: Find heredoc openings and capture the delimiter word.
    # Phase 2: For each opening, find the closing delimiter with a targeted search.
    heredoc_open_re = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n")
    for m in heredoc_open_re.finditer(content):
        delimiter = m.group(1)
        body_start = m.end()
        # Phase 2: find the closing delimiter on its own line
        close_re = re.compile(r"^\s*" + re.escape(delimiter) + r"\s*$", re.MULTILINE)
        close_match = close_re.search(content, body_start)
        if not close_match:
            continue
        body = content[body_start : close_match.start()]
        if "location" in body and "markdown_" in body:
            base_line = content[: m.start()].count("\n") + 1
            configs.append((body, base_line))

    # If no heredocs found but file contains nginx config inline
    # (some scripts build config with echo/printf), scan the whole file
    if not configs and "location" in content and "markdown_cache_validation" in content:
        configs.append((content, 1))

    return configs


class Finding:
    """Represents a single detector finding."""

    def __init__(self, file_path: str, line: int, loc_path: str, message: str):
        self.file_path = file_path
        self.line = line
        self.loc_path = loc_path
        self.message = message

    def __str__(self) -> str:
        return f"WARN: {self.file_path}:{self.line}: location {self.loc_path}: {self.message}"


def _check_block(
    loc_path: str,
    block_content: str,
    original_block: str,
    file_path: str,
    base_line: int,
    block_line: int,
) -> Finding | None:
    """Check a single location block for contradictory streaming config.

    Args:
        loc_path: The location path (e.g. /cache-full/)
        block_content: Comment-stripped block content (for directive detection)
        original_block: Original block content (for intentional-comment detection)
        file_path: Relative file path for reporting
        base_line: Line offset of the config text within the source file
        block_line: Line number of the location directive within the config text
    """
    # Check if this block has markdown_cache_validation full
    if not _RE_CACHE_VALIDATION_FULL.search(block_content):
        return None

    abs_line = base_line + block_line - 1

    # Check for explicit markdown_streaming directive
    streaming_match = _RE_STREAMING_DIRECTIVE.search(block_content)
    if not streaming_match:
        return Finding(
            file_path,
            abs_line,
            loc_path,
            "markdown_cache_validation full without explicit markdown_streaming. "
            "Implicit default is 'auto' which generates a startup warning. "
            "Add 'markdown_streaming off;' to clarify intent.",
        )

    # Has explicit directive — check if it's "auto" with full validation
    mode = streaming_match.group(1)
    if mode == "auto":
        # Check for intentional-use comment in the ORIGINAL (non-stripped) block
        if _RE_INTENTIONAL_COMMENT.search(original_block):
            return None
        return Finding(
            file_path,
            abs_line,
            loc_path,
            "markdown_streaming auto + markdown_cache_validation full. "
            "This combination generates a startup warning. "
            "Use 'off' unless intentionally testing the runtime-block mechanism.",
        )

    # Explicit off/force/etc. with full — this is fine
    return None


def scan_file(file_path: Path, repo_root: Path) -> list[Finding]:
    """Scan a single file for contradictory streaming configs."""
    findings: list[Finding] = []
    rel_path = str(file_path.relative_to(repo_root))

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    # Quick check: skip files without relevant directives
    if "markdown_cache_validation" not in content:
        return findings

    # Determine file type and extract nginx config sections
    suffix = file_path.suffix
    if suffix == ".rs":
        config_sections = _extract_nginx_from_rust(content)
    elif suffix == ".sh":
        config_sections = _extract_nginx_from_shell(content)
    else:
        # For .conf or other text files, treat as raw nginx config
        config_sections = [(content, 1)]

    for config_text, base_line in config_sections:
        # Extract location blocks with brace-depth tracking
        blocks = _extract_location_blocks(config_text)

        for loc_path, block_content, block_line, preamble in blocks:
            # Check if this block has cache_validation full
            if "markdown_cache_validation" not in block_content:
                continue

            # Strip comments for directive detection
            stripped_block = _strip_nginx_comments(block_content)
            # Keep original block + preamble for intentional-comment detection
            original_with_preamble = preamble + "\n" + block_content

            if finding := _check_block(
                loc_path,
                stripped_block,
                original_with_preamble,
                rel_path,
                base_line,
                block_line,
            ):
                findings.append(finding)

    return findings


def _collect_findings(scan_dir: Path) -> list[Finding]:
    """Scan all relevant files under scan_dir and collect findings."""
    findings: list[Finding] = []

    # Scan shell E2E scripts
    e2e_dir = scan_dir / "tools" / "e2e"
    if e2e_dir.is_dir():
        for sh_file in sorted(e2e_dir.rglob("*.sh")):
            findings.extend(scan_file(sh_file, scan_dir))

    # Scan Rust E2E harness
    harness_dir = scan_dir / "tools" / "e2e-harness"
    if harness_dir.is_dir():
        for rs_file in sorted(harness_dir.rglob("*.rs")):
            # Skip build artifacts
            if "target" in str(rs_file.relative_to(harness_dir)):
                continue
            findings.extend(scan_file(rs_file, scan_dir))

    # Scan nginx config examples that are used in E2E
    examples_dir = scan_dir / "examples" / "nginx-configs"
    if examples_dir.is_dir():
        for conf_file in sorted(examples_dir.rglob("*.conf")):
            findings.extend(scan_file(conf_file, scan_dir))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect contradictory E2E streaming config (Rule 60)."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(REPO_ROOT),
        help="Directory to scan (default: repository root)",
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

    if findings := _collect_findings(scan_dir):
        for f in findings:
            print(f, file=sys.stderr)
        count = len(findings)
        if args.strict:
            print(
                f"FAIL: found {count} contradictory E2E streaming config(s)",
                file=sys.stderr,
            )
            return 1
        print(
            f"WARN: found {count} contradictory E2E streaming config(s) (advisory)",
            file=sys.stderr,
        )
    else:
        print("OK: no contradictory E2E streaming configs found", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
