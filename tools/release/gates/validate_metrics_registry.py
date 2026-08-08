#!/usr/bin/env python3
"""Validate the canonical Metrics v1 contract against the C renderer.

The checked-in contract is the independent source for family names, types,
label allowlists, histogram buckets, and closed label values.  The release
artifact is only a versioned projection of that contract; it is not allowed to
define the contract by itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.path_validation import validate_read_path  # noqa: E402


DEFAULT_VERSION = "0.9.2"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
METRICS_CONTRACT = "schemas/metrics-v1.registry.json"


def find_repo_root(start: Path | None = None) -> Path:
    """Find a repository root containing the Makefile and source tree."""
    path = (start or Path(__file__)).resolve()
    candidates = (path,) + tuple(path.parents)
    for parent in candidates:
        if (parent / "Makefile").exists() and (parent / "components").exists():
            return parent
    print("ERROR: Cannot locate repository root", file=sys.stderr)
    raise SystemExit(1)


def _load_json(path: Path, purpose: str) -> dict:
    """Load a JSON object through the repository path validation boundary."""
    validated_path = validate_read_path(path, purpose=purpose)
    try:
        value = json.loads(validated_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def load_contract(repo_root: Path) -> dict:
    """Load the independent checked-in Metrics v1 contract."""
    return _load_json(repo_root / METRICS_CONTRACT, "metrics v1 contract")


def load_registry(
    repo_root: Path,
    version: str = DEFAULT_VERSION,
    artifact_dir: Path | None = None,
) -> dict:
    """Load a versioned release artifact, or an explicitly supplied directory."""
    registry_path = (
        artifact_dir / "metrics-registry.json"
        if artifact_dir is not None
        else repo_root / "artifacts" / "release" / version / "metrics-registry.json"
    )
    if not registry_path.exists():
        print(
            f"FAIL: Registry artifact not found at {registry_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return _load_json(registry_path, "metrics registry artifact")


def _family_map(registry: dict) -> dict[str, dict]:
    """Return valid named family entries from a registry document."""
    return {
        family["name"]: family
        for family in registry.get("families", [])
        if isinstance(family, dict) and isinstance(family.get("name"), str)
    }


def _contract_projection(document: dict) -> dict:
    """Return fields that define the contract, excluding generation metadata."""
    return {
        key: document.get(key)
        for key in ("schema_version", "format", "constraints", "families")
    }


def validate_registry_matches_contract(registry: dict, contract: dict) -> list[str]:
    """Ensure the generated artifact has not changed the canonical contract."""
    if _contract_projection(registry) != _contract_projection(contract):
        return [
            "metrics-registry.json does not match schemas/metrics-v1.registry.json"
        ]
    return []


def _validate_family_structure(
    family: dict,
    seen_names: set[str],
    constraints: dict,
) -> list[str]:
    """Validate one metric family using canonical structural constraints."""
    errors = []
    name = family.get("name", "")
    family_type = family.get("type", "")
    prefix = constraints.get("family_prefix")
    valid_types = set(constraints.get("valid_types", []))

    if not isinstance(name, str) or not name.startswith(prefix):
        errors.append(f"Family '{name}' missing {prefix} prefix")
    if family_type not in valid_types:
        errors.append(f"Family '{name}' has invalid type '{family_type}'")
    if name in seen_names:
        errors.append(f"Duplicate family name: {name}")
    seen_names.add(name)

    labels = family.get("labels", [])
    label_names = [label.get("name") for label in labels]
    if len(label_names) != len(set(label_names)):
        errors.append(f"Family '{name}' has duplicate label names")
    forbidden = set(constraints.get("forbidden_labels", []))
    for label_name in label_names:
        if label_name in forbidden:
            errors.append(
                f"Family '{name}' has forbidden label '{label_name}'"
            )

    if family_type == "histogram":
        bucket_count = len(family.get("bucket_boundaries", []))
        max_buckets = constraints.get("max_histogram_buckets")
        if max_buckets is not None and bucket_count > max_buckets:
            errors.append(
                f"Family '{name}' has {bucket_count} buckets "
                f"(max {max_buckets})"
            )
        if family.get("bucket_count") != bucket_count:
            errors.append(
                f"Family '{name}' bucket_count does not match boundaries"
            )
    return errors


def validate_registry_structure(registry: dict, contract: dict | None = None) -> list[str]:
    """Validate registry structure and canonical family-count constraints."""
    errors = []
    if registry.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    families = registry.get("families", [])
    if not isinstance(families, list) or not families:
        return errors + ["families must be a non-empty list"]

    expected_count = len(contract.get("families", [])) if contract else None
    if expected_count is not None and len(families) != expected_count:
        errors.append(
            f"Expected {expected_count} families from canonical contract, "
            f"got {len(families)}"
        )

    constraints = (contract or {}).get("constraints", {})
    seen_names: set[str] = set()
    for family in families:
        if not isinstance(family, dict):
            errors.append("Metric family must be an object")
            continue
        errors.extend(_validate_family_structure(family, seen_names, constraints))
    return errors


def validate_specific_constraints(
    registry: dict, contract: dict | None = None
) -> list[str]:
    """Validate all closed values and special family fields via the contract."""
    if contract is None:
        return []
    return validate_registry_matches_contract(registry, contract)


def _renderer_path(repo_root: Path) -> Path:
    return (
        repo_root
        / "components"
        / "nginx-module"
        / "src"
        / "ngx_http_markdown_metrics_v1_renderer.h"
    )


def _renderer_label_sources(renderer_content: str, family: str) -> list[str]:
    """Return label-bearing string fragments for one renderer family."""
    if family == "nginx_markdown_conversion_duration_seconds":
        # Histogram label templates live in the shared render helper before
        # the family HELP/TYPE block.
        return [
            source
            for source in re.findall(r"\{([^{}]*)\}", renderer_content)
            if "engine=\\\"%s\\\"" in source
        ]
    marker = f"# HELP {family}"
    start = renderer_content.find(marker)
    if start < 0:
        return []
    end = renderer_content.find("# HELP ", start + len(marker))
    block = renderer_content[start:] if end < 0 else renderer_content[start:end]
    return re.findall(r"\{([^{}]*)\}", block)


def _renderer_label_values(label_sources: list[str]) -> dict[str, set[str]]:
    """Parse escaped label names and literal values from renderer fragments."""
    values: dict[str, set[str]] = {}
    for source in label_sources:
        for name, value in re.findall(
            r"([A-Za-z_]\w*)=\\\"([^\\\"]*)\\\"",
            source,
            flags=re.ASCII,
        ):
            if name != "le":
                values.setdefault(name, set()).add(value)
    return values


def _renderer_histogram_engines(renderer_content: str) -> set[str]:
    """Extract the closed engine values passed to the histogram helper."""
    return set(
        re.findall(
            r"ngx_http_markdown_metrics_v1_render_histogram\(\s*"
            r"p,\s*end,\s*\"([^\"]+)\"",
            renderer_content,
            flags=re.DOTALL,
        )
    )


def _extract_renderer_labels(renderer_content: str, family: str) -> dict[str, set[str]]:
    """Extract label names and literal values for one renderer family."""
    values = _renderer_label_values(
        _renderer_label_sources(renderer_content, family)
    )

    if family == "nginx_markdown_conversion_duration_seconds":
        values["engine"] = _renderer_histogram_engines(renderer_content)
    return values


def extract_renderer_label_names(renderer_content: str, family: str) -> set[str]:
    """Extract only label keys for compatibility with focused unit tests."""
    return set(_extract_renderer_labels(renderer_content, family))


def _renderer_family_name_errors(
    renderer_types: dict[str, str], registry_families: dict[str, dict]
) -> list[str]:
    """Report renderer families missing from or extra to the registry."""
    errors: list[str] = []
    renderer_names = set(renderer_types)
    registry_names = set(registry_families)
    extra = renderer_names - registry_names
    missing = registry_names - renderer_names
    if extra:
        errors.append(f"Renderer has extra families not in registry: {sorted(extra)}")
    if missing:
        errors.append(
            f"Renderer missing families from registry: {sorted(missing)}"
        )
    return errors


def _renderer_family_errors(
    name: str,
    family: dict,
    renderer_types: dict[str, str],
    renderer_content: str,
) -> list[str]:
    """Verify one renderer family's type, labels, and closed values."""
    errors: list[str] = []
    if renderer_types.get(name) != family.get("type"):
        errors.append(
            f"Renderer type for {name} mismatch: expected "
            f"{family.get('type')}, got {renderer_types.get(name)}"
        )
    expected_labels = {
        label.get("name"): set(label.get("values", []))
        for label in family.get("labels", [])
    }
    actual_labels = _extract_renderer_labels(renderer_content, name)
    if set(actual_labels) != set(expected_labels):
        errors.append(
            f"Renderer labels for {name} mismatch: expected "
            f"{sorted(expected_labels)}, got {sorted(actual_labels)}"
        )
        return errors
    for label_name, expected_values in expected_labels.items():
        if expected_values and actual_labels.get(label_name) != expected_values:
            errors.append(
                f"Renderer values for {name}.{label_name} mismatch: "
                f"expected {sorted(expected_values)}, got "
                f"{sorted(actual_labels.get(label_name, set()))}"
            )
    return errors


def validate_renderer_matches_registry(repo_root: Path, registry: dict) -> list[str]:
    """Verify renderer families, types, label keys, and closed values."""
    renderer_path = _renderer_path(repo_root)
    if not renderer_path.exists():
        return ["v1 renderer header not found"]
    renderer_content = renderer_path.read_text(encoding="utf-8")
    renderer_types = dict(
        re.findall(
            r"# TYPE (nginx_markdown_\w+) (counter|gauge|histogram)",
            renderer_content,
        )
    )
    registry_families = _family_map(registry)
    errors = _renderer_family_name_errors(renderer_types, registry_families)

    for name, family in registry_families.items():
        errors.extend(
            _renderer_family_errors(
                name, family, renderer_types, renderer_content
            )
        )
    return errors


def validate_no_synonym_duplicates(registry: dict) -> list[str]:
    """Check for synonym or singular/plural duplicate families."""
    errors = []
    base_names = set()
    for name in _family_map(registry):
        base = name.removeprefix("nginx_markdown_")
        if base.endswith("s"):
            base = base.removesuffix("s")
        if base in base_names:
            errors.append(f"Potential synonym/plural duplicate: {name}")
        base_names.add(base)
    return errors


def _print_section_errors(errors, prefix="FAIL"):
    """Print validation errors with the section's severity prefix."""
    for error in errors:
        print(f"  {prefix}: {error}")


def _run_structure_section(registry, contract):
    print("[1/5] Validating registry structure...")
    errors = validate_registry_structure(registry, contract)
    if errors:
        _print_section_errors(errors)
    else:
        print(f"  PASS: {len(registry['families'])} families, valid types and labels")
    return errors


def _run_contract_section(registry, contract):
    print("[2/5] Validating canonical contract projection...")
    errors = validate_specific_constraints(registry, contract)
    if errors:
        _print_section_errors(errors)
    else:
        print("  PASS: release artifact matches schemas/metrics-v1.registry.json")
    return errors


def _run_renderer_section(repo_root, registry):
    print("[3/5] Validating renderer matches registry...")
    errors = validate_renderer_matches_registry(repo_root, registry)
    if errors:
        _print_section_errors(errors)
    else:
        print("  PASS: renderer families, types, labels, and values match contract")
    return errors


def _run_synonym_section(registry):
    print("[4/5] Checking for synonym/plural duplicates...")
    errors = validate_no_synonym_duplicates(registry)
    if errors:
        _print_section_errors(errors)
    else:
        print("  PASS: No synonym or plural duplicates")
    return errors


def _run_format_section(registry, contract):
    print("[5/5] Validating format policy...")
    expected = contract.get("format")
    actual = registry.get("format")
    if actual != expected:
        error = f"Format must be {expected}, got {actual}"
        _print_section_errors([error])
        return [error]
    print(f"  PASS: {actual} only")
    return []


def main(argv: list[str] | None = None) -> int:
    """Run all Metrics v1 validation checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Override the release artifact directory (primarily for tests).",
    )
    args = parser.parse_args(argv)
    if VERSION_PATTERN.fullmatch(args.version) is None:
        parser.error("--version must use MAJOR.MINOR.PATCH")

    repo_root = find_repo_root()
    registry = load_registry(repo_root, args.version, args.artifact_dir)
    contract = load_contract(repo_root)

    print("=== Metrics Registry v1 Release Gate ===")
    print()
    all_errors = []
    all_errors.extend(_run_structure_section(registry, contract))
    all_errors.extend(_run_contract_section(registry, contract))
    all_errors.extend(_run_renderer_section(repo_root, registry))
    all_errors.extend(_run_synonym_section(registry))
    all_errors.extend(_run_format_section(registry, contract))

    print()
    if all_errors:
        print(f"FAILED: {len(all_errors)} error(s) found", file=sys.stderr)
        return 1
    print("PASSED: Metrics Registry v1 gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
