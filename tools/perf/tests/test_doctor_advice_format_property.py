"""Property-based tests for doctor advice output format validity.

**Validates: Requirements 8.4**

Property 11: Doctor Advice Output Format Validity
    For any non-empty findings, verify:
    - JSON output parses as valid JSON with required schema fields
      (timestamp, source, findings[], summary, skipped_rules)
    - Human-readable format contains [SEVERITY], rule ID, and advice text
      for each finding

Run:
    python3 -m pytest tools/perf/tests/test_doctor_advice_format_property.py -q
"""

import json
import sys
from pathlib import Path
from typing import List

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Make the parent package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doctor_advice import (
    Finding,
    format_json,
    format_text,
    compute_exit_code,
    evaluate_rules,
    SEVERITY_ORDER,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

severity_st = st.sampled_from(["info", "warn", "critical"])
rule_id_st = st.sampled_from(["D01", "D02", "D03", "D04", "D05", "D06", "D07"])

# Printable strings for messages and advice text (non-empty)
text_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=100,
)

# Metric values: positive numbers used in findings
metric_value_st = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Strategy for a single Finding
finding_st = st.builds(
    Finding,
    rule_id=rule_id_st,
    severity=severity_st,
    message=text_st,
    advice=text_st,
    metrics_used=st.dictionaries(
        keys=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), blacklist_characters="\x00"),
            min_size=1,
            max_size=30,
        ),
        values=metric_value_st,
        min_size=0,
        max_size=3,
    ),
)

# Strategy for a list of findings (non-empty for format validation)
findings_list_st = st.lists(finding_st, min_size=1, max_size=7)

# Strategy for skipped rules
skipped_rule_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), blacklist_characters="\x00"),
    min_size=1,
    max_size=80,
)
skipped_list_st = st.lists(skipped_rule_st, min_size=0, max_size=5)

# Source string strategy
source_st = st.one_of(
    st.just("http://localhost:9145/metrics"),
    st.just("/tmp/metrics.json"),
    text_st,
)


# ---------------------------------------------------------------------------
# Property 11: JSON output format validity
# ---------------------------------------------------------------------------


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_output_parses_as_valid_json(findings, skipped, source):
    """**Validates: Requirements 8.4**

    For any non-empty set of findings, format_json SHALL produce output that
    parses as valid JSON without errors.
    """
    output = format_json(findings, skipped, source)
    parsed = json.loads(output)
    assert isinstance(parsed, dict)


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_schema_has_required_fields(findings, skipped, source):
    """**Validates: Requirements 8.4**

    For any non-empty findings, the JSON output SHALL contain all required
    schema fields: timestamp, source, findings[], summary, skipped_rules.
    """
    output = format_json(findings, skipped, source)
    parsed = json.loads(output)

    # Top-level required keys
    assert "timestamp" in parsed, "Missing 'timestamp' field"
    assert "source" in parsed, "Missing 'source' field"
    assert "findings" in parsed, "Missing 'findings' field"
    assert "summary" in parsed, "Missing 'summary' field"
    assert "skipped_rules" in parsed, "Missing 'skipped_rules' field"

    # Type checks
    assert isinstance(parsed["timestamp"], str)
    assert isinstance(parsed["source"], str)
    assert isinstance(parsed["findings"], list)
    assert isinstance(parsed["summary"], dict)
    assert isinstance(parsed["skipped_rules"], list)


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_findings_schema(findings, skipped, source):
    """**Validates: Requirements 8.4**

    Each finding in the JSON output SHALL contain id, severity, message,
    advice, and metrics fields.
    """
    output = format_json(findings, skipped, source)
    parsed = json.loads(output)

    assert len(parsed["findings"]) == len(findings)

    for entry in parsed["findings"]:
        assert "id" in entry, "Finding missing 'id'"
        assert "severity" in entry, "Finding missing 'severity'"
        assert "message" in entry, "Finding missing 'message'"
        assert "advice" in entry, "Finding missing 'advice'"
        assert "metrics" in entry, "Finding missing 'metrics'"

        # Severity must be a valid value
        assert entry["severity"] in ("info", "warn", "critical"), (
            f"Invalid severity: {entry['severity']}"
        )


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_summary_counts_match(findings, skipped, source):
    """**Validates: Requirements 8.4**

    The summary object SHALL contain correct counts for critical, warn,
    and info findings that match the actual findings list.
    """
    output = format_json(findings, skipped, source)
    parsed = json.loads(output)

    summary = parsed["summary"]
    assert "critical" in summary
    assert "warn" in summary
    assert "info" in summary

    # Verify counts match actual findings
    expected_critical = sum(1 for f in findings if f.severity == "critical")
    expected_warn = sum(1 for f in findings if f.severity == "warn")
    expected_info = sum(1 for f in findings if f.severity == "info")

    assert summary["critical"] == expected_critical, (
        f"Critical count mismatch: {summary['critical']} != {expected_critical}"
    )
    assert summary["warn"] == expected_warn, (
        f"Warn count mismatch: {summary['warn']} != {expected_warn}"
    )
    assert summary["info"] == expected_info, (
        f"Info count mismatch: {summary['info']} != {expected_info}"
    )


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_skipped_rules_match(findings, skipped, source):
    """**Validates: Requirements 8.4**

    The skipped_rules array in JSON output SHALL contain the same entries
    as the input skipped list.
    """
    output = format_json(findings, skipped, source)
    parsed = json.loads(output)

    assert parsed["skipped_rules"] == skipped


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_source_preserved(findings, skipped, source):
    """**Validates: Requirements 8.4**

    The source field in JSON output SHALL match the input source string.
    """
    output = format_json(findings, skipped, source)
    parsed = json.loads(output)

    assert parsed["source"] == source


# ---------------------------------------------------------------------------
# Property 11: Human-readable text output format validity
# ---------------------------------------------------------------------------


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_text_contains_severity_tag(findings, skipped, source):
    """**Validates: Requirements 8.4**

    The human-readable text output SHALL contain the severity tag
    [INFO], [WARN], or [CRITICAL] for each finding.
    """
    output = format_text(findings, skipped, source)

    for f in findings:
        tag = f"[{f.severity.upper()}]"
        assert tag in output, (
            f"Missing severity tag '{tag}' in text output for finding {f.rule_id}"
        )


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_text_contains_rule_id(findings, skipped, source):
    """**Validates: Requirements 8.4**

    The human-readable text output SHALL contain the rule ID (D01-D07)
    for each finding.
    """
    output = format_text(findings, skipped, source)

    for f in findings:
        assert f.rule_id in output, (
            f"Missing rule ID '{f.rule_id}' in text output"
        )


@given(
    findings=findings_list_st,
    skipped=skipped_list_st,
    source=source_st,
)
@settings(max_examples=200)
def test_property11_text_contains_advice_text(findings, skipped, source):
    """**Validates: Requirements 8.4**

    The human-readable text output SHALL contain the advice text for each
    finding, prefixed by "Advice:".
    """
    output = format_text(findings, skipped, source)

    for f in findings:
        assert f.advice in output, (
            f"Missing advice text '{f.advice[:40]}...' in text output "
            f"for finding {f.rule_id}"
        )
        # Verify the "Advice:" label exists
        assert "Advice:" in output, "Missing 'Advice:' label in text output"


# ---------------------------------------------------------------------------
# Property 11: Mixed severity combinations
# ---------------------------------------------------------------------------


@given(
    findings=st.lists(finding_st, min_size=1, max_size=7),
    source=source_st,
)
@settings(max_examples=200)
def test_property11_json_and_text_consistent_finding_count(findings, source):
    """**Validates: Requirements 8.4**

    Both JSON and text formats SHALL represent the same number of findings
    when given identical input.
    """
    skipped: List[str] = []

    json_output = format_json(findings, skipped, source)
    text_output = format_text(findings, skipped, source)

    parsed = json.loads(json_output)
    assert len(parsed["findings"]) == len(findings)

    # Each rule_id should appear in both outputs
    for f in findings:
        assert f.rule_id in text_output


# ---------------------------------------------------------------------------
# Property 11: Exit code matches maximum severity
# ---------------------------------------------------------------------------


@given(findings=findings_list_st)
@settings(max_examples=200)
def test_property11_exit_code_reflects_max_severity(findings):
    """**Validates: Requirements 8.4**

    The exit code SHALL equal the numeric value of the maximum severity
    among all findings (0=info, 1=warn, 2=critical).
    """
    exit_code = compute_exit_code(findings)

    max_sev = max(SEVERITY_ORDER.get(f.severity, 0) for f in findings)
    assert exit_code == max_sev, (
        f"Exit code {exit_code} != max severity {max_sev}"
    )
