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

# Function signatures are located by deterministic line scanning below.  A
# regex is intentionally not used for the whole signature: balanced-looking
# parenthesis character classes still invite super-linear backtracking as the
# C source grows.
FUNC_NAME_RE = re.compile(r"[A-Za-z_]\w*$")

# Access-control call signals.
ACCESS_CALL_RE = re.compile(
    r"\b(?:check_access|ngx_http_access_handler|ngx_http_core_access_phase|"
    r"ngx_http_markdown_diagnostics_check_access|"
    r"ngx_http_markdown_metrics_check_access)\s*\("
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

# Literal/comment tokenizer used only for locating the opening body brace:
# it must NOT match braces (the blanked copy is searched for the first
# real '{'), so it covers comments and quoted literals only.
_LITERAL_ONLY_RE = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r'|"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'",
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


def _iter_top_level_parentheses(blanked: str):
    """Yield offsets for balanced top-level parenthesis groups."""
    paren_depth = 0
    open_idx = -1
    for index, char in enumerate(blanked):
        if char == "(":
            if paren_depth == 0:
                open_idx = index
            paren_depth += 1
            continue
        if char != ")" or paren_depth == 0:
            continue
        paren_depth -= 1
        if paren_depth == 0 and open_idx != -1:
            yield open_idx, index
            open_idx = -1


def _signature_body_start(blanked: str, close_idx: int) -> int:
    """Return the body brace after a signature, or -1 if absent."""
    body_idx = close_idx + 1
    while body_idx < len(blanked) and blanked[body_idx].isspace():
        body_idx += 1
    if body_idx >= len(blanked) or blanked[body_idx] != "{":
        return -1
    return body_idx


def _signature_name(blanked: str, open_idx: int) -> str | None:
    """Return a function name before an opening signature parenthesis."""
    name_match = FUNC_NAME_RE.search(blanked[:open_idx].rstrip())
    if name_match is None:
        return None
    if name_match.group(0) in {"if", "for", "while", "switch", "catch"}:
        return None
    return name_match.group(0)


def _iter_signature_candidates(text: str):
    """Yield function names and body offsets for single/multiline signatures."""
    blanked = _blank_literals_and_comments(text)
    for open_idx, close_idx in _iter_top_level_parentheses(blanked):
        body_idx = _signature_body_start(blanked, close_idx)
        if body_idx == -1:
            continue
        name = _signature_name(blanked, open_idx)
        if name is not None:
            yield name, body_idx


def _iter_functions(text: str):
    """Yield (name, body) for each function with a braced body."""
    # Compute the blanked copy once: every function match reuses the same
    # comment/literal-aware text, avoiding O(functions × text) rescans.
    blanked = _blank_literals_and_comments(text)
    for name, signature_end in _iter_signature_candidates(text):
        # Locate the opening body brace in comment- and literal-aware
        # text: a '{' inside a doc comment or string literal between the
        # signature and the real body must not be mistaken for the body
        # opener.  The blanked copy keeps every character position, so
        # the found offset maps back to the original text and the brace
        # balance / body extraction use the original content.
        open_idx = blanked.find("{", signature_end)
        if open_idx == -1:
            continue
        close_idx = _matching_brace(text, open_idx)
        if close_idx == -1:
            continue
        body = text[open_idx : close_idx + 1]
        yield name, body


def _blank_literals_and_comments(text: str) -> str:
    """Blank out comments, string literals, and char literals in-place.

    Keeps every character position (replaced with spaces) so the
    resulting offsets remain aligned with the original text.  Used only
    to find the opening body brace; braces are preserved so the first
    real '{' is discoverable.
    """
    return _LITERAL_ONLY_RE.sub(lambda m: " " * len(m.group(0)), text)


def audit_dir(directory: Path) -> tuple[list[str], list[str]]:
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
    violations, reviews = audit_dir(directory)

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
