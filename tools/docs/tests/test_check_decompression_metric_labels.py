"""Regression tests for the decompression metric contract gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_decompression_metric_labels as checker


ROOT = Path(__file__).resolve().parents[3]


def _inputs() -> tuple[dict, dict[Path, str]]:
    """Load the metric registry and decompression feature documents used by the regression tests.
    
    Returns:
    	tuple[dict, dict[Path, str]]: The parsed metric registry and a mapping of document paths to their contents.
    """
    registry = json.loads(
        (ROOT / "schemas/metrics-v1.registry.json").read_text(encoding="utf-8")
    )
    paths = {
        ROOT / "docs/features/AUTOMATIC_DECOMPRESSION.md",
        ROOT / "docs/features/DECOMPRESSION.md",
    }
    return registry, {path: path.read_text(encoding="utf-8") for path in paths}


def test_documents_match_registry():
    registry, documents = _inputs()
    assert checker.validate_documents(registry, documents) == []


def test_prefixed_reason_names_are_rejected():
    registry, documents = _inputs()
    path = next(iter(documents))
    documents[path] = documents[path].replace(
        "budget_exceeded|format_error|io_error|ok|truncated_input",
        "decompression_budget_exceeded|format_error|io_error|ok|truncated_input",
    )
    errors = checker.validate_documents(registry, documents)
    assert errors
