"""Naming convention regex validation helpers for 0.4.0 release gates.

These helpers validate operator-facing names against the regex patterns
defined in the cross-spec naming convention reference
(docs/project/release-gates/naming-conventions.md).

Used by both the validation script and property-based tests.
"""

import re

# Regex patterns from the design document
DIRECTIVE_PATTERN = re.compile(r'^markdown_[a-z][a-z0-9_]*$')
METRIC_PATTERN = re.compile(r'^nginx_markdown_[a-z][a-z0-9_]*(_total|_bytes|_seconds|_info)?$')
REASON_CODE_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]*$')
BENCHMARK_FIELD_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')
C_MACRO_PATTERN = re.compile(r'^NGX_HTTP_MARKDOWN_[A-Z][A-Z0-9_]*$')


def is_valid_directive_name(name: str) -> bool:
    """Validate an NGINX configuration directive name."""
    return bool(DIRECTIVE_PATTERN.match(name))


def is_valid_metric_name(name: str) -> bool:
    """Validate a Prometheus metric name."""
    return bool(METRIC_PATTERN.match(name))


def is_valid_reason_code(name: str) -> bool:
    """Validate a decision reason code."""
    return bool(REASON_CODE_PATTERN.match(name))


def is_valid_benchmark_field(name: str) -> bool:
    """Validate a benchmark report field key."""
    return bool(BENCHMARK_FIELD_PATTERN.match(name))


def is_valid_c_macro(name: str) -> bool:
    """Validate a C macro constant name."""
    return bool(C_MACRO_PATTERN.match(name))
