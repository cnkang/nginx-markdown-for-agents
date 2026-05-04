#!/usr/bin/env python3
"""Unit tests for tools/ci/coverage_gate.py.

Zero external dependencies — uses only the Python 3.10+ stdlib unittest.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from coverage_gate import (
    CoverageSummary,
    GateResult,
    _compute_from_records,
    check_gate,
    format_results,
    parse_lcov_summary,
)


class TestCoverageSummary(unittest.TestCase):
    def test_line_pct_normal(self) -> None:
        s = CoverageSummary(lines_found=100, lines_hit=80, functions_found=50, functions_hit=40)
        self.assertAlmostEqual(s.line_pct, 80.0)

    def test_line_pct_zero_found(self) -> None:
        s = CoverageSummary(lines_found=0, lines_hit=0, functions_found=0, functions_hit=0)
        self.assertAlmostEqual(s.line_pct, 0.0)

    def test_function_pct_normal(self) -> None:
        s = CoverageSummary(lines_found=100, lines_hit=80, functions_found=50, functions_hit=45)
        self.assertAlmostEqual(s.function_pct, 90.0)

    def test_function_pct_zero_found(self) -> None:
        s = CoverageSummary(lines_found=100, lines_hit=80, functions_found=0, functions_hit=0)
        self.assertAlmostEqual(s.function_pct, 0.0)


class TestParseLcovSummary(unittest.TestCase):
    def test_parse_summary_header(self) -> None:
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

    def test_parse_records_fallback(self) -> None:
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
        with self.assertRaises(FileNotFoundError):
            parse_lcov_summary(Path("/nonexistent/file.lcov"))


class TestComputeFromRecords(unittest.TestCase):
    def test_empty(self) -> None:
        result = _compute_from_records("")
        self.assertEqual(result.lines_found, 0)
        self.assertEqual(result.lines_hit, 0)

    def test_single_file_da(self) -> None:
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

    def test_multiple_files(self) -> None:
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


class TestCheckGate(unittest.TestCase):
    def test_both_pass(self) -> None:
        summary = CoverageSummary(lines_found=100, lines_hit=85, functions_found=50, functions_hit=42)
        results = check_gate("test", summary, 80.0, 80.0)
        self.assertTrue(all(r.passed for r in results))

    def test_line_fail(self) -> None:
        summary = CoverageSummary(lines_found=100, lines_hit=75, functions_found=50, functions_hit=42)
        results = check_gate("test", summary, 80.0, 80.0)
        line_result = [r for r in results if r.metric == "line"][0]
        self.assertFalse(line_result.passed)

    def test_func_fail(self) -> None:
        summary = CoverageSummary(lines_found=100, lines_hit=85, functions_found=50, functions_hit=35)
        results = check_gate("test", summary, 80.0, 80.0)
        func_result = [r for r in results if r.metric == "function"][0]
        self.assertFalse(func_result.passed)


class TestFormatResults(unittest.TestCase):
    def test_output_contains_pass(self) -> None:
        results = [
            GateResult(label="C module", metric="line", actual=85.0, threshold=80.0, passed=True),
            GateResult(label="C module", metric="function", actual=82.0, threshold=80.0, passed=True),
        ]
        output = format_results(results)
        self.assertIn("PASS", output)
        self.assertIn("85.0%", output)

    def test_output_contains_fail(self) -> None:
        results = [
            GateResult(label="Rust", metric="line", actual=75.0, threshold=80.0, passed=False),
        ]
        output = format_results(results)
        self.assertIn("FAIL", output)
        self.assertIn("75.0%", output)


if __name__ == "__main__":
    unittest.main()
