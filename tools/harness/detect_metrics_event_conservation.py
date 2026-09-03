#!/usr/bin/env python3
"""detect_metrics_event_conservation.py — v1 Metrics Conservation Audit.

Validates the structural conservation contract of the metrics renderer
(ngx_http_markdown_metrics_to_v1).  Terminal outcomes must partition the
conversion-failure space without double counting:

    conversions_failed = failed_open + failed_closed + aborted

In the renderer this appears as the derived field `failed_closed`:

    failed_closed = conversions_failed
                    - failopen_count
                    - terminal_aborted_total     (streaming build)

and `v1->requests.aborted` must read from `terminal_aborted_total` (never
the stale per-path counter `streaming_failure_postcommit_abort`).

Recurring review history: metrics terminal-outcome conservation was fixed
repeatedly — mutually-exclusive terminal outcomes, conservation across
backpressure, and latency-bucket population all regressed at different
times because the renderer arithmetic had no standing guard.

The detector is conservative: it verifies structural presence of the
conservation pattern in the renderer file and reports REVIEW-level
findings when the pattern drifts.  Exit code is 1 only for hard
structural violations (missing renderer, missing failopen deduction).
A missing `v1->requests.aborted` assignment is reported as a REVIEW-level
finding and affects exit status only under `--strict`.

Usage:
    PYTHONPATH=. python3 tools/harness/detect_metrics_event_conservation.py
        [--strict]   treat REVIEW findings as violations (exit 1)

Exit codes:
    0 — conservation pattern present (advisory findings may print)
    1 — hard violation or (with --strict) any REVIEW finding
    2 — usage error
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILE = (
    REPO_ROOT
    / "components/nginx-module/src/ngx_http_markdown_metrics_impl.h"
)

# The renderer function that derives failed_closed and aborted.
RENDERER_RE = re.compile(r"ngx_http_markdown_metrics_to_v1\s*\(")

# v1->requests.aborted assignment must source from terminal_aborted_total.
# Capture the full RHS expression up to ';' then check its final field.
ABORTED_ASSIGN_RE = re.compile(
    r"v1->requests\.aborted[ \t]*=([^;]*);"
)
# The per-path postcommit abort counter is stale for v1 terminal outcome.
STALE_ABORT_SRC = "streaming_failure_postcommit_abort"
# The terminal-outcome source field for v1 aborted.
ABORTED_SOURCE_FIELD = "terminal_aborted_total"

# failed_closed derivation: each `failed_closed = ...;` statement must be
# examined independently (the streaming and non-streaming branches both
# assign failed_closed; a missing deduction in one branch must not be
# masked by the other branch still containing the counter name).
# Only direct assignment is accepted: additive updates (`+=`, `-=`) and
# equality comparisons (`==`) are not derivation statements and must not
# be treated as one (a `==` would otherwise match on its second `=`).
FAILED_CLOSED_ASSIGN_RE = re.compile(
    r"failed_closed[ \t]*(?<![!<>=+\-*/%&|^])=(?!=)([^;]*);"
)


def _strip_c_comments(text: str) -> str:
    """Remove C block and line comments, preserving newlines and offsets.

    The conservation regexes must not fire on prose inside comments (a
    comment that merely mentions ``failed_closed = conversions_failed``
    or the stale abort counter name is not a real assignment).  Block
    comments are replaced with spaces and line comments are truncated
    at their newline, so statement structure, string literals, and
    preprocessor directives stay intact for the block audit.
    """
    pattern = re.compile(r"/\*.*?\*/|//[^\n]*", flags=re.DOTALL)

    def _blank(match: re.Match) -> str:
        token = match.group(0)
        return "".join("\n" if char == "\n" else " " for char in token)

    return pattern.sub(_blank, text)


def _audit_aborted(
    text: str, name: str
) -> tuple[list[str], list[str]]:
    """Check v1->requests.aborted sourcing rules."""
    violations: list[str] = []
    reviews: list[str] = []
    abort_matches = ABORTED_ASSIGN_RE.findall(text)
    if not abort_matches:
        reviews.append(
            f"{name}: no v1->requests.aborted assignment found; "
            "terminal-outcome partition may be incomplete"
        )
        return violations, reviews

    saw_terminal_source = False
    for expr in abort_matches:
        expr = expr.strip()
        if STALE_ABORT_SRC in expr:
            violations.append(
                f"{name}: v1->requests.aborted reads stale "
                f"{STALE_ABORT_SRC}; must read {ABORTED_SOURCE_FIELD} "
                "(terminal-outcome conservation)"
            )
        elif re.search(rf"\b{re.escape(ABORTED_SOURCE_FIELD)}\b", expr):
            saw_terminal_source = True
        elif expr != "0":
            reviews.append(
                f"{name}: v1->requests.aborted sources from "
                f"'{expr}' (expected {ABORTED_SOURCE_FIELD})"
            )
    if not saw_terminal_source:
        reviews.append(
            f"{name}: no aborted assignment reads "
            f"{ABORTED_SOURCE_FIELD}; terminal-outcome partition "
            "may be incomplete in the streaming build"
        )
    return violations, reviews


def _directive_kind(stripped: str) -> str:
    """Classify a preprocessor directive line: if | endif | else | other."""
    if stripped.startswith("#if"):
        return "if"
    if stripped.startswith("#endif"):
        return "endif"
    if stripped.startswith(("#else", "#elif")):
        return "else"
    return "other"


def _emit_open_block(
    blocks: list[str], stack: list[list[str]], directives: list[str]
) -> None:
    """Emit the innermost open block with its ancestor directives.

    The block itself already carries its own opening directive line, so
    only the ancestors (directives[:-1]) are prepended as context.
    """
    if stack[-1]:
        blocks.append("".join(directives[:-1]) + "".join(stack.pop()))
    else:
        stack.pop()


def _close_preprocessor_block(
    blocks: list[str], stack: list[list[str]], directives: list[str]
) -> None:
    """Close one conditional level at #endif: emit, pop stack and directive."""
    _emit_open_block(blocks, stack, directives)
    directives.pop()


def _split_preprocessor_blocks(text: str) -> list[str]:
    """Split C preprocessor conditionals into independent blocks.

    Returns each region between #if/#ifdef/#ifndef and its matching
    #endif as its own block, plus the top-level region.  Nested blocks
    carry their active ancestor directives as a prefix so a block's
    failed_closed derivation is still associated with e.g.
    MARKDOWN_STREAMING_ENABLED even when nested inside another guard.
    Deductions are verified per block so a missing counter in one branch
    cannot be masked by another branch still naming it.
    """
    blocks: list[str] = []
    stack: list[list[str]] = [[]]        # one entry per nesting level
    directives: list[str] = []           # active ancestor directive lines
    for line in text.splitlines(keepends=True):
        kind = _directive_kind(line.lstrip())
        if kind == "if":
            stack.append([line])
            directives.append(line)
            continue
        if kind == "endif" and len(stack) > 1:
            _close_preprocessor_block(blocks, stack, directives)
            continue
        if kind == "else" and len(stack) > 1:
            # Emit the #if block, then replace its directive with the #else
            # line so the else branch inherits the same ancestors.
            _emit_open_block(blocks, stack, directives)
            directives[-1] = line
            stack.append([line])
            continue
        stack[-1].append(line)
    if stack[0]:
        blocks.append("".join(stack[0]))
    return [b for b in blocks if b.strip()]


def _audit_failed_closed(
    text: str, name: str
) -> tuple[list[str], list[str]]:
    """Check failed_closed derivation deductions.

    The renderer assigns failed_closed in each preprocessor branch, often
    as a sequence of stepwise deductions (failopen, then aborted).  Each
    branch block is evaluated independently so a missing deduction in one
    branch is not masked by another branch still containing the counter
    name.
    """
    violations: list[str] = []
    reviews: list[str] = []
    failed_closed_stmts = FAILED_CLOSED_ASSIGN_RE.findall(text)
    if not failed_closed_stmts:
        reviews.append(
            f"{name}: failed_closed derivation block not found; "
            "cannot verify conservation arithmetic"
        )
        return violations, reviews

    for block in _split_preprocessor_blocks(text):
        block_stmts = FAILED_CLOSED_ASSIGN_RE.findall(block)
        if not block_stmts:
            continue
        # Only deduction statements that source from the snapshot renderer
        # participate in conservation; a declaration-scope `failed_closed =
        # 0` initializer in an include guard or struct block is not a
        # derivation branch.
        if not any("snapshot" in stmt for stmt in block_stmts):
            continue
        if not any("failopen_count" in stmt for stmt in block_stmts):
            violations.append(
                f"{name}: failed_closed derivation does not deduct "
                "failopen_count in this branch; failed_open must "
                "partition conversions_failed in every branch"
            )
        # terminal_aborted_total only exists under the streaming build; the
        # non-streaming branch assigns aborted = 0 and legitimately has no
        # aborted deduction.
        if (
            re.search(
                r"#if(?:def)?\s+(?:defined\s*\(\s*)?MARKDOWN_STREAMING_ENABLED",
                block,
            )
            and not any(
                "terminal_aborted_total" in stmt for stmt in block_stmts
            )
        ):
            reviews.append(
                f"{name}: failed_closed derivation does not deduct "
                "terminal_aborted_total in the streaming branch; "
                "aborted may be double counted"
            )
    return violations, reviews


def audit(path: Path) -> tuple[list[str], list[str]]:
    """Return (violations, reviews)."""
    resolved = validate_read_path(path, purpose="metrics implementation")
    if not resolved.exists():
        return (
            [f"metrics renderer not found: {resolved}"],
            [],
        )

    text = resolved.read_text(encoding="utf-8", errors="replace")
    name = resolved.name

    if not RENDERER_RE.search(text):
        return (
            [f"metrics renderer ngx_http_markdown_metrics_to_v1 not found in {name}"],
            [],
        )

    # Conservation statements are real C code, not comment prose: strip
    # comments before applying the assignment regexes so a comment that
    # merely mentions failed_closed or the stale counter name cannot
    # satisfy (or trip) the conservation audit.
    code_text = _strip_c_comments(text)

    violations: list[str] = []
    reviews: list[str] = []
    for check in (_audit_aborted, _audit_failed_closed):
        v, r = check(code_text, name)
        violations.extend(v)
        reviews.extend(r)
    return violations, reviews


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit v1 metrics terminal-outcome conservation."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat advisory REVIEW findings as violations",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_FILE,
        help="metrics implementation file (default: nginx-module src)",
    )
    args = parser.parse_args()

    try:
        violations, reviews = audit(args.path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for v in violations:
        print(f"VIOLATION: {v}")
    for r in reviews:
        print(f"REVIEW: {r}")

    if violations:
        print(f"ERROR: {len(violations)} conservation violation(s)", file=sys.stderr)
        return 1
    if args.strict and reviews:
        print(f"ERROR: {len(reviews)} REVIEW finding(s) under --strict", file=sys.stderr)
        return 1

    if not violations and not reviews:
        print("OK: v1 metrics terminal-outcome conservation verified")
    else:
        print(f"OK: conservation structural pattern present "
              f"({len(reviews)} advisory review(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
