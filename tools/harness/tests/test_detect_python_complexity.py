"""Fixture tests for detect_python_complexity.py.

Pins the local Rule 17 Python complexity detector against the concrete
SonarCloud failure pattern fixed in detect_duplicate_code.py: a summary
function with many inline generator filters must fail, while the
data-driven helper-based form must pass.
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
        "detect_python_complexity",
        REPO_ROOT / "tools/harness/detect_python_complexity.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["detect_python_complexity"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def det():
    return _load_module()


def _scan_source(det, tmp_path: Path, source: str):
    fixture = tmp_path / "fixture.py"
    fixture.write_text(textwrap.dedent(source), encoding="utf-8")
    return det.scan_file(fixture).functions


def _score_by_name(functions, name: str) -> int:
    matches = [function.score for function in functions if function.name == name]
    assert len(matches) == 1
    return matches[0]


def test_historical_summary_counter_exceeds_threshold(det, tmp_path):
    functions = _scan_source(
        det,
        tmp_path,
        """
        def _count_by_category(all_warnings, all_reviews, all_infos):
            return {
                "memory": sum(1 for m in all_warnings if "[memory]" in m),
                "rollback": sum(1 for m in all_warnings if "[rollback]" in m),
                "ffi": sum(1 for m in all_warnings if "[ffi-validation]" in m),
                "postcommit": sum(1 for m in all_warnings if "[postcommit]" in m),
                "adjacent": len([w for w in all_warnings if "adjacent" in w]),
                "state": sum(1 for m in all_reviews if "[state-machine]" in m),
                "struct": sum(1 for m in all_reviews if "[structural]" in m),
                "log": sum(1 for m in all_infos if "[log-only]" in m),
                "sig": sum(1 for m in all_infos if "[signature]" in m),
            }
        """,
    )

    assert _score_by_name(functions, "_count_by_category") > det.DEFAULT_THRESHOLD


def test_data_driven_summary_counter_stays_under_threshold(det, tmp_path):
    functions = _scan_source(
        det,
        tmp_path,
        """
        SUMMARY_CATEGORY_PATTERNS = (
            ("memory", "warnings", "[memory]"),
            ("rollback", "warnings", "[rollback]"),
            ("ffi", "warnings", "[ffi-validation]"),
            ("postcommit", "warnings", "[postcommit]"),
            ("adjacent", "warnings", "adjacent"),
            ("state", "reviews", "[state-machine]"),
            ("struct", "reviews", "[structural]"),
            ("log", "infos", "[log-only]"),
            ("sig", "infos", "[signature]"),
        )

        def _count_by_category(all_warnings, all_reviews, all_infos):
            buckets = {
                "warnings": all_warnings,
                "reviews": all_reviews,
                "infos": all_infos,
            }
            return {
                name: _count_containing(buckets[bucket], pattern)
                for name, bucket, pattern in SUMMARY_CATEGORY_PATTERNS
            }

        def _count_containing(messages, pattern):
            return sum(1 for message in messages if pattern in message)
        """,
    )

    assert _score_by_name(functions, "_count_by_category") <= det.DEFAULT_THRESHOLD
    assert _score_by_name(functions, "_count_containing") <= det.DEFAULT_THRESHOLD
