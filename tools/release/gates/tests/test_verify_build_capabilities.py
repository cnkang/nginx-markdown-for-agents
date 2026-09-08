"""Tests for the fail-closed build capability gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "verify_build_capabilities.py"


def _report() -> dict:
    return {
        "engines": {"full_buffer": True, "streaming": True},
        "encodings": {
            "identity": True,
            "gzip": True,
            "zlib_deflate": True,
            "raw_deflate": True,
            "brotli": True,
        },
    }


def test_complete_report_passes(tmp_path: Path) -> None:
    report = tmp_path / "capabilities.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--capabilities", str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "CAPABILITY_CHECK_PASSED" in result.stdout


def test_missing_engine_fails_explicitly(tmp_path: Path) -> None:
    report = _report()
    report["engines"]["streaming"] = False
    path = tmp_path / "missing-engine.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--capabilities", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing promised engine capability: streaming" in result.stderr


def test_missing_encoding_fails_explicitly(tmp_path: Path) -> None:
    report = _report()
    report["encodings"]["raw_deflate"] = False
    path = tmp_path / "missing-encoding.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--capabilities", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "missing promised encoding capability: raw_deflate" in result.stderr
