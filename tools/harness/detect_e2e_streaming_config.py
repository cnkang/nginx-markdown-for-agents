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
# Supports modifiers: location = /path {, location ^~ /prefix/ {,
# location ~ \.html$ {, location ~* \.(png|jpg)$ {, location @named {
_RE_LOCATION_START = re.compile(
    r"^[ \t]*location\s+(?:(?:=|~\*?|\^~)\s+)?([^\s{]+)\s*\{", re.MULTILINE
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
        blocks.append(_LocationBlock(
            path=loc_path,
            content_start=content_start,
            content_end=content_end,
            line_number=line_num,
        ))
    return blocks, errors


def _extract_direct_depth_block(
    text: str, block: _LocationBlock,
) -> tuple[str, list[ScanError]]:
    """Return the block's direct-depth content with nested blocks removed.

    Nested ``location`` (or any ``{ ... }``) sub-blocks are replaced with
    blank lines so that directive regexes operating on the result only see
    directives at the immediate depth of this block.

    Quoted strings are respected so that braces inside quotes (e.g.
    ``set $value "{";``) do not trigger nested-block detection.
    """
    body = text[block.content_start:block.content_end]
    masked_body = _mask_nginx_comments(body)
    result, errors = _mask_nested_blocks(masked_body, masked_body)
    return result, errors


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
    content: str, _file_path: str,  # kept for API symmetry with _extract_nginx_from_shell
) -> tuple[list[tuple[str, int]], list[ScanError]]:
    """Extract nginx config from Rust raw strings or escaped string literals."""
    configs: list[tuple[str, int]] = []
    errors: list[ScanError] = []

    # Scan for Rust raw strings using a deterministic character scanner.
    # Supports: r"...", r#"..."#, r##"..."##, r###"..."###, br"...", br#"..."#
    raw_results, raw_errors = _scan_rust_raw_strings(content)
    configs.extend(raw_results)
    errors.extend(raw_errors)

    # Match regular string literals (quoted with ") that contain escaped
    # newlines.  Use a manual scanner that respects ``\"`` escapes so the
    # string can span multiple physical lines (Rust line-continuation with
    # a trailing backslash-newline is part of the literal).
    configs.extend(_extract_rust_escaped_strings(content))
    return configs, errors


# ---------------------------------------------------------------------------
# Rust string scanning: verbose comments + extraction pipeline
# ---------------------------------------------------------------------------


def _scan_rust_raw_strings(
    content: str,
) -> tuple[list[tuple[str, int]], list[ScanError]]:
    """Scan for Rust raw string literals and candidate nginx config bodies.

    This is a two-pass-ish reader: strip comments first, then extract
    ``r"..."``/``br"..."`` bodies with correct hash-counted fences.
    Unterminated raw strings are reported as ``ScanError``; successful
    matches are filtered to only likely nginx config later.
    """
    configs: list[tuple[str, int]] = []
    errors: list[ScanError] = []
    i = 0
    n = len(content)

    while i < n:
        # Skip Rust comments
        skip_end = _try_skip_rust_comment(content, i, n)
        if skip_end > i:
            i = skip_end
            continue

        raw_start, hash_count, body_start = _try_match_raw_prefix(content, i, n)
        if raw_start < 0:
            i += 1
            continue

        i = _process_raw_string_match(
            content, raw_start, hash_count, body_start, n, configs, errors,
        )
    return configs, errors


def _try_skip_rust_comment(content: str, i: int, n: int) -> int:
    """If position i starts a Rust comment, return end index; else return i."""
    if i + 1 >= n or content[i] != '/':
        return i
    if content[i + 1] == '/':
        return _skip_to_eol(content, i, n)
    if content[i + 1] == '*':
        return _skip_rust_block_comment(content, i, n)
    return i


def _process_raw_string_match(
    content: str, raw_start: int, hash_count: int,
    body_start: int, n: int,
    configs: list[tuple[str, int]], errors: list[ScanError],
) -> int:
    """Process a matched raw string prefix and extract/report the body.

    Returns the new scanner position after the raw string (or body_start on error).
    """
    close_idx = _find_raw_string_close(content, body_start, n, hash_count)
    if close_idx < 0:
        line_num = content[:raw_start].count("\n") + 1
        errors.append(ScanError(
            file_path="",
            line=line_num,
            message=(
                f"unterminated Rust raw string (expected closing "
                f"'\"{'#' * hash_count}') starting here"
            ),
        ))
        return body_start

    body = content[body_start:close_idx]
    base_line = content[:raw_start].count("\n") + 1
    if "location" in body and "markdown_" in body:
        configs.append((body, base_line))
    return close_idx + 1 + hash_count


def _try_match_raw_prefix(
    content: str, i: int, n: int,
) -> tuple[int, int, int]:
    """Try to match a Rust raw string prefix at position i.

    Returns (start_pos, hash_count, body_start) or (-1, 0, 0) if no match.
    start_pos is the index of 'r' (or 'b' for byte strings).
    body_start is the index of the first character after the opening quote.
    """
    pos = i
    # Optional 'b' prefix for byte raw strings
    if pos < n and content[pos] == 'b':
        pos += 1
    if pos >= n or content[pos] != 'r':
        return -1, 0, 0
    # Check that 'r' is not part of an identifier (preceded by alphanumeric/_)
    if i > 0 and (content[i - 1].isalnum() or content[i - 1] == '_'):
        return -1, 0, 0
    pos += 1  # past 'r'
    # Count '#' characters
    hash_count = 0
    while pos < n and content[pos] == '#':
        hash_count += 1
        pos += 1
    # Must have opening quote
    if pos >= n or content[pos] != '"':
        return -1, 0, 0
    pos += 1  # past opening '"'
    return i, hash_count, pos


def _find_raw_string_close(
    content: str, body_start: int, n: int, hash_count: int,
) -> int:
    """Find the closing fence of a Rust raw string.

    The closing fence is: '"' followed by exactly hash_count '#' characters.
    Returns the index of the closing '"', or -1 if not found.
    """
    i = body_start
    while i < n:
        if content[i] == '"':
            # Check if followed by the right number of hashes
            j = i + 1
            count = 0
            while j < n and content[j] == '#' and count < hash_count:
                count += 1
                j += 1
            if count == hash_count:
                return i
        i += 1
    return -1


def _skip_to_eol(content: str, i: int, n: int) -> int:
    """Skip to end of line (past \\n or to end of content)."""
    while i < n and content[i] != '\n':
        i += 1
    return i + 1 if i < n else i


def _skip_rust_block_comment(content: str, i: int, n: int) -> int:
    """Skip a Rust /* ... */ block comment (supports nesting)."""
    depth = 1
    i += 2  # past /*
    while i < n - 1 and depth > 0:
        if content[i] == '/' and content[i + 1] == '*':
            depth += 1
            i += 2
        elif content[i] == '*' and content[i + 1] == '/':
            depth -= 1
            i += 2
        else:
            i += 1
    return i


def _extract_rust_escaped_strings(
    content: str,
) -> list[tuple[str, int]]:
    """Extract nginx config from Rust ``\"...\"`` string literals.

    Rust escaped string literals are only interesting here when they span
    physical lines via ``\\n`` continuations.  Extracted bodies are
    unescaped and then passed through nginx-config analysis.
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


def _try_mask_format_brace(text: str, i: int, n: int) -> int | None:
    """Try to mask a Rust format brace at position *i*.

    Returns the new index past the placeholder if ``text[i]`` starts a Rust
    format placeholder (``{}``, ``{name}``), or ``None`` if it does not.
    """
    j = i + 1
    while j < n and text[j] not in "{};\n ":
        j += 1
    if j >= n or text[j] != "}":
        return None
    if j == i + 1:
        # Empty ``{}`` placeholder.
        return j + 1
    # ``{name}`` placeholder — only if content is a valid Rust identifier.
    inner = text[i + 1:j]
    return j + 1 if inner.replace("_", "").isalnum() else None


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
        if text[i] == "{":
            end = _try_mask_format_brace(text, i, n)
            if end is not None:
                out.append("_")
                i = end
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _extract_nginx_from_shell(
    content: str, file_path: str,
) -> tuple[list[tuple[str, int]], list[ScanError]]:
    """Extract nginx config from shell heredocs and inline strings.

    ``content`` is the full text of a shell file.  The scanner only
    considers ``cat <<EOF ... EOF``-style heredocs and the rare case where
    inline file content already contains nginx config.  Directive/brace
    parsing happens later; this stage merely extracts candidate config
    bodies.  Read/parse problems surface as ``ScanError`` and fail-strict
    later in the pipeline.
    """
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
# Finding emission / scanning helpers
# ---------------------------------------------------------------------------

def _check_block(
    loc_path: str,
    direct_content: str,
    original_block: str,
    file_path: str,
    base_line: int,
    block_line: int,
) -> Finding | None:
    """Apply Rule 60 checks to a single location block.

    ``direct_content`` is the comment-stripped, nested-block-removed body
    for directive detection.  ``original_block`` preserves comments for
    intentional-use exemption checks.  Returns ``None`` when the block
    does not contradict test intent or is explicitly exempt.
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
# File scanning / fail-closed I/O
# ---------------------------------------------------------------------------

def scan_file(
    file_path: Path, repo_root: Path,
) -> tuple[list[Finding], list[ScanError]]:
    """Scan one file and return ``(findings, scan_errors)``.

    This pipeline is fail-closed by design: read failures, malformed
    heredocs, and unmatched braces become ``ScanError`` objects that
    callers/Make targets can surface instead of silently reporting
    ``no findings``.
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
    # For intentional-comment detection, use only the preamble and the
    # direct-depth original text (before comment stripping).  This prevents
    # comments inside nested sub-blocks from exempting the parent.
    original_direct = _extract_direct_depth_original(config_text, block)
    original_block = preamble + "\n" + original_direct
    finding = _check_block(
        block.path, stripped_direct, original_block,
        rel_path, base_line, block.line_number,
    )
    return finding, errors


def _extract_direct_depth_original(
    text: str, block: _LocationBlock,
) -> str:
    """Return the block's direct-depth content WITH comments but WITHOUT nested blocks.

    This is used for intentional-comment detection so that comments inside
    nested locations do not exempt the parent block.
    """
    body = text[block.content_start:block.content_end]
    masked_body = _mask_nginx_comments(body)
    result, _ = _mask_nested_blocks(masked_body, body)
    return result


# ---------------------------------------------------------------------------
# Shared nested-block masking core
# ---------------------------------------------------------------------------

def _mask_nested_blocks(
    structural: str, output_source: str,
) -> tuple[str, list[ScanError]]:
    """Mask nested ``{ ... }`` blocks, preserving newlines as blank lines.

    ``structural`` is used for brace/quote detection (comment-masked text).
    ``output_source`` determines which text is emitted for non-masked chars
    (may be the same as structural, or the original text with comments).

    Returns (result_text, errors).
    """
    out_chars: list[str] = []
    errors: list[ScanError] = []
    i = 0
    n = len(structural)
    while i < n:
        ch = structural[i]
        if ch in ('"', "'"):
            end = _skip_quoted_string(structural, i)
            out_chars.append(output_source[i:end])
            i = end
            continue
        if ch == "{":
            i = _mask_one_nested_block(structural, output_source, i, n, out_chars, errors)
            continue
        out_chars.append(output_source[i])
        i += 1
    return "".join(out_chars), errors


def _mask_one_nested_block(
    structural: str, output_source: str, i: int, n: int,
    out_chars: list[str], errors: list[ScanError],
) -> int:
    """Mask a single nested ``{...}`` block starting at position ``i``.

    Returns the new index after the masked block.
    """
    close = _find_matching_brace(structural, i + 1)
    if close < 0:
        errors.append(ScanError(
            file_path="", line=0,
            message="unmatched nested opening brace in location block",
        ))
        out_chars.extend(
            "\n" if output_source[k] == "\n" else " " for k in range(i, n)
        )
        return n
    out_chars.extend(
        "\n" if output_source[k] == "\n" else " " for k in range(i, close)
    )
    return close


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