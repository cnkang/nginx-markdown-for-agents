"""Unit tests for shared perf report helpers."""

import json
import tempfile
from pathlib import Path

import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report_utils import (
    _build_aggregated_tier,
    build_baseline_report,
    detect_platform,
    merge_measurement_reports,
)


def _write_report(data):
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, handle)
    handle.close()
    return handle.name


def test_detect_platform_normalizes_known_aliases():
    assert detect_platform("macOS", "aarch64") == "darwin-arm64"
    assert detect_platform("Linux", "AMD64") == "linux-x86_64"


def test_build_baseline_report_keeps_only_core_metrics():
    baseline = build_baseline_report({
        "timestamp": "2026-03-15T11:00:00Z",
        "git_commit": "deadbeef",
        "platform": "linux-x86_64",
        "tiers": {
            "small": {
                "p50_ms": 1.0,
                "p95_ms": 1.5,
                "p99_ms": 2.0,
                "peak_memory_bytes": 1234,
                "req_per_s": 1000.0,
                "input_mb_per_s": 50.0,
                "html_bytes": 42,
            },
        },
    })

    assert baseline == {
        "schema_version": "1.0.0",
        "timestamp": "2026-03-15T11:00:00Z",
        "git_commit": "deadbeef",
        "platform": "linux-x86_64",
        "tiers": {
            "small": {
                "p50_ms": 1.0,
                "p95_ms": 1.5,
                "p99_ms": 2.0,
                "peak_memory_bytes": 1234,
                "req_per_s": 1000.0,
                "input_mb_per_s": 50.0,
            },
        },
    }


def test_merge_measurement_reports_combines_tiers():
    report_a = _write_report({
        "schema_version": "1.0.0",
        "report_type": "measurement",
        "platform": "linux-x86_64",
        "tiers": {"small": {"p50_ms": 1.0}},
    })
    report_b = _write_report({
        "schema_version": "1.0.0",
        "report_type": "measurement",
        "platform": "linux-x86_64",
        "tiers": {"medium": {"p50_ms": 2.0}},
    })

    merged = merge_measurement_reports([report_b, report_a])

    assert merged["tiers"] == {
        "small": {"p50_ms": 1.0},
        "medium": {"p50_ms": 2.0},
    }


def test_build_aggregated_tier_rejects_empty_reports():
    with pytest.raises(ValueError, match="tier_reports cannot be empty"):
        _build_aggregated_tier([])
