#!/usr/bin/env python3
"""
Reason-code validator for v0.7.0 release gates.

Validates that all new v0.7.0 reason codes are properly defined and aligned
across four surfaces:

1. Rust registry — The legacy v0.7.0 reason code maps to a current
   ReasonCode variant/string/metric family in
   components/rust-converter/src/decision/reason_code.rs
2. C define/constant — The reason code has a corresponding ERROR_* define
   in the C header (markdown_converter.h)
3. Documentation — The reason code is documented in
   docs/features/DECISION_CHAIN.md
4. Metrics — The reason code has a corresponding Prometheus metric in the
   renderer (ngx_http_markdown_prometheus_impl.h)

Reason codes validated:
- DECOMPRESSION_BUDGET_EXCEEDED
- DECOMPRESSION_FORMAT_ERROR
- DECOMPRESSION_TRUNCATED_INPUT
- DECOMPRESSION_IO_ERROR
- PARSE_TIMEOUT
- PARSE_BUDGET_EXCEEDED
- REPLAY_BUFFER_ERROR

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

# Source files to check
REASON_CODE_RS = (
    PROJECT_ROOT
    / "components"
    / "rust-converter"
    / "src"
    / "decision"
    / "reason_code.rs"
)

# C headers (cbindgen-generated and nginx-module copy)
C_HEADER_RUST = (
    PROJECT_ROOT
    / "components"
    / "rust-converter"
    / "include"
    / "markdown_converter.h"
)
C_HEADER_NGINX = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "markdown_converter.h"
)

# Documentation
DECISION_CHAIN_MD = PROJECT_ROOT / "docs" / "features" / "DECISION_CHAIN.md"

# Prometheus renderer
PROMETHEUS_IMPL_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_prometheus_impl.h"
)

# Metrics struct definition
FILTER_MODULE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_filter_module.h"
)

# Reason code definitions: each entry specifies what to look for in each surface.
# - rust_variant: the enum variant name in reason_code.rs
# - rust_as_str: the current lowercase string returned by as_str()
# - c_define: the ERROR_* define name in the C header (None if no separate define)
# - doc_pattern: text pattern to find in DECISION_CHAIN.md
# - metric_name: the full Prometheus metric name (nginx_markdown_ prefix)
# - metric_struct_field: the struct field name in the metrics struct
REASON_CODES = [
    {
        "name": "DECOMPRESSION_BUDGET_EXCEEDED",
        "rust_variant": "DecompressionBudgetExceeded",
        "rust_as_str": "decompression_budget_exceeded",
        "c_define": "ERROR_DECOMPRESSION_BUDGET_EXCEEDED",
        "doc_pattern": "DECOMPRESSION_BUDGET_EXCEEDED",
        "metric_name": "nginx_markdown_decompression_budget_exceeded_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "budget_exceeded_total",
        "description": "decompression output exceeded configured budget",
    },
    {
        "name": "DECOMPRESSION_FORMAT_ERROR",
        "rust_variant": "DecompressionFormatError",
        "rust_as_str": "decompression_format_error",
        "c_define": None,
        "doc_pattern": "decompression_format_error",
        "metric_name": "nginx_markdown_decompression_format_error_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "format_error_total",
        "description": "decompression input has invalid format",
    },
    {
        "name": "DECOMPRESSION_TRUNCATED_INPUT",
        "rust_variant": "DecompressionTruncatedInput",
        "rust_as_str": "decompression_truncated_input",
        "c_define": None,
        "doc_pattern": "decompression_truncated_input",
        "metric_name": "nginx_markdown_decompression_truncated_input_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "truncated_input_total",
        "description": "decompression input was truncated",
    },
    {
        "name": "DECOMPRESSION_IO_ERROR",
        "rust_variant": "DecompressionIoError",
        "rust_as_str": "decompression_io_error",
        "c_define": None,
        "doc_pattern": "decompression_io_error",
        "metric_name": "nginx_markdown_decompression_io_error_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "io_error_total",
        "description": "decompression I/O error",
    },
    {
        "name": "PARSE_TIMEOUT",
        "rust_variant": "Timeout",
        "rust_as_str": "timeout",
        "c_define": "ERROR_PARSE_TIMEOUT",
        "doc_pattern": "PARSE_TIMEOUT",
        "metric_name": "nginx_markdown_parse_timeouts_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "parse_timeouts_total",
        "description": "HTML parsing exceeded configured timeout",
    },
    {
        "name": "PARSE_BUDGET_EXCEEDED",
        "rust_variant": "BudgetExceeded",
        "rust_as_str": "budget_exceeded",
        "c_define": "ERROR_PARSE_BUDGET_EXCEEDED",
        "doc_pattern": "PARSE_BUDGET_EXCEEDED",
        "metric_name": "nginx_markdown_parse_budget_exceeded_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "parse_budget_exceeded_total",
        "description": "parser memory allocation exceeded budget",
    },
    {
        "name": "REPLAY_BUFFER_ERROR",
        "rust_variant": "ReplayError",
        "rust_as_str": "replay_error",
        "c_define": None,
        "doc_pattern": "REPLAY_BUFFER_ERROR",
        "metric_name": "nginx_markdown_replay_buffer_errors_total",
        "rust_metric_key": "markdown_errors_total",
        "metric_struct_field": "replay_buffer_errors_total",
        "description": "replay buffer init or append failure",
    },
    {
        "name": "SKIPPED_NO_ACCEPT",
        "rust_variant": "SkippedNoAccept",
        "rust_as_str": "skipped_no_accept",
        "c_define": None,
        "doc_pattern": "SKIPPED_NO_ACCEPT",
        "metric_name": "nginx_markdown_skips_total",
        "rust_metric_key": "markdown_skipped_total",
        "metric_struct_field": "no_accept",
        "description": "no Accept header present with on_wildcard off",
    },
    {
        "name": "SKIPPED_CONDITIONAL",
        "rust_variant": "SkippedConditional",
        "rust_as_str": "skipped_conditional",
        "c_define": None,
        "doc_pattern": "SKIPPED_CONDITIONAL",
        "metric_name": "nginx_markdown_skips_total",
        "rust_metric_key": "markdown_skipped_total",
        "metric_struct_field": "conditional",
        "description": "conditional request matched returning 304",
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
    return resolved.read_text(encoding="utf-8") if resolved.is_file() else ""


def check_rust_enum(
    code: dict, rust_src: str, result: ValidationResult
) -> None:
    """Check that the reason code exists as a Rust enum variant."""
    variant = code["rust_variant"]
    check_id = f"rust_enum:{code['name']}"

    # Look for the variant definition in the enum
    pattern = rf"\b{re.escape(variant)}\b\s*="
    if re.search(pattern, rust_src):
        result.pass_(check_id, f"enum variant '{variant}' found")
    else:
        result.fail(
            check_id,
            f"enum variant '{variant}' NOT found in reason_code.rs",
        )


def check_rust_as_str(
    code: dict, rust_src: str, result: ValidationResult
) -> None:
    """Check that the reason code has a string representation in as_str()."""
    as_str = code["rust_as_str"]
    check_id = f"rust_str:{code['name']}"

    pattern = rf'"{re.escape(as_str)}"'
    if re.search(pattern, rust_src):
        result.pass_(check_id, f"as_str() returns \"{as_str}\"")
    else:
        result.fail(
            check_id,
            f"string \"{as_str}\" NOT found in reason_code.rs as_str()",
        )


def check_rust_metric_key(
    code: dict, rust_src: str, result: ValidationResult
) -> None:
    """Check that the reason code has a metric_key() mapping in Rust."""
    metric = code.get("rust_metric_key", code["metric_name"].replace("nginx_", ""))
    variant = code["rust_variant"]
    check_id = f"rust_metric:{code['name']}"

    # Validate the match arm in ReasonCode::metric_key(). Current 0.9.0 code
    # intentionally groups multiple reasons into one metric family, so allow
    # alternatives before the arm's `=>`.
    pattern = (
        rf"ReasonCode::{re.escape(variant)}"
        rf"(?:(?!=>).)*=>\s*\{{?\s*"
        rf'"{re.escape(metric)}"'
    )
    if re.search(pattern, rust_src, re.DOTALL):
        result.pass_(check_id, f"metric_key() maps '{variant}' to '{metric}'")
    else:
        result.fail(
            check_id,
            f"metric_key() mapping '{variant} => \"{metric}\"' "
            "NOT found in reason_code.rs",
        )


def check_c_define(
    code: dict, c_header: str, result: ValidationResult
) -> None:
    """Check that the reason code has a C ERROR_* define."""
    c_define = code["c_define"]
    check_id = f"c_define:{code['name']}"

    if c_define is None:
        # This reason code does not require a separate C error define
        # (it's a reason code only, not an FFI error code)
        result.skip(
            check_id,
            "no separate C error define required (reason-code only)",
        )
        return

    pattern = rf"#define\s+{re.escape(c_define)}\b"
    if re.search(pattern, c_header):
        result.pass_(check_id, f"C define '{c_define}' found")
    else:
        result.fail(
            check_id,
            f"C define '{c_define}' NOT found in markdown_converter.h",
        )


def check_c_header_sync(
    code: dict, rust_header: str, nginx_header: str, result: ValidationResult
) -> None:
    """Check that both C header copies are in sync for this define."""
    c_define = code["c_define"]
    check_id = f"c_sync:{code['name']}"

    if c_define is None:
        result.skip(check_id, "no C define to sync")
        return

    pattern = rf"#define\s+{re.escape(c_define)}\b"
    in_rust = bool(re.search(pattern, rust_header))
    in_nginx = bool(re.search(pattern, nginx_header))

    if in_rust and in_nginx:
        result.pass_(check_id, "both header copies contain the define")
    elif in_rust:
        result.fail(check_id, "define in rust-converter header but NOT nginx-module copy")
    elif in_nginx:
        result.fail(check_id, "define in nginx-module copy but NOT rust-converter header")
    else:
        result.fail(check_id, "define missing from both header copies")


def check_documentation(
    code: dict, docs: str, result: ValidationResult
) -> None:
    """Check that the reason code is documented in DECISION_CHAIN.md."""
    doc_pattern = code["doc_pattern"]
    check_id = f"docs:{code['name']}"

    if not docs:
        result.fail(check_id, "DECISION_CHAIN.md not found")
        return

    if doc_pattern in docs:
        result.pass_(check_id, "documented in DECISION_CHAIN.md")
    else:
        result.fail(
            check_id,
            f"pattern '{doc_pattern}' NOT found in DECISION_CHAIN.md",
        )


def check_metric_struct(
    code: dict, filter_h: str, result: ValidationResult
) -> None:
    """Check that the metric has a struct field in the metrics definition."""
    field = code["metric_struct_field"]
    check_id = f"metric_struct:{code['name']}"

    if not filter_h:
        result.fail(check_id, "filter_module.h not found")
        return

    pattern = rf"\b{re.escape(field)}\b"
    if re.search(pattern, filter_h):
        result.pass_(check_id, f"struct field '{field}' found")
    else:
        result.fail(
            check_id,
            f"struct field '{field}' NOT found in filter_module.h",
        )


def check_prometheus_renderer(
    code: dict, prometheus_src: str, result: ValidationResult
) -> None:
    """Check that the metric appears in the Prometheus text renderer."""
    metric = code["metric_name"]
    check_id = f"prometheus:{code['name']}"

    if not prometheus_src:
        result.fail(check_id, "prometheus_impl.h not found")
        return

    if metric in prometheus_src:
        result.pass_(check_id, f"'{metric}' in Prometheus renderer")
    else:
        result.fail(
            check_id,
            f"'{metric}' NOT found in prometheus_impl.h",
        )


def validate_all(result: ValidationResult) -> None:
    """Run all validation checks for v0.7.0 reason codes."""
    rust_src = read_safe(REASON_CODE_RS)
    rust_header = read_safe(C_HEADER_RUST)
    nginx_header = read_safe(C_HEADER_NGINX)
    docs = read_safe(DECISION_CHAIN_MD)
    filter_h = read_safe(FILTER_MODULE_H)
    prometheus_src = read_safe(PROMETHEUS_IMPL_H)

    if not rust_src:
        result.fail(
            "prereq:reason_code.rs",
            "Rust reason_code.rs not found — cannot validate",
        )
        return

    if not rust_header and not nginx_header:
        result.fail(
            "prereq:c_header",
            "neither C header copy found — cannot validate C defines",
        )

    # Use whichever C header is available for define checks
    c_header = rust_header or nginx_header

    for code in REASON_CODES:
        # Surface 1: Rust enum
        check_rust_enum(code, rust_src, result)
        check_rust_as_str(code, rust_src, result)
        check_rust_metric_key(code, rust_src, result)

        # Surface 2: C define
        check_c_define(code, c_header, result)
        check_c_header_sync(code, rust_header, nginx_header, result)

        # Surface 3: Documentation
        check_documentation(code, docs, result)

        # Surface 4: Metrics
        check_metric_struct(code, filter_h, result)
        check_prometheus_renderer(code, prometheus_src, result)


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("v0.7.0 Reason Code Validation Report")
    print("=" * 70)
    print()
    print("Surfaces validated:")
    print("  1. Rust enum (reason_code.rs)")
    print("  2. C define (markdown_converter.h)")
    print("  3. Documentation (DECISION_CHAIN.md)")
    print("  4. Metrics (prometheus_impl.h + filter_module.h)")
    print()
    print("-" * 70)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:40s}  {message}")
    print()
    print("-" * 70)
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    """CLI entry point for v0.7.0 reason code validation."""
    result = ValidationResult()
    validate_all(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
