"""Regression tests for the generic Markdown table duplication detector."""

from __future__ import annotations

import sys
from pathlib import Path


# Allow imports from tools/docs/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_duplicate_docs import _find_duplicate_tables


def _substantial_table() -> str:
    rows = [
        "| Key | Description |",
        "|-----|-------------|",
    ]
    for index in range(4):
        rows.append(
            f"| item-{index} | "
            "This deliberately long contract description prevents the detector "
            "from treating tiny reference tables as duplicate documentation. |"
        )
    return "\n".join(rows)


def test_duplicate_tables_are_reported_across_current_docs(tmp_path):
    docs = tmp_path / "docs"
    (docs / "architecture").mkdir(parents=True)
    (docs / "features").mkdir()
    table = _substantial_table()
    (docs / "architecture" / "one.md").write_text(table, encoding="utf-8")
    (docs / "features" / "two.md").write_text(table, encoding="utf-8")

    findings = _find_duplicate_tables(docs)

    assert findings == [
        (
            "docs/architecture/one.md",
            1,
            "docs/features/two.md",
            1,
        )
    ]


def test_historical_project_tables_are_not_current_duplicates(tmp_path):
    docs = tmp_path / "docs"
    (docs / "project").mkdir(parents=True)
    (docs / "architecture").mkdir()
    table = _substantial_table()
    (docs / "project" / "history.md").write_text(table, encoding="utf-8")
    (docs / "architecture" / "current.md").write_text(table, encoding="utf-8")

    assert _find_duplicate_tables(docs) == []


def test_document_update_tables_are_ignored(tmp_path):
    docs = tmp_path / "docs"
    (docs / "architecture").mkdir(parents=True)
    table = "## Document Updates\n\n" + _substantial_table()
    (docs / "architecture" / "one.md").write_text(table, encoding="utf-8")
    (docs / "architecture" / "two.md").write_text(table, encoding="utf-8")

    assert _find_duplicate_tables(docs) == []
