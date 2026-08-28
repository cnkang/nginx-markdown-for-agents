#!/usr/bin/env python3
"""Unit tests for tools/ci/coverage_gate.py.

Zero external dependencies — uses only the Python 3.10+ stdlib unittest.
"""

from __future__ import annotations

import io
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import NamedTemporaryFile

from coverage_gate import (
    CoverageSummary,
    GateResult,
    _compute_from_records,
    _append_critical_results,
    check_gate,
    format_results,
    parse_lcov_critical_paths,
    parse_lcov_summary,
    _finish_gate,
)


class TestCoverageSummary(unittest.TestCase):
    """Tests for CoverageSummary computed percentage properties."""

    def test_line_pct_normal(self) -> None:
        """Verify line_pct returns correct percentage with non-zero lines_found."""
        s = CoverageSummary(lines_found=100, lines_hit=80, functions_found=50, functions_hit=40)
        self.assertAlmostEqual(s.line_pct, 80.0)

    def test_line_pct_zero_found(self) -> None:
        """Verify line_pct returns 0.0 when lines_found is zero."""
        s = CoverageSummary(lines_found=0, lines_hit=0, functions_found=0, functions_hit=0)
        self.assertAlmostEqual(s.line_pct, 0.0)

    def test_function_pct_normal(self) -> None:
        """Verify function_pct returns correct percentage with non-zero functions_found."""
        s = CoverageSummary(lines_found=100, lines_hit=80, functions_found=50, functions_hit=45)
        self.assertAlmostEqual(s.function_pct, 90.0)

    def test_function_pct_zero_found(self) -> None:
        """Verify function_pct returns 0.0 when functions_found is zero."""
        s = CoverageSummary(lines_found=100, lines_hit=80, functions_found=0, functions_hit=0)
        self.assertAlmostEqual(s.function_pct, 0.0)


class TestParseLcovSummary(unittest.TestCase):
    """Tests for parse_lcov_summary parsing various lcov record formats."""

    def test_parse_summary_header(self) -> None:
        """Verify parsing of lcov summary header lines (LF/LH/FNF/FNH)."""
        content = textwrap.dedent("""\
            TN:
            SF:src/example.c
            DA:10,1
            LF:100
            LH:80
            FNF:50
            FNH:40
            end_of_record
            lines.: 80 of 100
            functions.: 40 of 50
        """)
        with NamedTemporaryFile(mode="w", suffix=".lcov", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            result = parse_lcov_summary(Path(f.name))
        self.assertEqual(result.lines_found, 100)
        self.assertEqual(result.lines_hit, 80)
        self.assertEqual(result.functions_found, 50)
        self.assertEqual(result.functions_hit, 40)

    def test_parse_summary_header_with_multiple_separator_dots(self) -> None:
        """Verify lcov versions that render repeated separator dots."""
        content = "lines......: 9 of 10\nfunctions....: 4 of 5\n"
        with NamedTemporaryFile(mode="w", suffix=".lcov", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            result = parse_lcov_summary(Path(f.name))
        self.assertEqual(result.lines_found, 10)
        self.assertEqual(result.lines_hit, 9)
        self.assertEqual(result.functions_found, 5)
        self.assertEqual(result.functions_hit, 4)

    def test_parse_records_fallback(self) -> None:
        """Verify fallback to counting DA/FNDA records when summary headers are absent."""
        content = textwrap.dedent("""\
            TN:
            SF:src/example.c
            FN:10,my_func
            FN:20,other_func
            FNDA:5,my_func
            FNDA:0,other_func
            DA:10,1
            DA:11,0
            DA:20,3
            end_of_record
        """)
        with NamedTemporaryFile(mode="w", suffix=".lcov", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            result = parse_lcov_summary(Path(f.name))
        self.assertEqual(result.lines_found, 3)
        self.assertEqual(result.lines_hit, 2)
        self.assertEqual(result.functions_found, 2)
        self.assertEqual(result.functions_hit, 1)

    def test_parse_records_with_fna_format(self) -> None:
        """Verify parsing of FNL/FNA function record format."""
        content = textwrap.dedent("""\
            TN:
            SF:src/example.c
            FNL:0,10,20
            FNA:0,3,first_func
            FNL:1,21,30
            FNA:1,0,second_func
            FNF:2
            FNH:1
            DA:10,1
            DA:11,0
            end_of_record
        """)
        with NamedTemporaryFile(mode="w", suffix=".lcov", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            result = parse_lcov_summary(Path(f.name))
        self.assertEqual(result.lines_found, 2)
        self.assertEqual(result.lines_hit, 1)
        self.assertEqual(result.functions_found, 2)
        self.assertEqual(result.functions_hit, 1)

    def test_file_not_found(self) -> None:
        """Verify FileNotFoundError is raised for a nonexistent path."""
        bad_path = Path("/nonexistent/file.lcov")
        with self.assertRaises(FileNotFoundError):
            parse_lcov_summary(bad_path)


class TestComputeFromRecords(unittest.TestCase):
    """Tests for _compute_from_records low-level record parsing."""

    def test_empty(self) -> None:
        """Verify empty input returns zero counts."""
        result = _compute_from_records("")
        self.assertEqual(result.lines_found, 0)
        self.assertEqual(result.lines_hit, 0)

    def test_single_file_da(self) -> None:
        """Verify correct counts from a single-file lcov record with DA lines."""
        content = textwrap.dedent("""\
            TN:
            SF:src/foo.c
            DA:5,1
            DA:6,0
            DA:7,3
            end_of_record
        """)
        result = _compute_from_records(content)
        self.assertEqual(result.lines_found, 3)
        self.assertEqual(result.lines_hit, 2)


class TestCriticalPathCoverage(unittest.TestCase):
    """Tests for source-record critical-path coverage aggregation."""

    def test_merges_reports_and_measures_all_categories(self) -> None:
        """Verify duplicate reports are merged by source file and line."""
        first = textwrap.dedent("""\
            SF:/repo/components/nginx-module/src/ngx_http_markdown_auth.c
            DA:1,1
            DA:2,0
            SF:/repo/components/nginx-module/src/ngx_http_markdown_error.c
            DA:1,1
            SF:/repo/components/rust-converter/src/ffi/abi.rs
            DA:1,1
            DA:2,1
            SF:/repo/components/rust-converter/src/decision/conditional.rs
            DA:1,1
            DA:2,1
        """)
        second = textwrap.dedent("""\
            SF:/repo/components/nginx-module/src/ngx_http_markdown_auth.c
            DA:1,1
            DA:2,1
            SF:/repo/components/rust-converter/src/ffi/abi.rs
            DA:1,1
            DA:2,0
        """)
        paths = []
        for content in (first, second):
            with NamedTemporaryFile(
                mode="w", suffix=".lcov", delete=False, encoding="utf-8"
            ) as f:
                f.write(content)
                f.flush()
                paths.append(Path(f.name))

        summaries = parse_lcov_critical_paths(paths)

        self.assertAlmostEqual(summaries["auth"].line_pct, 100.0)
        self.assertAlmostEqual(summaries["error handling"].line_pct, 100.0)
        self.assertAlmostEqual(summaries["FFI boundary"].line_pct, 100.0)
        self.assertAlmostEqual(summaries["conditional requests"].line_pct, 100.0)

    def test_multiple_files(self) -> None:
        """Verify correct aggregation across multiple file records."""
        content = textwrap.dedent("""\
            TN:
            SF:src/a.c
            DA:1,1
            DA:2,0
            end_of_record
            SF:src/b.c
            DA:3,1
            end_of_record
        """)
        result = _compute_from_records(content)
        self.assertEqual(result.lines_found, 3)
        self.assertEqual(result.lines_hit, 2)

    def test_partial_report_checks_only_covered_categories(self) -> None:
        """C-only or Rust-only reports must not fail absent categories."""
        content = textwrap.dedent("""\
            SF:/repo/components/nginx-module/src/ngx_http_markdown_auth.c
            DA:1,1
        """)
        with NamedTemporaryFile(
            mode="w", suffix=".lcov", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            f.flush()
            results: list[GateResult] = []
            errors: list[str] = []
            _append_critical_results(results, errors, [Path(f.name)], 90.0)

        self.assertEqual(errors, [])
        self.assertEqual([result.label for result in results], ["Critical: auth"])

    def test_covered_category_without_lines_still_fails(self) -> None:
        """A selected source file with no measured lines remains an error."""
        content = "SF:/repo/components/nginx-module/src/ngx_http_markdown_auth.c\n"
        with NamedTemporaryFile(
            mode="w", suffix=".lcov", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            f.flush()
            results: list[GateResult] = []
            errors: list[str] = []
            _append_critical_results(results, errors, [Path(f.name)], 90.0)

        self.assertIn("critical-path category has no measured lines: auth", errors)


class TestCheckGate(unittest.TestCase):
    """Tests for check_gate threshold evaluation logic."""

    def test_both_pass(self) -> None:
        """Verify both line and function gates pass when above thresholds."""
        summary = CoverageSummary(lines_found=100, lines_hit=85, functions_found=50, functions_hit=42)
        results = check_gate("test", summary, 80.0, 80.0)
        self.assertTrue(all(r.passed for r in results))

    def test_line_fail(self) -> None:
        """Verify line gate fails when line coverage is below threshold."""
        summary = CoverageSummary(lines_found=100, lines_hit=75, functions_found=50, functions_hit=42)
        results = check_gate("test", summary, 80.0, 80.0)
        line_result = [r for r in results if r.metric == "line"][0]
        self.assertFalse(line_result.passed)

    def test_func_fail(self) -> None:
        """Verify function gate fails when function coverage is below threshold."""
        summary = CoverageSummary(lines_found=100, lines_hit=85, functions_found=50, functions_hit=35)
        results = check_gate("test", summary, 80.0, 80.0)
        func_result = [r for r in results if r.metric == "function"][0]
        self.assertFalse(func_result.passed)


class TestFormatResults(unittest.TestCase):
    """Tests for format_results human-readable output formatting."""

    def test_output_contains_pass(self) -> None:
        """Verify formatted output includes PASS status and actual percentage."""
        results = [
            GateResult(label="C module", metric="line", actual=85.0, threshold=80.0, passed=True),
            GateResult(label="C module", metric="function", actual=82.0, threshold=80.0, passed=True),
        ]
        output = format_results(results)
        self.assertIn("PASS", output)
        self.assertIn("85.0%", output)

    def test_output_contains_fail(self) -> None:
        """Verify formatted output includes FAIL status and actual percentage."""
        results = [
            GateResult(label="Rust", metric="line", actual=75.0, threshold=80.0, passed=False),
        ]
        output = format_results(results)
        self.assertIn("FAIL", output)
        self.assertIn("75.0%", output)


class TestFinishGate(unittest.TestCase):
    """Tests for the policy details printed by the command-line gate."""

    def test_reports_selected_thresholds(self) -> None:
        """Verify output reflects parsed thresholds rather than fixed values."""
        output = io.StringIO()
        reports = (("C module", None, 73.0, 74.0),)
        results = [
            GateResult(
                label="C module",
                metric="line",
                actual=100.0,
                threshold=73.0,
                passed=True,
            )
        ]

        with redirect_stdout(output):
            status = _finish_gate(results, [], 87.5, reports)

        assert status == 0
        rendered = output.getvalue()
        self.assertIn("critical paths: 87.5%", rendered)
        self.assertIn("C module: 73.0% line/74.0% function", rendered)


if __name__ == "__main__":
    unittest.main()
