#!/usr/bin/env python3
"""Performance Doctor Advice Tool.

Analyzes nginx-markdown module metrics and produces actionable tuning
recommendations for operators. Implements 7 rules (D01-D07) mapping metric
patterns to severity-tagged advice.

Requirements: Python 3.8+ stdlib only — no external pip dependencies.

Exit codes:
    0 — info-only findings (or no findings)
    1 — at least one warning finding
    2 — at least one critical finding

Usage:
    python3 tools/perf/doctor_advice.py --endpoint <url>
    python3 tools/perf/doctor_advice.py --metrics-file <path>
    python3 tools/perf/doctor_advice.py --metrics-file <path> --format json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


# ---------------------------------------------------------------------------
# Metric schema validation
# ---------------------------------------------------------------------------

_SCHEMA_RELATIVE_PATH = os.path.join("perf", "metrics-schema.json")
_ALLOWED_ENDPOINT_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _find_schema_path() -> Optional[str]:
    """Locate perf/metrics-schema.json relative to the repository root."""
    # Try relative to this script (tools/perf/doctor_advice.py -> repo root)
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent
    candidate = repo_root / _SCHEMA_RELATIVE_PATH
    if candidate.is_file():
        return str(candidate)
    # Try current working directory
    cwd_candidate = Path.cwd() / _SCHEMA_RELATIVE_PATH
    return str(cwd_candidate) if cwd_candidate.is_file() else None


def _load_valid_metric_names() -> Optional[set]:
    """Load valid metric names from perf/metrics-schema.json."""
    schema_path = _find_schema_path()
    if schema_path is None:
        return None
    try:
        with open(schema_path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        names = set()
        for entry in schema.get("metrics", []):
            if name := entry.get("name"):
                names.add(name)
        return names
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Metrics fetcher
# ---------------------------------------------------------------------------


def fetch_metrics_http(url: str) -> Dict[str, Any]:
    """Fetch metrics JSON from an HTTP(S) endpoint."""
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme
    if scheme not in ("http", "https"):
        print(
            "ERROR: --endpoint must use http or https scheme",
            file=sys.stderr,
        )
        sys.exit(2)

    if parsed.hostname not in _ALLOWED_ENDPOINT_HOSTS:
        print(
            "ERROR: --endpoint host must be localhost, 127.0.0.1, or ::1",
            file=sys.stderr,
        )
        sys.exit(2)

    if parsed.username is not None or parsed.password is not None:
        print("ERROR: --endpoint must not include credentials", file=sys.stderr)
        sys.exit(2)

    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            return json.loads(data)
    except OSError as exc:
        print(f"ERROR: Failed to fetch metrics from {url}: {exc}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON from {url}: {exc}", file=sys.stderr)
        sys.exit(2)


def fetch_metrics_file(path: str) -> Dict[str, Any]:
    """Read metrics JSON from a local file."""
    try:
        validated_path = validate_read_path(path, purpose="metrics file")
    except ValueError as exc:
        print(f"ERROR: Invalid metrics file path: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        with validated_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        print(f"ERROR: Cannot read file {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    except ValueError as exc:
        print(f"ERROR: Invalid metrics data in {path}: {exc}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Metric extraction helpers
# ---------------------------------------------------------------------------


def _get_metric(metrics: Dict[str, Any], name: str) -> Optional[float]:
    """Extract a numeric metric value from the metrics dict.

    Supports flat keys and nested structures (e.g. metrics inside sub-objects).
    Returns None if not found or not numeric.
    """
    # Direct top-level lookup
    if name in metrics:
        val = metrics[name]
        return float(val) if isinstance(val, (int, float)) else None
    # Search one level deep for nested metric objects
    for sub in metrics.values():
        if isinstance(sub, dict) and name in sub:
            val = sub[name]
            if isinstance(val, (int, float)):
                return float(val)

    return None


def _get_metric_path(metrics: Dict[str, Any], *path: str) -> Optional[float]:
    """Extract a numeric metric from an explicit dotted path.

    For example ``_get_metric_path(metrics, "streaming_metrics", "requests_total")``
    reads ``metrics["streaming_metrics"]["requests_total"]``.  This avoids
    the ambiguity of ``_get_metric`` which returns the first matching key
    at any level (the production diagnostics JSON emits ``requests_total``
    in both ``metrics_snapshot`` and ``streaming_metrics``).
    """
    current: Any = metrics
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if isinstance(current, (int, float)):
        return float(current)
    return None


# ---------------------------------------------------------------------------
# Rule Engine — D01 through D07
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}


class Finding:
    """Represents a single doctor advice finding."""

    def __init__(
        self,
        rule_id: str,
        severity: str,
        message: str,
        advice: str,
        metrics_used: Dict[str, Any],
    ):
        self.rule_id = rule_id
        self.severity = severity
        self.message = message
        self.advice = advice
        self.metrics_used = metrics_used


class RuleResult:
    """Result of evaluating a single rule."""

    def __init__(
        self,
        rule_id: str,
        finding: Optional[Finding] = None,
        skipped: bool = False,
        skip_reason: str = "",
    ):
        self.rule_id = rule_id
        self.finding = finding
        self.skipped = skipped
        self.skip_reason = skip_reason


# Rule definitions with explicit metric_source mapping
_SOURCE_EXISTING = "existing (ngx_http_markdown_metrics_t)"
_SOURCE_PERF_SPEC = "new (perf spec §6)"

RULE_METRICS = {
    "D01": {
        "required": ["fallback_total", "requests_total"],
        "metric_source": {
            "fallback_total": _SOURCE_EXISTING,
            "requests_total": _SOURCE_EXISTING,
        },
    },
    "D02": {
        "required": ["overload_total"],
        "metric_source": {
            "overload_total": "existing (ngx_http_markdown_metrics_t.inflight)",
        },
    },
    "D03": {
        "required": ["backpressure_total", "requests_total"],
        "metric_source": {
            "backpressure_total": _SOURCE_PERF_SPEC,
            "requests_total": _SOURCE_EXISTING,
        },
    },
    "D04": {
        "required": ["decompression_fullbuffer_total", "decompression_streaming_total"],
        "metric_source": {
            "decompression_fullbuffer_total": _SOURCE_PERF_SPEC,
            "decompression_streaming_total": _SOURCE_PERF_SPEC,
        },
    },
    "D05": {
        "required": ["decompression_budget_exceeded_total"],
        "metric_source": {
            "decompression_budget_exceeded_total": _SOURCE_PERF_SPEC,
        },
    },
    "D06": {
        "required": ["pending_output_high_watermark_bytes"],
        "optional": ["streaming_buffer_budget"],
        "metric_source": {
            "pending_output_high_watermark_bytes": _SOURCE_PERF_SPEC,
            "streaming_buffer_budget": _SOURCE_EXISTING,
        },
    },
    "D07": {
        "required": ["copied_output_total", "zero_copy_output_total"],
        "metric_source": {
            "copied_output_total": _SOURCE_PERF_SPEC,
            "zero_copy_output_total": _SOURCE_PERF_SPEC,
        },
    },
}

# Default streaming buffer budget for D06 (markdown_streaming_buffer_size default)
DEFAULT_STREAMING_BUFFER_BUDGET = 1048576  # 1 MiB


def _evaluate_d01(metrics: Dict[str, Any]) -> RuleResult:
    """D01: High streaming fallback rate.

    Pattern: fallback_total / requests_total > 10%

    Uses explicit path lookup to read from the ``streaming_metrics`` object
    in the production diagnostics JSON, avoiding ambiguity with the
    ``metrics_snapshot.requests_total`` counter.
    """
    fallback = _get_metric_path(metrics, "streaming_metrics", "fallback_total")
    requests = _get_metric_path(metrics, "streaming_metrics", "requests_total")

    if fallback is None or requests is None:
        missing = []
        if fallback is None:
            missing.append("streaming_metrics.fallback_total")
        if requests is None:
            missing.append("streaming_metrics.requests_total")
        return RuleResult(
            "D01", skipped=True, skip_reason=f"metric missing: {', '.join(missing)}"
        )

    if requests == 0:
        return RuleResult("D01")  # No streaming requests, rule not applicable

    rate = fallback / requests
    if rate > 0.10:
        return RuleResult(
            "D01",
            finding=Finding(
                rule_id="D01",
                severity="warn",
                message=(
                    f"Streaming fallback rate is {rate:.1%} "
                    f"({int(fallback)}/{int(requests)} requests)"
                ),
                advice=(
                    "High fallback rate suggests streaming eligibility issues. "
                    "Check response sizes, Content-Type patterns, and "
                    "markdown_stream_threshold thresholds."
                ),
                metrics_used={
                    "fallback_total": fallback,
                    "requests_total": requests,
                },
            ),
        )
    return RuleResult("D01")


def _evaluate_d02(metrics: Dict[str, Any]) -> RuleResult:
    """D02: Overload events detected.

    Pattern: overload_total > 0
    """
    overload = _get_metric(metrics, "overload_total")

    if overload is None:
        return RuleResult(
            "D02", skipped=True, skip_reason="metric missing: overload_total"
        )

    if overload > 0:
        return RuleResult(
            "D02",
            finding=Finding(
                rule_id="D02",
                severity="warn",
                message=f"Overload rejection count: {int(overload)}",
                advice=(
                    "Worker inflight limit was reached. Consider increasing "
                    "markdown_limits max_inflight or adding more worker processes."
                ),
                metrics_used={"overload_total": overload},
            ),
        )
    return RuleResult("D02")


def _evaluate_d03(metrics: Dict[str, Any]) -> RuleResult:
    """D03: High backpressure rate.

    Pattern: backpressure_total / requests_total > 5%

    ``backpressure_total`` is emitted in the ``metrics_snapshot`` object by
    the C diagnostics JSON renderer.  ``requests_total`` for streaming is
    emitted in the ``streaming_metrics`` object.  Uses explicit path
    lookup to avoid ambiguity (production JSON emits ``requests_total``
    in both ``metrics_snapshot`` and ``streaming_metrics``).
    """
    bp = _get_metric_path(metrics, "metrics_snapshot", "backpressure_total")
    requests = _get_metric_path(metrics, "streaming_metrics", "requests_total")

    if bp is None or requests is None:
        missing = []
        if bp is None:
            missing.append("metrics_snapshot.backpressure_total")
        if requests is None:
            missing.append("streaming_metrics.requests_total")
        return RuleResult(
            "D03", skipped=True, skip_reason=f"metric missing: {', '.join(missing)}"
        )

    if requests == 0:
        return RuleResult("D03")

    rate = bp / requests
    if rate > 0.05:
        return RuleResult(
            "D03",
            finding=Finding(
                rule_id="D03",
                severity="warn",
                message=(
                    f"Backpressure rate is {rate:.1%} "
                    f"({int(bp)}/{int(requests)} streaming requests)"
                ),
                advice=(
                    "Frequent backpressure indicates slow downstream clients or "
                    "large response bodies. Consider increasing output buffer "
                    "sizes or reducing concurrency."
                ),
                metrics_used={
                    "backpressure_total": bp,
                    "streaming_requests_total": requests,
                },
            ),
        )
    return RuleResult("D03")


def _evaluate_d04(metrics: Dict[str, Any]) -> RuleResult:
    """D04: Decompression heavily favors full-buffer over streaming.

    Pattern: decompression_fullbuffer_total >> decompression_streaming_total (>10:1)
    """
    fullbuf = _get_metric(metrics, "decompression_fullbuffer_total")
    streaming = _get_metric(metrics, "decompression_streaming_total")

    if fullbuf is None or streaming is None:
        missing = []
        if fullbuf is None:
            missing.append("decompression_fullbuffer_total")
        if streaming is None:
            missing.append("decompression_streaming_total")
        return RuleResult(
            "D04", skipped=True, skip_reason=f"metric missing: {', '.join(missing)}"
        )

    # If neither has any events, not applicable
    if fullbuf == 0 and streaming == 0:
        return RuleResult("D04")

    # Check ratio: fullbuffer >> streaming means streaming is underutilized
    if streaming == 0:
        ratio_str = f"{int(fullbuf)}:0"
        triggered = fullbuf > 0
    else:
        ratio = fullbuf / streaming
        ratio_str = f"{ratio:.1f}:1"
        triggered = ratio > 10.0

    if triggered:
        return RuleResult(
            "D04",
            finding=Finding(
                rule_id="D04",
                severity="info",
                message=(
                    f"Decompression routing ratio (full-buffer:streaming) = "
                    f"{ratio_str}"
                ),
                advice=(
                    "Most decompression uses the full-buffer path. If TTFB is "
                    "important, consider enabling streaming decompression via "
                    "streaming_first profile with auto_decompress on."
                ),
                metrics_used={
                    "decompression_fullbuffer_total": fullbuf,
                    "decompression_streaming_total": streaming,
                },
            ),
        )
    return RuleResult("D04")


def _evaluate_d05(metrics: Dict[str, Any]) -> RuleResult:
    """D05: Decompression budget exceeded events.

    Pattern: decompression_budget_exceeded_total > 0
    """
    exceeded = _get_metric(metrics, "decompression_budget_exceeded_total")

    if exceeded is None:
        return RuleResult(
            "D05",
            skipped=True,
            skip_reason="metric missing: decompression_budget_exceeded_total",
        )

    if exceeded > 0:
        return RuleResult(
            "D05",
            finding=Finding(
                rule_id="D05",
                severity="warn",
                message=(
                    f"Decompression budget exceeded {int(exceeded)} time(s)"
                ),
                advice=(
                    "Responses are exceeding markdown_decompress_max_size. "
                    "Increase the budget or investigate upstream response sizes."
                ),
                metrics_used={"decompression_budget_exceeded_total": exceeded},
            ),
        )
    return RuleResult("D05")


def _evaluate_d06(metrics: Dict[str, Any]) -> RuleResult:
    """D06: Pending output watermark near budget.

    Pattern: pending_output_high_watermark_bytes > 80% of streaming buffer budget
    """
    watermark = _get_metric(metrics, "pending_output_high_watermark_bytes")

    if watermark is None:
        return RuleResult(
            "D06",
            skipped=True,
            skip_reason="metric missing: pending_output_high_watermark_bytes",
        )

    # Use configured budget or default
    budget = _get_metric(metrics, "streaming_buffer_budget")
    if budget is None or budget <= 0:
        budget = DEFAULT_STREAMING_BUFFER_BUDGET

    threshold = budget * 0.80
    if watermark > threshold:
        pct = (watermark / budget) * 100
        return RuleResult(
            "D06",
            finding=Finding(
                rule_id="D06",
                severity="warn",
                message=(
                    f"Pending output watermark is {int(watermark)} bytes "
                    f"({pct:.0f}% of {int(budget)} budget)"
                ),
                advice=(
                    "Output pending buffer is near capacity. This increases "
                    "backpressure likelihood. Consider increasing buffer budget "
                    "or reducing conversion output size."
                ),
                metrics_used={
                    "pending_output_high_watermark_bytes": watermark,
                    "budget": budget,
                },
            ),
        )
    return RuleResult("D06")


def _evaluate_d07(metrics: Dict[str, Any]) -> RuleResult:
    """D07: High copied vs zero-copy output ratio (when zero-copy enabled).

    Pattern: copied_output_total >> zero_copy_output_total (>5:1)
    """
    copied = _get_metric(metrics, "copied_output_total")
    zero_copy = _get_metric(metrics, "zero_copy_output_total")

    if copied is None or zero_copy is None:
        missing = []
        if copied is None:
            missing.append("copied_output_total")
        if zero_copy is None:
            missing.append("zero_copy_output_total")
        return RuleResult(
            "D07", skipped=True, skip_reason=f"metric missing: {', '.join(missing)}"
        )

    # If zero_copy is 0, check if it's simply disabled (no zero-copy attempts)
    # When zero-copy is disabled entirely, both counters may be present but
    # zero_copy stays at 0 — we skip the rule in that case since the feature
    # isn't enabled.
    if zero_copy == 0 and copied == 0:
        return RuleResult("D07")

    if zero_copy == 0:
        # Feature may not be enabled; skip if copied also low
        if copied <= 0:
            return RuleResult("D07")
        ratio_str = f"{int(copied)}:0"
        triggered = True
    else:
        ratio = copied / zero_copy
        ratio_str = f"{ratio:.1f}:1"
        triggered = ratio > 5.0

    if triggered:
        return RuleResult(
            "D07",
            finding=Finding(
                rule_id="D07",
                severity="info",
                message=(
                    f"Copied vs zero-copy output ratio = {ratio_str}"
                ),
                advice=(
                    "Most output uses buffer copies instead of zero-copy. "
                    "Verify markdown_streaming_zero_copy is enabled and that "
                    "chunks are eligible (non-terminal, no backpressure)."
                ),
                metrics_used={
                    "copied_output_total": copied,
                    "zero_copy_output_total": zero_copy,
                },
            ),
        )
    return RuleResult("D07")


# Rule registry (ordered D01-D07)
_RULES: List[Any] = [
    _evaluate_d01,
    _evaluate_d02,
    _evaluate_d03,
    _evaluate_d04,
    _evaluate_d05,
    _evaluate_d06,
    _evaluate_d07,
]


def evaluate_rules(
    metrics: Dict[str, Any],
    *,
    streaming_buffer_budget: Optional[int] = None,
) -> Tuple[List[Finding], List[str]]:
    """Run all rules against metrics. Returns (findings, skipped_rules).

    Parameters:
        metrics: Metric data from the endpoint or file.
        streaming_buffer_budget: Optional CLI override for D06 threshold.
            When provided and the metrics payload does not contain
            ``streaming_buffer_budget``, this value is injected.
    """
    # Inject CLI budget override if the metrics payload lacks one
    if (streaming_buffer_budget is not None
            and _get_metric(metrics, "streaming_buffer_budget") is None):
        metrics = {**metrics, "streaming_buffer_budget": streaming_buffer_budget}

    findings: List[Finding] = []
    skipped: List[str] = []

    for rule_fn in _RULES:
        result = rule_fn(metrics)
        if result.skipped:
            skipped.append(f"{result.rule_id} ({result.skip_reason})")
        elif result.finding is not None:
            findings.append(result.finding)

    return findings, skipped


# ---------------------------------------------------------------------------
# Metric validation
# ---------------------------------------------------------------------------


def validate_metric_names(
    metrics: Dict[str, Any], valid_names: set
) -> List[str]:
    """Warn about metric keys not found in the schema."""
    warnings: List[str] = []
    # Check all rule-referenced metrics are in schema
    for rule_id, spec in RULE_METRICS.items():
        warnings.extend(
            f"Rule {rule_id} references '{metric_name}' not found in metrics-schema.json"
            for metric_name in spec["required"] + spec.get("optional", [])
            if metric_name not in valid_names
        )
    return warnings


# ---------------------------------------------------------------------------
def format_text(
    findings: List[Finding],
    skipped: List[str],
    source: str,
    validation_warnings: Optional[List[str]] = None,
) -> str:
    """Format findings as human-readable text."""
    lines: List[str] = [
        "=== Performance Doctor Advice ===",
        f"Source: {source}",
        f"Time: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    if not findings and not skipped:
        lines.append("No findings. All checks passed.")
        return "\n".join(lines)

    if findings:
        lines.extend((f"Findings ({len(findings)}):", "-" * 40))
        for f in findings:
            tag = f"[{f.severity.upper()}]"
            lines.extend(
                (
                    f"  {tag} {f.rule_id}: {f.message}",
                    f"         Advice: {f.advice}",
                    "",
                )
            )
    if skipped:
        _append_labeled_list(lines, 'Skipped rules (', skipped, '  - ')
    if validation_warnings:
        _append_labeled_list(
            lines, 'Schema validation warnings (', validation_warnings, '  ! '
        )
    # Summary
    summary = {"info": 0, "warn": 0, "critical": 0}
    for f in findings:
        summary[f.severity] += 1
    lines.append(
        f"Summary: {summary['critical']} critical, "
        f"{summary['warn']} warnings, {summary['info']} info"
    )

    return "\n".join(lines)


def _append_labeled_list(lines, header_prefix, items, item_prefix):
    lines.append(f"{header_prefix}{len(items)}):")
    for s in items:
        lines.append(f"{item_prefix}{s}")
    lines.append("")


def format_json(
    findings: List[Finding],
    skipped: List[str],
    source: str,
    validation_warnings: Optional[List[str]] = None,
) -> str:
    """Format findings as structured JSON conforming to output schema."""
    summary = {"critical": 0, "warn": 0, "info": 0}
    findings_list = []
    for f in findings:
        summary[f.severity] += 1
        findings_list.append(
            {
                "id": f.rule_id,
                "severity": f.severity,
                "message": f.message,
                "advice": f.advice,
                "metrics": f.metrics_used,
            }
        )

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "findings": findings_list,
        "summary": summary,
        "skipped_rules": skipped,
    }

    if validation_warnings:
        output["validation_warnings"] = validation_warnings

    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# Exit code determination
# ---------------------------------------------------------------------------


def compute_exit_code(findings: List[Finding]) -> int:
    """Determine exit code from max severity: 0=info, 1=warn, 2=critical."""
    if not findings:
        return 0
    return max(SEVERITY_ORDER.get(f.severity, 0) for f in findings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="doctor_advice",
        description=(
            "Analyze nginx-markdown module metrics and produce actionable "
            "tuning recommendations."
        ),
    )
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument(
        "--endpoint",
        metavar="URL",
        help="Fetch metrics from a JSON HTTP endpoint",
    )
    source_group.add_argument(
        "--metrics-file",
        metavar="PATH",
        help="Read metrics from a local JSON file",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--streaming-buffer-budget",
        type=int,
        default=DEFAULT_STREAMING_BUFFER_BUDGET,
        metavar="BYTES",
        help=(
            f"Streaming buffer budget for D06 threshold "
            f"(default: {DEFAULT_STREAMING_BUFFER_BUDGET})"
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.endpoint is None and args.metrics_file is None:
        parser.print_help(sys.stderr)
        return 2

    # Load metrics
    if args.endpoint:
        metrics = fetch_metrics_http(args.endpoint)
        source = args.endpoint
    else:
        metrics = fetch_metrics_file(args.metrics_file)
        source = args.metrics_file

    # Load schema for validation
    valid_names = _load_valid_metric_names()

    # Validate metric names if schema available
    validation_warnings: List[str] = []
    if valid_names is not None:
        validation_warnings = validate_metric_names(metrics, valid_names)

    # Evaluate rules
    findings, skipped = evaluate_rules(
        metrics,
        streaming_buffer_budget=args.streaming_buffer_budget,
    )

    # Format output
    if args.format == "json":
        output = format_json(findings, skipped, source, validation_warnings)
    else:
        output = format_text(findings, skipped, source, validation_warnings)

    print(output)

    return compute_exit_code(findings)


if __name__ == "__main__":
    sys.exit(main())
