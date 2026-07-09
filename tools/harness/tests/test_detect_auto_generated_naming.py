"""Fixture tests for detect_auto_generated_naming.py.

Pins the meaningful-naming detector against the two smells it must block:

* IDE "Extract Method" artifacts (``_extracted_from_...``) — always an error.
* Generic numeric-index suffixes (``helper_1`` / ``helper_2``) — error in
  ``--strict``, warning otherwise.

and the semantic suffixes it must *not* flag (exit codes, spec versions,
ADR references, semver, charsets, demo numbering).
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "detect_auto_generated_naming",
        REPO_ROOT / "tools/harness/detect_auto_generated_naming.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["detect_auto_generated_naming"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def det():
    return _load_module()


def _scan_source(det, tmp_path: Path, source: str, strict: bool = True):
    fixture = tmp_path / "fixture.py"
    fixture.write_text(textwrap.dedent(source), encoding="utf-8")
    errors, warnings = det._scan_file(fixture, strict)
    return errors, warnings


def test_ide_extract_artifact_is_error(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def _extracted_from_test_foo_3(self, arg0, arg1):
            return arg0 + arg1
        """,
    )
    assert any("extracted_from_" in e for e in errors)
    assert warnings == []


def test_generic_index_suffix_is_strict_error(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def helper_1():
            pass

        def helper_2():
            pass
        """,
    )
    assert len(errors) == 2
    assert all("generic numeric-index" in e for e in errors)
    assert warnings == []


def test_generic_index_suffix_is_warning_when_not_strict(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def helper_1():
            pass
        """,
        strict=False,
    )
    assert errors == []
    assert len(warnings) == 1


def test_spec_version_suffix_is_exempt(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def validate_module_benchmark_091(report):
            return report
        """,
    )
    assert errors == [] and warnings == []


def test_adr_code_suffix_is_exempt(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def check_adr_0007(result):
            return result
        """,
    )
    assert errors == [] and warnings == []


def test_exit_code_suffix_is_exempt(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def test_cli_check_only_fresh_exit_0():
            pass

        def test_blocking_with_allow_skip_exits_0():
            pass
        """,
    )
    assert errors == [] and warnings == []


def test_error_code_suffix_is_exempt(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def test_svg_postcommit_returns_error_code_8():
            pass
        """,
    )
    assert errors == [] and warnings == []


def test_semver_suffix_is_exempt(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def test_schema_version_is_1_0_0():
            pass
        """,
    )
    assert errors == [] and warnings == []


def test_charset_standard_suffix_is_exempt(det, tmp_path):
    errors, warnings = _scan_source(
        det, tmp_path,
        """
        def test_extract_charset_from_content_type_iso_8859_1():
            pass
        """,
    )
    assert errors == [] and warnings == []


def test_rust_example_numbering_is_exempt(det, tmp_path):
    fixture = tmp_path / "fixture.rs"
    fixture.write_text(
        textwrap.dedent(
            """
            fn example_1() {}
            fn example_2() {}
            """
        ),
        encoding="utf-8",
    )
    errors, warnings = det._scan_file(fixture, True)
    assert errors == [] and warnings == []


def test_classify_name_helper(det):
    assert det.classify_name("_extracted_from_test_foo_3") == "extracted"
    assert det.classify_name("helper_1") == "index"
    assert det.classify_name("validate_module_benchmark_091") is None
    assert det.classify_name("check_adr_0007") is None
    assert det.classify_name("test_cli_exit_0") is None
    assert det.classify_name("do_real_work") is None
