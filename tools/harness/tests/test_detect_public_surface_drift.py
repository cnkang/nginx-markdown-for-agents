"""Regression tests for public-surface inventory drift detection."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid

import pytest

from tools.harness import detect_public_surface_drift as detector


def test_inventory_loader_honors_explicit_path(monkeypatch) -> None:
    """Explicit inventory paths must reach the validated reader unchanged."""
    requested = "/tmp/custom-public-surface.json"
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(requested)["metrics"] == []
    assert seen == [detector.os.path.realpath(requested)]


def test_inventory_loader_resolves_explicit_symlink_path(tmp_path, monkeypatch) -> None:
    """Symlinked inventory paths must be resolved before repository reads."""
    target = tmp_path / "public-surface.json"
    link = tmp_path / "public-surface-link.json"
    target.write_text('{"metrics": []}', encoding="utf-8")
    link.symlink_to(target)
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(str(link))["metrics"] == []
    assert seen == [str(target.resolve())]


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


def test_comment_metadata_parsers_preserve_contract_annotations() -> None:
    """Comment annotations remain stable while using deterministic parsing."""
    lines = [
        "markdown_test value (optional)",
        "Default: off",
        "Public default: on",
        "Public syntax: <size>",
        "Public status: active",
        "Migration:",
        "  -> markdown_replacement",
    ]

    assert detector._comment_syntax(lines, "markdown_test") == "value"
    assert detector._comment_public_metadata(lines) == {
        "default": "on",
        "syntax": "<size>",
        "status": "active",
    }
    assert detector._comment_migration(lines) == "markdown_replacement"


def test_metric_cardinality_policy_drift_is_reported() -> None:
    """A changed cardinality policy must fail the public-surface gate."""
    inventory = copy.deepcopy(detector.load_inventory())
    actual = detector.extract_metric_contract_from_c()
    metric_name = inventory["metrics"][0]["name"]
    mutated = not actual[
        metric_name
    ]["bounded_cardinality"]
    inventory["metrics"][0]["bounded_cardinality"] = mutated

    drift = detector.check_metric_contract(inventory, actual)

    assert drift == [
        "metric bounded_cardinality mismatch for {}: "
        "inventory={!r} source={!r}".format(
            metric_name,
            mutated,
            actual[metric_name]["bounded_cardinality"],
        )
    ]


def test_live_inventory_matches_all_extracted_surfaces() -> None:
    """The checked-in inventory must match the currently extracted surfaces."""
    inventory = detector.load_inventory()
    live_directives = detector.extract_directive_contract_from_c()

    # Post-convergence target contract (pre-LTS 0.9.2): 20 active directives,
    # no reject-only migration handlers left in the command table, and no OTel
    # surface.
    assert len(inventory["directives"]) == detector.FINAL_DIRECTIVE_COUNT
    assert len(inventory["reject_only_directives"]) == \
        detector.FINAL_REJECT_ONLY_COUNT
    assert inventory["otel"]["directives"] == []
    assert inventory["otel"]["reject_only"] == []
    assert inventory["otel"]["status"] == "removed"
    assert "dynconf_keys" not in inventory

    live_active = {
        name: entry for name, entry in live_directives.items()
        if entry["classification"] == "active"
    }
    live_reject = {
        name: entry for name, entry in live_directives.items()
        if entry["classification"] == "reject_only"
    }
    assert len(live_active) == detector.FINAL_DIRECTIVE_COUNT
    assert len(live_reject) == detector.FINAL_REJECT_ONLY_COUNT
    assert all(
        entry["handler"] == detector.REMOVED_DIRECTIVE_HANDLER
        for entry in live_reject.values()
    )

    assert detector.check_metrics(
        inventory, detector.extract_metric_names_from_c()
    ) == []
    assert detector.check_ffi_exports(
        inventory, detector.extract_ffi_exports_from_rust()
    ) == []


def test_live_inventory_passes_full_drift_check() -> None:
    """The checked-in inventory must pass every source-contract comparison."""
    inventory = detector.load_inventory()

    assert detector.validate_inventory_schema(inventory) == []
    assert detector.check_directive_contract(
        inventory, detector.extract_directive_contract_from_c()) == []
    assert detector.check_reason_contract(
        inventory, detector.extract_reason_contract_from_rust()) == []
    assert detector.check_metric_contract(
        inventory, detector.extract_metric_contract_from_c()) == []
    assert detector.check_ffi_contract(
        inventory, detector.extract_ffi_contract_from_rust()) == []


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
    header = (
        "#define MARKDOWN_ABI_VERSION 7\n"
        "uint32_t\n"
        "markdown_test(const uint8_t *input, uint8_t *output);\n"
        "void markdown_empty(void);\n"
    )

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


def test_c_ffi_parameter_name_stripping_preserves_pointer_types() -> None:
    """C parameter names are stripped without changing pointer qualifiers."""
    assert detector._c_ffi_param_types(
        "const uint8_t *input, uint8_t *output, size_t length"
    ) == ["ptr:const:u8", "ptr:mut:u8", "usize"]


def test_ffi_contract_rejects_changed_c_parameter_type(monkeypatch) -> None:
    """A C prototype type change must fail even when Rust export names match."""
    rust = (
        'pub unsafe extern "C" fn markdown_test(input: *const u8, '
        'output: *mut u8) -> u32 {}\n'
    )
    header = (
        "#define MARKDOWN_ABI_VERSION 7\n"
        "uint32_t markdown_test(const uint8_t *input, const uint8_t *output);\n"
    )

    def fake_read_text(path):
        return rust if path == "ffi.rs" else header

    monkeypatch.setattr(detector, "FFI_PATHS", ("ffi.rs",))
    monkeypatch.setattr(detector, "FFI_HEADER_PATH", "header.h")
    monkeypatch.setattr(detector, "read_text", fake_read_text)

    with pytest.raises(ValueError, match="parameter mismatch"):
        detector.extract_ffi_contract_from_rust()


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
        "schema_version": "0.9.3",
        "metrics": [],
    })

    assert "inventory missing top-level keys: contract_version, directive_count, directives, ffi_abi_version, ffi_exports, otel, reason_codes, registry_count, reject_only_directives" in errors


def test_reject_only_migration_directives_are_rejected() -> None:
    """No directive name may be declared as a reject-only migration entry."""
    inventory = copy.deepcopy(detector.load_inventory())
    assert inventory["reject_only_directives"] == []
    # Declaring a migration entry must fail: the removals dropped those names
    # from the command table entirely, so the inventory must stay empty.
    inventory["reject_only_directives"] = [{
        "name": "markdown_dynamic_config",
        "status": "reject_only",
        "classification": "reject_only",
        "handler": detector.REMOVED_DIRECTIVE_HANDLER,
        "migration_target": "markdown_filter",
        "default": "(not applicable)",
    }]

    errors = detector.validate_inventory_schema(inventory)

    assert (
        "post-convergence inventory must contain exactly {} reject-only "
        "migration directives".format(detector.FINAL_REJECT_ONLY_COUNT)
        in errors
    )
    assert "reject_only_directives contains unexpected names" in "\n".join(errors)


def test_reject_only_migration_directive_shape_is_validated() -> None:
    """A declared migration entry must still be shaped as reject-only."""
    inventory = copy.deepcopy(detector.load_inventory())
    inventory["reject_only_directives"] = [
        {
            "name": "markdown_dynamic_config",
            "status": "active",
            "classification": "reject_only",
            "handler": detector.REMOVED_DIRECTIVE_HANDLER,
            "migration_target": "markdown_filter",
            "default": "(not applicable)",
        },
        {
            "name": "markdown_dynamic_config_path",
            "status": "reject_only",
            "classification": "reject_only",
            "handler": "ngx_other_handler",
            "migration_target": "markdown_filter",
            "default": "(not applicable)",
        },
        {
            "name": "markdown_dynconf_dry_run",
            "status": "reject_only",
            "classification": "active",
            "handler": detector.REMOVED_DIRECTIVE_HANDLER,
            "migration_target": "markdown_filter",
            "default": "(not applicable)",
        },
    ]

    errors = detector.validate_inventory_schema(inventory)

    assert "reject_only_directives[0].status must be reject_only" in errors
    assert "reject_only_directives[2].classification must be reject_only" in errors
    assert (
        "reject_only_directives[1].handler must be {}".format(
            detector.REMOVED_DIRECTIVE_HANDLER)
        in errors
    )


def test_removed_otel_status_is_valid_for_empty_surface() -> None:
    """An empty OTel surface can be explicitly marked as removed."""
    inventory = copy.deepcopy(detector.load_inventory())
    inventory["otel"]["status"] = "removed"

    assert detector._validate_otel_schema(inventory) == []


def test_final_inventory_requires_frozen_public_counts() -> None:
    """The target inventory keeps the post-convergence 20/10 public counts."""
    inventory = copy.deepcopy(detector.load_inventory())
    inventory["directives"] = inventory["directives"][:-1]
    inventory["directive_count"] = detector.FINAL_DIRECTIVE_COUNT - 1
    inventory["metrics"] = inventory["metrics"][:-1]

    errors = detector.validate_inventory_schema(inventory)

    assert (
        "post-convergence inventory must contain exactly {} active "
        "directives".format(detector.FINAL_DIRECTIVE_COUNT)
        in errors
    )
    assert (
        "post-convergence inventory must contain exactly {} metric "
        "families".format(detector.FINAL_METRIC_COUNT)
        in errors
    )


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


@pytest.mark.parametrize(
    ("path", "replacement", "expected_path"),
    [
        ("otel.directives", [None], "otel.directives[0]"),
        ("otel.reject_only", [1], "otel.reject_only[0]"),
        ("metrics", [[]], "metrics[0]"),
        ("reason_codes", ["bad"], "reason_codes[0]"),
        ("ffi_exports", [{}], "ffi_exports[0]"),
        ("ffi_exports", [{"name": None}], "ffi_exports[0].name"),
        ("ffi_exports", [{"name": []}], "ffi_exports[0].name"),
        ("directives", [None], "directives[0]"),
        ("reject_only_directives", [None], "reject_only_directives[0]"),
    ],
)
def test_malformed_inventory_main_is_deterministic(
    path, replacement, expected_path
) -> None:
    """Every malformed inventory shape fails as stable DRIFT diagnostics."""
    inventory = copy.deepcopy(detector.load_inventory())
    target = inventory
    components = path.split(".")
    for component in components[:-1]:
        target = target[component]
    target[components[-1]] = replacement

    # Keep the temporary path inside the repository boundary enforced by the
    # production loader, while still making cleanup explicit and test-local.
    inventory_path = os.path.join(
        detector.ROOT, ".public-surface-invalid-{}.json".format(uuid.uuid4()))
    with open(inventory_path, "w", encoding="utf-8") as stream:
        json.dump(inventory, stream, sort_keys=True)
    try:
        command = [
            sys.executable,
            os.path.join(detector.ROOT, "tools", "harness",
                         "detect_public_surface_drift.py"),
            "--inventory",
            inventory_path,
        ]
        first = subprocess.run(
            command, cwd=detector.ROOT, text=True, capture_output=True,
            check=False,
        )
        second = subprocess.run(
            command, cwd=detector.ROOT, text=True, capture_output=True,
            check=False,
        )
    finally:
        os.unlink(inventory_path)

    assert first.returncode != 0
    assert "Traceback" not in first.stderr
    assert "DRIFT:" in first.stderr
    assert expected_path in first.stderr
    assert (first.stdout, first.stderr) == (second.stdout, second.stderr)
