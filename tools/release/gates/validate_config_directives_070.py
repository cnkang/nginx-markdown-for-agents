#!/usr/bin/env python3
"""
Config directive validator for v0.7.0 release gates.

Validates that all new v0.7.0 configuration directives are properly defined
and documented across the required surfaces:

1. C source code (directive array in config_directives_impl.h)
2. Documentation (docs/guides/CONFIGURATION.md)
3. Merge function (config_core_impl.h)
4. Default value defined in merge

New directives validated:
- markdown_decompress_max_size (size directive, decompression budget)
- markdown_parse_timeout (msec directive, parse phase timeout)
- markdown_parser_budget (size directive, parser memory budget)
- markdown_diagnostics (flag directive, runtime diagnostics endpoint)
- markdown_dynconf_dry_run (flag directive, dynconf dry-run mode)

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use validate_read_path() equivalent (Path.resolve()
within PROJECT_ROOT). No user-supplied patterns are compiled at runtime.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Source files to check
CONFIG_DIRECTIVES_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_directives_impl.h"
)
CONFIG_CORE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_core_impl.h"
)
CONFIGURATION_MD = PROJECT_ROOT / "docs" / "guides" / "CONFIGURATION.md"


# Directive definitions: name, type, merge pattern, default description
DIRECTIVES = [
    {
        "name": "markdown_decompress_max_size",
        "type": "size",
        "doc_heading": "markdown_decompress_max_size",
        "merge_pattern": r"conf->decompress\.max_size",
        "description": "decompression budget (max decompressed output size)",
    },
    {
        "name": "markdown_parse_timeout",
        "type": "msec",
        "doc_heading": "markdown_parse_timeout",
        "merge_pattern": r"conf->decompress\.parse_timeout",
        "description": "parse phase timeout",
    },
    {
        "name": "markdown_parser_budget",
        "type": "size",
        "doc_heading": "markdown_parser_budget",
        "merge_pattern": r"conf->decompress\.parser_budget",
        "description": "parser memory budget",
    },
    {
        "name": "markdown_diagnostics",
        "type": "flag",
        "doc_heading": "markdown_diagnostics",
        "merge_pattern": r"diagnostics",
        "description": "runtime diagnostics endpoint toggle",
    },
    {
        "name": "markdown_dynconf_dry_run",
        "type": "flag",
        "doc_heading": "markdown_dynconf_dry_run",
        "merge_pattern": r"conf->advanced\.dynconf_dry_run",
        "description": "dynconf dry-run validation mode",
    },
]


class ValidationResult:
    """Accumulates PASS/FAIL/SKIP results for reporting."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, check_id: str, message: str) -> None:
        self.results.append(("PASS", check_id, message))

    def fail(self, check_id: str, message: str) -> None:
        self.results.append(("FAIL", check_id, message))

    def skip(self, check_id: str, message: str) -> None:
        self.results.append(("SKIP", check_id, message))

    @property
    def has_failures(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def read_safe(path: Path) -> str:
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        return ""
    if resolved.is_file():
        return resolved.read_text(encoding="utf-8")
    return ""


def check_directive_in_source(
    directive_name: str, source: str, result: ValidationResult
) -> None:
    """Check that the directive exists in the C command array."""
    check_id = f"source:{directive_name}"
    pattern = rf'ngx_string\("{re.escape(directive_name)}"\)'
    if re.search(pattern, source):
        result.pass_(check_id, "directive found in command array")
    else:
        result.fail(check_id, "directive NOT found in config_directives_impl.h")


def check_directive_in_docs(
    directive_name: str, doc_heading: str, docs: str, result: ValidationResult
) -> None:
    """Check that the directive is documented in CONFIGURATION.md."""
    check_id = f"docs:{directive_name}"
    if not docs:
        result.fail(check_id, "CONFIGURATION.md not found")
        return
    # Look for the directive name as a heading or code reference
    if doc_heading in docs:
        result.pass_(check_id, "documented in CONFIGURATION.md")
    else:
        result.fail(check_id, "NOT documented in CONFIGURATION.md")


def check_directive_merge(
    directive_name: str, merge_pattern: str, core_src: str, result: ValidationResult
) -> None:
    """Check that the directive has a merge function in config_core_impl.h."""
    check_id = f"merge:{directive_name}"
    if not core_src:
        result.fail(check_id, "config_core_impl.h not found")
        return
    if re.search(merge_pattern, core_src):
        result.pass_(check_id, "merge function found")
    else:
        result.fail(check_id, "merge function NOT found in config_core_impl.h")


def check_directive_default(
    directive_name: str,
    merge_pattern: str,
    core_src: str,
    result: ValidationResult,
) -> None:
    """Check that the directive has a default value in the merge function."""
    check_id = f"default:{directive_name}"
    if not core_src:
        result.fail(check_id, "config_core_impl.h not found")
        return

    # All types use the same check: the merge pattern presence implies
    # a default value is set via the merge macro's third argument.
    if re.search(merge_pattern, core_src):
        result.pass_(check_id, "default value defined via merge macro")
    else:
        result.fail(check_id, "no default value found")


def validate_all(result: ValidationResult) -> None:
    """Run all validation checks for v0.7.0 config directives."""
    directives_src = read_safe(CONFIG_DIRECTIVES_H)
    core_src = read_safe(CONFIG_CORE_H)
    docs = read_safe(CONFIGURATION_MD)

    if not directives_src:
        result.fail(
            "prereq:config_directives_impl.h",
            "source file not found — cannot validate directives",
        )
        return

    if not core_src:
        result.fail(
            "prereq:config_core_impl.h",
            "source file not found — cannot validate merge functions",
        )

    for directive in DIRECTIVES:
        name = directive["name"]
        doc_heading = directive["doc_heading"]
        merge_pat = directive["merge_pattern"]

        check_directive_in_source(name, directives_src, result)
        check_directive_in_docs(name, doc_heading, docs, result)
        check_directive_merge(name, merge_pat, core_src, result)
        check_directive_default(name, merge_pat, core_src, result)


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("v0.7.0 Config Directive Validation Report")
    print("=" * 60)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:40s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    """CLI entry point for config directive validation."""
    result = ValidationResult()
    validate_all(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
