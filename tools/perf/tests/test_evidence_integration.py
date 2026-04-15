"""Integration test: verify evidence pack generator works with real Rust binary output.

This test loads an actual measurement report JSON produced by the Rust
perf_baseline binary and verifies that the evidence pack generator can
process it correctly, catching field name mismatches (html_bytes vs
input_bytes) and key name mismatches (max_slope_bytes_per_input_byte vs
max_slope).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence_pack_generator import (
    evaluate_bounded_memory,
    evaluate_no_regression,
    evaluate_ttfb_improvement,
    generate_evidence_pack,
)

# Minimal realistic measurement report matching Rust binary's exact schema.
REALISTIC_FULLBUFFER_REPORT = {
    "schema_version": "1.0.0",
    "report_type": "measurement",
    "timestamp": "2026-04-09T10:00:00Z",
    "git_commit": "abc1234",
    "platform": "darwin-arm64",
    "engine": "full-buffer",
    "tiers": {
        "small": {
            "html_bytes": 379,
            "markdown_bytes_avg": 120,
            "token_estimate_avg": 30,
            "p50_ms": 0.50,
            "p95_ms": 0.55,
            "p99_ms": 0.60,
            "peak_memory_bytes": 52428800,
            "req_per_s": 2000.0,
            "input_mb_per_s": 0.72,
            "stage_breakdown": {
                "parse_pct": 10.0,
                "convert_pct": 80.0,
                "etag_pct": 5.0,
                "token_pct": 5.0,
            },
            "iterations": 3000,
            "warmup": 100,
        },
        "medium": {
            "html_bytes": 10240,
            "markdown_bytes_avg": 3200,
            "token_estimate_avg": 800,
            "p50_ms": 2.50,
            "p95_ms": 2.70,
            "p99_ms": 2.90,
            "peak_memory_bytes": 53477376,
            "req_per_s": 400.0,
            "input_mb_per_s": 3.91,
            "stage_breakdown": {
                "parse_pct": 10.0,
                "convert_pct": 80.0,
                "etag_pct": 5.0,
                "token_pct": 5.0,
            },
            "iterations": 1000,
            "warmup": 50,
        },
        "large-1m": {
            "html_bytes": 1048576,
            "markdown_bytes_avg": 327680,
            "token_estimate_avg": 81920,
            "p50_ms": 18.0,
            "p95_ms": 19.5,
            "p99_ms": 21.0,
            "peak_memory_bytes": 58720256,
            "req_per_s": 55.0,
            "input_mb_per_s": 58.18,
            "stage_breakdown": {
                "parse_pct": 10.0,
                "convert_pct": 80.0,
                "etag_pct": 5.0,
                "token_pct": 5.0,
            },
            "iterations": 40,
            "warmup": 5,
        },
    },
}

REALISTIC_STREAMING_REPORT = {
    "schema_version": "1.0.0",
    "report_type": "measurement",
    "timestamp": "2026-04-09T10:05:00Z",
    "git_commit": "abc1234",
    "platform": "darwin-arm64",
    "engine": "streaming",
    "tiers": {
        "small": {
            "html_bytes": 379,
            "markdown_bytes_avg": 120,
            "token_estimate_avg": 30,
            "p50_ms": 0.52,
            "p95_ms": 0.57,
            "p99_ms": 0.62,
            "peak_memory_bytes": 52428800,
            "req_per_s": 1900.0,
            "input_mb_per_s": 0.69,
        },
        "medium": {
            "html_bytes": 10240,
            "markdown_bytes_avg": 3200,
            "token_estimate_avg": 800,
            "p50_ms": 2.80,
            "p95_ms": 3.00,
            "p99_ms": 3.20,
            "peak_memory_bytes": 53477376,
            "req_per_s": 360.0,
            "input_mb_per_s": 3.52,
        },
        "large-1m": {
            "html_bytes": 1048576,
            "markdown_bytes_avg": 327680,
            "token_estimate_avg": 81920,
            "p50_ms": 20.0,
            "p95_ms": 21.5,
            "p99_ms": 23.0,
            "peak_memory_bytes": 58720256,
            "req_per_s": 50.0,
            "input_mb_per_s": 52.43,
        },
    },
    "streaming_metrics": {
        "small": {
            "ttfb_ms": 0.10,
            "ttlb_ms": 0.52,
            "cpu_time_ms": 0.48,
            "flush_count": 5,
            "fallback_rate": 0.0,
            "peak_memory_bytes": 52428800,
        },
        "medium": {
            "ttfb_ms": 0.50,
            "ttlb_ms": 2.80,
            "cpu_time_ms": 2.60,
            "flush_count": 15,
            "fallback_rate": 0.0,
            "peak_memory_bytes": 53477376,
        },
        "large-1m": {
            "ttfb_ms": 2.5,
            "ttlb_ms": 20.0,
            "cpu_time_ms": 18.5,
            "flush_count": 42,
            "fallback_rate": 0.0,
            "peak_memory_bytes": 58720256,
        },
    },
}

EVIDENCE_TARGETS = {
    "schema_version": "1.0.0",
    "targets": {
        "bounded_memory": {
            "description": "Peak RSS does not scale linearly",
            "max_slope_bytes_per_input_byte": 0.5,
            "min_data_points": 4,
        },
        "ttfb_improvement": {
            "description": "Streaming TTFB < full-buffer p50 * 0.5",
            "max_ratio": 0.5,
        },
        "no_regression_small_medium": {
            "description": "Streaming p50 <= full-buffer p50 * 1.3",
            "max_ratio": 1.3,
        },
        "streaming_supported_parity": {
            "description": "100% parity for supported corpus",
        },
        "fallback_expected_correctness": {
            "description": "100% correct fallback",
        },
    },
}


class TestRealisticRustOutputCompatibility:
    """Verify evidence pack generator works with real Rust binary output schema."""

    def test_bounded_memory_reads_html_bytes(self):
        """Bounded memory evaluation should read 'html_bytes' from tier data.

        The Rust binary emits 'html_bytes' but the spec/docs use 'input_bytes'.
        The Python code must accept both.
        """
        # Only large-1m tier is present, which is < min_data_points (4)
        # so this returns INSUFFICIENT_DATA — but should NOT fail due to
        # missing 'input_bytes' key.
        result = evaluate_bounded_memory(
            {"tiers": REALISTIC_STREAMING_REPORT["tiers"]},
            max_slope=0.5,
            min_data_points=4,
        )
        # Should be INSUFFICIENT_DATA because we only have 1 large tier
        assert result["status"] == "INSUFFICIENT_DATA"
        assert result["data_point_count"] == 1  # Found the large-1m tier
        # Verify it extracted html_bytes correctly
        assert result["data_points"][0]["input_bytes"] == 1048576

    def test_ttfb_improvement_with_realistic_data(self):
        """TTFB evaluation should work with realistic tier data."""
        # Provide streaming_metrics (where ttf_ms actually lives)
        streaming_with_metrics = {
            **REALISTIC_STREAMING_REPORT,
            "streaming_metrics": REALISTIC_STREAMING_REPORT["streaming_metrics"],
        }
        result = evaluate_ttfb_improvement(
            streaming_with_metrics,
            {"tiers": REALISTIC_FULLBUFFER_REPORT["tiers"]},
            max_ratio=0.5,
        )
        # large-1m: streaming ttfb=2.5, fullbuffer p50=18.0, ratio=0.14
        assert result["status"] == "PASS"
        assert "large-1m" in result["details"]
        assert result["details"]["large-1m"]["ratio"] == pytest.approx(0.139, abs=0.01)

    def test_no_regression_with_realistic_data(self):
        """No-regression evaluation should work with realistic tier data."""
        # Test with streaming_metrics (the --engine both combined report case)
        streaming_with_metrics = {
            **REALISTIC_STREAMING_REPORT,
            "streaming_metrics": REALISTIC_STREAMING_REPORT["streaming_metrics"],
        }
        result = evaluate_no_regression(
            streaming_with_metrics,
            {"tiers": REALISTIC_FULLBUFFER_REPORT["tiers"]},
            max_ratio=1.3,
        )
        # small: 0.52/0.50=1.04, medium: 2.80/2.50=1.12, both < 1.3
        assert result["status"] == "PASS"
        assert "small" in result["details"]
        assert "medium" in result["details"]

    def test_no_regression_detects_regression(self):
        """No-regression evaluation should detect actual regression."""
        # Simulate a scenario where streaming has significant regression
        # by putting streaming data in streaming_metrics and fullbuffer in tiers
        streaming_combined = {
            "tiers": {  # fullbuffer data in tiers
                "small": {"p50_ms": 0.50},
                "medium": {"p50_ms": 2.50},
            },
            "streaming_metrics": {  # streaming data in streaming_metrics
                "small": {"p50_ms": 0.80},  # 60% regression!
                "medium": {"p50_ms": 4.00},  # 60% regression!
            },
        }
        result = evaluate_no_regression(
            streaming_combined,
            {"tiers": {"small": {"p50_ms": 0.50}, "medium": {"p50_ms": 2.50}}},
            max_ratio=1.3,
        )
        # small: 0.80/0.50=1.6 > 1.3, medium: 4.00/2.50=1.6 > 1.3
        assert result["status"] == "FAIL"
        assert result["details"]["small"]["ratio"] == pytest.approx(1.6, abs=0.01)
        assert result["details"]["medium"]["ratio"] == pytest.approx(1.6, abs=0.01)

    def test_generate_evidence_pack_with_realistic_schema(self):
        """
        Verify evidence-pack generation using realistic full-buffer and streaming reports.
        
        Asserts the returned pack contains required top-level keys, that the TTFB and no-regression gates report `PASS`, that bounded-memory reports `INSUFFICIENT_DATA` when insufficient tiers are present, that streaming parity is `UNKNOWN` when no parity report is provided, and that the overall `streaming_evidence_verdict` is `NO_GO`.
        """
        evidence_pack = generate_evidence_pack(
            fullbuffer_report=REALISTIC_FULLBUFFER_REPORT,
            streaming_report=REALISTIC_STREAMING_REPORT,
            evidence_targets=EVIDENCE_TARGETS,
            parity_report=None,
        )
        # Should have all required top-level keys
        assert evidence_pack["schema_version"] == "1.0.0"
        assert evidence_pack["type"] == "evidence-pack"
        assert "metadata" in evidence_pack
        assert "release_gates" in evidence_pack
        assert "streaming_evidence_verdict" in evidence_pack
        assert "p1_status" in evidence_pack

        # TTFB should PASS (2.5ms < 18.0ms * 0.5 = 9.0ms)
        assert evidence_pack["evidence_targets"]["ttfb_improvement"]["status"] == "PASS"

        # No-regression should PASS
        assert (
            evidence_pack["evidence_targets"]["no_regression_small_medium"]["status"]
            == "PASS"
        )

        # Bounded-memory should be INSUFFICIENT_DATA (only 1 large tier, need 4)
        assert (
            evidence_pack["evidence_targets"]["bounded_memory"]["status"]
            == "INSUFFICIENT_DATA"
        )

        # Parity should be UNKNOWN (no parity report provided)
        assert (
            evidence_pack["evidence_targets"]["streaming_supported_parity"]["status"]
            == "UNKNOWN"
        )

        # Verdict should be NO_GO because bounded_memory and parity are not PASS
        assert evidence_pack["streaming_evidence_verdict"] == "NO_GO"
