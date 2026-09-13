"""Tests for the duplicate-definition detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_duplicate_definitions as detector  # noqa: E402


def test_duplicate_function_is_reported(tmp_path: Path) -> None:
    """The second definition wins at runtime, so the first is dead code."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "packaging").mkdir()
    (tmp_path / "tools" / "dupe.py").write_text(
        "def scan(files):\n    return 1\n\n\ndef scan(files):\n    return 2\n",
        encoding="utf-8",
    )

    errors = detector.collect_errors(tmp_path)

    assert any("scan" in error and "dupe.py" in error for error in errors), errors


def test_duplicate_class_is_reported(tmp_path: Path) -> None:
    """Classes count as well as functions."""
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "dupe.py").write_text(
        "class Gate:\n    pass\n\n\nclass Gate:\n    pass\n",
        encoding="utf-8",
    )

    errors = detector.collect_errors(tmp_path)

    assert any("Gate" in error for error in errors), errors


def test_unique_names_pass(tmp_path: Path) -> None:
    """One definition per name raises nothing."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "ok.py").write_text(
        "def one():\n    pass\n\n\ndef two():\n    pass\n",
        encoding="utf-8",
    )

    assert detector.collect_errors(tmp_path) == []


def test_duplicate_assignment_is_reported(tmp_path: Path) -> None:
    """A second binding of the same top-level name is dead code too."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "dupe.py").write_text(
        "FOO = 1\n\n\nFOO = 2\n",
        encoding="utf-8",
    )

    errors = detector.collect_errors(tmp_path)

    assert any("FOO" in error for error in errors), errors


def test_annotated_assignment_counts(tmp_path: Path) -> None:
    """An annotated binding is a binding as well."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "dupe.py").write_text(
        "BAR: int = 1\n\n\nBAR = 2\n",
        encoding="utf-8",
    )

    errors = detector.collect_errors(tmp_path)

    assert any("BAR" in error for error in errors), errors


def test_attribute_assignment_is_not_a_binding(tmp_path: Path) -> None:
    """`config.value = 1` assigns to an attribute, not to `config`."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "attr.py").write_text(
        "config = {}\n\n\nconfig.value = 1\n",
        encoding="utf-8",
    )

    errors = detector.collect_errors(tmp_path)

    assert errors == [], errors
