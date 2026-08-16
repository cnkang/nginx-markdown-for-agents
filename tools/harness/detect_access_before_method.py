#!/usr/bin/env python3
"""detect_access_before_method.py — Access-Control-before-Method Ordering Audit.

Enforces the security ordering contract for HTTP content handlers: access
control must be evaluated BEFORE method handling (405 rejection).  A denied
request must not disclose the endpoint's method-rejection behavior or
receive an Allow header.

Recurring review history: the diagnostics endpoint handler regressed
repeatedly on this ordering — access-after-method leaks handler behavior to
unauthorized callers and was fixed three separate times in review.

Detection model (conservative, per handler function):
1. A handler is a function whose body references `r->method` and either
   `NGX_HTTP_NOT_ALLOWED` or a `method_not_allowed` helper call.
2. The function must call an access-control function (`check_access`,
   `ngx_http_core_module` access phase helpers) BEFORE the method-rejection
   branch.  "Before" means the access call appears earlier in source order
   than any `NGX_HTTP_NOT_ALLOWED` assignment or `*_method_not_allowed(` call.
3. Handlers that never reject methods (no 405 path) are exempt.

Advisory (REVIEW) by default; --strict promotes findings to exit 1.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "components/nginx-module/src"

# A function definition line, e.g.:
#   ngx_int_t ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
# or with the return type on its own line:
#   ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
# The optional type prefix consumes any leading type tokens; the captured
# identifier is the function name.  Control-flow lines (if/while/for/return/
# switch) never match because their condition is followed by '{' or ';',
# not a bare ')' at end-of-line.
FUNC_DEF_RE = re.compile(
    r"^(?:\w[\w \t*]*\s+)?(\w+)\s*\([^;]*\)\s*$",
    re.MULTILINE,
)
# Function may span lines: collect braces by scanning from a def line.

# Access-control call signals.
ACCESS_CALL_RE = re.compile(
    r"\b(?:check_access|ngx_http_access_handler|ngx_http_core_access_phase|"
    r"ngx_http_markdown_diagnostics_check_access)\s*\("
)
# Method-rejection signals (405).
METHOD_REJECT_RE = re.compile(
    r"\bNGX_HTTP_NOT_ALLOWED\b|\b\w+method_not_allowed\s*\("
)
# r->method reference (handler detection).
METHOD_REF_RE = re.compile(r"\br->method\b")


# Tokenizer for brace matching: line comments, block comments, string and
# char literals, and braces.  Non-greedy alternation keeps scanning linear.
_TOKEN_RE = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r'|"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
    r"|[{}]",
    flags=re.DOTALL,
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


def _iter_functions(text: str):
    """Yield (name, body) for each function with a braced body."""
    for m in FUNC_DEF_RE.finditer(text):
        name = m.group(1)
        open_idx = text.find("{", m.end())
        if open_idx == -1:
            continue
        close_idx = _matching_brace(text, open_idx)
        if close_idx == -1:
            continue
        body = text[open_idx : close_idx + 1]
        yield name, body


def audit_dir(directory: Path, strict: bool) -> tuple[list[str], list[str]]:
    """Return (violations, reviews) across *.c and *.h files."""
    violations: list[str] = []
    reviews: list[str] = []
    if not directory.is_dir():
        return (
            [f"directory not found: {directory}"],
            [],
        )
    for path in sorted(directory.glob("*.c")) + sorted(directory.glob("*.h")):
        v, r = audit_file(path)
        violations.extend(v)
        reviews.extend(r)
    return violations, reviews


def _strip_literals_and_comments(text: str) -> str:
    """Blank out comments, string literals, and char literals in-place.

    Keeps every character position (replaced with spaces) so regex match
    offsets stay aligned with the original text.  Audit patterns must not
    fire on comments or literals that merely mention access-control or
    method-rejection identifiers.
    """
    return _TOKEN_RE.sub(lambda m: " " * len(m.group(0)), text)


def audit_file(path: Path) -> tuple[list[str], list[str]]:
    """Return (violations, reviews) for one file."""
    violations: list[str] = []
    reviews: list[str] = []
    resolved = validate_read_path(path, purpose="source file")
    text = resolved.read_text(encoding="utf-8", errors="replace")
    for name, body in _iter_functions(text):
        code = _strip_literals_and_comments(body)
        if not METHOD_REF_RE.search(code):
            continue  # not an HTTP handler
        has_method_reject = bool(METHOD_REJECT_RE.search(code))
        if not has_method_reject:
            continue  # no 405 path — ordering contract not applicable
        access_pos = ACCESS_CALL_RE.search(code)
        reject_pos = METHOD_REJECT_RE.search(code)
        assert reject_pos is not None  # guarded by has_method_reject above
        if access_pos is None:
            reviews.append(
                f"{path.name}:{name}: handler rejects methods but never "
                "calls an access-control function before the 405 branch; "
                "denied requests may disclose handler behavior"
            )
        elif reject_pos.start() < access_pos.start():
            violations.append(
                f"{path.name}:{name}: method rejection (405) appears BEFORE "
                "access control; access must be evaluated first so denied "
                "requests do not disclose method behavior or Allow headers"
            )
    return violations, reviews


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit access-control-before-method ordering in handlers."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat advisory REVIEW findings as violations",
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
    violations, reviews = audit_dir(directory, args.strict)

    for v in violations:
        print(f"VIOLATION: {v}")
    for r in reviews:
        print(f"REVIEW: {r}")

    if violations:
        print(f"ERROR: {len(violations)} access-ordering violation(s)",
              file=sys.stderr)
        return 1
    if args.strict and reviews:
        print(f"ERROR: {len(reviews)} REVIEW finding(s) under --strict",
              file=sys.stderr)
        return 1
    print(f"OK: access-before-method ordering verified "
          f"({len(reviews)} advisory review(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
