"""Regression tests for controlled report-schema CLI failures."""

from __future__ import annotations

from tools.perf import report_schema


def test_main_returns_controlled_error_for_missing_report(tmp_path, capsys):
    """Missing report paths return a validation error instead of raising."""
    missing_report = tmp_path / "missing.json"

    assert report_schema.main([str(missing_report)]) == 1

    captured = capsys.readouterr()
    assert "ERROR: failed to load report" in captured.err
