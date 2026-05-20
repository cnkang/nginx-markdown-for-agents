#!/usr/bin/env python3
"""
Metric validator for v0.7.0 release gates.

Validates that all new v0.7.0 Prometheus metrics are properly defined
and documented across the required surfaces:

1. Each new metric exists in the C source code (metric struct field or
   SHM definition in filter_module.h / metrics_impl.h)
2. Each new metric has documentation in docs/guides/prometheus-metrics.md
3. Each new metric has a runtime write site (NGX_HTTP_MARKDOWN_METRIC_INC
   or similar macro in production source)
4. Each new metric name follows Prometheus naming conventions (lowercase,
   _total suffix for counters, snake_case)

New metrics validated:
- markdown_decompression_budget_exceeded_total
- markdown_decompression_format_error_total
- markdown_decompression_truncated_input_total
- markdown_decompression_io_error_total
- markdown_parse_timeouts_total
- markdown_parse_budget_exceeded_total
- markdown_replay_buffer_errors_total

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

# Source files to check for metric struct definitions
FILTER_MODULE_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_filter_module.h"
)
METRICS_IMPL_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_metrics_impl.h"
)

# Prometheus renderer (where metrics are emitted as Prometheus text)
PROMETHEUS_IMPL_H = (
    PROJECT_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_prometheus_impl.h"
)

# Production source files where METRIC_INC calls live
PRODUCTION_SOURCES = [
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_conversion_impl.h",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_streaming_impl.h",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_payload_impl.h",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_request_impl.h",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_module_state_impl.h",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_decision_log_impl.h",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_replay.c",
    PROJECT_ROOT / "components" / "nginx-module" / "src"
    / "ngx_http_markdown_body_filter.c",
]

# Documentation file
PROMETHEUS_METRICS_MD = PROJECT_ROOT / "docs" / "guides" / "prometheus-metrics.md"

# Metric definitions: short_name is the struct field path used in METRIC_INC,
# prometheus_name is the full Prometheus metric name with nginx_markdown_ prefix.
METRICS = [
    {
        "prometheus_name": "nginx_markdown_decompression_budget_exceeded_total",
        "short_name": "decompression_budget_exceeded_total",
        "struct_field": "budget_exceeded_total",
        "struct_parent": "decompressions",
        "metric_inc_pattern": r"decompressions\.budget_exceeded_total",
        "description": "decompression operations exceeding configured budget",
    },
    {
        "prometheus_name": "nginx_markdown_decompression_format_error_total",
        "short_name": "decompression_format_error_total",
        "struct_field": "format_error_total",
        "struct_parent": "decompressions",
        "metric_inc_pattern": r"decompressions\.format_error_total",
        "description": "decompression format errors",
    },
    {
        "prometheus_name": "nginx_markdown_decompression_truncated_input_total",
        "short_name": "decompression_truncated_input_total",
        "struct_field": "truncated_input_total",
        "struct_parent": "decompressions",
        "metric_inc_pattern": r"decompressions\.truncated_input_total",
        "description": "decompression truncated input errors",
    },
    {
        "prometheus_name": "nginx_markdown_decompression_io_error_total",
        "short_name": "decompression_io_error_total",
        "struct_field": "io_error_total",
        "struct_parent": "decompressions",
        "metric_inc_pattern": r"decompressions\.io_error_total",
        "description": "decompression I/O errors",
    },
    {
        "prometheus_name": "nginx_markdown_parse_timeouts_total",
        "short_name": "parse_timeouts_total",
        "struct_field": "parse_timeouts_total",
        "struct_parent": "parse_interrupts",
        "metric_inc_pattern": r"parse_interrupts\.parse_timeouts_total",
        "description": "parse operations exceeding configured timeout",
    },
    {
        "prometheus_name": "nginx_markdown_parse_budget_exceeded_total",
        "short_name": "parse_budget_exceeded_total",
        "struct_field": "parse_budget_exceeded_total",
        "struct_parent": "parse_interrupts",
        "metric_inc_pattern": r"parse_interrupts\.parse_budget_exceeded_total",
        "description": "parse operations exceeding memory budget",
    },
    {
        "prometheus_name": "nginx_markdown_replay_buffer_errors_total",
        "short_name": "replay_buffer_errors_total",
        "struct_field": "replay_buffer_errors_total",
        "struct_parent": None,
        "metric_inc_pattern": r"results\.replay_buffer_errors_total",
        "description": "replay buffer init/append failures",
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


def check_naming_convention(metric: dict, result: ValidationResult) -> None:
    """
    Check that the metric name follows Prometheus naming conventions:
    - lowercase only
    - snake_case
    - _total suffix for counters
    - starts with nginx_markdown_ namespace prefix
    """
    name = metric["prometheus_name"]
    check_id = f"naming:{metric['short_name']}"

    errors = []

    if name != name.lower():
        errors.append("not lowercase")

    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        errors.append("invalid characters (must be [a-z0-9_])")

    if not name.endswith("_total"):
        errors.append("counter metric missing _total suffix")

    if not name.startswith("nginx_markdown_"):
        errors.append("missing nginx_markdown_ namespace prefix")

    if errors:
        result.fail(check_id, f"naming violations: {'; '.join(errors)}")
    else:
        result.pass_(check_id, "follows Prometheus naming conventions")


def check_source_definition(
    metric: dict, filter_h: str, metrics_h: str, result: ValidationResult
) -> None:
    """
    Check that the metric has a struct field definition in the C source
    (filter_module.h or metrics_impl.h).
    """
    field = metric["struct_field"]
    check_id = f"source:{metric['short_name']}"

    # Look for the field name in struct definitions
    pattern = rf"\b{re.escape(field)}\b"
    if re.search(pattern, filter_h) or re.search(pattern, metrics_h):
        result.pass_(check_id, "struct field found in C source")
    else:
        result.fail(
            check_id,
            f"struct field '{field}' NOT found in "
            "filter_module.h or metrics_impl.h",
        )


def check_documentation(metric: dict, docs: str, result: ValidationResult) -> None:
    """Check that the metric is documented in prometheus-metrics.md."""
    name = metric["prometheus_name"]
    check_id = f"docs:{metric['short_name']}"

    if not docs:
        result.fail(check_id, "prometheus-metrics.md not found")
        return

    if name in docs:
        result.pass_(check_id, "documented in prometheus-metrics.md")
    else:
        result.fail(check_id, f"'{name}' NOT found in prometheus-metrics.md")


def check_runtime_write_site(
    metric: dict, sources: str, result: ValidationResult
) -> None:
    """
    Check that the metric has a runtime write site
    (NGX_HTTP_MARKDOWN_METRIC_INC or METRIC_ADD call).
    """
    pattern = metric["metric_inc_pattern"]
    check_id = f"write_site:{metric['short_name']}"

    # Search for METRIC_INC/METRIC_ADD with the field path
    metric_call_pattern = (
        rf"NGX_HTTP_MARKDOWN_METRIC_(INC|ADD)\(\s*{pattern}\s*\)"
    )
    if re.search(metric_call_pattern, sources):
        result.pass_(check_id, "runtime write site (METRIC_INC/ADD) found")
    else:
        result.fail(
            check_id,
            f"no METRIC_INC/ADD call found for pattern '{pattern}'",
        )


def check_prometheus_renderer(
    metric: dict, prometheus_src: str, result: ValidationResult
) -> None:
    """
    Check that the metric appears in the Prometheus text renderer output.
    """
    name = metric["prometheus_name"]
    check_id = f"prometheus:{metric['short_name']}"

    if not prometheus_src:
        result.fail(check_id, "prometheus_impl.h not found")
        return

    if name in prometheus_src:
        result.pass_(check_id, "present in Prometheus renderer output")
    else:
        result.fail(
            check_id,
            f"'{name}' NOT found in prometheus_impl.h renderer",
        )


def validate_all(result: ValidationResult) -> None:
    """Run all validation checks for v0.7.0 metrics."""
    filter_h = read_safe(FILTER_MODULE_H)
    metrics_h = read_safe(METRICS_IMPL_H)
    prometheus_src = read_safe(PROMETHEUS_IMPL_H)
    docs = read_safe(PROMETHEUS_METRICS_MD)

    all_sources = "".join(read_safe(src_path) for src_path in PRODUCTION_SOURCES)
    if not filter_h and not metrics_h:
        result.fail(
            "prereq:source",
            "neither filter_module.h nor metrics_impl.h found — "
            "cannot validate metric definitions",
        )
        return

    for metric in METRICS:
        check_naming_convention(metric, result)
        check_source_definition(metric, filter_h, metrics_h, result)
        check_documentation(metric, docs, result)
        check_runtime_write_site(metric, all_sources, result)
        check_prometheus_renderer(metric, prometheus_src, result)


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("v0.7.0 Metric Validation Report")
    print("=" * 70)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:45s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    """CLI entry point for v0.7.0 metric validation."""
    result = ValidationResult()
    validate_all(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
