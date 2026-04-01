#!/usr/bin/env python3
"""
Naming convention validation for 0.5.0 release gates.

Validates NGINX directives, Prometheus metrics, decision reason codes,
and C macro constants against the naming conventions defined in
docs/project/naming-conventions-0-5-0.md.

Security: All regex patterns are pre-compiled constants — no user-supplied
patterns are compiled at runtime (ReDoS prevention).
"""

import re
import sys

# Pre-compiled naming convention patterns (constants, not user-supplied)
NGINX_DIRECTIVE_RE = re.compile(r"^markdown_(streaming_)?[a-z][a-z0-9_]*$")
PROMETHEUS_METRIC_RE = re.compile(
    r"^nginx_markdown_[a-z][a-z0-9_]*(_total|_bytes|_seconds|_info)?$"
)
REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
C_MACRO_RE = re.compile(r"^NGX_HTTP_MARKDOWN_[A-Z][A-Z0-9_]*$")

# High-cardinality labels that are forbidden in Prometheus metrics
FORBIDDEN_LABELS = frozenset(
    {"url", "host", "ua", "query", "referer", "remote_addr", "path"}
)


def is_valid_nginx_directive(name: str) -> bool:
    """Return True if *name* matches the NGINX directive naming convention."""
    return bool(NGINX_DIRECTIVE_RE.match(name))


def is_valid_prometheus_metric(name: str) -> bool:
    """Return True if *name* matches the Prometheus metric naming convention."""
    return bool(PROMETHEUS_METRIC_RE.match(name))


def is_valid_reason_code(code: str) -> bool:
    """Return True if *code* matches the decision reason code convention."""
    return bool(REASON_CODE_RE.match(code))


def is_valid_c_macro(name: str) -> bool:
    """Return True if *name* matches the C macro constant naming convention."""
    return bool(C_MACRO_RE.match(name))


def is_forbidden_label(label: str) -> bool:
    """Return True if *label* is in the forbidden high-cardinality set."""
    return label in FORBIDDEN_LABELS


def validate_names(
    directives: list[str] | None = None,
    metrics: list[str] | None = None,
    reason_codes: list[str] | None = None,
    c_macros: list[str] | None = None,
    labels: list[str] | None = None,
) -> dict[str, list[str]]:
    """Validate lists of names and return a dict of category → invalid names."""
    errors: dict[str, list[str]] = {}

    if directives:
        bad = [n for n in directives if not is_valid_nginx_directive(n)]
        if bad:
            errors["nginx_directives"] = bad

    if metrics:
        bad = [n for n in metrics if not is_valid_prometheus_metric(n)]
        if bad:
            errors["prometheus_metrics"] = bad

    if reason_codes:
        bad = [c for c in reason_codes if not is_valid_reason_code(c)]
        if bad:
            errors["reason_codes"] = bad

    if c_macros:
        bad = [n for n in c_macros if not is_valid_c_macro(n)]
        if bad:
            errors["c_macros"] = bad

    if labels:
        bad = [l for l in labels if is_forbidden_label(l)]
        if bad:
            errors["forbidden_labels"] = bad

    return errors


def main() -> int:
    """CLI entry point — validates known 0.5.0 names from the design doc."""
    known_directives = [
        "markdown_filter",
        "markdown_streaming_enabled",
    ]
    known_metrics = [
        "nginx_markdown_streaming_requests_total",
        "nginx_markdown_streaming_fallback_total",
        "nginx_markdown_streaming_precommit_failopen_total",
        "nginx_markdown_streaming_postcommit_error_total",
        "nginx_markdown_streaming_budget_exceeded_total",
        "nginx_markdown_streaming_peak_memory_bytes",
        "nginx_markdown_streaming_ttfb_seconds",
    ]
    known_reason_codes = [
        "STREAMING_CONVERT",
        "STREAMING_FALLBACK_PREBUFFER",
        "STREAMING_FAIL_POSTCOMMIT",
        "STREAMING_SKIP_UNSUPPORTED",
        "ENGINE_FULLBUFFER",
        "ENGINE_STREAMING",
        "STREAMING_BUDGET_EXCEEDED",
    ]

    errors = validate_names(
        directives=known_directives,
        metrics=known_metrics,
        reason_codes=known_reason_codes,
    )

    if errors:
        print("FAIL: naming convention violations found:")
        for category, names in errors.items():
            for name in names:
                print(f"  [{category}] {name}")
        return 1

    print("PASS: all known 0.5.0 names conform to naming conventions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
