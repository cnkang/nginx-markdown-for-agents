#!/usr/bin/env python3
"""
detect_representation_metadata_clearing.py — Rule 69 (nginx-idioms).

Representation changes (HTML -> Markdown conversion commit, streaming
commit metadata removal, 304 Decision G, HEAD representation) must clear
EVERY upstream metadata surface in the same function, because NGINX
emission predicates read paired state and partial clears resurrect or
leak source-HTML metadata.  Historical chain: aac2f341, ccc320c7,
8b3633c1, de6cec38, 516df672, 0db8042e, a47a3e40, 1fa75117.

Checks per function body:

  T (trailer suppression): invalidating the Trailer declaration header
     (hdr_trailer marker) requires a ngx_http_markdown_clear_trailers()
     call in the same function.  HTTP/2/3 emit trailer entries without a
     Trailer declaration, so declaration-only invalidation leaks digests.

  P (mirror-pair strip completeness):
     - last_modified_time = (time_t) -1 requires ALL THREE of, in the
       same function: headers_out.last_modified = NULL, an invalidate
       call covering Last-Modified that clears every duplicate entry
       (stop_after_first = 0 — a stop_after_first = 1 argument fails the
       check), or delegation to send_304.  A partial clear resurrects
       the source mtime: the header filter synthesizes Last-Modified
       whenever last_modified_time != -1 AND last_modified == NULL is
       false, and a surviving duplicate list entry emits a second
       header.  (Historical defect: the HEAD path stripped the time
       mirror and invalidated only the FIRST list entry while leaving
       the typed pointer set — the previous OR-semantics accepted it.)
     - headers_out.last_modified = NULL requires the time mirror strip,
       an invalidate call, or send_304 delegation.
     - content_type_lowcase = NULL requires content_type_hash = 0.
     - content_type_hash = 0 requires content_type_lowcase = NULL.

Function discovery handles both NGINX definition styles: the single-line
`ngx_int_t foo(args) {` form and the multi-line form where the signature
spans lines and the brace opens on a later line.  The scan is fail-closed:
with --strict (or --fail-on-unparsed), a file whose production target
functions cannot be parsed reports an error instead of passing silently.
Every run prints functions_seen / functions_scanned / functions_skipped.

Usage:
    python3 tools/harness/detect_representation_metadata_clearing.py \
        [directory] [--strict]

Exit codes: 0 clean, 1 violations found, 2 usage/parse errors.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

DEFAULT_DIR = "components/nginx-module/src"

# Allowlist: "file:function:check:justification" (substring file match).
# Empty by default; entries require a written justification.
ALLOWLIST = [
]

# A function definition start: an identifier followed by an opening paren,
# with no statement-terminating punctuation before the paren (excludes
# if/while/for/switch calls and casts).  The signature may span lines.
FUNC_DEF_START_RE = re.compile(
    r"^[^;{}#]*\b([a-zA-Z_]\w*)\s*\("
)
# Control-flow keywords that look like a definition start but are calls.
CONTROL_FLOW_RE = re.compile(
    r"^\s*(if|while|for|switch|return|sizeof)\b"
)
FUNC_NAME_RE = re.compile(r"([a-zA-Z_]\w*)$")

# stop_after_first = 1 passed to an invalidate call: a duplicate list
# entry survives, so the invalidation is NOT complete.
LM_STOP_AFTER_FIRST_RE = re.compile(
    r"stop_after_first[^,;)]{0,40}1\b|,\s*1\s*,\s*NULL\s*\)"
)
LM_INVALIDATE_CALL_START_RE = re.compile(
    r"ngx_http_markdown_(?:invalidate_headers|"
    r"stream_commit_invalidate_header|invalidate_response_header)\s*\("
)

TRAILER_MARKER_RE = re.compile(r"hdr_trailer\b|[\"']Trailer[\"']")
CLEAR_TRAILERS_RE = re.compile(r"ngx_http_markdown_clear_trailers\s*\(")
LM_TIME_STRIP_RE = re.compile(
    r"->\s*headers_out\s*\.\s*last_modified_time\s*=\s*"
    r"(?:\(time_t\)\s*)-\s*1\b"
)
LM_PTR_NULL_RE = re.compile(
    r"->\s*headers_out\s*\.\s*last_modified\s*=\s*NULL\b"
)
LM_INVALIDATE_RE = re.compile(
    # A real list-invalidation call plus its Last-Modified argument
    # within the SAME call (bodies are normalized to one line).  Three
    # audited APIs exist across the module, all iterating every list
    # part with no early exit:
    #   - ngx_http_markdown_invalidate_headers(...)          (shared impl)
    #   - ngx_http_markdown_stream_commit_invalidate_header(...)
    #   - ngx_http_markdown_invalidate_response_header(...)  (304 path)
    # Matching any hdr_last_modified / "Last-Modified" occurrence anywhere
    # in the function would accept a function that merely mentions the
    # header without invalidating its list entries.
    r"ngx_http_markdown_"
    r"(?:invalidate_headers|stream_commit_invalidate_header"
    r"|invalidate_response_header)\s*\(.{0,200}?"
    r"(?:hdr_last_modified\b|[\"']Last-Modified[\"'])"
)
SEND_304_RE = re.compile(r"ngx_http_markdown_send_304\s*\(")
CT_LOWCASE_NULL_RE = re.compile(
    r"->\s*headers_out\s*\.\s*content_type_lowcase\s*=\s*NULL\b"
)
CT_HASH_ZERO_RE = re.compile(
    r"->\s*headers_out\s*\.\s*content_type_hash\s*=\s*0\b"
)


def _function_def_name(line):
    """Return the identifier preceding the parameter list, or None."""
    paren = line.find("(")
    if paren <= 0:
        return None
    match = FUNC_NAME_RE.search(line[:paren].rstrip())
    return match.group(1) if match else None


def _is_preprocessor_continuation(lines, start_index):
    """Return whether a candidate is part of a continued preprocessor line."""
    index = start_index - 1
    while index >= 0 and lines[index].rstrip().endswith("\\"):
        if lines[index].lstrip().startswith("#"):
            return True
        index -= 1
    return start_index > 0 and lines[start_index - 1].lstrip().startswith("#")


def _is_c_identifier(token):
    """Return whether *token* is an ASCII C identifier."""
    return bool(token) and (
        token[0].isalpha() or token[0] == "_"
    ) and all(char.isalnum() or char == "_" for char in token)


def _is_declaration_prefix(prefix):
    """Return whether a prefix consists only of C declaration tokens."""
    tokens = prefix.split()
    while tokens and tokens[-1] == "*":
        tokens.pop()
    return bool(tokens) and all(_is_c_identifier(token) for token in tokens)


def _looks_like_function_definition(lines, start_index, match):
    """Reject macro calls and accept C declaration/function-definition forms."""
    line = lines[start_index]
    prefix = line[:match.start(1)].strip()
    if prefix:
        return _is_declaration_prefix(prefix)

    index = start_index - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index < 0:
        return False
    previous = lines[index].strip()
    return _is_declaration_prefix(previous)


def _copy_string_literal(text, start, out):
    """Copy one C string or character literal and return the next index."""
    quote = text[start]
    out.append(quote)
    i = start + 1
    while i < len(text):
        c = text[i]
        out.append(c)
        i += 1
        if c == "\\" and i < len(text):
            out.append(text[i])
            i += 1
        elif c == quote:
            break
    return i


def _append_masked_text(text, out, preserve_newlines):
    """Append spaces for comment text while optionally preserving newlines."""
    for char in text:
        out.append("\n" if preserve_newlines and char == "\n" else " ")


def _consume_block_comment(text, start, out):
    """Mask a block comment and return the first index after it."""
    end = text.find("*/", start + 2)
    if end == -1:
        end = len(text)
    finish = end + 2 if end < len(text) else end
    _append_masked_text(text[start:finish], out, True)
    return finish


def _consume_line_comment(text, start, out):
    """Mask a line comment and return the newline index or end of input."""
    end = text.find("\n", start + 2)
    if end == -1:
        end = len(text)
    _append_masked_text(text[start:end], out, False)
    return end


def strip_comments(text):
    """Remove comments while preserving string literals and line numbers."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in ("'", '"'):
            i = _copy_string_literal(text, i, out)
        elif text.startswith("/*", i):
            i = _consume_block_comment(text, i, out)
        elif text.startswith("//", i):
            i = _consume_line_comment(text, i, out)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _function_body_is_complete(depth, line_index, start_index, line):
    return depth <= 0 and (
        line_index > start_index + 1 or "{" in line
    )


def _collect_function_body(lines, start_index):
    """Return the function body lines and the last consumed line index."""
    depth = 0
    body_lines = []
    for end_index, line in enumerate(
        lines[start_index + 1:], start=start_index + 1
    ):
        body_lines.append(line)
        depth += line.count("{") - line.count("}")
        if _function_body_is_complete(depth, end_index, start_index, line):
            break
    else:
        # Unterminated body: the collected lines span the rest of the source.
        # Consume through the final line so the caller's index advances past
        # them instead of returning start_index unchanged and re-scanning.
        return len(lines) - 1, body_lines
    return end_index, body_lines


def _signature_completes(lines, start_index, max_scan=32):
    """Return (ok, brace_line_index, terminated) for a signature.

    A definition is confirmed when a line whose first non-space character
    is `{` appears within max_scan lines and no `;` terminates the
    signature first (a `;` means it was a declaration or call).  The
    *terminated* flag distinguishes a declaration/call from a malformed
    or unsupported definition-shaped candidate.
    """
    paren_depth = 0
    limit = min(len(lines), start_index + max_scan)
    for idx in range(start_index, limit):
        line = lines[idx]
        if "{" in line and (
            idx == start_index or line.strip() == "{"
        ):
            return True, idx - 1, True  # collect the body from this line onward
        paren_depth += line.count("(") - line.count(")")
        # A semicolon outside parens ends a prototype/call, not a def.
        if ";" in re.sub(r"\([^()]*\)", "", line) and paren_depth <= 0:
            return False, start_index, True
    return False, start_index, False


def extract_functions_from_source(source, rejected=None):
    """Yield (name, start_line, normalized_body) parsed from C source text.

    Handles both single-line signatures (`ngx_int_t foo(args)` with the
    brace on the next line) and multi-line signatures where parameters
    span lines before the opening brace — the previous single-line-only
    matcher silently skipped multi-line functions, which is exactly the
    shape of the production helpers this detector must police.

    When *rejected* is a list, each definition-shaped candidate whose
    scan window closes without a `{` or `;` terminator is appended to it
    as ``(name, start_line)``.  Most of these are multi-line calls the
    window happened to consume, but a genuinely unsupported signature
    shape lands here too — recording them lets --strict refuse scans
    whose production coverage cannot be proven.
    """
    lines = strip_comments(source).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        match = FUNC_DEF_START_RE.match(line)
        if (
            not match
            or CONTROL_FLOW_RE.match(line)
            or "=" in line.split("(")[0]
            or _is_preprocessor_continuation(lines, i)
            or not _looks_like_function_definition(lines, i, match)
        ):
            i += 1
            continue
        name = match.group(1)
        ok, brace_idx, terminated = _signature_completes(lines, i)
        if not ok:
            if rejected is not None and not terminated:
                rejected.append((name, i + 1))
            i += 1
            continue
        end_index, body_lines = _collect_function_body(lines, brace_idx)
        raw = "\n".join(body_lines)
        yield name, i + 1, re.sub(r"\s+", " ", strip_comments(raw))
        i = end_index + 1


def extract_functions(path, rejected=None):
    """Yield (name, start_line, normalized_body) for each function.

    *rejected* collects ``(file_str, name, start_line)`` triples for
    definition-shaped candidates that could not be parsed.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"WARNING cannot read {path}: {exc}", file=sys.stderr)
        return
    if rejected is None:
        yield from extract_functions_from_source(source)
        return
    local = []
    yield from extract_functions_from_source(source, local)
    for name, line in local:
        rejected.append((str(path), name, line))


def _check_trailer_suppression(body, flag):
    """Rule 69 T: Trailer declaration invalidation implies clear_trailers()."""
    if TRAILER_MARKER_RE.search(body) and not CLEAR_TRAILERS_RE.search(body):
        flag("T", "Trailer declaration invalidated without "
                  "ngx_http_markdown_clear_trailers() in same function")


def _balanced_call_text(body, match):
    """Return one invalidate call, or None when its parentheses are unbalanced."""
    depth = 1
    index = match.end()
    while index < len(body) and depth:
        if body[index] == "(":
            depth += 1
        elif body[index] == ")":
            depth -= 1
        index += 1
    if depth:
        return None
    return body[match.start():index]


def _is_partial_lm_call(body, match):
    """Return whether one invalidate call stops after the first entry."""
    call = _balanced_call_text(body, match)
    if call is None:
        return False
    return bool(
        re.search(r"hdr_last_modified\b|[\"']Last-Modified[\"']", call)
        and LM_STOP_AFTER_FIRST_RE.search(call)
    )


def _has_partial_lm_invalidation(body):
    """Return whether an audited Last-Modified call stops after one entry."""
    for match in LM_INVALIDATE_CALL_START_RE.finditer(body):
        if _is_partial_lm_call(body, match):
            return True
    return False


def _check_last_modified_mirrors(body, flag):
    """Rule 69 P: Last-Modified mirror-pair/list completeness.

    Triple invariant: the time mirror, the typed pointer, and ALL list
    entries must be cleared together.  An invalidate call with
    stop_after_first=1 leaves duplicate entries alive, so it does not
    satisfy the completeness requirement.  The previous OR-semantics
    accepted "an invalidate exists" and missed exactly this defect.
    """
    time_strip = bool(LM_TIME_STRIP_RE.search(body))
    ptr_null = bool(LM_PTR_NULL_RE.search(body))
    invalidate = bool(LM_INVALIDATE_RE.search(body))
    partial_invalidate = _has_partial_lm_invalidation(body)
    delegates_304 = bool(SEND_304_RE.search(body))

    if time_strip:
        complete = (
            ptr_null
            and invalidate
            and not partial_invalidate
        )
        if not complete and not delegates_304:
            flag("P", "last_modified_time stripped to -1 without the full "
                      "mirror cleanup (typed pointer NULL + invalidate of "
                      "ALL Last-Modified entries; stop_after_first=1 is "
                      "incomplete) or send_304 delegation")
    if ptr_null and not (time_strip or invalidate or delegates_304):
        flag("P", "headers_out.last_modified set to NULL without stripping "
                  "last_modified_time mirror (or invalidate/delegation)")


def _check_content_type_mirrors(body, flag):
    """Rule 69 P: content_type lowcase/hash cache mirrors strip together."""
    lowcase_null = bool(CT_LOWCASE_NULL_RE.search(body))
    hash_zero = bool(CT_HASH_ZERO_RE.search(body))
    if lowcase_null and not hash_zero:
        flag("P", "content_type_lowcase cleared without content_type_hash=0")
    if hash_zero and not lowcase_null:
        flag("P", "content_type_hash zeroed without content_type_lowcase=NULL")


def check_body(path, name, start_line, body, findings):
    def flag(check, detail):
        findings.append((str(path), name, start_line, check, detail))

    _check_trailer_suppression(body, flag)
    _check_last_modified_mirrors(body, flag)
    _check_content_type_mirrors(body, flag)


def is_allowlisted(file_str, name, check):
    for entry in ALLOWLIST:
        parts = entry.split(":")
        if len(parts) < 4:
            continue
        allow_file, allow_func, allow_check = parts[0], parts[1], parts[2]
        if not allow_file or len(parts[3].strip()) < 5:
            continue
        if (
            allow_file in file_str
            and allow_func == name
            and allow_check == check
        ):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Rule 69 representation-metadata completeness check"
    )
    parser.add_argument("directory", nargs="?", default=DEFAULT_DIR)
    parser.add_argument("--strict", "--fail-on-unparsed",
                        dest="strict", action="store_true",
                        help="fail when definition-shaped candidates cannot "
                             "be parsed, instead of scanning only what "
                             "parsed cleanly")
    args = parser.parse_args()

    try:
        root = validate_read_path(
            args.directory, must_exist=True, purpose="scan directory"
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR invalid scan directory: {exc}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"ERROR scan path is not a directory: {root}", file=sys.stderr)
        return 2

    files = sorted(root.rglob("*.[ch]"))
    findings = []
    functions_seen = 0
    functions_scanned = 0
    unparsed = []
    for path in files:
        file_unparsed = []
        file_scanned = 0
        for name, start_line, body in extract_functions(
            path, rejected=file_unparsed
        ):
            file_scanned += 1
            check_body(path, name, start_line, body, findings)
        functions_scanned += file_scanned
        functions_seen += file_scanned + len(file_unparsed)
        unparsed.extend(file_unparsed)

    print(
        f"=== representation-metadata-clearing scan: "
        f"functions_seen={functions_seen} "
        f"functions_scanned={functions_scanned} "
        f"functions_skipped={len(unparsed)} "
        f"unparsed_candidates={len(unparsed)} ===",
        file=sys.stderr,
    )
    if args.strict and unparsed:
        for file_str, name, line in unparsed:
            print(
                f"UNPARSED {file_str}:{line} {name}: "
                f"definition-shaped candidate could not be parsed",
                file=sys.stderr,
            )
        print(
            "ERROR --strict: unparsed definition candidates present; "
            "production coverage cannot be proven (exit 2)",
            file=sys.stderr,
        )
        return 2

    active = [f for f in findings if not is_allowlisted(f[0], f[1], f[3])]
    for file_str, name, line, check, detail in active:
        print(f"VIOLATION [{check}] {file_str}:{line} {name}: {detail}",
              file=sys.stderr)

    print(f"=== representation-metadata-clearing check: "
          f"{len(files)} files, {len(findings)} finding(s), "
          f"{len(active)} violation(s) ===", file=sys.stderr)
    return 1 if active else 0


if __name__ == "__main__":
    sys.exit(main())
