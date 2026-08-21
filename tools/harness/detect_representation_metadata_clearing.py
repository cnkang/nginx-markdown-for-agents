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
     - last_modified_time = (time_t) -1 requires, in the same function:
       headers_out.last_modified = NULL, OR an invalidate_headers call
       covering Last-Modified, OR delegation to send_304.
     - headers_out.last_modified = NULL requires the time mirror strip,
       an invalidate call, or send_304 delegation.
     - content_type_lowcase = NULL requires content_type_hash = 0.
     - content_type_hash = 0 requires content_type_lowcase = NULL.

Usage:
    python3 tools/harness/detect_representation_metadata_clearing.py \
        [directory] [--strict]

Exit codes: 0 clean, 1 violations found.
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

FUNC_DEF_RE = re.compile(r"^[^();{}]*\([^;{}]*\)[ \t]*$")
FUNC_NAME_RE = re.compile(r"([a-zA-Z_]\w*)$")


def _function_def_name(line):
    """Return the identifier preceding the parameter list, or None."""
    paren = line.find("(")
    if paren <= 0:
        return None
    match = FUNC_NAME_RE.search(line[:paren].rstrip())
    return match.group(1) if match else None

TRAILER_MARKER_RE = re.compile(r"hdr_trailer\b|[\"']Trailer[\"']")
CLEAR_TRAILERS_RE = re.compile(r"ngx_http_markdown_clear_trailers\s*\(")
LM_TIME_STRIP_RE = re.compile(
    r"->\s*headers_out\s*\.\s*last_modified_time\s*=\s*"
    r"(?:\(time_t\)\s*)?-\s*1\b"
)
LM_PTR_NULL_RE = re.compile(
    r"->\s*headers_out\s*\.\s*last_modified\s*=\s*NULL\b"
)
LM_INVALIDATE_RE = re.compile(
    r"hdr_last_modified\b|[\"']Last-Modified[\"']"
)
SEND_304_RE = re.compile(r"ngx_http_markdown_send_304\s*\(")
CT_LOWCASE_NULL_RE = re.compile(
    r"->\s*headers_out\s*\.\s*content_type_lowcase\s*=\s*NULL\b"
)
CT_HASH_ZERO_RE = re.compile(
    r"->\s*headers_out\s*\.\s*content_type_hash\s*=\s*0\b"
)


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


def _skip_comment(text, start, terminator, include_terminator):
    """Return the index after a comment, preserving line-comment newlines."""
    end = text.find(terminator, start + 2)
    if end == -1:
        return len(text)
    return end + len(terminator) if include_terminator else end


def strip_comments(text):
    """Remove /* */ and // comments, preserving string literals."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in ("'", '"'):
            i = _copy_string_literal(text, i, out)
        elif text.startswith("/*", i):
            i = _skip_comment(text, i, "*/", True)
            out.append(" ")
        elif text.startswith("//", i):
            i = _skip_comment(text, i, "\n", False)
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
        return start_index, body_lines
    return end_index, body_lines


def extract_functions_from_source(source):
    """Yield (name, start_line, normalized_body) parsed from C source text."""
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        if not FUNC_DEF_RE.match(lines[i].rstrip()) or i + 1 >= len(lines):
            i += 1
            continue
        name = _function_def_name(lines[i].rstrip())
        if not name or lines[i + 1].strip() != "{":
            i += 1
            continue
        end_index, body_lines = _collect_function_body(lines, i)
        raw = "\n".join(body_lines)
        yield name, i + 1, re.sub(r"\s+", " ", strip_comments(raw))
        i = end_index + 1


def extract_functions(path):
    """Yield (name, start_line, normalized_body) for each function."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"WARNING cannot read {path}: {exc}", file=sys.stderr)
        return
    yield from extract_functions_from_source(source)


def check_body(path, name, start_line, body, findings):
    def flag(check, detail):
        findings.append((str(path), name, start_line, check, detail))

    has_trailer_invalidate = bool(TRAILER_MARKER_RE.search(body))
    has_clear_trailers = bool(CLEAR_TRAILERS_RE.search(body))
    if has_trailer_invalidate and not has_clear_trailers:
        flag("T", "Trailer declaration invalidated without "
                  "ngx_http_markdown_clear_trailers() in same function")

    lm_time_strip = bool(LM_TIME_STRIP_RE.search(body))
    lm_ptr_null = bool(LM_PTR_NULL_RE.search(body))
    lm_invalidate = bool(LM_INVALIDATE_RE.search(body))
    delegates_304 = bool(SEND_304_RE.search(body))

    if lm_time_strip and not (lm_ptr_null or lm_invalidate or delegates_304):
        flag("P", "last_modified_time stripped to -1 without clearing "
                  "headers_out.last_modified (or invalidate/delegation)")
    if lm_ptr_null and not (lm_time_strip or lm_invalidate or delegates_304):
        flag("P", "headers_out.last_modified set to NULL without stripping "
                  "last_modified_time mirror (or invalidate/delegation)")

    ct_lowcase_null = bool(CT_LOWCASE_NULL_RE.search(body))
    ct_hash_zero = bool(CT_HASH_ZERO_RE.search(body))
    if ct_lowcase_null and not ct_hash_zero:
        flag("P", "content_type_lowcase cleared without content_type_hash=0")
    if ct_hash_zero and not ct_lowcase_null:
        flag("P", "content_type_hash zeroed without content_type_lowcase=NULL")


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
    parser.add_argument("--strict", action="store_true",
                        help="kept for CLI symmetry; findings always block")
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
    for path in files:
        for name, start_line, body in extract_functions(path):
            check_body(path, name, start_line, body, findings)

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
