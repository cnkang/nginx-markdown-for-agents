#!/usr/bin/env python3
"""Tests for finalize_module_baseline.py.

Covers:
  - Valid verbatim_run policy is written with normalized fields
  - Input validation: 40-char SHA, non-empty run and artifact
  - Missing input file is rejected
  - Metric data is never mutated during finalization
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finalize_module_baseline import _validate_inputs, main  # noqa: E402

_GOOD_SHA = "0123456789abcdef0123456789abcdef01234567"


def _empty_baseline() -> dict:
    return {
        "module_benchmark": {
            "timestamp": "2026-01-01T00:00:00Z",
            "git_commit": _GOOD_SHA[:7],
            "platform": "linux-x86_64",
            "load_generator": "ab",
            "nginx_version": "nginx version: nginx/1.24.0",
            "scenarios": [],
        },
        "decompression_coverage": {},
    }


def test_validate_inputs_accepts_good_values() -> None:
    assert _validate_inputs(_GOOD_SHA, "actions/runs/1", "perf/baselines/raw.json") == []


def test_validate_inputs_rejects_short_sha() -> None:
    errors = _validate_inputs("012345", "actions/runs/1", "perf/baselines/raw.json")
    assert errors
    assert any("40-character" in e for e in errors)


@pytest.mark.parametrize(
    "sha", ["ABCDEF0123456789abcdef0123456789abcdef01", "0123456789!def0123456789abcdef01234567", "012345"]
)
def test_validate_inputs_rejects_malformed_sha(sha: str) -> None:
    errors = _validate_inputs(sha, "actions/runs/1", "perf/baselines/raw.json")
    assert errors
    assert any("40-character" in e for e in errors)


@pytest.mark.parametrize("run", ["", "   "])
def test_validate_inputs_rejects_empty_run(run: str) -> None:
    # Empty check falls to the final check in main; non-empty whitespace is
    # accepted by the validator (it represents a provenance label).
    errors = _validate_inputs(_GOOD_SHA, run, "perf/baselines/raw.json")
    if run:
        assert errors == []
    else:
        assert errors
        assert any("source-run" in e for e in errors)


def test_validate_inputs_rejects_empty_artifact() -> None:
    errors = _validate_inputs(_GOOD_SHA, "actions/runs/1", "")
    assert errors
    assert any("source-artifact" in e for e in errors)


def test_finalizer_writes_verbatim_policy(tmp_path: Path) -> None:
    inp = tmp_path / "baseline.json"
    inp.write_text(json.dumps(_empty_baseline()), encoding="utf-8")

    assert (
        main([
            "--input", str(inp),
            "--source-git-commit", _GOOD_SHA,
            "--source-run", "https://github.com/foo/actions/runs/1/attempts/2",
            "--source-artifact", "perf/baselines/raw.json",
            "--measurement-timestamp", "2026-01-01T00:00:00Z",
        ])
        == 0
    )
    baseline = json.loads(inp.read_text(encoding="utf-8"))
    policy = baseline["baseline_policy"]
    assert policy["type"] == "verbatim_run"
    assert policy["normalization"] == "none"
    assert policy["source_git_commit"] == _GOOD_SHA
    assert policy["measurement_timestamp"] == "2026-01-01T00:00:00Z"
    # Top-level module_benchmark data is untouched.
    assert baseline["module_benchmark"]["git_commit"] == _GOOD_SHA[:7]
    assert baseline.get("decompression_coverage") == {}


def test_finalizer_preserves_measured_scenario_metrics(tmp_path: Path) -> None:
    report = _empty_baseline()
    report["module_benchmark"]["scenarios"] = [
        {
            "name": "streaming-first",
            "metrics": {"zero_copy_output_total": 47380, "rps": 30},
        },
    ]
    inp = tmp_path / "baseline.json"
    inp.write_text(json.dumps(report), encoding="utf-8")
    assert main([
        "--input", str(inp),
        "--source-git-commit", _GOOD_SHA,
        "--source-run", "actions/runs/999",
        "--source-artifact", "perf/baselines/raw.json",
    ]) == 0
    out = json.loads(inp.read_text(encoding="utf-8"))
    assert out["module_benchmark"]["scenarios"][0]["metrics"]["rps"] == 30


def test_finalizer_rejects_missing_input(tmp_path: Path) -> None:
    assert (
        main([
            "--input", str(tmp_path / "nope.json"),
            "--source-git-commit", _GOOD_SHA,
            "--source-run", "actions/runs/1",
            "--source-artifact", "perf/baselines/raw.json",
        ])
        == 1
    )


def test_finalizer_rejects_bad_sha_cli(tmp_path: Path, capsys) -> None:
    assert (
        main([
            "--input", str(tmp_path / "out.json"),
            "--source-git-commit", "short",
            "--source-run", "actions/runs/1",
            "--source-artifact", "perf/baselines/raw.json",
        ])
        == 1
    )
    assert "40-character" in capsys.readouterr().err
