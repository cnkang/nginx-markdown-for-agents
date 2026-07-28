"""Regression tests for packaging matrix validation dependencies."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow imports from tools/docs/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate_packaging_matrix as validator


def test_missing_pyyaml_fails_when_packaging_matrix_exists(monkeypatch, tmp_path):
    """A present matrix must not silently bypass validation without PyYAML."""
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text("nginx_versions: []\n", encoding="utf-8")
    monkeypatch.setattr(validator, "PACKAGING_MATRIX", matrix_path)
    monkeypatch.setattr(validator, "yaml", None)

    assert validator.main() == 1


def test_missing_pyyaml_does_not_block_absent_packaging_matrix(monkeypatch, tmp_path):
    """Repositories without a packaging matrix retain the documented skip."""
    monkeypatch.setattr(
        validator,
        "PACKAGING_MATRIX",
        tmp_path / "missing-matrix.yaml",
    )
    monkeypatch.setattr(validator, "yaml", None)

    assert validator.main() == 0
