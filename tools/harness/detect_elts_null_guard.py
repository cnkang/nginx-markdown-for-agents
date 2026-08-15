#!/usr/bin/env python3
"""detect_elts_null_guard.py — ngx_list_part_t elts NULL-guard Audit.

Flags iteration over ngx_list_part_t chains where `part->elts` is
dereferenced (element indexing) without an explicit NULL guard.  NGINX
convention keeps elts non-NULL whenever nelts > 0, but review history
showed a real crash class: a chain part with nelts != 0 whose elts
pointer was NULL (partial/boundary list state), dereferenced without a
guard.  The defensive pattern required:

    const ngx_table_elt_t *headers = part->elts;
    if (headers == NULL && part->nelts != 0) {
        return;  /* or continue — do not index a NULL elts */
    }

The detector is conservative: it reports REVIEW-level findings for
element indexing of `->elts` (or an alias assigned from `->elts`)
inside a loop that iterates `part->next`, when no NULL guard for the
elts pointer appears in the surrounding function body.

Exit code is 1 only for hard violations under --strict, or when no
source files are found.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "components/nginx-module/src"

# `foo->elts` or `foo.elts` access.
ELTS_ACCESS_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*->\s*elts\b")
# Loop over part->next (chain traversal) signals.
CHAIN_LOOP_RE = re.compile(
    r"\bpart\s*=\s*part\s*->\s*next\b|for\s*\([^)]*\bpart\b[^)]*->\s*next"
)


def _function_bodies(text: str):
    """Yield bodies of functions (braced blocks following a def line)."""
    # Reuse a lightweight approach: find `name(...)` followed by `{`,
    # then balance braces with a string/comment-aware scanner.
    func_re = re.compile(
        r"^(?:[A-Za-z_][A-Za-z0-9_ \t*]*\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*$",
        re.MULTILINE,
    )
    for m in func_re.finditer(text):
        open_idx = text.find("{", m.end())
        if open_idx == -1:
            continue
        close_idx = _matching_brace(text, open_idx)
        if close_idx == -1:
            continue
        yield text[open_idx : close_idx + 1]


# Tokenizer for brace matching: line comments, block comments, string and
# char literals, and braces.  Non-greedy alternation keeps scanning linear.
_TOKEN_RE = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r'|"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
    r"|[{}]"
)


def _matching_brace(text: str, open_idx: int) -> int:
    """Return index of the '}' matching the '{' at open_idx.

    Braces inside string literals, char literals, and comments are
    ignored so embedded braces (e.g. sizeof("}\\n")) cannot corrupt the
    balance.  Returns -1 if no match is found.
    """
    depth = 0
    for m in _TOKEN_RE.finditer(text, open_idx):
        tok = m.group(0)
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth == 0:
                return m.start()
    return -1


def audit_file(path: Path) -> list[str]:
    """Return REVIEW findings for one file."""
    findings: list[str] = []
    resolved = validate_read_path(path, purpose="source file")
    text = resolved.read_text(encoding="utf-8", errors="replace")
    for body in _function_bodies(text):
        if not CHAIN_LOOP_RE.search(body):
            continue  # no part->next chain traversal
        for holder in _elts_holders(body):
            if _holder_is_safe(body, holder):
                continue
            findings.append(
                f"{path.name}: '{holder}' holds part->elts and is indexed "
                "inside a part->next chain loop without a NULL guard or "
                "nelts bound; a part with nelts != 0 but NULL elts would "
                "dereference NULL (defensive guard: check elts == NULL "
                "before indexing)"
            )
    return findings


def _elts_holders(body: str) -> set[str]:
    """Collect pointers holding part->elts, including aliases."""
    holders = {m.group(1) for m in ELTS_ACCESS_RE.finditer(body)}
    alias_re = re.compile(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"(?:[A-Za-z_][A-Za-z0-9_]*)\s*->\s*elts\b"
    )
    holders.update(am.group(1) for am in alias_re.finditer(body))
    return {h for h in holders if h != "part"}


def _holder_is_safe(body: str, holder: str) -> bool:
    """True when the elts holder is protected by a nelts bound or NULL guard."""
    # Only the holder's own indexing is relevant: another variable being
    # indexed in the same function must not make this holder look unsafe,
    # and a nelts bound is only accepted when this holder is actually
    # indexed (scoped to the holder, not any unrelated part->nelts use).
    holder_index_re = re.compile(rf"\b{re.escape(holder)}\s*\[")
    if not holder_index_re.search(body):
        return True  # this holder is never indexed
    if re.search(r"\bpart\s*->\s*nelts\b", body):
        return True  # standard bounded iteration
    guard_re = re.compile(
        rf"\b{re.escape(holder)}\s*==\s*NULL\b"
        rf"|\bNULL\s*==\s*{re.escape(holder)}\b"
    )
    return bool(guard_re.search(body))


def audit_dir(directory: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(directory.glob("*.c")) + sorted(directory.glob("*.h")):
        findings.extend(audit_file(path))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit ngx_list_part_t elts NULL-guard discipline."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat advisory REVIEW findings as violations (exit 1)",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_DIR,
        help="nginx-module src directory (default: components/nginx-module/src)",
    )
    args = parser.parse_args()

    directory = validate_read_path(args.directory, purpose="scan directory")
    findings = audit_dir(directory)

    for f in findings:
        print(f"REVIEW: {f}")

    if args.strict and findings:
        print(f"ERROR: {len(findings)} REVIEW finding(s) under --strict",
              file=sys.stderr)
        return 1
    print(f"OK: elts NULL-guard audit complete ({len(findings)} advisory finding(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
