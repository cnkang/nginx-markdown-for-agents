"""Regression tests for public-surface inventory drift detection."""

from __future__ import annotations

import pytest

from tools.harness import detect_public_surface_drift as detector


def test_inventory_loader_honors_explicit_path(monkeypatch) -> None:
    """Explicit inventory paths must reach the validated reader unchanged."""
    requested = "/tmp/custom-public-surface.json"
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"dynconf_keys": [], "metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(requested)["dynconf_keys"] == []
    assert seen == [detector.os.path.realpath(requested)]


def test_inventory_loader_resolves_explicit_symlink_path(tmp_path, monkeypatch) -> None:
    """Symlinked inventory paths must be resolved before repository reads."""
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
    """Dynamic-configuration additions and removals must be reported."""
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
    """Metric additions and removals must produce deterministic drift text."""
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
    """The checked-in inventory must match the currently extracted surfaces."""
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


def test_ffi_contract_parser_handles_multiline_signatures(monkeypatch) -> None:
    """FFI extraction must preserve multiline parameters and ABI metadata."""
    rust = """
pub unsafe extern "C" fn markdown_test(
    input: *const u8,
    output: *mut u8,
) ->
    u32 {
}

pub extern "C" fn markdown_empty() {
}
"""
    header = "#define MARKDOWN_ABI_VERSION 7\nmarkdown_test(\nmarkdown_empty(\n"

    def fake_read_text(path):
        return rust if path == "ffi.rs" else header

    monkeypatch.setattr(detector, "FFI_PATHS", ("ffi.rs",))
    monkeypatch.setattr(detector, "FFI_HEADER_PATH", "header.h")
    monkeypatch.setattr(detector, "read_text", fake_read_text)

    contract = detector.extract_ffi_contract_from_rust()

    assert contract["markdown_test"]["params"] == (
        "input: *const u8, output: *mut u8"
    )
    assert contract["markdown_test"]["signature"] == (
        "markdown_test(input: *const u8, output: *mut u8) -> u32"
    )
    assert contract["markdown_test"]["return_type"] == "u32"
    assert contract["markdown_test"]["safety"] == "unsafe"
    assert contract["markdown_empty"]["return_type"] == "()"


@pytest.mark.parametrize(
    ("inventory", "expected"),
    [
        (
            {"otel": {"directives": [None], "reject_only": []}},
            "otel.directives[0] must be an object",
        ),
        (
            {"metrics": [None]},
            "metrics[0] must be an object",
        ),
        (
            {"reason_codes": [None], "registry_count": 1},
            "reason_codes[0] must be an object",
        ),
        (
            {"ffi_exports": [{}, {"name": "markdown_test"}]},
            "ffi_exports[0] missing fields:",
        ),
    ],
)
def test_malformed_names_are_reported_without_raising(inventory, expected) -> None:
    """Malformed names must not escape schema validation as type errors."""
    errors = detector.validate_inventory_schema(inventory)

    assert any(error.startswith(expected) for error in errors)


def test_ffi_contract_comparison_normalizes_legacy_formatting() -> None:
    """Legacy trailing commas must not create FFI drift."""
    entry = {
        "name": "markdown_test",
        "signature": "markdown_test(input: *const u8,) -> u32",
        "params": "input: *const u8,",
        "return_type": "u32",
        "safety": "safe",
        "abi_version": 1,
        "generated_header": "header.h",
    }
    actual = dict(entry)
    actual["signature"] = "markdown_test(input: *const u8) -> u32"
    actual["params"] = "input: *const u8"

    assert detector.check_ffi_contract(
        {"ffi_exports": [entry]}, {"markdown_test": actual}
    ) == []


def test_invalid_inventory_schema_reports_contract_fields() -> None:
    """Malformed inventory metadata must report all missing contract fields."""
    errors = detector.validate_inventory_schema({
        "schema_version": "0.9.2",
        "dynconf_keys": [],
        "metrics": [],
    })

    assert "inventory missing top-level keys: contract_version, directives, ffi_abi_version, ffi_exports, otel, reason_codes, registry_count, reject_only_directives" in errors


def test_directive_metadata_drift_is_reported() -> None:
    """Directive context changes must be detected even when the name is stable."""
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
    """Malformed and duplicate command rows must stop source extraction."""
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
