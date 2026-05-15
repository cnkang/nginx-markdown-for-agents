#!/usr/bin/env python3
"""C const-correctness Detection Script.

Scans C source files for functions that take pointer parameters where
the function only reads through the pointer (does not modify the
pointed-to data), but the parameter is not const-qualified.

This addresses the repeated const-correctness fix pattern observed in
10+ commits during 2026-04 to 2026-05:
  - 579f3ab: const-qualify conf in is_excluded_stream_type
  - 9741286: const-qualify conf in select_processing_path
  - 86f34df: complete remaining const variable cleanup
  - a09669e: remove final body-filter const warnings
  - cd5cc53: finalize const-correct leak-period cleanup
  - dcf7f44: clear remaining leak-period const findings

Per AGENTS.md Rule 24, const-correctness is required for read-only
data paths.  This detector uses heuristic pattern matching (not a
full semantic analysis), so findings should be reviewed by a human.

Usage:
    python3 tools/harness/detect_const_correctness.py [directory]
    python3 tools/harness/detect_const_correctness.py components/nginx-module/src

Exit codes:
    0 — no findings (or only allowlisted patterns)
    1 — one or more findings requiring review
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# NGINX types commonly passed as non-const pointers that should be const
# when the function only reads through them.
NGINX_STRUCT_TYPES = re.compile(
    r"ngx_http_markdown_(?:conf_t|ctx_t|request_ctx_t|effective_conf_t|"
    r"dynconf_snapshot_t|metrics_t|otel_span_t)"
)

# Function parameter pattern: type *name or type *name,
# where type matches NGINX struct types and pointer is not const-qualified.
# This catches: ngx_http_markdown_conf_t *conf
# But not: const ngx_http_markdown_conf_t *conf
# Also not: ngx_http_markdown_conf_t *const conf (const pointer, mutable data)
NON_CONST_PARAM_RE = re.compile(
    r"(ngx_http_markdown_\w+_t)\s*\*\s*(\w+)"
)

# Check if const precedes the type
CONST_PREFIXED_RE = re.compile(
    r"const\s+ngx_http_markdown_\w+_t\s*\*"
)

# Lines that are comments
COMMENT_RE = re.compile(
    r"^\s*/\*|^\s*\*\s|^\s*//"
)

# Known files where non-const conf parameters are intentional
# (configuration init/merge/write code)
EXEMPT_FILES = {
    "ngx_http_markdown_config_core_impl.h",
    "ngx_http_markdown_config_handlers_impl.h",
    "ngx_http_markdown_config_impl.h",
    "ngx_http_markdown_config_directives_impl.h",
    "ngx_http_markdown_dynconf_impl.h",
    "ngx_http_markdown_filter_module.c",
    "ngx_http_markdown_module_state_impl.h",
    "ngx_http_markdown_lifecycle_impl.h",
    "ngx_http_markdown_otel.c",
}

# Known function patterns where non-const is intentional
# (init, merge, create, set, apply, write, free, update, reset)
INTENTIONAL_MUTATOR_RE = re.compile(
    r"(?:create|merge|init|set|apply|free|update|reset|destroy|alloc|"
    r"cleanup|write|handle_ctx_alloc_failure|bind_request_snapshot|"
    r"dynconf_snapshot_from_conf|dynconf_apply_snapshot|"
    r"build_effective_conf)"
)


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _has_const_prefix(line: str, match_start: int, type_name: str, param_name: str) -> bool:
    """Check whether const already qualifies this pointer parameter."""
    prefix = line[:match_start]
    if CONST_PREFIXED_RE.search(prefix):
        return True
    return bool(re.search(
        rf"const\s+{re.escape(type_name)}\s*\*\s*{re.escape(param_name)}",
        line,
    ))


def _is_in_mutator_function(line: str, match_start: int) -> bool:
    """Check whether the enclosing function name suggests intentional mutation."""
    context = line[:match_start] if match_start > 0 else line
    func_name = _extract_func_name(context)
    if not func_name:
        return False
    return bool(INTENTIONAL_MUTATOR_RE.search(func_name))


def _extract_func_name(context: str) -> str | None:
    """Extract the function token before '(' with bounded string parsing."""
    idx = context.find("(")
    if idx <= 0:
        return None
    head = context[:idx].strip()
    if not head:
        return None
    token = head.split()[-1]
    return token if token.isidentifier() else None


def _should_skip_line(line: str) -> bool:
    if COMMENT_RE.search(line):
        return True
    if "(" not in line:
        return True
    return bool(INTENTIONAL_MUTATOR_RE.search(line))


def _build_finding_message(
    rel: str, lineno: int, type_name: str, param_name: str, strict: bool,
) -> str:
    level = "ERROR" if strict else "WARNING"
    return (
        f"  {level} {rel}:{lineno} — non-const pointer parameter "
        f"'{type_name} *{param_name}' in read-only context "
        f"(consider const-qualification per AGENTS.md Rule 24)"
    )


def _check_line_for_const_violations(
    line: str, lineno: int, rel: str, strict: bool,
) -> tuple[list[str], list[str]]:
    """Check a single line for non-const pointer parameters.

    Args:
        line: Source line to check.
        lineno: Line number (1-indexed).
        rel: Repo-relative file path for reporting.
        strict: If True, promote warnings to errors.

    Returns:
        Tuple of (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if _should_skip_line(line):
        return errors, warnings

    for m in NON_CONST_PARAM_RE.finditer(line):
        type_name = m.group(1)
        param_name = m.group(2)

        if _has_const_prefix(line, m.start(), type_name, param_name):
            continue
        if _is_in_mutator_function(line, m.start()):
            continue

        msg = _build_finding_message(
            rel, lineno, type_name, param_name, strict,
        )
        if strict:
            errors.append(msg)
        else:
            warnings.append(msg)

    return errors, warnings


def check_file(
    filepath: Path, *, strict: bool = False,
) -> tuple[list[str], list[str]]:
    """Check a single C file for const-correctness violations.

    Args:
        filepath: Path to the C source file to check.
        strict: If True, promote warnings to errors.

    Returns:
        Tuple of (errors, warnings) lists.
    """
    errors: list[str] = []
    warnings: list[str] = []

    rel = _display_path(filepath)
    basename = filepath.name

    if basename in EXEMPT_FILES:
        return errors, warnings

    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return errors, warnings

    lines = source.splitlines()

    for lineno, line in enumerate(lines, start=1):
        line_errors, line_warnings = _check_line_for_const_violations(
            line, lineno, rel, strict,
        )
        errors.extend(line_errors)
        warnings.extend(line_warnings)

    return errors, warnings


def main() -> int:
    """Main entry point.

    Returns:
        Exit code: 0 for pass, 1 for findings.
    """
    parser = argparse.ArgumentParser(
        description="Detect const-correctness violations in C source files",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="components/nginx-module/src",
        help="Directory to scan (default: components/nginx-module/src); trusted input only",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat all findings as errors",
    )
    args = parser.parse_args()
    strict = args.strict

    scan_dir = validate_read_path(args.directory, purpose="scan directory")
    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory", file=sys.stderr)
        return 1

    print("=== C const-correctness Detection ===", file=sys.stderr)
    print(f"Scanning: {scan_dir}", file=sys.stderr)
    print("", file=sys.stderr)

    all_errors: list[str] = []
    all_warnings: list[str] = []

    c_files = sorted(scan_dir.rglob("*.c")) + sorted(scan_dir.rglob("*.h"))
    for filepath in c_files:
        file_errors, file_warnings = check_file(filepath, strict=strict)
        all_errors.extend(file_errors)
        all_warnings.extend(file_warnings)

    for e in all_errors:
        print(e, file=sys.stderr)
    for w in all_warnings:
        print(w, file=sys.stderr)

    print("", file=sys.stderr)
    print("=== Summary ===", file=sys.stderr)
    print(f"  Errors:   {len(all_errors)}", file=sys.stderr)
    print(f"  Warnings: {len(all_warnings)}", file=sys.stderr)
    print("", file=sys.stderr)

    if all_errors:
        print(
            f"FAIL: {len(all_errors)} error(s) found — fix before merge",
            file=sys.stderr,
        )
        return 1

    if all_warnings:
        print(
            f"PASS with warnings: {len(all_warnings)} warning(s) — review recommended",
            file=sys.stderr,
        )
        return 0

    print("PASS: no const-correctness findings", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
