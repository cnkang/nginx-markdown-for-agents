"""Regression tests for public-surface inventory drift detection."""

from __future__ import annotations

import pytest

from tools.harness import detect_public_surface_drift as detector


def test_inventory_loader_honors_explicit_path(monkeypatch) -> None:
    requested = "/tmp/custom-public-surface.json"
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"dynconf_keys": [], "metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(requested)["dynconf_keys"] == []
    assert seen == [detector.os.path.realpath(requested)]


def test_inventory_loader_resolves_explicit_symlink_path(tmp_path, monkeypatch) -> None:
    target = tmp_path / "public-surface.json"
    link = tmp_path / "public-surface-link.json"
    target.write_text('{"dynconf_keys": [], "metrics": []}', encoding="utf-8")
    link.symlink_to(target)
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"dynconf_keys": [], "metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(str(link))["dynconf_keys"] == []
    assert seen == [str(target.resolve())]


def test_dynconf_key_drift_is_reported() -> None:
    inventory = {"dynconf_keys": ["markdown_filter", "memory_budget"]}

    drift = detector.check_dynconf_keys(
        inventory,
        ["markdown_filter", "streaming_budget"],
    )

    assert drift == [
        "dynconf keys in source but not in inventory: streaming_budget",
        "dynconf keys in inventory but not in source: memory_budget",
    ]


def test_metric_drift_is_reported() -> None:
    inventory = {
        "metrics": [
            {"name": "nginx_markdown_requests_total"},
            {"name": "nginx_markdown_conversions_total"},
        ]
    }

    drift = detector.check_metrics(
        inventory,
        ["nginx_markdown_requests_total", "nginx_markdown_failures_total"],
    )

    assert drift == [
        "metrics in source but not in inventory: nginx_markdown_failures_total",
        "metrics in inventory but not in source: nginx_markdown_conversions_total",
    ]


def test_live_inventory_matches_all_extracted_surfaces() -> None:
    inventory = detector.load_inventory()

    assert detector.check_dynconf_keys(
        inventory, detector.extract_dynconf_keys_from_c()
    ) == []
    assert detector.check_metrics(
        inventory, detector.extract_metric_names_from_c()
    ) == []
    assert detector.check_ffi_exports(
        inventory, detector.extract_ffi_exports_from_rust()
    ) == []


def test_invalid_inventory_schema_reports_contract_fields() -> None:
    errors = detector.validate_inventory_schema({
        "schema_version": "0.9.2",
        "dynconf_keys": [],
        "metrics": [],
    })

    assert "inventory missing top-level keys: contract_version, directives, ffi_abi_version, ffi_exports, otel, reason_codes, reject_only_directives" in errors


def test_directive_metadata_drift_is_reported() -> None:
    inventory = {
        "directives": [{
            "name": "markdown_test",
            "classification": "active",
            "context": ["http"],
            "args": "no_args",
            "handler": "ngx_handler",
            "conf_offset": "NGX_HTTP_LOC_CONF_OFFSET",
            "source_flags": "NGX_HTTP_MAIN_CONF|NGX_CONF_NOARGS",
            "post": None,
            "otel_classification": "none",
        }],
        "reject_only_directives": [],
        "otel": {"directives": [], "reject_only": []},
    }
    actual = {
        "markdown_test": {
            "name": "markdown_test",
            "classification": "active",
            "context": ["server"],
            "args": "no_args",
            "handler": "ngx_handler",
            "conf_offset": "NGX_HTTP_LOC_CONF_OFFSET",
            "source_flags": "NGX_HTTP_MAIN_CONF|NGX_CONF_NOARGS",
            "post": None,
            "otel_classification": "none",
        }
    }

    assert detector.check_directive_contract(inventory, actual) == [
        "directive context mismatch for markdown_test: inventory=['http'] source=['server']"
    ]


def test_malformed_and_duplicate_directive_rows_fail_closed() -> None:
    duplicate = """
static ngx_command_t ngx_http_markdown_filter_commands[] = {
    { ngx_string(\"markdown_test\"), NGX_HTTP_MAIN_CONF|NGX_CONF_NOARGS, ngx_handler, NGX_HTTP_LOC_CONF_OFFSET, 0, NULL },
    { ngx_string(\"markdown_test\"), NGX_HTTP_MAIN_CONF|NGX_CONF_NOARGS, ngx_handler, NGX_HTTP_LOC_CONF_OFFSET, 0, NULL }
};
"""
    malformed = """
static ngx_command_t ngx_http_markdown_filter_commands[] = {
    { ngx_string(\"markdown_test\"), NGX_HTTP_MAIN_CONF|NGX_CONF_NOARGS, ngx_handler, NGX_HTTP_LOC_CONF_OFFSET, 0 }
};
"""

    with pytest.raises(ValueError, match="duplicate directive names"):
        detector._command_contracts(duplicate)
    with pytest.raises(ValueError, match="malformed ngx_command_t row"):
        detector._command_contracts(malformed)
