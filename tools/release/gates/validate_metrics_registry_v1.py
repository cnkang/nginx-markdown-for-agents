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


def validate_registry_structure(registry):
    """Validate the registry artifact structure and constraints."""
    errors = []

    # Schema version
    if registry.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    # Family count
    families = registry.get("families", [])
    if len(families) != 11:
        errors.append(
            f"Expected exactly 11 families, got {len(families)}"
        )

    # Valid Prometheus types
    valid_types = {"counter", "gauge", "histogram"}

    # Forbidden label names (unbounded cardinality)
    forbidden_labels = {"path", "uri", "host", "client", "query", "address"}

    seen_names = set()
    for fam in families:
        name = fam.get("name", "")
        fam_type = fam.get("type", "")

        # Prefix check
        if not name.startswith("nginx_markdown_"):
            errors.append(f"Family '{name}' missing nginx_markdown_ prefix")

        # Type check
        if fam_type not in valid_types:
            errors.append(
                f"Family '{name}' has invalid type '{fam_type}'"
            )

        # Duplicate check
        if name in seen_names:
            errors.append(f"Duplicate family name: {name}")
        seen_names.add(name)

        # Histogram bucket limit
        if fam_type == "histogram":
            buckets = fam.get("bucket_boundaries", [])
            if len(buckets) > 10:
                errors.append(
                    f"Family '{name}' has {len(buckets)} buckets "
                    f"(max 10)"
                )

        # Forbidden labels
        for label in fam.get("labels", []):
            label_name = label.get("name", "")
            if label_name in forbidden_labels:
                errors.append(
                    f"Family '{name}' has forbidden label "
                    f"'{label_name}'"
                )

    return errors


def validate_specific_constraints(registry):
    """Validate spec-specific constraints from requirements."""
    errors = []
    families = {f["name"]: f for f in registry.get("families", [])}

    # input_bytes_total must be counter, NOT histogram (Req 5.2)
    ibt = families.get("nginx_markdown_input_bytes_total")
    if ibt and ibt["type"] != "counter":
        errors.append(
            "input_bytes_total must be counter, not "
            f"{ibt['type']}"
        )

    # output_bytes_total must be counter, NOT histogram (Req 5.2)
    obt = families.get("nginx_markdown_output_bytes_total")
    if obt and obt["type"] != "counter":
        errors.append(
            "output_bytes_total must be counter, not "
            f"{obt['type']}"
        )

    # streaming_events_total must use 'transition' label (Req 5.8)
    se = families.get("nginx_markdown_streaming_events_total")
    if se:
        label_names = [l.get("name") for l in se.get("labels", [])]
        if "event" in label_names:
            errors.append(
                "streaming_events_total uses 'event' label — "
                "must be 'transition'"
            )
        if "transition" not in label_names:
            errors.append(
                "streaming_events_total missing 'transition' label"
            )

        # Verify closed allowlist values
        for label in se.get("labels", []):
            if label.get("name") == "transition":
                expected_values = {
                    "commit",
                    "fallback",
                    "safe_finish_start",
                    "abort_start",
                    "resume_success",
                    "resume_failure",
                }
                actual_values = set(label.get("values", []))
                if actual_values != expected_values:
                    errors.append(
                        "streaming_events_total transition values "
                        f"mismatch: expected {expected_values}, "
                        f"got {actual_values}"
                    )

    # build_info must be gauge with value=1 (Req 5.1)
    bi = families.get("nginx_markdown_build_info")
    if bi:
        if bi["type"] != "gauge":
            errors.append(
                f"build_info must be gauge, not {bi['type']}"
            )
        if bi.get("value") != 1:
            errors.append("build_info value must be 1")

    # inflight_requests must be gauge (live state, Req 5.3)
    ir = families.get("nginx_markdown_inflight_requests")
    if ir and ir["type"] != "gauge":
        errors.append(
            f"inflight_requests must be gauge, not {ir['type']}"
        )

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
            "/* ================================================================",
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


def main():
    """Run all metrics registry validation checks."""
    repo_root = find_repo_root()
    registry = load_registry(repo_root)

    all_errors = []

    print("=== Metrics Registry v1 Release Gate ===")
    print()

    # Structure validation
    print("[1/5] Validating registry structure...")
    structure_errors = validate_registry_structure(registry)
    all_errors.extend(structure_errors)
    if structure_errors:
        for e in structure_errors:
            print(f"  FAIL: {e}")
    else:
        print("  PASS: 11 families, valid types, proper prefix")

    # Specific constraints
    print("[2/5] Validating spec-specific constraints...")
    constraint_errors = validate_specific_constraints(registry)
    all_errors.extend(constraint_errors)
    if constraint_errors:
        for e in constraint_errors:
            print(f"  FAIL: {e}")
    else:
        print(
            "  PASS: bytes=counter, transition label, "
            "build_info=gauge"
        )

    # Renderer match
    print("[3/5] Validating renderer matches registry...")
    renderer_errors = validate_renderer_matches_registry(
        repo_root, registry
    )
    all_errors.extend(renderer_errors)
    if renderer_errors:
        for e in renderer_errors:
            print(f"  FAIL: {e}")
    else:
        print("  PASS: Renderer emits exactly 11 registry families")

    # No synonym duplicates
    print("[4/5] Checking for synonym/plural duplicates...")
    synonym_errors = validate_no_synonym_duplicates(registry)
    all_errors.extend(synonym_errors)
    if synonym_errors:
        for e in synonym_errors:
            print(f"  WARN: {e}")
    else:
        print("  PASS: No synonym or plural duplicates")

    # Format-only check
    print("[5/5] Validating format policy...")
    fmt = registry.get("format")
    if fmt != "prometheus_text_004":
        all_errors.append(
            f"Format must be prometheus_text_004, got {fmt}"
        )
        print(f"  FAIL: Format is '{fmt}'")
    else:
        print("  PASS: Prometheus text 0.0.4 only")

    print()
    if all_errors:
        print(
            f"FAILED: {len(all_errors)} error(s) found",
            file=sys.stderr,
        )
        return 1

    print("PASSED: All metrics registry v1 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
