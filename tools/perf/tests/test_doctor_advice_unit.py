"""Unit tests for the Doctor Advice tool (rules D01–D07).

Covers:
  - Each rule D01-D07 with synthetic metrics at threshold boundary
  - Missing metric graceful skip (no crash)
  - JSON output format validity (parseable, required keys)
  - Exit codes: 0 for info-only, 1 for warn, 2 for critical

Requirements: 8.3, 8.4, 8.5, 8.6

Run:
    python3 -m pytest tools/perf/tests/test_doctor_advice_unit.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doctor_advice import (
    Finding,
    RuleResult,
    _evaluate_d01,
    _evaluate_d02,
    _evaluate_d03,
    _evaluate_d04,
    _evaluate_d05,
    _evaluate_d06,
    _evaluate_d07,
    compute_exit_code,
    evaluate_rules,
    format_json,
    format_text,
    DEFAULT_STREAMING_BUFFER_BUDGET,
)


# ---------------------------------------------------------------------------
# D01: High streaming fallback rate (threshold > 10%)
# ---------------------------------------------------------------------------


class TestD01:
    """D01: streaming_fallback_total / streaming_requests_total > 10%."""

    def test_triggers_at_boundary(self):
        """Rate just above 10% triggers warn."""
        metrics = {
            "streaming_fallback_total": 11,
            "streaming_requests_total": 100,
        }
        result = _evaluate_d01(metrics)
        assert result.finding is not None
        assert result.finding.severity == "warn"
        assert result.finding.rule_id == "D01"

    def test_no_trigger_below_boundary(self):
        """Rate at exactly 10% does not trigger."""
        metrics = {
            "streaming_fallback_total": 10,
            "streaming_requests_total": 100,
        }
        result = _evaluate_d01(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_no_trigger_zero_requests(self):
        """Zero streaming requests is not applicable (no finding, no skip)."""
        metrics = {
            "streaming_fallback_total": 0,
            "streaming_requests_total": 0,
        }
        result = _evaluate_d01(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_skipped_missing_fallback(self):
        """Missing streaming_fallback_total skips gracefully."""
        metrics = {"streaming_requests_total": 100}
        result = _evaluate_d01(metrics)
        assert result.skipped
        assert "streaming_fallback_total" in result.skip_reason

    def test_skipped_missing_requests(self):
        """Missing streaming_requests_total skips gracefully."""
        metrics = {"streaming_fallback_total": 5}
        result = _evaluate_d01(metrics)
        assert result.skipped
        assert "streaming_requests_total" in result.skip_reason


# ---------------------------------------------------------------------------
# D02: Overload events detected (threshold > 0)
# ---------------------------------------------------------------------------


class TestD02:
    """D02: overload_total > 0."""

    def test_triggers_at_boundary(self):
        """Any overload_total > 0 triggers warn."""
        metrics = {"overload_total": 1}
        result = _evaluate_d02(metrics)
        assert result.finding is not None
        assert result.finding.severity == "warn"
        assert result.finding.rule_id == "D02"

    def test_no_trigger_zero(self):
        """overload_total == 0 produces no finding."""
        metrics = {"overload_total": 0}
        result = _evaluate_d02(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_skipped_missing_metric(self):
        """Missing overload_total skips gracefully."""
        metrics = {}
        result = _evaluate_d02(metrics)
        assert result.skipped
        assert "overload_total" in result.skip_reason


# ---------------------------------------------------------------------------
# D03: High backpressure rate (threshold > 5%)
# ---------------------------------------------------------------------------


class TestD03:
    """D03: backpressure_total / streaming_requests_total > 5%."""

    def test_triggers_at_boundary(self):
        """Rate just above 5% triggers warn."""
        metrics = {
            "backpressure_total": 6,
            "streaming_requests_total": 100,
        }
        result = _evaluate_d03(metrics)
        assert result.finding is not None
        assert result.finding.severity == "warn"
        assert result.finding.rule_id == "D03"

    def test_no_trigger_at_boundary(self):
        """Rate at exactly 5% does not trigger."""
        metrics = {
            "backpressure_total": 5,
            "streaming_requests_total": 100,
        }
        result = _evaluate_d03(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_no_trigger_zero_requests(self):
        """Zero streaming requests is not applicable."""
        metrics = {
            "backpressure_total": 0,
            "streaming_requests_total": 0,
        }
        result = _evaluate_d03(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_skipped_missing_backpressure(self):
        """Missing backpressure_total skips gracefully."""
        metrics = {"streaming_requests_total": 100}
        result = _evaluate_d03(metrics)
        assert result.skipped
        assert "backpressure_total" in result.skip_reason

    def test_skipped_missing_streaming_requests(self):
        """Missing streaming_requests_total skips gracefully."""
        metrics = {"backpressure_total": 10}
        result = _evaluate_d03(metrics)
        assert result.skipped
        assert "streaming_requests_total" in result.skip_reason


# ---------------------------------------------------------------------------
# D04: Decompression heavily favors full-buffer (threshold > 10:1)
# ---------------------------------------------------------------------------


class TestD04:
    """D04: decompression_fullbuffer_total >> decompression_streaming_total."""

    def test_triggers_above_ratio(self):
        """Ratio > 10:1 triggers info."""
        metrics = {
            "decompression_fullbuffer_total": 110,
            "decompression_streaming_total": 10,
        }
        result = _evaluate_d04(metrics)
        assert result.finding is not None
        assert result.finding.severity == "info"
        assert result.finding.rule_id == "D04"

    def test_triggers_when_streaming_zero(self):
        """Fullbuffer > 0 with streaming == 0 triggers info."""
        metrics = {
            "decompression_fullbuffer_total": 50,
            "decompression_streaming_total": 0,
        }
        result = _evaluate_d04(metrics)
        assert result.finding is not None
        assert result.finding.severity == "info"

    def test_no_trigger_balanced_ratio(self):
        """Ratio at 10:1 does not trigger."""
        metrics = {
            "decompression_fullbuffer_total": 100,
            "decompression_streaming_total": 10,
        }
        result = _evaluate_d04(metrics)
        assert result.finding is None

    def test_no_trigger_both_zero(self):
        """Both zero is not applicable."""
        metrics = {
            "decompression_fullbuffer_total": 0,
            "decompression_streaming_total": 0,
        }
        result = _evaluate_d04(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_skipped_missing_fullbuffer(self):
        """Missing decompression_fullbuffer_total skips gracefully."""
        metrics = {"decompression_streaming_total": 10}
        result = _evaluate_d04(metrics)
        assert result.skipped
        assert "decompression_fullbuffer_total" in result.skip_reason

    def test_skipped_missing_streaming(self):
        """Missing decompression_streaming_total skips gracefully."""
        metrics = {"decompression_fullbuffer_total": 100}
        result = _evaluate_d04(metrics)
        assert result.skipped
        assert "decompression_streaming_total" in result.skip_reason


# ---------------------------------------------------------------------------
# D05: Decompression budget exceeded (threshold > 0)
# ---------------------------------------------------------------------------


class TestD05:
    """D05: decompression_budget_exceeded_total > 0."""

    def test_triggers_at_boundary(self):
        """Any exceeded count > 0 triggers warn."""
        metrics = {"decompression_budget_exceeded_total": 1}
        result = _evaluate_d05(metrics)
        assert result.finding is not None
        assert result.finding.severity == "warn"
        assert result.finding.rule_id == "D05"

    def test_no_trigger_zero(self):
        """Zero exceedances produce no finding."""
        metrics = {"decompression_budget_exceeded_total": 0}
        result = _evaluate_d05(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_skipped_missing_metric(self):
        """Missing metric skips gracefully."""
        metrics = {}
        result = _evaluate_d05(metrics)
        assert result.skipped
        assert "decompression_budget_exceeded_total" in result.skip_reason


# ---------------------------------------------------------------------------
# D06: Pending output watermark near budget (threshold > 80%)
# ---------------------------------------------------------------------------


class TestD06:
    """D06: pending_output_high_watermark_bytes > 80% of streaming buffer."""

    def test_triggers_above_threshold(self):
        """Watermark above 80% of default budget triggers warn."""
        # 80% of 1 MiB = 838861. Use value above that.
        threshold_val = int(DEFAULT_STREAMING_BUFFER_BUDGET * 0.80) + 1
        metrics = {"pending_output_high_watermark_bytes": threshold_val}
        result = _evaluate_d06(metrics)
        assert result.finding is not None
        assert result.finding.severity == "warn"
        assert result.finding.rule_id == "D06"

    def test_no_trigger_below_threshold(self):
        """Watermark at exactly 80% does not trigger (>80% required)."""
        threshold_val = int(DEFAULT_STREAMING_BUFFER_BUDGET * 0.80)
        metrics = {"pending_output_high_watermark_bytes": threshold_val}
        result = _evaluate_d06(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_uses_custom_budget(self):
        """Watermark checked against streaming_buffer_budget if provided."""
        custom_budget = 2097152  # 2 MiB
        # 80% of 2 MiB = 1677722; use value above that
        metrics = {
            "pending_output_high_watermark_bytes": 1700000,
            "streaming_buffer_budget": custom_budget,
        }
        result = _evaluate_d06(metrics)
        assert result.finding is not None
        assert result.finding.severity == "warn"

    def test_skipped_missing_metric(self):
        """Missing pending_output_high_watermark_bytes skips gracefully."""
        metrics = {}
        result = _evaluate_d06(metrics)
        assert result.skipped
        assert "pending_output_high_watermark_bytes" in result.skip_reason


# ---------------------------------------------------------------------------
# D07: High copied vs zero-copy ratio (threshold > 5:1)
# ---------------------------------------------------------------------------


class TestD07:
    """D07: copied_output_total >> zero_copy_output_total (>5:1)."""

    def test_triggers_above_ratio(self):
        """Ratio > 5:1 triggers info."""
        metrics = {
            "copied_output_total": 60,
            "zero_copy_output_total": 10,
        }
        result = _evaluate_d07(metrics)
        assert result.finding is not None
        assert result.finding.severity == "info"
        assert result.finding.rule_id == "D07"

    def test_triggers_when_zero_copy_zero_but_copies_present(self):
        """copied > 0 with zero_copy == 0 triggers info."""
        metrics = {
            "copied_output_total": 10,
            "zero_copy_output_total": 0,
        }
        result = _evaluate_d07(metrics)
        assert result.finding is not None
        assert result.finding.severity == "info"

    def test_no_trigger_balanced_ratio(self):
        """Ratio at 5:1 does not trigger."""
        metrics = {
            "copied_output_total": 50,
            "zero_copy_output_total": 10,
        }
        result = _evaluate_d07(metrics)
        assert result.finding is None

    def test_no_trigger_both_zero(self):
        """Both zero means feature not used — no finding."""
        metrics = {
            "copied_output_total": 0,
            "zero_copy_output_total": 0,
        }
        result = _evaluate_d07(metrics)
        assert result.finding is None
        assert not result.skipped

    def test_skipped_missing_copied(self):
        """Missing copied_output_total skips gracefully."""
        metrics = {"zero_copy_output_total": 10}
        result = _evaluate_d07(metrics)
        assert result.skipped
        assert "copied_output_total" in result.skip_reason

    def test_skipped_missing_zero_copy(self):
        """Missing zero_copy_output_total skips gracefully."""
        metrics = {"copied_output_total": 100}
        result = _evaluate_d07(metrics)
        assert result.skipped
        assert "zero_copy_output_total" in result.skip_reason


# ---------------------------------------------------------------------------
# JSON format validity
# ---------------------------------------------------------------------------


class TestJSONFormat:
    """Verify JSON output is valid and contains required keys."""

    def test_json_parseable_with_findings(self):
        """JSON output parses correctly with findings present."""
        findings = [
            Finding(
                rule_id="D02",
                severity="warn",
                message="Overload rejection count: 5",
                advice="Increase inflight limit.",
                metrics_used={"overload_total": 5},
            )
        ]
        skipped = ["D04 (metric missing: decompression_streaming_total)"]
        output = format_json(findings, skipped, "test.json")

        parsed = json.loads(output)
        assert "timestamp" in parsed
        assert "source" in parsed
        assert "findings" in parsed
        assert "summary" in parsed
        assert "skipped_rules" in parsed

    def test_json_findings_structure(self):
        """Each finding in JSON has required keys: id, severity, message, advice, metrics."""
        findings = [
            Finding(
                rule_id="D01",
                severity="warn",
                message="High fallback",
                advice="Check thresholds",
                metrics_used={"streaming_fallback_total": 15},
            )
        ]
        output = format_json(findings, [], "http://localhost/metrics")
        parsed = json.loads(output)

        assert len(parsed["findings"]) == 1
        f = parsed["findings"][0]
        assert f["id"] == "D01"
        assert f["severity"] == "warn"
        assert "message" in f
        assert "advice" in f
        assert "metrics" in f

    def test_json_empty_findings(self):
        """JSON output is valid even with no findings."""
        output = format_json([], [], "test.json")
        parsed = json.loads(output)
        assert parsed["findings"] == []
        assert parsed["summary"] == {"critical": 0, "warn": 0, "info": 0}

    def test_json_summary_counts(self):
        """Summary correctly counts severities."""
        findings = [
            Finding("D01", "warn", "msg1", "adv1", {}),
            Finding("D04", "info", "msg2", "adv2", {}),
            Finding("D05", "warn", "msg3", "adv3", {}),
        ]
        output = format_json(findings, [], "test.json")
        parsed = json.loads(output)
        assert parsed["summary"]["warn"] == 2
        assert parsed["summary"]["info"] == 1
        assert parsed["summary"]["critical"] == 0


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


class TestExitCodes:
    """Verify exit code logic: 0=info, 1=warn, 2=critical."""

    def test_no_findings_returns_zero(self):
        """Empty findings list returns exit code 0."""
        assert compute_exit_code([]) == 0

    def test_info_only_returns_zero(self):
        """Info-only findings return exit code 0."""
        findings = [Finding("D04", "info", "msg", "adv", {})]
        assert compute_exit_code(findings) == 0

    def test_warn_returns_one(self):
        """Warn findings return exit code 1."""
        findings = [Finding("D01", "warn", "msg", "adv", {})]
        assert compute_exit_code(findings) == 1

    def test_critical_returns_two(self):
        """Critical findings return exit code 2."""
        findings = [Finding("D02", "critical", "msg", "adv", {})]
        assert compute_exit_code(findings) == 2

    def test_mixed_severities_returns_max(self):
        """Mixed severities return the maximum exit code."""
        findings = [
            Finding("D04", "info", "msg", "adv", {}),
            Finding("D01", "warn", "msg", "adv", {}),
        ]
        assert compute_exit_code(findings) == 1

    def test_warn_and_critical_returns_two(self):
        """Warn + critical returns exit code 2."""
        findings = [
            Finding("D01", "warn", "msg", "adv", {}),
            Finding("D02", "critical", "msg", "adv", {}),
        ]
        assert compute_exit_code(findings) == 2


# ---------------------------------------------------------------------------
# Integrated evaluate_rules: missing metric graceful skip
# ---------------------------------------------------------------------------


class TestEvaluateRulesSkipping:
    """Verify evaluate_rules gracefully skips when metrics are missing."""

    def test_all_metrics_missing(self):
        """Empty metrics dict causes all rules to be skipped gracefully."""
        findings, skipped = evaluate_rules({})
        assert findings == []
        assert len(skipped) == 7  # All D01-D07 skipped

    def test_partial_metrics_skip_subset(self):
        """Only rules with available metrics produce findings or pass cleanly."""
        metrics = {
            "overload_total": 5,  # D02 triggers
            "decompression_budget_exceeded_total": 0,  # D05 passes (no finding)
        }
        findings, skipped = evaluate_rules(metrics)

        # D02 should trigger (overload > 0)
        rule_ids = [f.rule_id for f in findings]
        assert "D02" in rule_ids

        # D05 should NOT be skipped and NOT produce a finding
        skipped_ids = [s.split(" ")[0] for s in skipped]
        assert "D05" not in skipped_ids
        assert "D05" not in rule_ids

        # Rules missing their metrics should be skipped
        assert "D01" in skipped_ids
        assert "D03" in skipped_ids
        assert "D04" in skipped_ids
        assert "D06" in skipped_ids
        assert "D07" in skipped_ids

    def test_nested_metrics_lookup(self):
        """Metrics can be found in nested sub-objects."""
        metrics = {
            "perf": {
                "overload_total": 3,
            }
        }
        findings, skipped = evaluate_rules(metrics)
        rule_ids = [f.rule_id for f in findings]
        assert "D02" in rule_ids
