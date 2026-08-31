#!/usr/bin/env python3
"""Check decompression metric labels in the two operator-facing contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "schemas/metrics-v1.registry.json"
DOC_PATHS = (
    ROOT / "docs/features/DECOMPRESSION.md",
)
FAMILY_NAME = "nginx_markdown_decompression_events_total"
REASON_RE = re.compile(r'(?m)^\s*reason="([^"]+)"\s*$')


def canonical_reason_values(registry: dict) -> tuple[str, ...]:
    """
    Extract the canonical values for the decompression metric's `reason` label.
    
    Parameters:
        registry (dict): Metrics registry containing family and label definitions.
    
    Returns:
        tuple[str, ...]: The configured `reason` label values.
    
    Raises:
        ValueError: If the metric family or its valid `reason` label values are missing.
    """
    for family in registry.get("families", []):
        if family.get("name") != FAMILY_NAME:
            continue
        for label in family.get("labels", []):
            if label.get("name") == "reason":
                values = label.get("values")
                if isinstance(values, list) and all(
                    isinstance(value, str) for value in values
                ):
                    return tuple(values)
    raise ValueError(f"registry is missing {FAMILY_NAME}.reason")


def validate_documents(
    registry: dict, documents: dict[Path, str]
) -> list[str]:
    """
    Validate documented decompression reason values against the metrics registry.
    
    Parameters:
        registry (dict): Metrics registry containing the canonical reason values.
        documents (dict[Path, str]): Decompression documents keyed by their paths.
    
    Returns:
        list[str]: Validation error messages for documents with missing, duplicated, or mismatched reason contracts.
    """
    expected = canonical_reason_values(registry)
    expected_set = set(expected)
    errors: list[str] = []
    for path, text in documents.items():
        matches = REASON_RE.findall(text)
        if len(matches) != 1:
            errors.append(f"{path}: expected one decompression reason contract")
            continue
        values = tuple(matches[0].split("|"))
        if set(values) != expected_set or len(values) != len(expected):
            errors.append(
                f"{path}: reason values {values!r} do not match registry {expected!r}"
            )
    return errors


def main() -> int:
    """Validate documented decompression metric labels and report the result.
    
    Returns:
    	int: 1 if validation errors are found, otherwise 0.
    """
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    documents = {
        path: path.read_text(encoding="utf-8") for path in DOC_PATHS
    }
    errors = validate_documents(registry, documents)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("decompression metric label contracts match the registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
