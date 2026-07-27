"""Regression tests for the complexity baseline CLI."""

import json
import sys

import tools.complexity._compare_baseline as compare_baseline


def test_output_file_mode_also_prints_report_to_stdout(tmp_path, monkeypatch, capsys):
    """--output writes the report and preserves the historical stdout output."""
    baseline_path = tmp_path / "baseline.json"
    output_path = tmp_path / "baseline-report.txt"
    baseline_path.write_text(json.dumps({"entries": []}) + "\n", encoding="utf-8")
    monkeypatch.setattr(compare_baseline, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "_compare_baseline.py",
            "--baseline",
            str(baseline_path),
            "--output",
            str(output_path),
        ],
    )

    assert compare_baseline.main() == 0

    report = output_path.read_text(encoding="utf-8")
    assert report
    assert capsys.readouterr().out == report + "\n"
