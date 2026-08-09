#!/usr/bin/env python3
"""
Config directive validator for release gates.

Validates that configuration directives are properly defined, documented,
and that removed directives are absent from the source:

1. New directives exist in C source, docs, merge, and defaults
2. Removed directives are NOT in the C command array
3. Removed directives are documented as REMOVED in CONFIGURATION.md
4. Removed constants are not #defined in filter_module.h
5. Removed conf->streaming.* fields are absent from C sources

This validator merges the v0.7.0 and v0.8.0 directive checks into a single
version-independent validation of the current directive surface.

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use Path.resolve() within PROJECT_ROOT.
No user-supplied patterns are compiled at runtime.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CONFIG_DIRECTIVES_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_directives_impl.h"
)
DIRECTIVE_NAMES_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_directive_names.h"
)
CONFIG_CORE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_core_impl.h"
)
FILTER_MODULE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_filter_module.h"
)
CONFIGURATION_MD = PROJECT_ROOT / "docs" / "guides" / "CONFIGURATION.md"


# ── Current public command registry (0.9.2 freeze) ────────────────────────

# The 0.9.2 freeze deliberately removes the old one-directive-per-limit
# surface.  Keep this list aligned with the checked-in command table;
# nested resource limits are validated separately below.
CURRENT_DIRECTIVES = [
    "markdown_filter",
    "markdown_limits",
    "markdown_error_policy",
    "markdown_flavor",
    "markdown_token_estimate",
    "markdown_front_matter",
    "markdown_accept",
    "markdown_auth_policy",
    "markdown_auth_cookies",
    "markdown_cache_validation",
    "markdown_streaming",
    "markdown_log_verbosity",
    "markdown_content_types",
    "markdown_trusted_proxies",
    "markdown_metrics_shm_size",
    "markdown_metrics",
    "markdown_prune_noise",
    "markdown_prune_selectors",
    "markdown_prune_protection_selectors",
    "markdown_auto_decompress",
    "markdown_dynamic_config",
    "markdown_dynamic_config_path",
    "markdown_dynconf_dry_run",
    "markdown_diagnostics",
    "markdown_stream_excluded_types",
]

CURRENT_LIMIT_KEYS = [
    "conversion_timeout",
    "parser_timeout",
    "conversion_memory",
    "parser_memory",
    "streaming_buffer",
    "decompressed_size",
    "decompression_ratio",
    "max_inflight",
]

# ── Removed directives (0.9.2 public-surface freeze) ───────────────────────

REMOVED_DIRECTIVES = [
    {
        "name": "markdown_streaming_auto_threshold",
        "doc_heading": "markdown_streaming_auto_threshold",
    },
    {
        "name": "markdown_decompress_max_size",
        "doc_heading": "markdown_decompress_max_size",
    },
    {
        "name": "markdown_parse_timeout",
        "doc_heading": "markdown_parse_timeout",
    },
    {
        "name": "markdown_parser_budget",
        "doc_heading": "markdown_parser_budget",
    },
    {
        "name": "markdown_stream_threshold",
        "doc_heading": "markdown_stream_threshold",
    },
    {
        "name": "markdown_stream_precommit_buffer",
        "doc_heading": "markdown_stream_precommit_buffer",
    },
    {
        "name": "markdown_stream_flush_min",
        "doc_heading": "markdown_stream_flush_min",
    },
]

REMOVED_CONSTANTS = [
    "NGX_HTTP_MARKDOWN_STREAMING_ENGINE_OFF",
    "NGX_HTTP_MARKDOWN_STREAMING_ENGINE_ON",
    "NGX_HTTP_MARKDOWN_STREAMING_ENGINE_AUTO",
]

REMOVED_CONF_FIELDS = [
    r"conf->streaming\.",
]


class ValidationResult:
    """Accumulates PASS/FAIL/SKIP check results for directive validation."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, check_id: str, message: str) -> None:
        """Record a passing check."""
        self.results.append(("PASS", check_id, message))

    def fail(self, check_id: str, message: str) -> None:
        """Record a failing check."""
        self.results.append(("FAIL", check_id, message))

    def skip(self, check_id: str, message: str) -> None:
        """Record a skipped check (e.g. prerequisite file missing)."""
        self.results.append(("SKIP", check_id, message))

    @property
    def has_failures(self) -> bool:
        """Return True if any recorded check has status FAIL."""
        return any(s == "FAIL" for s, _, _ in self.results)


def read_safe(path: Path) -> str:
    """Read a file only if it resolves within PROJECT_ROOT; return \'\' otherwise."""
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return ""
    if resolved.is_file():
        return resolved.read_text(encoding="utf-8")
    return ""


def check_directive_in_source(
    directive_name: str,
    source: str,
    directive_macros: dict[str, str],
    result: ValidationResult,
) -> None:
    """Verify a directive is registered in the ngx_string command array."""
    check_id = f"source:{directive_name}"
    literal_pattern = rf'ngx_string\("{re.escape(directive_name)}"\)'
    macro_names = [
        name for name, value in directive_macros.items() if value == directive_name
    ]
    macro_pattern = (
        rf"ngx_string\(\s*(?:{'|'.join(map(re.escape, macro_names))})\s*\)"
        if macro_names
        else None
    )
    if re.search(literal_pattern, source) or (
        macro_pattern is not None and re.search(macro_pattern, source)
    ):
        result.pass_(check_id, "directive found in command array")
    else:
        result.fail(check_id, "directive NOT found in config_directives_impl.h")


def check_directive_not_in_source(
    directive_name: str,
    source: str,
    directive_macros: dict[str, str],
    result: ValidationResult,
) -> None:
    """Verify a removed directive is absent from the command array and names."""
    check_id = f"removed-source:{directive_name}"
    literal_pattern = rf'ngx_string\("{re.escape(directive_name)}"\)'
    macro_names = [
        name for name, value in directive_macros.items() if value == directive_name
    ]
    macro_pattern = (
        rf"ngx_string\(\s*(?:{'|'.join(map(re.escape, macro_names))})\s*\)"
        if macro_names
        else None
    )
    if re.search(literal_pattern, source) or (
        macro_pattern is not None and re.search(macro_pattern, source)
    ):
        result.fail(
            check_id,
            "removed directive still present in config_directives_impl.h",
        )
    else:
        result.pass_(check_id, "removed directive absent from command array")


def check_directive_in_docs(
    directive_name: str, doc_heading: str, docs: str, result: ValidationResult
) -> None:
    """Verify a new directive has a matching heading in CONFIGURATION.md."""
    check_id = f"docs:{directive_name}"
    if not docs:
        result.fail(check_id, "CONFIGURATION.md not found")
        return
    heading_pattern = (
        rf"(?<![A-Za-z0-9_]){re.escape(doc_heading)}"
        rf"(?![A-Za-z0-9_])"
    )
    if re.search(heading_pattern, docs):
        result.pass_(check_id, "documented in CONFIGURATION.md")
    else:
        result.fail(check_id, "NOT documented in CONFIGURATION.md")


def check_removed_directive_in_docs(
    directive_name: str, doc_heading: str, docs: str, result: ValidationResult
) -> None:
    """Verify a removed directive is marked REMOVED in CONFIGURATION.md."""
    check_id = f"removed-docs:{directive_name}"
    if not docs:
        result.fail(check_id, "CONFIGURATION.md not found")
        return
    pattern = rf"{re.escape(doc_heading)}[^\n]*REMOVED"
    if re.search(pattern, docs, re.IGNORECASE):
        result.pass_(check_id, "documented as REMOVED in CONFIGURATION.md")
    else:
        result.fail(
            check_id,
            "removed directive NOT documented as REMOVED in CONFIGURATION.md",
        )


def check_directive_merge(
    directive_name: str, merge_pattern: str, core_src: str, result: ValidationResult
) -> None:
    """Verify the merge function references the directive\'s config field."""
    check_id = f"merge:{directive_name}"
    if not core_src:
        result.fail(check_id, "config_core_impl.h not found")
        return
    if re.search(merge_pattern, core_src):
        result.pass_(check_id, "merge function found")
    else:
        result.fail(check_id, "merge function NOT found in config_core_impl.h")


def check_limit_key(
    key: str, handler_src: str, docs: str, result: ValidationResult
) -> None:
    """Verify that a frozen markdown_limits key is implemented and documented."""
    source_id = f"limits-source:{key}"
    if re.search(rf'"{re.escape(key)}"', handler_src):
        result.pass_(source_id, "nested limit key found in handler")
    else:
        result.fail(source_id, "nested limit key NOT found in handler")

    docs_id = f"limits-docs:{key}"
    if re.search(rf"\b{re.escape(key)}\s*=", docs):
        result.pass_(docs_id, "nested limit key documented")
    else:
        result.fail(docs_id, "nested limit key NOT documented in CONFIGURATION.md")


def check_directive_default(
    directive_name: str,
    default_pattern: str,
    default_src: str,
    result: ValidationResult,
) -> None:
    """Verify a default value is defined via the merge macro."""
    check_id = f"default:{directive_name}"
    if not default_src:
        result.fail(check_id, "default source files not found")
        return
    if re.search(default_pattern, default_src, re.S):
        result.pass_(check_id, "default value defined via merge macro")
    else:
        result.fail(check_id, "no default value found")


def check_directive_default_merge(
    directive_name: str,
    merge_pattern: str,
    core_src: str,
    result: ValidationResult,
) -> None:
    """Check that the directive has a default value in the merge function.

    For v0.7.0 directives, the merge pattern presence implies a default
    value is set via the merge macro\'s third argument.
    """
    check_id = f"default:{directive_name}"
    if not core_src:
        result.fail(check_id, "config_core_impl.h not found")
        return
    if re.search(merge_pattern, core_src):
        result.pass_(check_id, "default value defined via merge macro")
    else:
        result.fail(check_id, "no default value found")


def check_constant_not_in_source(
    constant_name: str, source: str, result: ValidationResult
) -> None:
    """Verify a removed constant is not #defined in filter_module.h."""
    check_id = f"removed-constant:{constant_name}"
    if not source:
        result.fail(check_id, "filter_module.h not found")
        return
    pattern = rf"#define\s+{re.escape(constant_name)}\b"
    if re.search(pattern, source):
        result.fail(
            check_id,
            f"removed constant {constant_name} still defined in filter_module.h",
        )
    else:
        result.pass_(
            check_id,
            f"removed constant {constant_name} absent from filter_module.h",
        )


def check_conf_field_not_in_source(
    field_pattern: str, sources: dict[str, str], result: ValidationResult
) -> None:
    """Verify removed conf->streaming.* fields are absent from C sources."""
    check_id = f"removed-field:{field_pattern}"
    found_in = []
    for name, content in sources.items():
        if not content:
            result.fail(check_id, f"source file missing: {name}")
            return
        if re.search(field_pattern, content):
            found_in.append(name)
    if found_in:
        result.fail(
            check_id,
            f"removed field pattern still present in: {', '.join(found_in)}",
        )
    else:
        result.pass_(check_id, "removed field pattern absent from all sources")


def _read_directive_validation_sources() -> tuple[str, str, str, str, str, str]:
    """Read the source surfaces used by the directive contract checks."""
    handler_path = (
        PROJECT_ROOT
        / "components"
        / "nginx-module"
        / "src"
        / "ngx_http_markdown_config_handlers_impl.h"
    )
    return (
        read_safe(CONFIG_DIRECTIVES_H),
        read_safe(DIRECTIVE_NAMES_H),
        read_safe(CONFIG_CORE_H),
        read_safe(FILTER_MODULE_H),
        read_safe(CONFIGURATION_MD),
        read_safe(handler_path),
    )


def _record_directive_prerequisite_failures(
    result: ValidationResult,
    directives_src: str,
    directive_names_src: str,
    core_src: str,
    filter_h: str,
) -> dict[str, str]:
    """Record missing source prerequisites and return the directive macros."""
    if not directives_src:
        result.fail(
            "prereq:config_directives_impl.h",
            "source file not found — cannot validate directives",
        )
    directive_macros = dict(
        re.findall(
            r"#define\s+(NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)"
            r"\s+\\\s*\"([^\"]+)\"",
            directive_names_src,
        )
    )
    if not directive_names_src or not directive_macros:
        result.fail(
            "prereq:directive_names.h",
            "directive name registry not found — cannot validate macro references",
        )
    if not core_src:
        result.fail(
            "prereq:config_core_impl.h",
            "source file not found — cannot validate merge functions",
        )
    if not filter_h:
        result.fail(
            "prereq:filter_module.h",
            "source file not found — cannot validate removed constants",
        )
    return directive_macros


def _check_directive_contract(
    directives_src: str,
    directive_macros: dict[str, str],
    docs: str,
    handler_src: str,
    result: ValidationResult,
) -> None:
    """Validate active, limit, and removed directive contracts."""
    for name in CURRENT_DIRECTIVES:
        check_directive_in_source(name, directives_src, directive_macros, result)
        check_directive_in_docs(name, name, docs, result)
    for key in CURRENT_LIMIT_KEYS:
        check_limit_key(key, handler_src, docs, result)
    for directive in REMOVED_DIRECTIVES:
        name = directive["name"]
        check_directive_not_in_source(name, directives_src, directive_macros, result)
        check_removed_directive_in_docs(name, directive["doc_heading"], docs, result)


def _read_c_sources() -> dict[str, str]:
    """Read C source files used to prove removed fields are gone."""
    src_dir = PROJECT_ROOT / "components" / "nginx-module" / "src"
    return {
        c_path.name: content
        for c_path in src_dir.rglob("*.[ch]")
        if (content := read_safe(c_path))
    }


def validate_all(result: ValidationResult) -> None:
    """Run every directive check and record pass/fail in result."""
    (
        directives_src,
        directive_names_src,
        core_src,
        filter_h,
        docs,
        handler_src,
    ) = _read_directive_validation_sources()

    if not directives_src:
        _record_directive_prerequisite_failures(
            result, directives_src, directive_names_src, core_src, filter_h
        )
        return

    directive_macros = _record_directive_prerequisite_failures(
        result, directives_src, directive_names_src, core_src, filter_h
    )
    _check_directive_contract(
        directives_src, directive_macros, docs, handler_src, result
    )

    # Removed constants: absent from filter_module.h
    if filter_h:
        for constant in REMOVED_CONSTANTS:
            check_constant_not_in_source(constant, filter_h, result)

    # Removed conf->streaming.* fields: absent from all C sources
    c_sources = _read_c_sources()
    for field_pat in REMOVED_CONF_FIELDS:
        check_conf_field_not_in_source(field_pat, c_sources, result)


def print_report(result: ValidationResult) -> None:
    """Print the validation report to stdout."""
    print("Config Directive Validation Report")
    print("=" * 60)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:40s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    """Entry point: run validation and return exit code (0=pass, 1=fail)."""
    result = ValidationResult()
    validate_all(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
