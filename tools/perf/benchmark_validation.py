#!/usr/bin/env python3
"""Validate module benchmark load results and correctness probes."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _failure(summary: dict, reason: str) -> dict:
    summary["verdict"] = "fail"
    summary["failure_reason"] = reason
    return summary


def parse_ab_result(content: str, iterations: int) -> dict:
    """Return strict request-integrity evidence parsed from ab output."""
    complete_match = re.search(r"^Complete requests:\s+(\d+)\s*$", content, re.M)
    failed_match = re.search(r"^Failed requests:\s+(\d+)\s*$", content, re.M)
    non_2xx_match = re.search(r"^Non-2xx responses:\s+(\d+)\s*$", content, re.M)
    summary = {
        "configured_requests": iterations,
        "completed_requests": (
            int(complete_match.group(1)) if complete_match else None
        ),
        "failed_requests": int(failed_match.group(1)) if failed_match else None,
        "non_2xx_responses": (
            int(non_2xx_match.group(1)) if non_2xx_match else 0
        ),
        "transport_errors": 0,
        "verdict": "pass",
        "failure_reason": "",
    }
    if complete_match is None or failed_match is None:
        return _failure(summary, "load_result_unparseable: required ab fields")
    if summary["completed_requests"] != iterations:
        return _failure(summary, "request_count_mismatch")
    if summary["failed_requests"] != 0:
        return _failure(summary, "failed_requests_nonzero")
    if summary["non_2xx_responses"] != 0:
        return _failure(summary, "non_2xx_responses_nonzero")
    return summary


def parse_hey_result(content: str, iterations: int) -> dict:
    """Return strict request-integrity evidence parsed from hey CSV output."""
    try:
        reader = csv.DictReader(io.StringIO(content))
        if reader.fieldnames is None or "status-code" not in reader.fieldnames:
            raise ValueError("missing status-code column")
        rows = list(reader)
        statuses = [int(row["status-code"]) for row in rows]
    except (TypeError, ValueError, KeyError):
        return _failure(
            {
                "configured_requests": iterations,
                "completed_requests": None,
                "failed_requests": None,
                "non_2xx_responses": None,
                "transport_errors": None,
                "verdict": "pass",
                "failure_reason": "",
            },
            "load_result_unparseable: invalid hey CSV",
        )

    errors = sum(bool((row.get("error") or "").strip()) for row in rows)
    non_2xx = sum(not 200 <= status < 300 for status in statuses)
    summary = {
        "configured_requests": iterations,
        "completed_requests": len(rows),
        "failed_requests": errors,
        "non_2xx_responses": non_2xx,
        "transport_errors": errors,
        "verdict": "pass",
        "failure_reason": "",
    }
    if len(rows) != iterations:
        return _failure(summary, "request_count_mismatch")
    if errors:
        return _failure(summary, "transport_errors_nonzero")
    if non_2xx:
        return _failure(summary, "non_2xx_responses_nonzero")
    return summary


def validate_response_probe(
    *,
    status: int,
    headers: Mapping[str, str],
    body: bytes,
    expected_heading: str,
    expected_tail_token: str,
    expected_tail_count: int,
    compressed: bool,
) -> dict:
    """Validate a benchmark response before accepting scenario evidence."""
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    content_type = normalized_headers.get("content-type", "")
    content_encoding = normalized_headers.get("content-encoding", "")
    result = {
        "http_status": status,
        "headers": dict(normalized_headers),
        "content_type": content_type,
        "content_encoding": content_encoding,
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "heading_present": False,
        "tail_token_present": False,
        "tail_token_count": 0,
        "verdict": "pass",
        "failure_reason": "",
    }
    transport_failure = _probe_transport_failure(
        status, content_type, content_encoding, body, compressed
    )
    if transport_failure:
        return _failure(result, transport_failure)

    text = body.decode("utf-8", errors="replace")
    result["heading_present"] = expected_heading in text
    result["tail_token_count"] = text.count(expected_tail_token)
    result["tail_token_present"] = (
        result["tail_token_count"] == expected_tail_count
    )
    content_failure = _probe_content_failure(
        text,
        result["heading_present"],
        result["tail_token_count"],
        expected_tail_count,
    )
    if content_failure:
        return _failure(result, content_failure)
    return result


def _probe_transport_failure(
    status: int,
    content_type: str,
    content_encoding: str,
    body: bytes,
    compressed: bool,
) -> str | None:
    if status != 200:
        return f"http_status: expected 200, got {status}"
    if content_type.split(";", 1)[0].strip().lower() != "text/markdown":
        return f"content_type: expected text/markdown, got {content_type!r}"
    if not body:
        return "body_empty"
    if compressed and content_encoding:
        return f"content_encoding: must be absent, got {content_encoding!r}"
    if compressed and _is_wire_compressed(body):
        return "compressed_payload_detected"
    return None


def _is_wire_compressed(body: bytes) -> bool:
    is_gzip = body.startswith(b"\x1f\x8b")
    is_zlib = (
        len(body) >= 2
        and body[0] == 0x78
        and body[1] in (0x01, 0x5E, 0x9C, 0xDA)
    )
    return is_gzip or is_zlib


def _probe_content_failure(
    text: str,
    heading_present: bool,
    tail_token_count: int,
    expected_tail_count: int,
) -> str | None:
    if not heading_present:
        return "heading_missing"
    if tail_token_count != expected_tail_count:
        return (
            f"tail_token_count: expected {expected_tail_count}, "
            f"got {tail_token_count}"
        )
    if "<html" in text.lower() or "<!doctype html" in text.lower():
        return "raw_html_detected"
    return None


def compare_streaming_probe_bodies(probes: Mapping[str, bytes]) -> dict[str, str]:
    """Return compressed streaming scenarios whose Markdown differs."""
    reference = probes.get("streaming-first")
    if reference is None:
        return {}
    failures = {}
    for name in ("gzip-streaming-first", "deflate-streaming-first"):
        body = probes.get(name)
        if body is not None and body != reference:
            failures[name] = "response_body_mismatch: streaming-first"
    return failures


def attach_response_probe(scenario: dict, probe: Mapping[str, Any]) -> dict:
    """Attach probe evidence and fail the scenario when correctness fails."""
    scenario["response_correctness"] = dict(probe)
    if probe.get("verdict") != "pass":
        reason = (
            "response_correctness_failed: "
            f"{probe.get('failure_reason', 'unknown')}"
        )
        previous_reason = scenario.get("reason")
        scenario["reason"] = (
            f"{previous_reason}; {reason}" if previous_reason else reason
        )
        scenario["status"] = "failed"
    return scenario


def _percentile(values: list[float], fraction: float) -> float:
    return values[min(int(len(values) * fraction), len(values) - 1)]


def _ab_performance(content: str) -> tuple[float, float, float, float]:
    patterns = (
        r"Requests per second:\s+([\d.]+)",
        r"\s+50%\s+(\d+)",
        r"\s+95%\s+(\d+)",
        r"\s+99%\s+(\d+)",
    )
    values = []
    for pattern in patterns:
        match = re.search(pattern, content)
        if match is None:
            return 0.0, 0.0, 0.0, 0.0
        values.append(float(match.group(1)))
    return values[0], values[1], values[2], values[3]


def _hey_performance(content: str) -> tuple[float, float, float, float]:
    try:
        rows = list(csv.DictReader(io.StringIO(content)))
        latencies = sorted(float(row["response-time"]) * 1000.0 for row in rows)
        wall_end = max(
            float(row.get("offset") or 0) + float(row["response-time"])
            for row in rows
        )
    except (KeyError, TypeError, ValueError):
        return 0.0, 0.0, 0.0, 0.0
    if not latencies or wall_end <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        len(latencies) / wall_end,
        _percentile(latencies, 0.50),
        _percentile(latencies, 0.95),
        _percentile(latencies, 0.99),
    )


@dataclass(frozen=True, slots=True)
class ScenarioResultInput:
    """Inputs needed to build one benchmark scenario result."""

    raw_content: str
    load_generator: str
    iterations: int
    load_exit_code: int
    name: str
    profile: str
    compression: str
    transfer_encoding: str
    concurrency: int
    worker_rss_kb: int
    baseline_rss_kb: int
    peak_rss_kb: int
    input_bytes: int
    ttfb: Mapping[str, Any]
    nginx_metrics: Mapping[str, Any]


def build_scenario_result(data: ScenarioResultInput) -> dict:
    """Build a scenario report gated by strict load-integrity evidence."""
    if data.load_generator == "ab":
        load = parse_ab_result(data.raw_content, data.iterations)
        rps, p50, p95, p99 = _ab_performance(data.raw_content)
    elif data.load_generator == "hey":
        load = parse_hey_result(data.raw_content, data.iterations)
        rps, p50, p95, p99 = _hey_performance(data.raw_content)
    else:
        load = _failure(
            {}, f"unknown_load_generator: {data.load_generator}"
        )
        rps = p50 = p95 = p99 = 0.0
    if data.load_exit_code != 0:
        load = _failure(load, f"load_generator_exit: {data.load_exit_code}")

    perf = data.nginx_metrics.get("perf", {}) or {}
    streaming = data.nginx_metrics.get("streaming", {}) or {}
    streaming_hits = data.nginx_metrics.get("streaming_path_hits", 0)
    fullbuffer_hits = data.nginx_metrics.get("fullbuffer_path_hits", 0)
    total_hits = streaming_hits + fullbuffer_hits
    streaming_ratio = streaming_hits / total_hits if total_hits > 0 else None
    fullbuffer_ratio = fullbuffer_hits / total_hits if total_hits > 0 else None
    requests_total = streaming.get("requests_total", 0)
    failopen_total = streaming.get("precommit_failopen_total", 0)
    fallback_rate = (
        failopen_total / requests_total if requests_total > 0 else 0.0
    )
    result = {
        "name": data.name,
        "profile": data.profile,
        "compression": data.compression,
        "transfer_encoding": data.transfer_encoding,
        "concurrency": data.concurrency,
        "status": "completed" if load.get("verdict") == "pass" else "failed",
        "load_integrity": load,
        "metrics": {
            "rps": rps,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "latency_p99_ms": p99,
            "ttfb_p50_ms": data.ttfb.get("ttfb_p50_ms"),
            "ttfb_p95_ms": data.ttfb.get("ttfb_p95_ms"),
            "ttlb_p50_ms": p50,
            "worker_rss_mb": data.worker_rss_kb / 1024.0,
            "baseline_rss_bytes": data.baseline_rss_kb * 1024,
            "peak_rss_bytes": data.peak_rss_kb * 1024,
            "input_bytes": data.input_bytes,
            "streaming_path_hits": streaming_hits,
            "fullbuffer_path_hits": fullbuffer_hits,
            "streaming_ratio": streaming_ratio,
            "fullbuffer_ratio": fullbuffer_ratio,
            "fallback_rate": fallback_rate,
            "streaming_fallback_total": streaming.get("fallback_total", 0),
            "streaming_requests_total": requests_total,
            "precommit_failopen_total": failopen_total,
            "throughput_mbps": 0.0,
            "decompression_streaming_total": perf.get(
                "decompression_streaming_total", 0
            ),
            "decompression_fullbuffer_total": perf.get(
                "decompression_fullbuffer_total", 0
            ),
            "zero_copy_output_total": perf.get("zero_copy_output_total", 0),
            "copied_output_total": perf.get("copied_output_total", 0),
            "pending_output_high_watermark_bytes": perf.get(
                "pending_output_high_watermark_bytes", 0
            ),
        },
    }
    if result["status"] == "failed":
        result["reason"] = f"load_integrity_failed: {load['failure_reason']}"
    return result
