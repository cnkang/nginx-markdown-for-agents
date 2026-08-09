#!/usr/bin/env python3
"""Validate canonical schema contracts and their implementation projections.

The release artifacts are versioned evidence.  They are checked against the
independent contracts in ``schemas/`` before implementation drift is checked
against the same contracts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import validate_read_path  # noqa: E402


DEFAULT_VERSION = "0.9.2"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

# Release artifact paths.  configure_release_version updates these for the
# CLI-selected version while preserving simple function-level test hooks.
RELEASE_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "release" / DEFAULT_VERSION
METRICS_REGISTRY = RELEASE_ARTIFACT_DIR / "metrics-registry.json"
DIAGNOSTICS_FIELD_CONTRACT = (
    RELEASE_ARTIFACT_DIR / "diagnostics-field-contract.json"
)
DYNCONF_PRECEDENCE_REPORT = (
    RELEASE_ARTIFACT_DIR / "dynconf-precedence-report.json"
)
CURRENT_VERSION = DEFAULT_VERSION

# Canonical contract paths
METRICS_CONTRACT = REPO_ROOT / "schemas" / "metrics-v1.registry.json"
DYNCONF_PRECEDENCE_CONTRACT = (
    REPO_ROOT / "schemas" / "dynconf-precedence-v1.json"
)
DYNCONF_IMPLEMENTATION_SOURCES = [
    "schemas/dynconf.schema.json",
    "components/rust-converter/src/dynconf/schema.rs",
    "components/nginx-module/src/ngx_http_markdown_dynconf_precedence.h",
]

# Schema paths
DYNCONF_SCHEMA = REPO_ROOT / "schemas" / "dynconf.schema.json"
DIAGNOSTICS_SCHEMA = REPO_ROOT / "schemas" / "diagnostics.schema.json"

# Tool paths
METRICS_VALIDATOR = (
    REPO_ROOT / "tools" / "release" / "gates" / "validate_metrics_registry.py"
)

# Expected schema artifacts (must all exist and be valid JSON)
RELEASE_ARTIFACTS = [
    DYNCONF_PRECEDENCE_REPORT,
    METRICS_REGISTRY,
    DIAGNOSTICS_FIELD_CONTRACT,
]


def configure_release_version(version: str) -> None:
    """Bind all versioned artifact paths to one validated release version."""
    global CURRENT_VERSION
    global RELEASE_ARTIFACT_DIR
    global METRICS_REGISTRY
    global DIAGNOSTICS_FIELD_CONTRACT
    global DYNCONF_PRECEDENCE_REPORT
    global RELEASE_ARTIFACTS

    if VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("version must use MAJOR.MINOR.PATCH")
    CURRENT_VERSION = version
    RELEASE_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "release" / version
    METRICS_REGISTRY = RELEASE_ARTIFACT_DIR / "metrics-registry.json"
    DIAGNOSTICS_FIELD_CONTRACT = (
        RELEASE_ARTIFACT_DIR / "diagnostics-field-contract.json"
    )
    DYNCONF_PRECEDENCE_REPORT = (
        RELEASE_ARTIFACT_DIR / "dynconf-precedence-report.json"
    )
    RELEASE_ARTIFACTS = [
        DYNCONF_PRECEDENCE_REPORT,
        METRICS_REGISTRY,
        DIAGNOSTICS_FIELD_CONTRACT,
    ]


def _load_json(path: Path) -> dict:
    """Load and return parsed JSON from a validated path."""
    validated_path = validate_read_path(path, purpose="schema drift artifact")
    try:
        value = json.loads(validated_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"schema drift artifact must be an object: {path}")
    return value


def gate_release_artifact_existence() -> list[str]:
    """Verify all release artifacts exist and are valid JSON objects."""
    errors = []
    for artifact_path in RELEASE_ARTIFACTS:
        try:
            rel = artifact_path.relative_to(REPO_ROOT)
        except ValueError:
            rel = artifact_path
        if not artifact_path.exists():
            errors.append(f"Missing release artifact: {rel}")
            continue
        try:
            _load_json(artifact_path)
        except (OSError, ValueError) as exc:
            errors.append(f"Release artifact unreadable: {rel}: {exc}")
    return errors


def _check_metrics_registry_structure(reg: dict) -> list[str]:
    """Check basic metrics artifact structure before the dedicated validator."""
    errors = []
    if reg.get("schema_version") != 1:
        errors.append("metrics-registry.json: schema_version != 1")
    families = reg.get("families", [])
    if not isinstance(families, list) or len(families) == 0:
        errors.append("metrics-registry.json: families empty or missing")
    return errors


def gate_release_artifact_structure() -> list[str]:
    """Validate structural invariants of each schema artifact."""
    errors = []
    if METRICS_REGISTRY.exists():
        errors.extend(
            _check_metrics_registry_structure(_load_json(METRICS_REGISTRY))
        )
    if DIAGNOSTICS_FIELD_CONTRACT.exists():
        contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
        if "effective_fields" not in contract:
            errors.append(
                "diagnostics-field-contract.json: effective_fields missing"
            )
        if "constraints" not in contract:
            errors.append(
                "diagnostics-field-contract.json: constraints missing"
            )
    if DYNCONF_PRECEDENCE_REPORT.exists():
        report = _load_json(DYNCONF_PRECEDENCE_REPORT)
        if "five_tier_precedence_hierarchy" not in report:
            errors.append(
                "dynconf-precedence-report.json: "
                "five_tier_precedence_hierarchy missing"
            )
    return errors


def gate_metrics_registry() -> list[str]:
    """Run the metrics validator with the selected artifact version."""
    if not METRICS_VALIDATOR.exists():
        return ["validate_metrics_registry.py not found"]
    result = subprocess.run(
        [
            sys.executable,
            str(METRICS_VALIDATOR),
            "--version",
            CURRENT_VERSION,
            "--artifact-dir",
            str(METRICS_REGISTRY.parent),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return [
            "Metrics registry v1 gate FAILED:\n"
            + result.stdout
            + result.stderr
        ]
    return []


def _compare_field_sets(contract_names, schema_names) -> list[str]:
    """Compare two field name sets and return errors for mismatches."""
    errors = []
    extra_in_contract = contract_names - schema_names
    if extra_in_contract:
        errors.append(
            f"diagnostics field contract has fields not in schema: "
            f"{sorted(extra_in_contract)}"
        )
    missing_from_contract = schema_names - contract_names
    if missing_from_contract:
        errors.append(
            f"diagnostics schema has fields not in field contract: "
            f"{sorted(missing_from_contract)}"
        )
    return errors


def _compare_field_detail(field_name, contract_def, schema_field) -> list[str]:
    """Compare a single field's type, enum, and bounds."""
    errors = []
    c_type = contract_def.get("type")
    s_type = schema_field.get("type")
    if c_type != s_type:
        errors.append(
            f"diagnostics field '{field_name}' type mismatch: "
            f"contract={c_type}, schema={s_type}"
        )
    c_enum = contract_def.get("enum")
    s_enum = schema_field.get("enum")
    if c_enum is not None and s_enum is not None and sorted(c_enum) != sorted(s_enum):
        errors.append(
            f"diagnostics field '{field_name}' enum mismatch: "
            f"contract={sorted(c_enum)}, schema={sorted(s_enum)}"
        )
    c_bounds = contract_def.get("bounds")
    if c_bounds is not None:
        if c_bounds.get("minimum") != schema_field.get("minimum"):
            errors.append(
                f"diagnostics field '{field_name}' minimum mismatch: "
                f"contract={c_bounds.get('minimum')}, "
                f"schema={schema_field.get('minimum')}"
            )
        if c_bounds.get("maximum") != schema_field.get("maximum"):
            errors.append(
                f"diagnostics field '{field_name}' maximum mismatch: "
                f"contract={c_bounds.get('maximum')}, "
                f"schema={schema_field.get('maximum')}"
            )
    return errors


def gate_diagnostics_field_contract() -> list[str]:
    """Validate diagnostics schema against the field contract artifact."""
    if not DIAGNOSTICS_FIELD_CONTRACT.exists():
        return ["diagnostics-field-contract.json missing"]
    if not DIAGNOSTICS_SCHEMA.exists():
        return ["diagnostics.schema.json missing"]
    contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
    schema = _load_json(DIAGNOSTICS_SCHEMA)
    schema_props = schema.get("$defs", {}).get("effective_config", {}).get(
        "properties", {}
    )
    contract_fields = contract.get("effective_fields", {})
    contract_names = set(contract_fields)
    schema_names = set(schema_props)
    errors = _compare_field_sets(contract_names, schema_names)
    for field_name in contract_names & schema_names:
        errors.extend(
            _compare_field_detail(
                field_name, contract_fields[field_name], schema_props[field_name]
            )
        )
    expected_count = contract.get("constraints", {}).get("field_count")
    if expected_count is not None:
        if len(contract_names) != expected_count:
            errors.append(
                f"diagnostics field contract field_count ({expected_count}) "
                f"!= actual ({len(contract_names)})"
            )
        if len(schema_names) != expected_count:
            errors.append(
                f"diagnostics schema effective_config has {len(schema_names)} "
                f"fields, expected {expected_count}"
            )
    return errors


def _check_dynconf_key_alignment(dynconf_keys: set[str]) -> list[str]:
    """Check dynconf keys match diagnostics and precedence contracts."""
    errors = []
    if DIAGNOSTICS_FIELD_CONTRACT.exists():
        contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
        contract_fields = set(contract.get("effective_fields", {}))
        if extra := dynconf_keys - contract_fields:
            errors.append(
                f"dynconf.schema.json has keys not in field contract: "
                f"{sorted(extra)}"
            )
        if missing := contract_fields - dynconf_keys:
            errors.append(
                f"Field contract has fields not in dynconf schema: "
                f"{sorted(missing)}"
            )
    if DYNCONF_PRECEDENCE_REPORT.exists():
        report = _load_json(DYNCONF_PRECEDENCE_REPORT)
        report_fields = set(
            report.get("field_specific_provenance_rules", {})
            .get("fields", {})
        )
        if extra := dynconf_keys - report_fields:
            errors.append(
                f"dynconf schema has keys not in precedence report: "
                f"{sorted(extra)}"
            )
        if extra := report_fields - dynconf_keys:
            errors.append(
                f"precedence report has fields not in dynconf schema: "
                f"{sorted(extra)}"
            )
    return errors


def _check_dynconf_streaming_buffer(schema_props: dict) -> list[str]:
    """Check streaming_buffer bounds match across schema and diagnostics contract."""
    if not DIAGNOSTICS_FIELD_CONTRACT.exists():
        return []
    contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
    schema_field = schema_props.get("streaming_buffer", {})
    contract_field = contract.get("effective_fields", {}).get(
        "streaming_buffer", {}
    )
    bounds = contract_field.get("bounds", {})
    errors = []
    if bounds.get("minimum") != schema_field.get("minimum"):
        errors.append(
            "dynconf streaming_buffer minimum mismatch: "
            f"schema={schema_field.get('minimum')}, "
            f"contract={bounds.get('minimum')}"
        )
    if bounds.get("maximum") != schema_field.get("maximum"):
        errors.append(
            "dynconf streaming_buffer maximum mismatch: "
            f"schema={schema_field.get('maximum')}, "
            f"contract={bounds.get('maximum')}"
        )
    return errors


def _check_precedence_header_contract(contract: dict) -> list[str]:
    """Compare the C precedence header's numbered descriptions to the contract."""
    header_path = (
        REPO_ROOT
        / "components"
        / "nginx-module"
        / "src"
        / "ngx_http_markdown_dynconf_precedence.h"
    )
    try:
        content = validate_read_path(
            header_path, purpose="dynconf precedence implementation"
        ).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return [f"dynconf precedence header unreadable: {exc}"]

    matches = []
    for line in content.splitlines():
        marker = line.lstrip(" \t")
        if not marker.startswith("*"):
            continue
        remainder = marker[1:].lstrip(" \t")
        tier_text, separator, description = remainder.partition(".")
        if (
            not separator
            or not tier_text
            or tier_text[0] not in "123456789"
            or not all(char in "0123456789" for char in tier_text)
        ):
            continue
        description = description.lstrip(" \t").rstrip()
        if description:
            matches.append((tier_text, description))
    expected = contract.get("five_tier_precedence_hierarchy", [])
    actual = [(int(tier), description) for tier, description in matches]
    expected_pairs = [
        (entry.get("tier"), entry.get("description")) for entry in expected
    ]
    if actual != expected_pairs:
        return [
            "dynconf precedence header drifted from "
            "schemas/dynconf-precedence-v1.json: "
            f"expected={expected_pairs!r}, actual={actual!r}"
        ]
    return []


def _check_precedence_contract_projection() -> list[str]:
    """Check the release report is an exact projection of the canonical contract."""
    if not DYNCONF_PRECEDENCE_CONTRACT.exists():
        return ["dynconf-precedence-v1.json missing"]
    if not DYNCONF_PRECEDENCE_REPORT.exists():
        return ["dynconf-precedence-report.json missing"]
    contract = _load_json(DYNCONF_PRECEDENCE_CONTRACT)
    report = _load_json(DYNCONF_PRECEDENCE_REPORT)
    contract_fields = (
        "schema_version",
        "allowed_provenance",
        "five_tier_precedence_hierarchy",
        "field_specific_provenance_rules",
    )
    errors = []
    for field in contract_fields:
        if report.get(field) != contract.get(field):
            errors.append(
                f"dynconf precedence artifact field '{field}' does not match "
                "schemas/dynconf-precedence-v1.json"
            )
    expected_contract_source = "schemas/dynconf-precedence-v1.json"
    if report.get("contract_source") != expected_contract_source:
        errors.append(
            "dynconf precedence artifact contract_source must be "
            f"{expected_contract_source}"
        )
    if report.get("implementation_sources") != DYNCONF_IMPLEMENTATION_SOURCES:
        errors.append(
            "dynconf precedence artifact implementation_sources must be "
            f"{DYNCONF_IMPLEMENTATION_SOURCES!r}"
        )
    if "source" in report:
        errors.append(
            "dynconf precedence artifact must not use the ambiguous source field"
        )
    errors.extend(_check_precedence_header_contract(contract))
    return errors


def gate_dynconf_schema() -> list[str]:
    """Validate dynconf schema and precedence against independent contracts."""
    if not DYNCONF_SCHEMA.exists():
        return ["dynconf.schema.json missing"]
    schema = _load_json(DYNCONF_SCHEMA)
    schema_props = schema.get("properties", {})
    dynconf_keys = {key for key in schema_props if key != "schema_version"}
    errors = _check_dynconf_key_alignment(dynconf_keys)
    errors.extend(_check_precedence_contract_projection())
    if schema.get("additionalProperties") is not False:
        errors.append("dynconf.schema.json must have additionalProperties: false")
    errors.extend(_check_dynconf_streaming_buffer(schema_props))
    if DYNCONF_PRECEDENCE_CONTRACT.exists():
        contract = _load_json(DYNCONF_PRECEDENCE_CONTRACT)
        report_fields = set(
            contract.get("field_specific_provenance_rules", {})
            .get("fields", {})
        )
        if report_fields != dynconf_keys:
            errors.append(
                "dynconf schema keys do not match canonical precedence contract: "
                f"schema={sorted(dynconf_keys)}, contract={sorted(report_fields)}"
            )
    return errors


def _run_gate(index, total, label, gate_fn):
    """Run a single gate and return errors list."""
    print(f"[{index}/{total}] {label}...")
    try:
        errors = gate_fn()
    except (OSError, ValueError) as exc:
        errors = [f"gate raised a structured input error: {exc}"]
    if errors:
        for error in errors:
            print(f"  FAIL: {error}")
    else:
        print("  PASS")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Run all schema drift gates and report results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    args = parser.parse_args(argv)
    if VERSION_PATTERN.fullmatch(args.version) is None:
        parser.error("--version must use MAJOR.MINOR.PATCH")
    configure_release_version(args.version)

    all_errors = []
    total = 4
    passed = 0
    print("=== Schema Drift Gate Validator ===")
    print()
    gates = [
        (
            "Validating release artifact existence and structure",
            lambda: gate_release_artifact_existence()
            + gate_release_artifact_structure(),
        ),
        ("Validating metrics registry drift", gate_metrics_registry),
        ("Validating diagnostics field contract", gate_diagnostics_field_contract),
        ("Validating dynconf schema and precedence consistency", gate_dynconf_schema),
    ]
    for index, (label, gate_fn) in enumerate(gates, 1):
        errors = _run_gate(index, total, label, gate_fn)
        all_errors.extend(errors)
        if not errors:
            passed += 1

    print()
    print(f"Summary: {passed}/{total} gates passed")
    if all_errors:
        print(f"\nFAILED: {len(all_errors)} error(s) found", file=sys.stderr)
        return 1
    print("\nPASSED: All schema drift gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
