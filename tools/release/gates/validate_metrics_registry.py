#!/usr/bin/env python3
"""
Metrics Registry v1 Release Gate Validator.

Verifies that the v1 Prometheus renderer output matches the frozen
metrics-registry.json exactly: no extra families, no missing families.

This gate validates:
  - The registry artifact is well-formed and contains exactly 11 families
  - Family names use the nginx_markdown_ prefix
  - Types are valid Prometheus types (counter, gauge, histogram)
  - Histogram has <= 10 bucket boundaries
  - No per-path, per-URI, or unbounded labels exist
  - No synonym-duplicate or singular/plural duplicate families
  - input_bytes_total and output_bytes_total are counters (NOT histograms)
  - streaming_events_total uses label name 'transition' (NOT 'event')
  - build_info is a gauge (always 1)
  - The renderer header emits exactly these 11 family names and label keys

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 5.8
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.path_validation import validate_read_path  # noqa: E402


def find_repo_root():
    """Find repository root by walking up from script location."""
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "Makefile").exists() and (parent / "components").exists():
            return parent
    print("ERROR: Cannot locate repository root", file=sys.stderr)
    sys.exit(1)


def load_registry(repo_root):
    """Load and parse the metrics registry artifact."""
    registry_path = (
        repo_root / "artifacts" / "spec62" / "wave2" / "metrics-registry.json"
    )
    if not registry_path.exists():
        print(
            f"FAIL: Registry artifact not found at {registry_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    validated_path = validate_read_path(
        registry_path, purpose="metrics registry artifact"
    )
    with open(validated_path, encoding="utf-8") as f:
        return json.load(f)


VALID_TYPES = {"counter", "gauge", "histogram"}
FORBIDDEN_LABELS = {"path", "uri", "host", "client", "query", "address"}


def _validate_family_structure(family, seen_names):
    """Validate one metric family and update the duplicate-name set."""
    errors = []
    name = family.get("name", "")
    family_type = family.get("type", "")

    if not name.startswith("nginx_markdown_"):
        errors.append(f"Family '{name}' missing nginx_markdown_ prefix")
    if family_type not in VALID_TYPES:
        errors.append(f"Family '{name}' has invalid type '{family_type}'")
    if name in seen_names:
        errors.append(f"Duplicate family name: {name}")
    seen_names.add(name)

    if family_type == "histogram":
        bucket_count = len(family.get("bucket_boundaries", []))
        if bucket_count > 10:
            errors.append(
                f"Family '{name}' has {bucket_count} buckets (max 10)"
            )

    for label in family.get("labels", []):
        label_name = label.get("name", "")
        if label_name in FORBIDDEN_LABELS:
            errors.append(
                f"Family '{name}' has forbidden label '{label_name}'"
            )
    return errors


def validate_registry_structure(registry):
    """Validate the registry artifact structure and constraints."""
    errors = []
    if registry.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    families = registry.get("families", [])
    if len(families) != 11:
        errors.append(f"Expected exactly 11 families, got {len(families)}")

    seen_names = set()
    for family in families:
        errors.extend(_validate_family_structure(family, seen_names))
    return errors


def _validate_byte_counters(families):
    """Validate the input/output byte metric types."""
    errors = []
    for name in (
        "nginx_markdown_input_bytes_total",
        "nginx_markdown_output_bytes_total",
    ):
        family = families.get(name)
        if family and family["type"] != "counter":
            short_name = name.removeprefix("nginx_markdown_")
            errors.append(
                f"{short_name} must be counter, not {family['type']}"
            )
    return errors


def _validate_streaming_constraints(families):
    """Validate the streaming transition label and its closed values."""
    family = families.get("nginx_markdown_streaming_events_total")
    if not family:
        return []

    errors = []
    labels = family.get("labels", [])
    label_names = [label.get("name") for label in labels]
    if "event" in label_names:
        errors.append(
            "streaming_events_total uses 'event' label — must be 'transition'"
        )
    if "transition" not in label_names:
        errors.append("streaming_events_total missing 'transition' label")

    expected_values = {
        "commit", "fallback", "safe_finish_start", "abort_start",
        "resume_success", "resume_failure",
    }
    for label in labels:
        if label.get("name") != "transition":
            continue
        actual_values = set(label.get("values", []))
        if actual_values != expected_values:
            errors.append(
                "streaming_events_total transition values "
                f"mismatch: expected {expected_values}, got {actual_values}"
            )
    return errors


def _validate_build_info(families):
    """Validate the build-info gauge contract."""
    family = families.get("nginx_markdown_build_info")
    if not family:
        return []
    errors = []
    if family["type"] != "gauge":
        errors.append(f"build_info must be gauge, not {family['type']}")
    if family.get("value") != 1:
        errors.append("build_info value must be 1")
    return errors


def _validate_inflight_gauge(families):
    """Validate the live-state inflight gauge contract."""
    family = families.get("nginx_markdown_inflight_requests")
    if family and family["type"] != "gauge":
        return [f"inflight_requests must be gauge, not {family['type']}"]
    return []


def validate_specific_constraints(registry):
    """Validate spec-specific constraints from requirements."""
    families = {family["name"]: family for family in registry.get("families", [])}
    errors = _validate_byte_counters(families)
    errors.extend(_validate_streaming_constraints(families))
    errors.extend(_validate_build_info(families))
    errors.extend(_validate_inflight_gauge(families))
    return errors


def validate_renderer_matches_registry(repo_root, registry):
    """
    Verify the v1 renderer header emits exactly the families in
    the registry (no extra, no missing).
    """
    renderer_path = (
        repo_root
        / "components"
        / "nginx-module"
        / "src"
        / "ngx_http_markdown_metrics_v1_renderer.h"
    )
    if not renderer_path.exists():
        return ["v1 renderer header not found"]

    renderer_content = renderer_path.read_text(encoding="utf-8")

    # Extract family names from HELP lines in the renderer
    help_pattern = re.compile(
        r'# HELP (nginx_markdown_\w+) '
    )
    renderer_families = set(help_pattern.findall(renderer_content))

    # Also detect histogram sub-families (_bucket, _sum, _count)
    # These should NOT appear as separate HELP families
    registry_families = {
        f["name"] for f in registry.get("families", [])
    }

    errors = []

    # Extra families in renderer
    extra = renderer_families - registry_families
    if extra:
        errors.append(
            f"Renderer has extra families not in registry: "
            f"{sorted(extra)}"
        )

    # Missing families from renderer
    missing = registry_families - renderer_families
    if missing:
        errors.append(
            f"Renderer missing families from registry: "
            f"{sorted(missing)}"
        )

    for family in registry.get("families", []):
        name = family["name"]
        expected_labels = {
            label.get("name") for label in family.get("labels", [])
        }
        actual_labels = extract_renderer_label_names(renderer_content, name)
        if actual_labels != expected_labels:
            errors.append(
                f"Renderer labels for {name} mismatch: expected "
                f"{sorted(expected_labels)}, got {sorted(actual_labels)}"
            )

    return errors


def extract_renderer_label_names(renderer_content, family):
    """Extract label keys from the source strings for one metric family."""
    if family == "nginx_markdown_conversion_duration_seconds":
        label_sources = re.findall(
            r'\{engine=\\"%s\\"[^}]*\}', renderer_content
        )
    else:
        marker = f"# HELP {family}"
        start = renderer_content.find(marker)
        if start < 0:
            return set()
        end = renderer_content.find(
            "# HELP ",
            start + len(marker),
        )
        block = renderer_content[start:] if end < 0 else renderer_content[start:end]
        label_sources = re.findall(r"\{([^{}]*)\}", block)

    labels = set()
    for source in label_sources:
        labels.update(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=\\\"", source))
    if family == "nginx_markdown_conversion_duration_seconds":
        labels.discard("le")
    return labels


def validate_no_synonym_duplicates(registry):
    """Check for synonym or singular/plural duplicate families."""
    errors = []
    families = registry.get("families", [])
    names = [f["name"] for f in families]

    # Check for common synonym patterns
    base_names = set()
    for name in names:
        # Strip prefix and suffix
        base = name.replace("nginx_markdown_", "").rstrip("s")
        if base in base_names:
            errors.append(
                f"Potential synonym/plural duplicate: {name}"
            )
        base_names.add(base)

    return errors


def _print_section_errors(errors, prefix="FAIL"):
    """Print validation errors with the section's severity prefix."""
    for error in errors:
        print(f"  {prefix}: {error}")


def _run_structure_section(registry):
    print("[1/5] Validating registry structure...")
    errors = validate_registry_structure(registry)
    if errors:
        _print_section_errors(errors)
    else:
        print("  PASS: 11 families, valid types, proper prefix")
    return errors


def _run_constraint_section(registry):
    print("[2/5] Validating spec-specific constraints...")
    errors = validate_specific_constraints(registry)
    if errors:
        _print_section_errors(errors)
    else:
        print("  PASS: bytes=counter, transition label, build_info=gauge")
    return errors


def _run_renderer_section(repo_root, registry):
    print("[3/5] Validating renderer matches registry...")
    errors = validate_renderer_matches_registry(repo_root, registry)
    if errors:
        _print_section_errors(errors)
    else:
        print("  PASS: Renderer emits exactly 11 registry families")
    return errors


def _run_synonym_section(registry):
    print("[4/5] Checking for synonym/plural duplicates...")
    errors = validate_no_synonym_duplicates(registry)
    if errors:
        _print_section_errors(errors, prefix="WARN")
    else:
        print("  PASS: No synonym or plural duplicates")
    return errors


def _run_format_section(registry):
    print("[5/5] Validating format policy...")
    fmt = registry.get("format")
    if fmt != "prometheus_text_004":
        error = f"Format must be prometheus_text_004, got {fmt}"
        print(f"  FAIL: Format is '{fmt}'")
        return [error]
    print("  PASS: Prometheus text 0.0.4 only")
    return []


def main():
    """Run all metrics registry validation checks."""
    repo_root = find_repo_root()
    registry = load_registry(repo_root)

    print("=== Metrics Registry v1 Release Gate ===")
    print()
    all_errors = []
    all_errors.extend(_run_structure_section(registry))
    all_errors.extend(_run_constraint_section(registry))
    all_errors.extend(_run_renderer_section(repo_root, registry))
    all_errors.extend(_run_synonym_section(registry))
    all_errors.extend(_run_format_section(registry))

    print()
    if all_errors:
        print(f"FAILED: {len(all_errors)} error(s) found", file=sys.stderr)
        return 1
    print("PASSED: All metrics registry v1 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
