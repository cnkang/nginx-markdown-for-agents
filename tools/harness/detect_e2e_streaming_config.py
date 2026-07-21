#!/usr/bin/env python3
"""detect_e2e_streaming_config.py — Block-aware detection of contradictory streaming config.

Rule 60 (e2e-runner): E2E nginx.conf location blocks that use
markdown_cache_validation full must have an explicit markdown_streaming
directive. Using the implicit default (auto) with a blocking directive
generates a startup warning and obscures the test's intent.

Detection strategy:
  1. Parse nginx config content (from shell heredocs or Rust string literals)
     into logical location blocks using brace-depth tracking.  Comment text
     is masked *before* brace parsing so that ``# example: location /x {``
     does not inflate the location depth or satisfy a parent location's
     directive check.
  2. Within each location block, only directives at the block's *direct*
     depth are considered — nested ``location`` sub-blocks do not satisfy the
     parent.  Nested sub-blocks are checked independently.
  3. Strip comments (both # line comments in nginx config and /* */ in Rust).
  4. Flag blocks missing the directive (implicit auto) or having explicit
     auto without a documented-intent comment.
  5. Read/parse errors (file unreadable, unmatched brace, malformed heredoc)
     surface as scan errors.  In --strict mode they cause a non-zero exit.

This detector fails closed: a read or parse error never silently produces
"no findings".  The scanner reports the error and, under --strict, fails.

Usage:
    python3 tools/harness/detect_e2e_streaming_config.py [directory] [--strict]

Exit codes:
    0 — no findings (or, in non-strict mode, findings reported as warnings)
    1 — findings in --strict mode, scan errors in --strict mode, or usage error
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns for detecting directives in nginx config text (applied to
# comment-stripped, direct-depth block content).
_RE_CACHE_VALIDATION_FULL = re.compile(
    r"^\s*markdown_cache_validation\s+full\s*;", re.MULTILINE
)
_RE_STREAMING_DIRECTIVE = re.compile(
    r"^\s*markdown_streaming\s+(\w+)\s*;", re.MULTILINE
)
# Intentional-use comment pattern (case-insensitive).  Matches comments that
# document why auto + full is deliberately used.
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
# Location block start (applied to comment-masked structural text).  Use
# [ \t]* rather than \s* so the leading-whitespace match does not span
# newlines (which would make match.start() point at an earlier line and
# break line-number accounting).
_RE_LOCATION_START = re.compile(
    r"^[ \t]*location\s+([^\s{]+)\s*\{", re.MULTILINE
)


@dataclass
class Finding:
    """A single detector finding (advisory or strict-mode blocking)."""
    file_path: str
    line: int
    loc_path: str
    message: str

    def __str__(self) -> str:
        return f"WARN: {self.file_path}:{self.line}: location {self.loc_path}: {self.message}"


@dataclass
class ScanError:
    """A non-silent scanner error (read failure, parse error, etc.)."""
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"SCAN_ERROR: {self.file_path}:{self.line}: {self.message}"


# ---------------------------------------------------------------------------
# Comment masking (used before brace parsing and directive detection)
# ---------------------------------------------------------------------------

def _mask_nginx_comments(text: str) -> str:
    """Return a copy of ``text`` with ``#`` comment content replaced by spaces.

    Preserves line structure (newlines and lengths) so that offset-to-line
    mapping against the original text remains valid.  Quoted strings are
    respected.  The masked text is used for structural scanning; the original
    text is retained for intentional-comment detection and error reporting.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_quote: str | None = None
    while i < n:
        ch = text[i]
        if in_quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            i = _mask_comment_run(text, i, n, out)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _mask_comment_run(
    text: str, i: int, n: int, out: list[str],
) -> int:
    """Replace a ``#`` comment run with spaces; return index after the run."""
    j = i
    while j < n and text[j] != "\n":
        j += 1
    out.append(" " * (j - i))
    return j


def _strip_comment_from_line(line: str) -> str:
    """Strip trailing # comment from a single nginx config line.

    Respects quoted strings so # inside quotes is not treated as comment
    start.  Used for directive-detection line scanning.
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
    """Remove # line comments from nginx config text, preserving line count."""
    return "\n".join(_strip_comment_from_line(line) for line in text.split("\n"))


# ---------------------------------------------------------------------------
# Brace-matching on masked (comment-free) text
# ---------------------------------------------------------------------------

def _skip_quoted_string(text: str, pos: int) -> int:
    """Advance past a quoted string starting at pos (the quote character)."""
    quote_char = text[pos]
    i = pos + 1
    while i < len(text) and text[i] != quote_char:
        if text[i] == "\\":
            i += 1
        i += 1
    return i + 1


def _find_matching_brace(text: str, start_pos: int) -> int:
    """Find the position after the matching closing brace.

    ``text`` should already be comment-masked so braces inside comments do
    not affect depth.  Returns the index after the closing brace, or -1 if
    unmatched.
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


# ---------------------------------------------------------------------------
# Location block extraction (uses masked text for structure, original for
# content/intentional-comment detection)
# ---------------------------------------------------------------------------

@dataclass
class _LocationBlock:
    """A single location block extracted from nginx config text."""
    path: str
    # Byte offsets into the ORIGINAL config text.
    content_start: int
    content_end: int
    line_number: int  # 1-based line of the location directive


def _extract_location_blocks(
    text: str, masked: str,
) -> tuple[list[_LocationBlock], list[ScanError]]:
    """Extract location blocks from nginx config text.

    ``text`` is the original config text (used for offset→content mapping).
    ``masked`` is the comment-masked version (used for structural scanning).

    Returns (blocks, errors).  Unmatched opening braces produce ScanErrors.
    """
    blocks: list[_LocationBlock] = []
    errors: list[ScanError] = []
    consumed_offsets: set[int] = set()  # avoid double-reporting nested
    for match in _RE_LOCATION_START.finditer(masked):
        loc_path = match.group(1)
        # Compute the 1-based line number from the masked text offset.
        line_num = masked[: match.start()].count("\n") + 1
        end_pos = _find_matching_brace(masked, match.end())
        if end_pos < 0:
            errors.append(ScanError(
                file_path="", line=line_num,
                message=(
                    f"unmatched opening brace for location {loc_path} "
                    "(no matching '}')"
                ),
            ))
            continue
        # Map masked offsets back to original-text offsets.  Because masking
        # preserves lengths and offsets, masked offsets == original offsets.
        content_start = match.end()
        content_end = end_pos - 1
        # Skip nested locations whose open brace is inside an already-recorded
        # outer block — we still want them checked independently, so do NOT
        # skip.  The caller iterates over all blocks and uses direct-depth
        # extraction for each.
        blocks.append(_LocationBlock(
            path=loc_path,
            content_start=content_start,
            content_end=content_end,
            line_number=line_num,
        ))
        consumed_offsets.add(match.start())
    return blocks, errors


def _extract_direct_depth_block(
    text: str, block: _LocationBlock,
) -> tuple[str, list[ScanError]]:
    """Return the block's direct-depth content with nested blocks removed.

    Nested ``location`` (or any ``{ ... }``) sub-blocks are replaced with
    blank lines so that directive regexes operating on the result only see
    directives at the immediate depth of this block.
    """
    body = text[block.content_start:block.content_end]
    masked_body = _mask_nginx_comments(body)
    out_chars: list[str] = []
    errors: list[ScanError] = []
    i = 0
    n = len(masked_body)
    depth = 0
    while i < n:
        ch = masked_body[i]
        if ch == "{":
            i, depth = _handle_nested_brace(
                masked_body, i, n, depth, out_chars, errors,
            )
            continue
        if ch == "}" and depth > 0:
            depth -= 1
        out_chars.append(ch)
        i += 1
    return "".join(out_chars), errors


def _handle_nested_brace(
    masked_body: str, i: int, n: int, depth: int,
    out_chars: list[str], errors: list[ScanError],
) -> tuple[int, int]:
    """Process a ``{`` character.  Returns (new_i, new_depth)."""
    if depth == 0:
        close = _find_matching_brace(masked_body, i + 1)
        if close < 0:
            errors.append(ScanError(
                file_path="", line=0,
                message="unmatched nested opening brace in location block",
            ))
            out_chars.extend("\n" if masked_body[k] == "\n" else " " for k in range(i, n))
            return n, depth
        out_chars.extend(
            "\n" if masked_body[k] == "\n" else " " for k in range(i, close)
        )
        return close, depth
    out_chars.append(masked_body[i])
    return i + 1, depth + 1


# ---------------------------------------------------------------------------
# Intentional-comment preamble gathering
# ---------------------------------------------------------------------------

def _gather_preamble(lines: list[str], location_line_num: int) -> str:
    """Gather comment/blank lines immediately before a location directive."""
    preamble_lines: list[str] = []
    idx = location_line_num - 2
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped.startswith("#") or stripped == "":
            preamble_lines.insert(0, lines[idx])
            idx -= 1
        else:
            break
    return "\n".join(preamble_lines)


# ---------------------------------------------------------------------------
# Rust / shell nginx-config extraction
# ---------------------------------------------------------------------------

def _extract_nginx_from_rust(
    content: str, file_path: str,
) -> tuple[list[tuple[str, int]], list[ScanError]]:
    """Extract nginx config from Rust raw strings or escaped string literals."""
    configs: list[tuple[str, int]] = []
    errors: list[ScanError] = []
    # Match Rust raw strings: r#"..."#, r##"..."##, etc.
    # nosec:regex-safety -- bounded Rust raw-string fence, applied to a single
    # source file with deterministic fence matching; no nested quantifiers.
    raw_string_re = re.compile(r'r(#+)"(.*?)\1"', re.DOTALL)
    for m in raw_string_re.finditer(content):
        base_line = content[: m.start()].count("\n") + 1
        configs.append((m.group(2), base_line))

    # Match regular string literals (quoted with ") that contain escaped
    # newlines.  Use a manual scanner that respects ``\"`` escapes so the
    # string can span multiple physical lines (Rust line-continuation with
    # a trailing backslash-newline is part of the literal).
    configs.extend(_extract_rust_escaped_strings(content))
    return configs, errors


def _extract_rust_escaped_strings(
    content: str,
) -> list[tuple[str, int]]:
    """Scan for Rust ``"..."`` string literals containing ``\\n``.

    Handles escaped quotes (``\\\"``) so multi-line strings joined with a
    trailing backslash are captured as a single literal.  The resulting text
    has escape sequences unescaped, ``{{``/``}}`` converted to ``{``/``}``,
    and Rust format-argument braces (``{}``, ``{name}``) masked.
    """
    configs: list[tuple[str, int]] = []
    i = 0
    n = len(content)
    while i < n:
        if content[i] != '"':
            i += 1
            continue
        start = i
        base_line = content[:start].count("\n") + 1
        j, raw = _scan_rust_string(content, i, n)
        if j <= start:
            i += 1
            continue
        i = j
        if "\\n" not in raw:
            continue
        unescaped = _unescape_rust_string_body(raw)
        if "location" in unescaped and "markdown_" in unescaped:
            configs.append((unescaped, base_line))
    return configs


def _unescape_rust_string_body(raw: str) -> str:
    """Unescape a Rust string literal body for nginx-config analysis.

    Handles: ``\\n`` → newline, ``\\t`` → tab, ``\\"`` → quote, ``\\\\`` →
    backslash, and Rust line-continuation (``\\`` immediately followed by a
    real newline) which is removed.  Also converts Rust brace-doubling
    (``{{``/``}}``) to single braces and masks format-argument braces.
    """
    # Rust line continuation: backslash immediately followed by a real
    # newline is removed (joins the physical lines).
    text = raw.replace("\\\n", "")
    text = (text.replace("\\n", "\n")
              .replace("\\t", "\t")
              .replace('\\"', '"')
              .replace("\\\\", "\\"))
    text = text.replace("{{", "{").replace("}}", "}")
    return _mask_rust_format_args(text)


def _scan_rust_string(
    content: str, start: int, n: int,
) -> tuple[int, str]:
    """Scan a ``"..."`` string literal starting at ``start``.

    Returns (index_after_close, raw_body) or (start, "") if not a string.
    Respects ``\\`` escapes including ``\\"``.
    """
    i = start + 1
    body: list[str] = []
    while i < n:
        ch = content[i]
        if ch == "\\" and i + 1 < n:
            body.append(content[i:i + 2])
            i += 2
            continue
        if ch == '"':
            return i + 1, "".join(body)
        body.append(ch)
        i += 1
    return start, ""


def _mask_rust_format_args(text: str) -> str:
    """Replace Rust format-argument braces (``{}``, ``{name}``) with a token.

    These are Rust format placeholders, not nginx config braces.  Only short
    placeholders (no newlines, no semicolons, no spaces) are masked so that
    multi-line nginx ``{ ... ; ... }`` blocks are preserved for brace-depth
    tracking.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            j = i + 1
            while j < n and text[j] not in "{};\n ":
                j += 1
            if j < n and text[j] == "}" and j == i + 1:
                # Empty ``{}`` placeholder.
                out.append("_")
                i = j + 1
                continue
            if j < n and text[j] == "}" and j > i + 1:
                # ``{name}`` placeholder — only if content is a Rust ident.
                inner = text[i + 1:j]
                if inner.replace("_", "").isalnum():
                    out.append("_")
                    i = j + 1
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_nginx_from_shell(
    content: str, file_path: str,
) -> tuple[list[tuple[str, int]], list[ScanError]]:
    """Extract nginx config from shell heredocs and inline strings."""
    configs: list[tuple[str, int]] = []
    errors: list[ScanError] = []
    # Two-phase heredoc extraction to avoid super-linear backtracking (S8786):
    # nosec:regex-safety -- bounded heredoc opener, deterministic delimiter.
    heredoc_open_re = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\s*\n")
    found_heredoc = False
    for m in heredoc_open_re.finditer(content):
        delimiter = m.group(1)
        body_start = m.end()
        # nosec:regex-safety -- anchored single-delimiter line match.
        close_re = re.compile(r"^\s*" + re.escape(delimiter) + r"\s*$", re.MULTILINE)
        close_match = close_re.search(content, body_start)
        if not close_match:
            errors.append(ScanError(
                file_path=file_path, line=0,
                message=(
                    f"unterminated heredoc with delimiter '{delimiter}' — "
                    "no closing line found"
                ),
            ))
            continue
        found_heredoc = True
        body = content[body_start : close_match.start()]
        if "location" in body and "markdown_" in body:
            base_line = content[: m.start()].count("\n") + 1
            configs.append((body, base_line))

    if not configs and not found_heredoc and "location" in content \
            and "markdown_cache_validation" in content:
        configs.append((content, 1))
    return configs, errors


# ---------------------------------------------------------------------------
# Per-block checking
# ---------------------------------------------------------------------------

def _check_block(
    loc_path: str,
    direct_content: str,
    original_block: str,
    file_path: str,
    base_line: int,
    block_line: int,
) -> Finding | None:
    """Check a single location block for contradictory streaming config.

    ``direct_content`` is the comment-stripped, nested-block-removed block
    body (only directives at this location's direct depth remain).
    ``original_block`` is the original block content (with preamble) for
    intentional-comment detection.
    """
    if not _RE_CACHE_VALIDATION_FULL.search(direct_content):
        return None

    abs_line = base_line + block_line - 1

    streaming_match = _RE_STREAMING_DIRECTIVE.search(direct_content)
    if not streaming_match:
        return Finding(
            file_path, abs_line, loc_path,
            "markdown_cache_validation full without explicit markdown_streaming. "
            "Implicit default is 'auto' which generates a startup warning. "
            "Add 'markdown_streaming off;' to clarify intent.",
        )

    mode = streaming_match.group(1)
    if mode == "auto":
        if _RE_INTENTIONAL_COMMENT.search(original_block):
            return None
        return Finding(
            file_path, abs_line, loc_path,
            "markdown_streaming auto + markdown_cache_validation full. "
            "This combination generates a startup warning. "
            "Use 'off' unless intentionally testing the runtime-block mechanism.",
        )
    return None


# ---------------------------------------------------------------------------
# File scanning (fail-closed)
# ---------------------------------------------------------------------------

def scan_file(
    file_path: Path, repo_root: Path,
) -> tuple[list[Finding], list[ScanError]]:
    """Scan a single file.  Returns (findings, errors).

    Read failures, malformed heredocs, and unmatched braces surface as
    ScanErrors.  The function never silently swallows OSError.
    """
    findings: list[Finding] = []
    errors: list[ScanError] = []
    try:
        rel_path = str(file_path.relative_to(repo_root))
    except ValueError:
        rel_path = str(file_path)

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        errors.append(ScanError(
            file_path=rel_path, line=0,
            message=f"cannot read file: {e}",
        ))
        return findings, errors

    if "markdown_cache_validation" not in content:
        return findings, errors

    config_sections, extract_errors = _extract_config_sections(file_path, content, rel_path)
    errors.extend(extract_errors)
    for config_text, base_line in config_sections:
        section_findings, section_errors = _scan_config_section(
            config_text, base_line, rel_path,
        )
        findings.extend(section_findings)
        errors.extend(section_errors)
    return findings, errors


def _extract_config_sections(
    file_path: Path, content: str, rel_path: str,
) -> tuple[list[tuple[str, int]], list[ScanError]]:
    suffix = file_path.suffix
    if suffix == ".rs":
        return _extract_nginx_from_rust(content, rel_path)
    if suffix == ".sh":
        return _extract_nginx_from_shell(content, rel_path)
    return [(content, 1)], []


def _scan_config_section(
    config_text: str, base_line: int, rel_path: str,
) -> tuple[list[Finding], list[ScanError]]:
    findings: list[Finding] = []
    errors: list[ScanError] = []
    masked = _mask_nginx_comments(config_text)
    blocks, block_errors = _extract_location_blocks(config_text, masked)
    errors.extend(
        ScanError(
            file_path=rel_path,
            line=base_line + be.line - 1 if be.line else 0,
            message=be.message,
        )
        for be in block_errors
    )
    source_lines = config_text.split("\n")
    for block in blocks:
        if "markdown_cache_validation" not in config_text[
            block.content_start:block.content_end
        ]:
            continue
        finding, block_errors = _scan_one_block(
            block, config_text, source_lines, base_line, rel_path,
        )
        if finding is not None:
            findings.append(finding)
        errors.extend(block_errors)
    return findings, errors


def _scan_one_block(
    block: _LocationBlock, config_text: str, source_lines: list[str],
    base_line: int, rel_path: str,
) -> tuple[Finding | None, list[ScanError]]:
    errors: list[ScanError] = []
    direct_content, direct_errors = _extract_direct_depth_block(
        config_text, block,
    )
    errors.extend(
        ScanError(
            file_path=rel_path,
            line=base_line + block.line_number - 1,
            message=de.message,
        )
        for de in direct_errors
    )
    stripped_direct = _strip_nginx_comments(direct_content)
    preamble = _gather_preamble(source_lines, block.line_number)
    original_block = preamble + "\n" + config_text[
        block.content_start:block.content_end
    ]
    finding = _check_block(
        block.path, stripped_direct, original_block,
        rel_path, base_line, block.line_number,
    )
    return finding, errors


def _collect_findings(
    scan_dir: Path,
) -> tuple[list[Finding], list[ScanError]]:
    """Scan all relevant files under scan_dir."""
    findings: list[Finding] = []
    errors: list[ScanError] = []

    e2e_dir = scan_dir / "tools" / "e2e"
    if e2e_dir.is_dir():
        for sh_file in sorted(e2e_dir.rglob("*.sh")):
            f, e = scan_file(sh_file, scan_dir)
            findings.extend(f)
            errors.extend(e)

    harness_dir = scan_dir / "tools" / "e2e-harness"
    if harness_dir.is_dir():
        for rs_file in sorted(harness_dir.rglob("*.rs")):
            if "target" in str(rs_file.relative_to(harness_dir)):
                continue
            f, e = scan_file(rs_file, scan_dir)
            findings.extend(f)
            errors.extend(e)

    examples_dir = scan_dir / "examples" / "nginx-configs"
    if examples_dir.is_dir():
        for conf_file in sorted(examples_dir.rglob("*.conf")):
            f, e = scan_file(conf_file, scan_dir)
            findings.extend(f)
            errors.extend(e)
    return findings, errors


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

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
        help=(
            "Exit 1 on findings or scan errors (read failures, parse "
            "errors, unmatched braces).  Default is advisory exit 0."
        ),
    )
    args = parser.parse_args()

    scan_dir = validate_read_path(args.directory, must_exist=True, purpose="scan directory")
    if not scan_dir.is_dir():
        print(f"ERROR: not a directory: {scan_dir}", file=sys.stderr)
        return 1

    findings, errors = _collect_findings(scan_dir)

    # Print scan errors first (always, regardless of strict).
    for e in errors:
        print(e, file=sys.stderr)
    for f in findings:
        print(f, file=sys.stderr)

    len(findings) > 0 or len(errors) > 0

    if args.strict:
        if errors:
            print(
                f"FAIL: {len(errors)} scan error(s) encountered",
                file=sys.stderr,
            )
            return 1
        if findings:
            count = len(findings)
            print(
                f"FAIL: found {count} contradictory E2E streaming config(s)",
                file=sys.stderr,
            )
            return 1
        print("OK: no contradictory E2E streaming configs found", file=sys.stderr)
        return 0

    if findings:
        print(
            f"WARN: found {len(findings)} contradictory E2E streaming "
            f"config(s) (advisory)",
            file=sys.stderr,
        )
    elif errors:
        print(
            f"WARN: {len(errors)} scan error(s) encountered (advisory)",
            file=sys.stderr,
        )
    else:
        print("OK: no contradictory E2E streaming configs found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())