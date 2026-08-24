#!/usr/bin/env python3
"""Check decompression metric labels in the two operator-facing contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "schemas/metrics-v1.registry.json"
DOC_PATHS = (
    ROOT / "docs/features/AUTOMATIC_DECOMPRESSION.md",
    ROOT / "docs/features/DECOMPRESSION.md",
)
FAMILY_NAME = "nginx_markdown_decompression_events_total"
REASON_RE = re.compile(r'(?m)^\s*reason="([^"]+)"\s*$')


def canonical_reason_values(registry: dict) -> tuple[str, ...]:
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
