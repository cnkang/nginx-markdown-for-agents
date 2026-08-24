"""Regression tests for the knowledge-base contract drift gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_kb_contract as checker


ROOT = Path(__file__).resolve().parents[3]


def _inputs() -> tuple[dict, str, str]:
    inventory = json.loads(
        (ROOT / "docs/harness/public-surface-inventory.json").read_text(
            encoding="utf-8"
        )
    )
    contract = (ROOT / "docs/knowledge-base/config-contract.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "docs/knowledge-base/README.md").read_text(encoding="utf-8")
    return inventory, contract, readme


def test_contract_matches_inventory():
    inventory, contract, readme = _inputs()
    assert checker.validate_contract(inventory, contract, readme) == []


def test_directive_drift_is_reported():
    inventory, contract, readme = _inputs()
    contract = contract.replace(
        "| `markdown_filter` | `on\\|off\\|$variable` | off | http/server/location |",
        "| `markdown_filter` | `on\\|off\\|$variable` | on | http/server/location |",
    )
    errors = checker.validate_contract(inventory, contract, readme)
    assert any("directives markdown_filter: default" in error for error in errors)


def test_metric_drift_is_reported():
    inventory, contract, readme = _inputs()
    contract = contract.replace(
        "| `nginx_markdown_requests_total` | counter | `outcome`, `reason`, `stage` | bounded |",
        "| `nginx_markdown_requests_total` | gauge | `outcome`, `reason`, `stage` | bounded |",
    )
    errors = checker.validate_contract(inventory, contract, readme)
    assert any("metrics nginx_markdown_requests_total: type" in error for error in errors)


def test_limit_key_drift_is_reported():
    inventory, contract, readme = _inputs()
    contract = contract.replace(
        "| `max_inflight` | Per-worker concurrent conversion bound |",
        "| `removed_limit` | Per-worker concurrent conversion bound |",
    )
    errors = checker.validate_contract(inventory, contract, readme)
    assert any("markdown_limits: missing rows" in error for error in errors)


def test_readme_frozen_numbers_are_rejected():
    inventory, contract, readme = _inputs()
    readme += "\n## Key Numbers\n\n| Active directives | 25 | source |\n"
    errors = checker.validate_contract(inventory, contract, readme)
    assert any("README must not duplicate" in error for error in errors)


def test_ffi_summary_drift_is_reported():
    inventory, contract, readme = _inputs()
    expected_heading = (
        f"## FFI Surface Summary ({len(inventory['ffi_exports'])} exports, "
        f"ABI v{inventory['ffi_abi_version']})"
    )
    contract = contract.replace(
        expected_heading,
        expected_heading.replace(
            f"{len(inventory['ffi_exports'])} exports",
            f"{len(inventory['ffi_exports']) - 1} exports",
        ),
    )
    errors = checker.validate_contract(inventory, contract, readme)
    assert any("FFI: summary export count" in error for error in errors)


def test_ffi_heading_values_are_literal_not_regular_expressions():
    inventory, contract, readme = _inputs()
    inventory["ffi_abi_version"] = "2.*"

    errors = checker.validate_contract(inventory, contract, readme)

    assert any(
        "FFI: summary export count or ABI heading does not match inventory" in error
        for error in errors
    )
