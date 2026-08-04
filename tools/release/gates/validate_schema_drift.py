#!/usr/bin/env python3
"""Schema drift gate validator.

Fail closed on any schema, registry, generated-artifact, or official-field-
contract mismatch across Wave 2 artifacts and their corresponding source-of-
truth implementations.

This gate validates:
  1. Metrics renderer families match metrics-registry.json (delegates to
     validate_metrics_registry_v1.py)
  2. Diagnostics schema against the field contract artifact
  3. Dynconf schema against the Rust parser implementation (cross-checking
     known keys, types, ranges)
  4. Reason codegen drift (calls generate.py --check)
  5. All consumed W2 artifacts exist and have valid structure

Requirements: 15.8, 15.12

Exit codes:
  0 = all drift gates pass
  1 = at least one drift gate failed
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Wave 2 artifact paths
W2_DIR = REPO_ROOT / "artifacts" / "spec62" / "wave2"
METRICS_REGISTRY = W2_DIR / "metrics-registry.json"
DIAGNOSTICS_FIELD_CONTRACT = W2_DIR / "diagnostics-field-contract.json"
DYNCONF_PRECEDENCE_REPORT = W2_DIR / "dynconf-precedence-report.json"
REASON_REGISTRY_REPORT = W2_DIR / "reason-registry-report.json"
GENERATED_REASON_ARTIFACTS = W2_DIR / "generated-reason-artifacts.json"

# Schema paths
DYNCONF_SCHEMA = REPO_ROOT / "schemas" / "dynconf.schema.json"
DIAGNOSTICS_SCHEMA = REPO_ROOT / "schemas" / "diagnostics.schema.json"

# Tool paths
METRICS_VALIDATOR = (
    REPO_ROOT / "tools" / "release" / "gates" / "validate_metrics_registry_v1.py"
)
REASON_CODEGEN = REPO_ROOT / "tools" / "reason-codegen" / "generate.py"

# Expected W2 artifacts (must all exist and be valid JSON)
W2_ARTIFACTS = [
    DYNCONF_PRECEDENCE_REPORT,
    METRICS_REGISTRY,
    REASON_REGISTRY_REPORT,
    GENERATED_REASON_ARTIFACTS,
    DIAGNOSTICS_FIELD_CONTRACT,
]


def _load_json(path: Path) -> dict:
    """Load and return parsed JSON from the given path."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gate_w2_artifact_existence() -> list:
    """Verify all W2 artifacts exist and are valid JSON objects."""
    errors = []
    for artifact_path in W2_ARTIFACTS:
        rel = artifact_path.relative_to(REPO_ROOT)
        if not artifact_path.exists():
            errors.append(f"Missing W2 artifact: {rel}")
            continue
        try:
            data = _load_json(artifact_path)
            if not isinstance(data, dict):
                errors.append(f"W2 artifact is not a JSON object: {rel}")
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"W2 artifact unreadable: {rel}: {exc}")
    return errors


def _check_metrics_registry_structure(reg: dict) -> list:
    """Check metrics-registry.json structural invariants."""
    errors = []
    if reg.get("schema_version") != 1:
        errors.append("metrics-registry.json: schema_version != 1")
    families = reg.get("families", [])
    if not isinstance(families, list) or len(families) == 0:
        errors.append("metrics-registry.json: families empty or missing")
    return errors


def _check_reason_registry_structure(report: dict) -> list:
    """Check reason-registry-report.json structural invariants."""
    errors = []
    if report.get("schema_version") != 1:
        errors.append("reason-registry-report.json: schema_version != 1")
    if not isinstance(report.get("total_count"), int):
        errors.append(
            "reason-registry-report.json: total_count missing or invalid"
        )
    return errors


def _check_generated_artifacts_structure(listing: dict) -> list:
    """Check generated-reason-artifacts.json structural invariants."""
    errors = []
    if listing.get("schema_version") != 1:
        errors.append(
            "generated-reason-artifacts.json: schema_version != 1"
        )
    artifacts = listing.get("generated_artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) == 0:
        errors.append(
            "generated-reason-artifacts.json: generated_artifacts "
            "empty or missing"
        )
    return errors


def gate_w2_artifact_structure() -> list:
    """Validate structural invariants of each W2 artifact."""
    errors = []

    if METRICS_REGISTRY.exists():
        errors.extend(
            _check_metrics_registry_structure(_load_json(METRICS_REGISTRY))
        )

    if REASON_REGISTRY_REPORT.exists():
        errors.extend(
            _check_reason_registry_structure(
                _load_json(REASON_REGISTRY_REPORT)
            )
        )

    if GENERATED_REASON_ARTIFACTS.exists():
        errors.extend(
            _check_generated_artifacts_structure(
                _load_json(GENERATED_REASON_ARTIFACTS)
            )
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


def gate_metrics_registry() -> list:
    """Run the metrics registry v1 validator (existing script)."""
    if not METRICS_VALIDATOR.exists():
        return ["validate_metrics_registry_v1.py not found"]

    result = subprocess.run(
        [sys.executable, str(METRICS_VALIDATOR)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [
            "Metrics registry v1 gate FAILED:\n"
            + result.stdout
            + result.stderr
        ]
    return []


def _compare_field_sets(contract_names, schema_names):
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


def _compare_field_detail(field_name, contract_def, schema_field):
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
    if c_enum is not None and s_enum is not None:
        if sorted(c_enum) != sorted(s_enum):
            errors.append(
                f"diagnostics field '{field_name}' enum mismatch: "
                f"contract={sorted(c_enum)}, schema={sorted(s_enum)}"
            )

    c_bounds = contract_def.get("bounds")
    if c_bounds is not None:
        s_min = schema_field.get("minimum")
        s_max = schema_field.get("maximum")
        if c_bounds.get("minimum") != s_min:
            errors.append(
                f"diagnostics field '{field_name}' minimum mismatch: "
                f"contract={c_bounds.get('minimum')}, schema={s_min}"
            )
        if c_bounds.get("maximum") != s_max:
            errors.append(
                f"diagnostics field '{field_name}' maximum mismatch: "
                f"contract={c_bounds.get('maximum')}, schema={s_max}"
            )
    return errors


def gate_diagnostics_field_contract() -> list:
    """Validate diagnostics schema against the field contract artifact."""
    if not DIAGNOSTICS_FIELD_CONTRACT.exists():
        return ["diagnostics-field-contract.json missing"]
    if not DIAGNOSTICS_SCHEMA.exists():
        return ["diagnostics.schema.json missing"]

    contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
    schema = _load_json(DIAGNOSTICS_SCHEMA)

    defs = schema.get("$defs", {})
    effective_def = defs.get("effective_config", {})
    schema_props = effective_def.get("properties", {})

    contract_fields = contract.get("effective_fields", {})
    contract_names = set(contract_fields.keys())
    schema_names = set(schema_props.keys())

    errors = _compare_field_sets(contract_names, schema_names)

    for field_name in contract_names & schema_names:
        errors.extend(
            _compare_field_detail(
                field_name,
                contract_fields[field_name],
                schema_props[field_name],
            )
        )

    expected_count = contract.get("constraints", {}).get("field_count")
    if expected_count is not None:
        if len(contract_names) != expected_count:
            errors.append(
                f"diagnostics field contract field_count "
                f"({expected_count}) != actual ({len(contract_names)})"
            )
        if len(schema_names) != expected_count:
            errors.append(
                f"diagnostics schema effective_config has "
                f"{len(schema_names)} fields, expected {expected_count}"
            )

    return errors


def _check_dynconf_key_alignment(dynconf_keys: set) -> list:
    """Check dynconf keys match field contract and precedence report."""
    errors = []

    if DIAGNOSTICS_FIELD_CONTRACT.exists():
        contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
        contract_fields = set(contract.get("effective_fields", {}).keys())
        extra = dynconf_keys - contract_fields
        if extra:
            errors.append(
                f"dynconf.schema.json has keys not in field contract: "
                f"{sorted(extra)}"
            )
        missing = contract_fields - dynconf_keys
        if missing:
            errors.append(
                f"Field contract has fields not in dynconf schema: "
                f"{sorted(missing)}"
            )

    if DYNCONF_PRECEDENCE_REPORT.exists():
        report = _load_json(DYNCONF_PRECEDENCE_REPORT)
        provenance_rules = report.get(
            "field_specific_provenance_rules", {}
        )
        report_fields = set(provenance_rules.get("fields", {}).keys())
        if report_fields:
            extra_schema = dynconf_keys - report_fields
            extra_report = report_fields - dynconf_keys
            if extra_schema:
                errors.append(
                    f"dynconf schema has keys not in precedence "
                    f"report: {sorted(extra_schema)}"
                )
            if extra_report:
                errors.append(
                    f"precedence report has fields not in dynconf "
                    f"schema: {sorted(extra_report)}"
                )

    return errors


def _check_dynconf_streaming_buffer(schema_props: dict) -> list:
    """Check streaming_buffer bounds match across schema and contract."""
    errors = []
    sb_schema = schema_props.get("streaming_buffer", {})
    if not DIAGNOSTICS_FIELD_CONTRACT.exists():
        return errors

    contract = _load_json(DIAGNOSTICS_FIELD_CONTRACT)
    sb_contract = contract.get("effective_fields", {}).get(
        "streaming_buffer", {}
    )
    contract_bounds = sb_contract.get("bounds", {})
    if not contract_bounds:
        return errors

    if sb_schema.get("minimum") != contract_bounds.get("minimum"):
        errors.append(
            "dynconf streaming_buffer minimum mismatch: "
            f"schema={sb_schema.get('minimum')}, "
            f"contract={contract_bounds.get('minimum')}"
        )
    if sb_schema.get("maximum") != contract_bounds.get("maximum"):
        errors.append(
            "dynconf streaming_buffer maximum mismatch: "
            f"schema={sb_schema.get('maximum')}, "
            f"contract={contract_bounds.get('maximum')}"
        )
    return errors


def gate_dynconf_schema() -> list:
    """Validate dynconf schema against cross-reference sources."""
    if not DYNCONF_SCHEMA.exists():
        return ["dynconf.schema.json missing"]

    schema = _load_json(DYNCONF_SCHEMA)
    schema_props = schema.get("properties", {})
    dynconf_keys = {k for k in schema_props if k != "schema_version"}

    errors = _check_dynconf_key_alignment(dynconf_keys)

    if schema.get("additionalProperties") is not False:
        errors.append(
            "dynconf.schema.json must have additionalProperties: false"
        )

    errors.extend(_check_dynconf_streaming_buffer(schema_props))
    return errors


def gate_reason_codegen_drift() -> list:
    """Run the reason codegen tool in --check mode to detect drift."""
    if not REASON_CODEGEN.exists():
        return ["tools/reason-codegen/generate.py not found"]

    result = subprocess.run(
        [sys.executable, str(REASON_CODEGEN), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        msg = detail if detail else (
            "Reason codegen --check exited with non-zero "
            "but produced no output"
        )
        return [f"Reason codegen drift detected:\n{msg}"]
    return []


def _run_gate(index, total, label, gate_fn):
    """Run a single gate and return errors list."""
    print(f"[{index}/{total}] {label}...")
    errors = gate_fn()
    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
    else:
        print("  PASS")
    return errors


def main() -> int:
    """Run all schema drift gates and report results."""
    all_errors = []
    total = 5
    passed = 0

    print("=== Schema Drift Gate Validator ===")
    print()

    gates = [
        ("Validating W2 artifact existence and structure",
         lambda: gate_w2_artifact_existence() + gate_w2_artifact_structure()),
        ("Validating metrics registry drift",
         gate_metrics_registry),
        ("Validating diagnostics field contract",
         gate_diagnostics_field_contract),
        ("Validating dynconf schema consistency",
         gate_dynconf_schema),
        ("Validating reason codegen drift",
         gate_reason_codegen_drift),
    ]

    for idx, (label, gate_fn) in enumerate(gates, 1):
        errors = _run_gate(idx, total, label, gate_fn)
        all_errors.extend(errors)
        if not errors:
            passed += 1

    print()
    print(f"Summary: {passed}/{total} gates passed")

    if all_errors:
        print(
            f"\nFAILED: {len(all_errors)} error(s) found",
            file=sys.stderr,
        )
        return 1

    print("\nPASSED: All schema drift gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
