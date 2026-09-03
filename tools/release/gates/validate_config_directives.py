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
CONFIG_MERGE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_config_merge_impl.h"
)
FILTER_MODULE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_filter_module.h"
)
CONFIGURATION_MD = PROJECT_ROOT / "docs" / "guides" / "CONFIGURATION.md"

# ── Expected C/header sources under components/nginx-module/src ──────────
# Explicit enumeration of the module sources that must exist for the
# removed-field absence proof to be sound.  Kept in sync with the
# ngx_module_srcs / ngx_module_deps lists in components/nginx-module/config.
EXPECTED_C_SOURCES = [
    "ngx_http_markdown_filter_module.c",
    "ngx_http_markdown_accept.c",
    "ngx_http_markdown_auth.c",
    "ngx_http_markdown_buffer.c",
    "ngx_http_markdown_eligibility.c",
    "ngx_http_markdown_error.c",
    "ngx_http_markdown_headers.c",
    "ngx_http_markdown_header_plan.c",
    "ngx_http_markdown_conditional.c",
    "ngx_http_markdown_decompression.c",
    "ngx_http_markdown_reason.c",
    "ngx_http_markdown_reason_ffi.c",
    "ngx_http_markdown_diagnostics_reason.c",
    "ngx_http_markdown_diagnostics.c",
    "ngx_http_markdown_stream_replay.c",
    "ngx_http_markdown_stream_commit.c",
    "ngx_http_markdown_stream_postcommit.c",
]

EXPECTED_H_SOURCES = [
    "ngx_http_markdown_filter_module.h",
    "ngx_http_markdown_header_plan.h",
    "ngx_http_markdown_diagnostics.h",
    "ngx_http_markdown_stream_replay.h",
    "ngx_http_markdown_stream_commit.h",
    "ngx_http_markdown_stream_postcommit.h",
]


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

# Each location-scoped directive must be represented by the shared merge
# implementation.  Keep the patterns tied to the actual config fields so a
# new command cannot silently become non-inheritable while still passing the
# command-table and documentation checks.
DIRECTIVE_MERGE_CONTRACTS: dict[str, tuple[str, str]] = {
    "markdown_filter": (
        r"ngx_http_markdown_merge_enabled\s*\(",
        r"conf->enabled_source\s*==\s*NGX_HTTP_MARKDOWN_ENABLED_UNSET",
    ),
    "markdown_limits": (
        r"ngx_http_markdown_merge_inherited_values\s*\(",
        r"ngx_conf_merge_(?:msec|size|uint)_value\(\s*conf->limits\.",
    ),
    "markdown_error_policy": (
        r"conf->on_error",
        r"ngx_conf_merge_uint_value\(\s*conf->on_error",
    ),
    "markdown_flavor": (
        r"conf->flavor",
        r"ngx_conf_merge_uint_value\(\s*conf->flavor",
    ),
    "markdown_token_estimate": (
        r"conf->token_estimate",
        r"ngx_conf_merge_value\(\s*conf->token_estimate",
    ),
    "markdown_front_matter": (
        r"conf->front_matter",
        r"ngx_conf_merge_value\(\s*conf->front_matter",
    ),
    "markdown_accept": (
        r"conf->accept_policy",
        r"ngx_conf_merge_uint_value\(\s*conf->accept_policy",
    ),
    "markdown_auth_policy": (
        r"conf->policy\.auth_policy",
        r"ngx_conf_merge_uint_value\(\s*conf->policy\.auth_policy",
    ),
    "markdown_auth_cookies": (
        r"conf->policy\.auth_cookies",
        r"ngx_conf_merge_ptr_value\(\s*conf->policy\.auth_cookies",
    ),
    "markdown_cache_validation": (
        r"conf->policy\.conditional_requests",
        r"ngx_conf_merge_uint_value\(\s*conf->policy\.conditional_requests",
    ),
    "markdown_streaming": (
        r"NGX_MD_MERGE_STREAM\(policy,",
        r"NGX_MD_MERGE_STREAM\(policy,",
    ),
    "markdown_log_verbosity": (
        r"conf->policy\.log_verbosity",
        r"ngx_conf_merge_uint_value\(\s*conf->policy\.log_verbosity",
    ),
    "markdown_content_types": (
        r"conf->routing\.content_types",
        r"ngx_conf_merge_ptr_value\(\s*conf->routing\.content_types",
    ),
    "markdown_metrics": (
        r"conf->ops\.metrics_enabled",
        r"ngx_conf_merge_value\(\s*conf->ops\.metrics_enabled",
    ),
    "markdown_prune_noise": (
        r"conf->advanced\.prune_noise",
        r"ngx_conf_merge_value\(\s*conf->advanced\.prune_noise",
    ),
    "markdown_prune_selectors": (
        r"conf->advanced\.prune_selectors",
        r"ngx_conf_merge_ptr_value\(\s*conf->advanced\.prune_selectors",
    ),
    "markdown_prune_protection_selectors": (
        r"conf->advanced\.prune_protection_selectors",
        r"ngx_conf_merge_ptr_value\(\s*conf->advanced\.prune_protection_selectors",
    ),
    "markdown_auto_decompress": (
        r"conf->decompress\.auto_decompress",
        r"ngx_conf_merge_value\(\s*conf->decompress\.auto_decompress",
    ),
    "markdown_dynamic_config": (
        r"conf->advanced\.dynconf_enabled",
        r"ngx_conf_merge_value\(\s*conf->advanced\.dynconf_enabled",
    ),
    "markdown_dynamic_config_path": (
        r"ngx_http_markdown_merge_str_if_unset\s*\(",
        r"ngx_http_markdown_merge_str_if_unset\s*\(",
    ),
    "markdown_dynconf_dry_run": (
        r"conf->advanced\.dynconf_dry_run",
        r"ngx_conf_merge_value\(\s*conf->advanced\.dynconf_dry_run",
    ),
    "markdown_diagnostics": (
        r"conf->ops\.diagnostics_enabled",
        r"ngx_conf_merge_value\(\s*conf->ops\.diagnostics_enabled",
    ),
    "markdown_stream_excluded_types": (
        r"conf->stream\.excluded_types",
        r"conf->stream\.excluded_types\s*==",
    ),
}

# Main-context directives do not inherit through a location merge function,
# but their initialization still needs an explicit default contract.
DIRECTIVE_DEFAULT_CONTRACTS: dict[str, str] = {
    "markdown_trusted_proxies": (
        r"conf->trusted_proxies\s*=\s*NULL"
        r"|conf->trusted_proxies_configured\s*=\s*0"
    ),
    "markdown_metrics_shm_size": (
        r"ngx_conf_init_size_value\(\s*mcf->metrics_shm_size"
    ),
}

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
    """Verify the shared merge implementation references the config field."""
    check_id = f"merge:{directive_name}"
    if not core_src:
        result.fail(check_id, "configuration merge source not found")
        return
    if re.search(merge_pattern, core_src):
        result.pass_(check_id, "merge function found")
    else:
        result.fail(check_id, "merge function NOT found in config_core_impl.h")


def check_limit_key(
    key: str, handler_src: str, docs: str, result: ValidationResult
) -> None:
    """Verify that a frozen markdown_limits key is implemented and documented."""
    if not handler_src or not docs:
        return
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
        result.fail(check_id, "configuration merge source not found")
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
    field_pattern: str, sources: dict[str, str | None], result: ValidationResult
) -> None:
    """Verify removed conf->streaming.* fields are absent from C sources."""
    check_id = f"removed-field:{field_pattern}"
    found_in = []
    missing_sources = []
    for name, content in sources.items():
        if content is None:
            result.fail(check_id, f"source file missing: {name}")
            missing_sources.append(name)
            continue
        if not content:
            continue
        if re.search(field_pattern, content):
            found_in.append(name)
    if found_in:
        result.fail(
            check_id,
            f"removed field pattern still present in: {', '.join(found_in)}",
        )
    elif missing_sources:
        # A missing source makes an absence verdict unsound: the field may
        # still be referenced in the file that could not be read.  The FAIL
        # record above already flags it; do not also emit a misleading PASS.
        return
    else:
        result.pass_(check_id, "removed field pattern absent from all sources")


def _read_directive_validation_sources() -> tuple[
    str, str, str, str, str, str, str
]:
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
        read_safe(CONFIG_MERGE_H),
        read_safe(FILTER_MODULE_H),
        read_safe(CONFIGURATION_MD),
        read_safe(handler_path),
    )


def _record_directive_prerequisite_failures(
    result: ValidationResult,
    directives_src: str,
    directive_names_src: str,
    core_src: str,
    merge_src: str,
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
            r"[ \t]+(?:\\[ \t]*\r?\n[ \t]*)?\"([^\"]+)\"",
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
    if not merge_src:
        result.fail(
            "prereq:config_merge_impl.h",
            "source file not found — cannot validate merge contracts",
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
    core_src: str,
    merge_src: str,
    filter_h: str,
    docs: str,
    handler_src: str,
    result: ValidationResult,
) -> None:
    """Validate active, limit, and removed directive contracts."""
    if not handler_src:
        result.fail(
            "limits-prerequisite:handler",
            "config handler source not found; cannot validate markdown_limits keys",
        )
    if not docs:
        result.fail(
            "limits-prerequisite:docs",
            "configuration documentation not found; cannot validate markdown_limits keys",
        )
    merge_contract_src = "\n".join((merge_src, filter_h))
    for name in CURRENT_DIRECTIVES:
        check_directive_in_source(name, directives_src, directive_macros, result)
        check_directive_in_docs(name, name, docs, result)
        merge_contract = DIRECTIVE_MERGE_CONTRACTS.get(name)
        if merge_contract is not None:
            merge_pattern, default_pattern = merge_contract
            check_directive_merge(
                name, merge_pattern, merge_contract_src, result
            )
            check_directive_default_merge(
                name, default_pattern, merge_contract_src, result
            )
        else:
            default_pattern = DIRECTIVE_DEFAULT_CONTRACTS.get(name)
            if default_pattern is None:
                result.fail(
                    f"contract:{name}",
                    "directive has no configured merge or default contract",
                )
            else:
                default_src = "\n".join(
                    (core_src, merge_src, filter_h, handler_src)
                )
                check_directive_default(
                    name, default_pattern, default_src, result
                )
    for key in CURRENT_LIMIT_KEYS:
        check_limit_key(key, handler_src, docs, result)
    for directive in REMOVED_DIRECTIVES:
        name = directive["name"]
        check_directive_not_in_source(name, directives_src, directive_macros, result)
        check_removed_directive_in_docs(name, directive["doc_heading"], docs, result)


def _read_c_sources() -> dict[str, str | None]:
    """Read the explicit C/header source files that back the
    removed-fields absence proof.

    Sources are enumerated by name (EXPECTED_C_SOURCES +
    EXPECTED_H_SOURCES) rather than discovered via rglob, so a deleted or
    renamed module file fails the gate instead of silently shrinking the
    scan surface.  A missing or unreadable source is recorded in the
    mapping as a ``missing`` marker (value None), which
    check_conf_field_not_in_source turns into a FAIL.
    """
    src_dir = PROJECT_ROOT / "components" / "nginx-module" / "src"
    sources: dict[str, str | None] = {}
    for rel_name in EXPECTED_C_SOURCES + EXPECTED_H_SOURCES:
        rel = f"components/nginx-module/src/{rel_name}"
        c_path = src_dir / rel_name
        if not c_path.is_file():
            sources[rel] = None
            continue
        try:
            content = read_safe(c_path)
        except (OSError, UnicodeError):
            # An unreadable source is not provably readable: record it as
            # missing so check_conf_field_not_in_source emits the
            # structured FAIL instead of crashing the gate.
            sources[rel] = None
            continue
        sources[rel] = content if content else None
    return sources


def validate_all(result: ValidationResult) -> None:
    """Run every directive check and record pass/fail in result."""
    (
        directives_src,
        directive_names_src,
        core_src,
        merge_src,
        filter_h,
        docs,
        handler_src,
    ) = _read_directive_validation_sources()

    if not directives_src:
        _record_directive_prerequisite_failures(
            result,
            directives_src,
            directive_names_src,
            core_src,
            merge_src,
            filter_h,
        )
        return

    directive_macros = _record_directive_prerequisite_failures(
        result,
        directives_src,
        directive_names_src,
        core_src,
        merge_src,
        filter_h,
    )
    _check_directive_contract(
        directives_src,
        directive_macros,
        core_src,
        merge_src,
        filter_h,
        docs,
        handler_src,
        result,
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
