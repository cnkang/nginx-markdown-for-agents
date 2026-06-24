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

# Small-block threshold: duplicates of this size or smaller are advisory
# (REVIEW/INFO) rather than direct-fix (WARNING), even if they match
# memory/rollback/ffi-validation/postcommit keywords.  Short 5-line
# duplicates that are simple allocation+check patterns are common in C
# and extracting a helper for them often reduces readability more than
# it prevents divergence.  Blocks above this threshold remain direct-fix.
SMALL_BLOCK_THRESHOLD = 7

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
    "FFIHeaderEntry", "FFIAcceptResult", "FFIDecisionResult",
    "validate_ffi", "ffi_guard", "defensive.*FFI",
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
    if stripped.startswith(("/*", "*", "//", "#")):
        return True
    return False


def _classify_duplicate(block_lines: list[str]) -> tuple[str, str]:
    """Classify a duplicate block by risk semantics.

    Returns a tuple of (category, action) where category is one of:
    memory, rollback, ffi-validation, postcommit, state-machine,
    log-only, signature, structural.

    Action is one of: direct-fix, needs-human-review, ignore-by-rule.

    Small blocks (≤ SMALL_BLOCK_THRESHOLD lines) that match memory /
    rollback / ffi-validation / postcommit keywords are downgraded from
    direct-fix to needs-human-review, because short 5-line allocation
    patterns are common in C and extracting a helper often hurts
    readability more than it prevents divergence.
    """
    joined = " ".join(block_lines)
    is_small = len(block_lines) <= SMALL_BLOCK_THRESHOLD

    # Data-driven classification: (keywords, category, fixed_action).
    # fixed_action=None means use the default (direct-fix / review based
    # on is_small); a string overrides for that category.
    _DIRECT_FIX_CATEGORIES: list[tuple[tuple[str, ...], str, str | None]] = [
        (MEMORY_KEYWORDS,        "memory",         None),
        (ROLLBACK_KEYWORDS,      "rollback",       None),
        (FFI_VALIDATION_KEYWORDS, "ffi-validation", None),
        (POSTCOMMIT_KEYWORDS,    "postcommit",     None),
        (STATE_MACHINE_KEYWORDS, "state-machine",  "needs-human-review"),
        (LOG_ONLY_KEYWORDS,      "log-only",       "ignore-by-rule"),
    ]

    for keywords, category, fixed_action in _DIRECT_FIX_CATEGORIES:
        if any(kw in joined for kw in keywords):
            action = fixed_action if fixed_action else (
                "needs-human-review" if is_small else "direct-fix"
            )
            return (category, action)

    # Pure signature/declaration duplicates — C structural, default skip
    if _is_signature_duplicate(joined):
        return ("signature", "ignore-by-rule")

    return ("structural", "needs-human-review")


def _is_signature_duplicate(joined: str) -> bool:
    """Return True when the block is a C function signature/declaration."""
    if "ngx_http_request_t" in joined:
        return True
    if any(kw in joined for kw in SIGNATURE_KEYWORDS):
        exclude = MEMORY_KEYWORDS + ROLLBACK_KEYWORDS + POSTCOMMIT_KEYWORDS
        if not any(kw in joined for kw in exclude):
            return True
    return False


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
        best_block = _find_best_adjacent_block(lines, i, n)
        if best_block >= ADJACENT_MIN_LINES:
            content_preview = lines[i].strip()
            if len(content_preview) > 60:
                content_preview = content_preview[:57] + "..."
            warnings.append(
                f"  WARNING {rel}:{i + 1} — adjacent duplicate block "
                f"({best_block} lines immediately repeated, merge residual "
                f"per Rule 31): first line: {content_preview}"
            )
            i += best_block * 2
        else:
            i += 1

    return warnings


def _find_best_adjacent_block(
    lines: list[str], start: int, n: int,
) -> int:
    """Find the largest adjacent duplicate block starting at 'start'.

    Returns the block size (>= ADJACENT_MIN_LINES) or 0 if none found.
    """
    best_block = 0
    for block_size in range(ADJACENT_MIN_LINES, min(50, (n - start) // 2) + 1):
        block_a = [lines[j].strip() for j in range(start, start + block_size)]
        block_b_start = start + block_size
        block_b = [
            lines[j].strip()
            for j in range(block_b_start, block_b_start + block_size)
        ]
        if any(_is_noise_line(line) for line in block_a):
            break
        if block_a == block_b:
            best_block = block_size
    return best_block


def _build_block_map(
    stripped: list[str], n: int,
) -> dict[tuple[str, ...], list[int]]:
    """Build a map from block-tuple to list of starting line indices."""
    block_map: dict[tuple[str, ...], list[int]] = {}
    for i in range(n - NON_ADJACENT_MIN_LINES + 1):
        block = tuple(stripped[i : i + NON_ADJACENT_MIN_LINES])
        if all(_is_noise_line(line) for line in block):
            continue
        noise_count = sum(1 for line in block if _is_noise_line(line))
        if noise_count > 2:
            continue
        block_map.setdefault(block, []).append(i)
    return block_map


def _has_non_adjacent_pair(starts: list[int]) -> bool:
    """Return True if any pair of starts is non-adjacent."""
    for a_idx in range(len(starts)):
        for b_idx in range(a_idx + 1, len(starts)):
            if starts[b_idx] != starts[a_idx] + NON_ADJACENT_MIN_LINES:
                return True
    return False


def _extend_block_length(
    stripped: list[str], starts: list[int], n: int,
) -> int:
    """Extend block to maximal common length across all occurrences."""
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
    return max_len


def _is_range_overlapping(
    s: int, end: int, reported: list[tuple[int, int]],
) -> bool:
    """Return True if [s, end] overlaps any reported range."""
    return any(
        not (end < r_start or s > r_end)
        for r_start, r_end in reported
    )


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

    stripped = [lines[i].strip() for i in range(n)]
    block_map = _build_block_map(stripped, n)
    reported_ranges: list[tuple[int, int]] = []

    for starts in block_map.values():
        if len(starts) < 2:
            continue
        if not _has_non_adjacent_pair(starts):
            continue

        total_len = _extend_block_length(stripped, starts, n)

        for s in starts:
            end = s + total_len - 1
            if _is_range_overlapping(s, end, reported_ranges):
                continue

            _report_non_adjacent_occurrences(
                lines, stripped, starts, s, total_len,
                rel, reported_ranges, infos,
            )

    return infos


def _report_non_adjacent_occurrences(
    lines: list[str],
    stripped: list[str],
    starts: list[int],
    s: int,
    total_len: int,
    rel: str,
    reported_ranges: list[tuple[int, int]],
    infos: list[str],
) -> None:
    """Report non-adjacent duplicate occurrences for a given block."""
    for s2 in starts:
        if s2 == s:
            continue
        if abs(s2 - s) < total_len:
            continue
        end2 = s2 + total_len - 1
        if _is_range_overlapping(s2, end2, reported_ranges):
            continue

        content_preview = lines[s].strip()
        if len(content_preview) > 60:
            content_preview = content_preview[:57] + "..."

        block_content = [stripped[s + j] for j in range(total_len)]
        category, action = _classify_duplicate(block_content)

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


def _count_by_category(
    all_warnings: list[str],
    all_reviews: list[str],
    all_infos: list[str],
) -> dict[str, int]:
    """Count findings by risk-semantic category."""
    return {
        "memory": sum(1 for m in all_warnings if "[memory]" in m),
        "rollback": sum(1 for m in all_warnings if "[rollback]" in m),
        "ffi": sum(1 for m in all_warnings if "[ffi-validation]" in m),
        "postcommit": sum(1 for m in all_warnings if "[postcommit]" in m),
        "adjacent": len([w for w in all_warnings if "adjacent" in w]),
        "state": sum(1 for m in all_reviews if "[state-machine]" in m),
        "struct": sum(1 for m in all_reviews if "[structural]" in m),
        "log": sum(1 for m in all_infos if "[log-only]" in m),
        "sig": sum(1 for m in all_infos if "[signature]" in m),
    }


def _print_summary_and_verdict(
    all_warnings: list[str],
    all_reviews: list[str],
    all_infos: list[str],
    strict: bool,
) -> int:
    """Print the risk-semantic summary and return exit code.

    Returns 1 in strict mode when direct-fix warnings or adjacent merge
    residuals are found; 0 otherwise.
    """
    counts = _count_by_category(all_warnings, all_reviews, all_infos)
    n_direct_fix = (
        counts["memory"] + counts["rollback"]
        + counts["ffi"] + counts["postcommit"]
    )

    print("", file=sys.stderr)
    print("=== Summary (by risk semantics) ===", file=sys.stderr)
    print(f"  memory (direct-fix):           {counts['memory']}", file=sys.stderr)
    print(f"  rollback (direct-fix):         {counts['rollback']}", file=sys.stderr)
    print(f"  ffi-validation (direct-fix):   {counts['ffi']}", file=sys.stderr)
    print(f"  postcommit (direct-fix):       {counts['postcommit']}", file=sys.stderr)
    print(f"  state-machine (human-review):   {counts['state']}", file=sys.stderr)
    print(f"  structural (human-review):      {counts['struct']}", file=sys.stderr)
    print(f"  log-only (ignore-by-rule):      {counts['log']}", file=sys.stderr)
    print(f"  signature (ignore-by-rule):     {counts['sig']}", file=sys.stderr)
    print(f"  adjacent (merge residual):      {counts['adjacent']}", file=sys.stderr)
    print("", file=sys.stderr)

    _print_verdict_line(
        all_warnings, all_reviews, all_infos,
        n_direct_fix, counts["adjacent"],
    )

    if strict and (n_direct_fix > 0 or counts["adjacent"] > 0):
        print(
            f"FAIL (strict): {n_direct_fix} direct-fix warning(s) and "
            f"{counts['adjacent']} adjacent merge-residual duplicate(s) — "
            f"fix before merge (Rule 31)",
            file=sys.stderr,
        )
        return 1

    return 0


def _print_verdict_line(
    all_warnings: list[str],
    all_reviews: list[str],
    all_infos: list[str],
    n_direct_fix: int,
    n_adjacent: int,
) -> None:
    """Print the single verdict line based on finding counts."""
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


def _resolve_scan_dir(directory: str) -> Path | None:
    """Resolve and validate the scan directory.

    Returns the resolved Path, or None if the directory is invalid.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.path_validation import validate_read_path
        return Path(validate_read_path(
            directory, purpose="scan directory",
        ))
    except (ImportError, FileNotFoundError, ValueError):
        scan_dir = Path(directory)
        if not scan_dir.is_absolute():
            scan_dir = REPO_ROOT / scan_dir
        try:
            return scan_dir.resolve()
        except OSError:
            return scan_dir


def _classify_file_infos(
    file_infos: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Split file infos into warnings, reviews, and info lists."""
    warnings: list[str] = []
    reviews: list[str] = []
    infos: list[str] = []
    for msg in file_infos:
        if "WARNING" in msg:
            warnings.append(msg)
        elif "REVIEW" in msg:
            reviews.append(msg)
        else:
            infos.append(msg)
    return warnings, reviews, infos


def _scan_files(
    scan_dir: Path,
) -> tuple[list[str], list[str], list[str]]:
    """Scan all C files in scan_dir and classify findings."""
    all_warnings: list[str] = []
    all_infos: list[str] = []
    all_reviews: list[str] = []

    c_files = sorted(scan_dir.rglob("*.c"))
    for filepath in c_files:
        file_warnings, file_infos = check_file(filepath)
        all_warnings.extend(file_warnings)
        w, r, i = _classify_file_infos(file_infos)
        all_warnings.extend(w)
        all_reviews.extend(r)
        all_infos.extend(i)

    return all_warnings, all_reviews, all_infos


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

    scan_dir = _resolve_scan_dir(args.directory)
    if scan_dir is None or not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory", file=sys.stderr)
        return 0  # advisory: exit 0 even on error

    print("=== Duplicate Code Block Detection (Rule 31) ===", file=sys.stderr)
    print(f"Scanning: {scan_dir}", file=sys.stderr)
    print(f"Strict: {1 if args.strict else 0}", file=sys.stderr)
    print("", file=sys.stderr)

    all_warnings, all_reviews, all_infos = _scan_files(scan_dir)

    for w in all_warnings:
        print(w, file=sys.stderr)
    for r in all_reviews:
        print(r, file=sys.stderr)
    for i in all_infos:
        print(i, file=sys.stderr)

    return _print_summary_and_verdict(
        all_warnings, all_reviews, all_infos, args.strict,
    )


if __name__ == "__main__":
    sys.exit(main())