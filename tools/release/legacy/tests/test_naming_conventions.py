"""Unit tests for naming convention validation helpers.

Tests each function with known-good and known-bad inputs derived from
the cross-spec naming convention reference.
"""

import pytest

from tools.release.legacy.naming_conventions import (
    is_valid_directive_name,
    is_valid_metric_name,
    is_valid_reason_code,
    is_valid_benchmark_field,
    is_valid_c_macro,
)


class TestDirectiveNames:
    def test_valid_existing_directives(self):
        valid = [
            "markdown_filter", "markdown_max_size", "markdown_timeout",
            "markdown_on_error", "markdown_metrics", "markdown_etag",
        ]
        for name in valid:
            assert is_valid_directive_name(name), f"{name} should be valid"

    def test_invalid_directives(self):
        invalid = [
            "filter", "MARKDOWN_FILTER", "markdown_", "markdown_Filter",
            "nginx_markdown_filter", "markdown-filter", "markdown_1start",
        ]
        for name in invalid:
            assert not is_valid_directive_name(name), f"{name} should be invalid"


class TestMetricNames:
    def test_valid_metrics(self):
        valid = [
            "nginx_markdown_conversions_total",
            "nginx_markdown_input_bytes",
            "nginx_markdown_duration_seconds",
            "nginx_markdown_duration_seconds_bucket",
            "nginx_markdown_duration_seconds_sum",
            "nginx_markdown_duration_seconds_count",
            "nginx_markdown_build_info",
            "nginx_markdown_failures_total",
        ]
        for name in valid:
            assert is_valid_metric_name(name), f"{name} should be valid"

    def test_invalid_metrics(self):
        invalid = [
            "markdown_conversions_total", "nginx_markdown_",
            "NGINX_MARKDOWN_TOTAL", "nginx_markdown_Conversions_total",
            "nginx_markdown_duration_bucket",
            "nginx_markdown_duration_count",
            "nginx_markdown_duration_sum",
        ]
        for name in invalid:
            assert not is_valid_metric_name(name), f"{name} should be invalid"


class TestReasonCodes:
    def test_valid_codes(self):
        valid = [
            "ELIGIBLE_CONVERTED", "SKIP_METHOD", "FAIL_CONVERSION",
            "SKIP_CONTENT_TYPE", "FAIL_RESOURCE_LIMIT",
        ]
        for name in valid:
            assert is_valid_reason_code(name), f"{name} should be valid"

    def test_invalid_codes(self):
        invalid = [
            "eligible_converted", "Skip_Method", "fail-conversion",
            "_SKIP_METHOD", "123CODE",
        ]
        for name in invalid:
            assert not is_valid_reason_code(name), f"{name} should be invalid"


class TestBenchmarkFields:
    def test_valid_fields(self):
        valid = [
            "token-reduction-percent", "p50-latency-ms", "input-bytes",
            "corpus-name", "conversion-result",
        ]
        for name in valid:
            assert is_valid_benchmark_field(name), f"{name} should be valid"

    def test_invalid_fields(self):
        invalid = [
            "Token-Reduction", "P50_latency_ms", "-input-bytes",
            "INPUT-BYTES", "123-field",
        ]
        for name in invalid:
            assert not is_valid_benchmark_field(name), f"{name} should be invalid"


class TestCMacros:
    def test_valid_macros(self):
        valid = [
            "NGX_HTTP_MARKDOWN_ON_ERROR_PASS",
            "NGX_HTTP_MARKDOWN_ON_ERROR_REJECT",
            "NGX_HTTP_MARKDOWN_FILTER_ENABLED",
        ]
        for name in valid:
            assert is_valid_c_macro(name), f"{name} should be valid"

    def test_invalid_macros(self):
        invalid = [
            "NGX_HTTP_MARKDOWN_", "ngx_http_markdown_on_error",
            "NGX_MARKDOWN_FILTER", "HTTP_MARKDOWN_FILTER",
        ]
        for name in invalid:
            assert not is_valid_c_macro(name), f"{name} should be invalid"
