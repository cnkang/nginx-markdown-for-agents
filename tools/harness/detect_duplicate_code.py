#!/usr/bin/env python3
"""Duplicate Code Block Detection Script (Rule 31).

Scans C source files for duplicate code blocks, detecting two patterns
that indicate merge residuals or copy-paste duplication:

  (a) Non-adjacent duplicates: blocks of 5+ consecutive lines that
      appear more than once in the same file (non-adjacent).  These
      indicate copy-paste duplication that should be refactored into
      a shared helper.

  (b) Adjacent duplicates (merge residual): blocks of 3+ identical
      consecutive lines that are immediately repeated.  These are the
      classic merge-residual pattern where a merge tool duplicates a
      block of code, leaving two identical copies back-to-back.

Per AGENTS.md Rule 31, after merge or >500-line changes, verify no
duplicate adjacent blocks exist.  This detector automates that check.

Usage:
    python3 tools/harness/detect_duplicate_code.py [directory]
    python3 tools/harness/detect_duplicate_code.py components/nginx-module/src

Exit codes:
    0 — always (advisory); findings are printed as WARNING/INFO
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Minimum block sizes
NON_ADJACENT_MIN_LINES = 5  # 5+ identical consecutive lines, non-adjacent
ADJACENT_MIN_LINES = 3      # 3+ identical consecutive lines, immediately repeated

# Risk-semantic keywords for classifying duplicate blocks.
# A duplicate block is classified by matching keywords in its content.
# The first matching category wins; if none match, it falls through to
# "structural" (low priority).
#
# Keywords are kept specific: bare substrings like "alloc", "buffer",
# "free", "memcpy" would match far too many benign lines and inflate
# the "memory" direct-fix count.  Instead we anchor on the NGINX
# allocator/free function names and the explicit buffer-lifecycle
# helpers that genuinely indicate duplicated memory-management
# logic.
MEMORY_KEYWORDS = (
    "ngx_palloc", "ngx_pnalloc", "ngx_pcalloc",
    "ngx_alloc_chain_link", "ngx_create_temp_buf",
    "ngx_create_buf", "ngx_calloc_buf",
    "pool_val",  # project-specific header-plan allocation helper
)
ROLLBACK_KEYWORDS = (
    "undo->", "rollback", "orig_hash", "orig_value", "op_type",
    "PLAN_OP_", "snapshot", "precommit", "failopen", "replay",
)
FFI_VALIDATION_KEYWORDS = (
    "entry->key", "entry->value", "entry->key_len", "entry->value_len",
    "is_null", "NULL", "validate", "guard", "defensive",
)
POSTCOMMIT_KEYWORDS = (
    "pending_output", "main_terminal_sent", "last_buf", "NGX_AGAIN",
    "next_body_filter", "buffered", "terminal", "send_terminal",
    "send_closing", "postcommit",
)
STATE_MACHINE_KEYWORDS = (
    "make_decision", "state", "event", "STREAMING_", "PASSTHROUGH",
    "FULL_BUFFER", "PRE_COMMIT", "switch", "case NGX_HTTP_MD",
)
# Keywords that indicate the duplicate is just a function signature
# or variable declaration (structural, not semantic).
SIGNATURE_KEYWORDS = (
    "ngx_http_request_t", "ngx_http_markdown_", "const ",
    "static ngx_int_t", "static void", "static ngx_str_t",
    "ngx_chain_t", "ngx_buf_t", "ngx_int_t",
)
LOG_ONLY_KEYWORDS = (
    "ngx_log_error", "ngx_log_debug", "NGX_LOG_",
    "category=", "return NGX_ERROR",
)


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _is_noise_line(line: str) -> bool:
    """Return True for lines that should be excluded from comparison.

    Blank lines, single braces, and comment-only lines are excluded
    because they are structural noise that would inflate duplicate
    counts without representing real code duplication.
    """
    stripped = line.strip()
    if not stripped:
        return True
    if stripped in ("{", "}", "};"):
        return True
    if stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("//"):
        return True
    if stripped.startswith("#"):  # preprocessor directives
        return True
    return False


def _classify_duplicate(block_lines: list[str]) -> tuple[str, str]:
    """Classify a duplicate block by risk semantics.

    Returns a tuple of (category, action) where category is one of:
    memory, rollback, ffi-validation, postcommit, state-machine,
    log-only, signature, structural.

    Action is one of: direct-fix, needs-human-review, ignore-by-rule.
    """
    joined = " ".join(block_lines)

    # Check memory allocation/free duplicates first (highest priority)
    if any(kw in joined for kw in MEMORY_KEYWORDS):
        return ("memory", "direct-fix")

    # Check rollback/header mutation duplicates
    if any(kw in joined for kw in ROLLBACK_KEYWORDS):
        return ("rollback", "direct-fix")

    # Check FFI validation duplicates
    if any(kw in joined for kw in FFI_VALIDATION_KEYWORDS):
        return ("ffi-validation", "direct-fix")

    # Check post-commit / streaming state duplicates
    if any(kw in joined for kw in POSTCOMMIT_KEYWORDS):
        return ("postcommit", "direct-fix")

    # State machine dispatching — needs human judgment
    if any(kw in joined for kw in STATE_MACHINE_KEYWORDS):
        return ("state-machine", "needs-human-review")

    # Log-only duplicates — low priority, usually not worth extracting
    if any(kw in joined for kw in LOG_ONLY_KEYWORDS):
        return ("log-only", "ignore-by-rule")

    # Pure signature/declaration duplicates — C structural, default skip
    if all(kw in joined for kw in ["ngx_http_request_t"]) or \
       any(kw in joined for kw in SIGNATURE_KEYWORDS) and \
       not any(kw in joined for kw in MEMORY_KEYWORDS + ROLLBACK_KEYWORDS + POSTCOMMIT_KEYWORDS):
        return ("signature", "ignore-by-rule")

    return ("structural", "needs-human-review")


def _find_adjacent_duplicates(
    lines: list[str], rel: str,
) -> list[str]:
    """Find immediately-repeated blocks of 3+ identical consecutive lines.

    The merge-residual pattern: a block of N lines is immediately
    followed by the same N lines (back-to-back duplicate).

    Args:
        lines: Source lines (original, with line numbers preserved).
        rel: Display path for reporting.

    Returns:
        List of WARNING message strings.
    """
    warnings: list[str] = []
    n = len(lines)
    if n < ADJACENT_MIN_LINES * 2:
        return warnings

    i = 0
    while i < n:
        # Try block sizes from ADJACENT_MIN_LINES up to a reasonable max
        best_block = 0
        for block_size in range(ADJACENT_MIN_LINES, min(50, (n - i) // 2) + 1):
            block_a = [lines[j].strip() for j in range(i, i + block_size)]
            block_b_start = i + block_size
            block_b = [
                lines[j].strip()
                for j in range(block_b_start, block_b_start + block_size)
            ]
            # Skip if any line in block is noise
            if any(_is_noise_line(l) for l in block_a):
                break
            if block_a == block_b:
                best_block = block_size

        if best_block >= ADJACENT_MIN_LINES:
            content_preview = lines[i].strip()
            if len(content_preview) > 60:
                content_preview = content_preview[:57] + "..."
            warnings.append(
                f"  WARNING {rel}:{i + 1} — adjacent duplicate block "
                f"({best_block} lines immediately repeated, merge residual "
                f"per Rule 31): first line: {content_preview}"
            )
            # Skip past both blocks to avoid overlapping reports
            i += best_block * 2
        else:
            i += 1

    return warnings


def _find_non_adjacent_duplicates(
    lines: list[str], rel: str,
) -> list[str]:
    """Find blocks of 5+ consecutive lines appearing more than once.

    Non-adjacent duplicates indicate copy-paste duplication within the
    same file.  We use a rolling-hash approach for efficiency: compute
    a hash for every window of NON_ADJACENT_MIN_LINES lines, then
    report groups of matching windows.

    Args:
        lines: Source lines (original, with line numbers preserved).
        rel: Display path for reporting.

    Returns:
        List of INFO message strings.
    """
    infos: list[str] = []
    n = len(lines)
    if n < NON_ADJACENT_MIN_LINES:
        return infos

    # Build stripped lines, preserving original line numbers
    stripped = [lines[i].strip() for i in range(n)]

    # Map from block-tuple → list of starting line indices
    block_map: dict[tuple[str, ...], list[int]] = {}

    for i in range(n - NON_ADJACENT_MIN_LINES + 1):
        block = tuple(
            stripped[i : i + NON_ADJACENT_MIN_LINES]
        )
        # Skip blocks that are entirely noise
        if all(_is_noise_line(l) for l in block):
            continue
        # Skip blocks with more than 2 noise lines (mostly structural)
        noise_count = sum(1 for l in block if _is_noise_line(l))
        if noise_count > 2:
            continue

        block_map.setdefault(block, []).append(i)

    # Report blocks that appear more than once
    reported_ranges: list[tuple[int, int]] = []

    for block, starts in block_map.items():
        if len(starts) < 2:
            continue
        # Filter out adjacent duplicates already reported by the
        # adjacent detector: only report if any pair is non-adjacent
        has_non_adjacent = False
        for a_idx in range(len(starts)):
            for b_idx in range(a_idx + 1, len(starts)):
                a_start = starts[a_idx]
                b_start = starts[b_idx]
                # Non-adjacent = the second block does not start
                # immediately after the first block ends
                if b_start != a_start + NON_ADJACENT_MIN_LINES:
                    has_non_adjacent = True
                    break
            if has_non_adjacent:
                break

        if not has_non_adjacent:
            continue

        # Extend each block to its maximal length (find the longest
        # common prefix among all occurrences).  ext is the total block
        # length being tested; we track the largest that still matches.
        max_len = NON_ADJACENT_MIN_LINES
        min_start = min(starts)
        for ext in range(NON_ADJACENT_MIN_LINES + 1, n - min_start + 1):
            ext_blocks = set()
            valid = True
            for s in starts:
                if s + ext > n:
                    valid = False
                    break
                ext_blocks.add(tuple(stripped[s : s + ext]))
            if not valid or len(ext_blocks) != 1:
                break
            max_len = ext

        total_len = max_len

        # Check for overlap with already-reported ranges
        for s in starts:
            end = s + total_len - 1
            if any(
                not (end < r_start or s > r_end)
                for r_start, r_end in reported_ranges
            ):
                continue

            # Report each non-adjacent occurrence
            for s2 in starts:
                if s2 == s:
                    continue
                if abs(s2 - s) < total_len:
                    continue  # overlapping occurrence, skip
                end2 = s2 + total_len - 1
                if any(
                    not (end2 < r_start or s2 > r_end)
                    for r_start, r_end in reported_ranges
                ):
                    continue

                content_preview = lines[s].strip()
                if len(content_preview) > 60:
                    content_preview = content_preview[:57] + "..."

                # Classify the duplicate by risk semantics
                block_content = [stripped[s + j] for j in range(total_len)]
                category, action = _classify_duplicate(block_content)

                # Determine output level based on action
                if action == "direct-fix":
                    level = "WARNING"
                elif action == "needs-human-review":
                    level = "REVIEW"
                else:
                    level = "INFO"

                infos.append(
                    f"  {level:7s} {rel}:{s + 1} — duplicate block "
                    f"({total_len} lines) also at line {s2 + 1} "
                    f"[{category}] ({action}): "
                    f"first line: {content_preview}"
                )
                reported_ranges.append((s, s + total_len - 1))
                reported_ranges.append((s2, s2 + total_len - 1))
                break

    return infos


def check_file(filepath: Path) -> tuple[list[str], list[str]]:
    """Check a single C file for duplicate code blocks.

    Args:
        filepath: Path to the C source file to check.

    Returns:
        Tuple of (warnings, infos) lists.
    """
    warnings: list[str] = []
    infos: list[str] = []

    rel = _display_path(filepath)

    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return warnings, infos

    if not source.strip():
        return warnings, infos

    lines = source.splitlines()

    warnings = _find_adjacent_duplicates(lines, rel)
    infos = _find_non_adjacent_duplicates(lines, rel)

    return warnings, infos


def main() -> int:
    """Main entry point.

    Returns:
        Exit code: 0 in advisory mode.  In --strict mode, exit 1 when any
        direct-fix (memory/rollback/ffi-validation/postcommit) warning or
        adjacent merge-residual duplicate is found.
    """
    parser = argparse.ArgumentParser(
        description="Detect duplicate code blocks in C source files "
                    "(Rule 31)",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="components/nginx-module/src",
        help="Directory to scan (default: components/nginx-module/src); "
             "trusted input only",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on direct-fix warnings or adjacent merge residuals",
    )
    args = parser.parse_args()

    scan_dir = Path(args.directory)
    if not scan_dir.is_absolute():
        scan_dir = REPO_ROOT / scan_dir

    try:
        scan_dir = scan_dir.resolve()
    except OSError:
        pass

    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory", file=sys.stderr)
        return 0  # advisory: exit 0 even on error

    print("=== Duplicate Code Block Detection (Rule 31) ===", file=sys.stderr)
    print(f"Scanning: {scan_dir}", file=sys.stderr)
    print(f"Strict: {1 if args.strict else 0}", file=sys.stderr)
    print("", file=sys.stderr)

    all_warnings: list[str] = []
    all_infos: list[str] = []
    all_reviews: list[str] = []

    c_files = sorted(scan_dir.rglob("*.c"))
    for filepath in c_files:
        file_warnings, file_infos = check_file(filepath)
        all_warnings.extend(file_warnings)
        # Split infos into warnings/reviews/info based on classification
        for msg in file_infos:
            if "WARNING" in msg:
                all_warnings.append(msg)
            elif "REVIEW" in msg:
                all_reviews.append(msg)
            else:
                all_infos.append(msg)

    for w in all_warnings:
        print(w, file=sys.stderr)
    for r in all_reviews:
        print(r, file=sys.stderr)
    for i in all_infos:
        print(i, file=sys.stderr)

    # Count by category
    n_memory = sum(1 for m in all_warnings if "[memory]" in m)
    n_rollback = sum(1 for m in all_warnings if "[rollback]" in m)
    n_ffi = sum(1 for m in all_warnings if "[ffi-validation]" in m)
    n_postcommit = sum(1 for m in all_warnings if "[postcommit]" in m)
    n_adjacent = len([w for w in all_warnings if "adjacent" in w])
    n_direct_fix = n_memory + n_rollback + n_ffi + n_postcommit
    n_state = sum(1 for m in all_reviews if "[state-machine]" in m)
    n_struct = sum(1 for m in all_reviews if "[structural]" in m)
    n_log = sum(1 for m in all_infos if "[log-only]" in m)
    n_sig = sum(1 for m in all_infos if "[signature]" in m)

    print("", file=sys.stderr)
    print("=== Summary (by risk semantics) ===", file=sys.stderr)
    print(f"  memory (direct-fix):           {n_memory}", file=sys.stderr)
    print(f"  rollback (direct-fix):         {n_rollback}", file=sys.stderr)
    print(f"  ffi-validation (direct-fix):   {n_ffi}", file=sys.stderr)
    print(f"  postcommit (direct-fix):       {n_postcommit}", file=sys.stderr)
    print(f"  state-machine (human-review):   {n_state}", file=sys.stderr)
    print(f"  structural (human-review):      {n_struct}", file=sys.stderr)
    print(f"  log-only (ignore-by-rule):      {n_log}", file=sys.stderr)
    print(f"  signature (ignore-by-rule):     {n_sig}", file=sys.stderr)
    print(f"  adjacent (merge residual):      {n_adjacent}", file=sys.stderr)
    print("", file=sys.stderr)

    # Determine the verdict considering all three buckets (warnings,
    # reviews, infos).  Previously reviews were dropped from the
    # verdict so a file with only REVIEW findings printed "PASS: no
    # duplicate code blocks found" — a misleading false-negative
    # message.
    n_total = (
        len(all_warnings) + len(all_reviews) + len(all_infos)
    )
    if all_warnings:
        print(
            f"PASS with warnings: {len(all_warnings)} warning(s) "
            f"({n_adjacent} adjacent merge-residual, {n_direct_fix} "
            f"direct-fix) — review recommended (Rule 31)",
            file=sys.stderr,
        )
    elif all_reviews:
        print(
            f"PASS with reviews: {len(all_reviews)} review(s) — "
            f"manual review recommended (Rule 31)",
            file=sys.stderr,
        )
    elif all_infos:
        print(
            f"PASS with info: {len(all_infos)} non-adjacent duplicate "
            f"block(s) — consider refactoring (Rule 31)",
            file=sys.stderr,
        )
    else:
        print("PASS: no duplicate code blocks found", file=sys.stderr)

    if args.strict and (n_direct_fix > 0 or n_adjacent > 0):
        print(
            f"FAIL (strict): {n_direct_fix} direct-fix warning(s) and "
            f"{n_adjacent} adjacent merge-residual duplicate(s) — "
            f"fix before merge (Rule 31)",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())