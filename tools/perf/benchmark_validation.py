#!/usr/bin/env python3
"""Validate module benchmark load results and correctness probes."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9a-z-]+$")
_HTTP_STATUS_LINE_RE = re.compile(
    r"^HTTP/[^\s]+[ \t]+([1-5]\d{2})",
    re.ASCII,
)
_PROMETHEUS_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[+-]?Inf|NaN)"
    r"(?:\s+\d+)?$"
)
_PROMETHEUS_LABEL_NAME_RE = re.compile(r"[a-zA-Z_]\w*", re.ASCII)

#: Canonical module-benchmark scenario names.
#: Kept here as a single source of truth shared by
#: ``run_module_benchmark.sh``, ``evidence_gate.py``, and
#: ``validate_module_probe_artifacts.py``.  Order matches the
#: fixture array in ``run_module_benchmark.sh``.
SCENARIOS: tuple[str, ...] = (
    "plain-small",
    "chunked-medium",
    "gzip-large",
    "large-body",
    "streaming-first",
    "gzip-streaming-first",
    "deflate-streaming-first",
    "brotli-streaming-first",
)

# The shell harness keeps its internal configuration selector for choosing an
# NGINX config, but benchmark evidence must not publish the removed
# ``profile`` vocabulary as if it were a user-facing directive.  Keep this
# mapping at the report boundary so old internal names cannot leak into a
# checked-in baseline.
_SCENARIO_CONFIG_BY_PROFILE = {
    "balanced": "explicit-defaults",
    "streaming_first": "explicit-streaming",
    "strict_cache": "explicit-strict-cache",
}


def _scenario_config_for_report(profile: str) -> str:
    """Translate an internal harness mode to the frozen report vocabulary."""
    try:
        return _SCENARIO_CONFIG_BY_PROFILE[profile]
    except KeyError as exc:
        raise ValueError(
            f"unsupported internal benchmark configuration: {profile!r}"
        ) from exc


def _normalize_header_name(name: str) -> str:
    normalized = name.strip().lower()
    if not _HEADER_NAME_RE.fullmatch(normalized):
        raise ValueError(f"invalid HTTP header name: {name!r}")
    return normalized


def normalize_header_mapping(headers: Mapping[str, str]) -> dict[str, str]:
    """Normalize one HTTP header mapping using the probe contract."""
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("HTTP header names and values must be strings")
        normalized_key = _normalize_header_name(key)
        if normalized_key in normalized:
            raise ValueError(f"duplicate HTTP header name: {normalized_key}")
        normalized[normalized_key] = value.strip()
    return normalized


def normalized_header_mapping_error(headers: object) -> str | None:
    """Return a schema error for a JSON normalized-header object."""
    if not isinstance(headers, dict):
        return "headers must be an object"
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return "headers key/value pairs must be strings"
        if key != key.strip() or key != key.lower():
            return f"header key must be normalized lowercase: {key!r}"
        try:
            _normalize_header_name(key)
        except ValueError as exc:
            return str(exc)
    return None


def _split_header_blocks(content: str) -> list[list[str]]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line == "":
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _parse_header_block(block: list[str]) -> tuple[int, dict[str, str]]:
    status_line = block[0]
    status_match = _HTTP_STATUS_LINE_RE.match(status_line)
    suffix = status_line[status_match.end():] if status_match else ""
    if status_match is None or (suffix and suffix[0] not in " \t"):
        raise ValueError(f"invalid HTTP status line: {block[0]!r}")
    status = int(status_match.group(1))
    headers: dict[str, str] = {}
    for line in block[1:]:
        if ":" not in line:
            raise ValueError(f"invalid HTTP header line: {line!r}")
        key, value = line.split(":", 1)
        normalized = normalize_header_mapping({key: value})
        normalized_key, normalized_value = next(iter(normalized.items()))
        if normalized_key in headers:
            raise ValueError(f"duplicate HTTP header name: {normalized_key}")
        headers[normalized_key] = normalized_value
    return status, headers


def parse_curl_header_artifact(content: str) -> tuple[int, dict[str, str]]:
    """Parse the final valid HTTP response block from curl ``-D`` output."""
    blocks = _split_header_blocks(content)
    if not blocks:
        raise ValueError("header artifact has no HTTP response block")
    parsed_blocks = [_parse_header_block(block) for block in blocks]
    return parsed_blocks[-1]


def _failure(summary: dict, reason: str) -> dict:
    summary["verdict"] = "fail"
    summary["failure_reason"] = reason
    return summary


def _skip_label_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t":
        position += 1
    return position


def _parse_label_name(
    text: str, position: int
) -> tuple[str, int] | None:
    position = _skip_label_whitespace(text, position)
    match = _PROMETHEUS_LABEL_NAME_RE.match(text, position)
    if match is None:
        return None
    position = _skip_label_whitespace(text, match.end())
    if position >= len(text) or text[position] != "=":
        return None
    return match.group(0), position + 1


def _decode_label_escape(
    text: str, position: int
) -> tuple[str, int] | None:
    if position >= len(text):
        return None
    escaped = text[position]
    decoded = {"\\": "\\", '"': '"', "n": "\n"}.get(escaped)
    if decoded is None:
        return None
    return decoded, position + 1


def _parse_label_value(
    text: str, position: int
) -> tuple[str, int] | None:
    position = _skip_label_whitespace(text, position)
    if position >= len(text) or text[position] != '"':
        return None
    position += 1
    value: list[str] = []
    while position < len(text):
        character = text[position]
        position += 1
        if character == '"':
            return "".join(value), position
        if character == "\\":
            decoded = _decode_label_escape(text, position)
            if decoded is None:
                return None
            character, position = decoded
        elif ord(character) < 0x20:
            return None
        value.append(character)
    return None


def _consume_label_separator(
    text: str, position: int
) -> tuple[int, bool] | None:
    position = _skip_label_whitespace(text, position)
    if position == len(text):
        return position, True
    if text[position] != ",":
        return None
    position += 1
    if position == len(text):
        return None
    return position, False


def _parse_prometheus_labels(label_text: str) -> dict[str, str] | None:
    """Parse a Prometheus label set with duplicate and escape checks."""
    labels: dict[str, str] = {}
    position = 0
    while position < len(label_text):
        parsed_name = _parse_label_name(label_text, position)
        if parsed_name is None:
            return None
        key, position = parsed_name
        if key in labels:
            return None
        parsed_value = _parse_label_value(label_text, position)
        if parsed_value is None:
            return None
        labels[key], position = parsed_value
        separator = _consume_label_separator(label_text, position)
        if separator is None:
            return None
        position, finished = separator
        if finished:
            return labels
    return labels


def _parse_prometheus_sample(
    raw_line: str,
) -> tuple[str, dict[str, str], int | float] | None:
    """Parse one Prometheus sample, ignoring comments and malformed lines."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    match = _PROMETHEUS_LINE_RE.fullmatch(line)
    if match is None:
        return None
    labels = _parse_prometheus_labels(match.group("labels") or "")
    if labels is None:
        return None
    try:
        value = float(match.group("value"))
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if value.is_integer():
        return match.group("name"), labels, int(value)
    return match.group("name"), labels, value


def _parse_prometheus_families(
    content: str,
) -> tuple[dict[str, list[tuple[dict[str, str], int | float]]], int]:
    """Collect valid Prometheus samples by family name.

    Returns (families, malformed_count) where malformed_count counts
    non-comment lines that were skipped because they could not be parsed
    as a well-formed sample (unparseable labels, non-numeric values, or
    non-finite values).  Exposing the count lets callers distinguish a
    clean document from one with silently dropped samples.
    """
    families: dict[str, list[tuple[dict[str, str], int | float]]] = {}
    malformed_count = 0
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample = _parse_prometheus_sample(raw_line)
        if sample is None:
            malformed_count += 1
            continue
        name, labels, value = sample
        families.setdefault(name, []).append((labels, value))
    return families, malformed_count


def _prometheus_total(
    families: dict[str, list[tuple[dict[str, str], int | float]]],
    name: str,
    **wanted: str,
) -> int | float:
    """Sum one family, optionally restricted to exact label values."""
    return sum(
        value
        for labels, value in families.get(name, [])
        if all(labels.get(key) == expected for key, expected in wanted.items())
    )


def parse_prometheus_metrics(content: str) -> dict[str, Any]:
    """Map the frozen Prometheus endpoint into benchmark-owned fields.

    The benchmark report keeps its historical, tool-owned metric names, while
    the module endpoint is intentionally Prometheus-only in 0.9.2. Unknown
    families are ignored at this compatibility boundary, but malformed or
    skipped samples are counted and exposed as ``malformed_samples`` so the
    benchmark validation can fail closed when a metrics document silently
    drops evidence.
    """
    families, malformed_count = _parse_prometheus_families(content)
    streaming_attempts = _prometheus_total(
        families, "nginx_markdown_conversion_attempts_total", engine="streaming"
    )
    full_buffer_attempts = _prometheus_total(
        families, "nginx_markdown_conversion_attempts_total", engine="full_buffer"
    )
    return {
        "streaming_path_hits": streaming_attempts,
        "fullbuffer_path_hits": full_buffer_attempts,
        "malformed_samples": malformed_count,
        "streaming": {
            "requests_total": streaming_attempts,
            "fallback_total": _prometheus_total(
                families, "nginx_markdown_streaming_events_total", transition="fallback"
            ),
        },
        "perf": {
            "decompression_events_total": _prometheus_total(
                families, "nginx_markdown_decompression_events_total"
            ),
            "decompression_success_total": _prometheus_total(
                families,
                "nginx_markdown_decompression_events_total",
                outcome="success",
            ),
            "decompression_budget_exceeded_total": _prometheus_total(
                families,
                "nginx_markdown_decompression_events_total",
                outcome="failure",
                reason="budget_exceeded",
            ),
        },
    }


def merge_diagnostics_metrics(
    metrics: dict[str, Any], diagnostics: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge exact internal counters from the diagnostics contract.

    The frozen Prometheus v1 endpoint intentionally exposes engine delivery
    counters, not output ownership or streaming pre-commit fail-open
    counters. Those counters are collected from the structured diagnostics
    endpoint instead of being inferred from unrelated labels.
    """
    runtime = diagnostics.get("runtime")
    if not isinstance(runtime, Mapping):
        return metrics
    module_metrics = runtime.get("module_metrics")
    if not isinstance(module_metrics, Mapping):
        return metrics

    streaming = metrics.setdefault("streaming", {})
    perf = metrics.setdefault("perf", {})
    field_map = {
        "streaming_requests_total": (streaming, "requests_total"),
        "precommit_failopen_total": (streaming, "precommit_failopen_total"),
        "copied_output_total": (perf, "copied_output_total"),
    }
    for source_key, (target, target_key) in field_map.items():
        if source_key in module_metrics:
            value = module_metrics[source_key]
            if not _is_numeric_count(value):
                raise ValueError(
                    f"diagnostic counter {source_key} is not a finite "
                    f"non-negative count: {value!r}"
                )
            target[target_key] = value
    return metrics


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
    normalized_headers = normalize_header_mapping(headers)
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
    # Brotli streams have no fixed magic number, but the Brotli window byte
    # starts with WBITS in the low 4 bits (1-24) and the stream typically
    # begins with a metablock header.  A heuristic: if the first byte has
    # bits [7:4] == 0 and the body does NOT look like valid UTF-8 Markdown,
    # it might be Brotli.  However, this is unreliable.  Instead, we rely on
    # content_encoding header detection above for Brotli — this function is
    # a secondary guard for gzip/zlib only.
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
    for name in ("gzip-streaming-first", "deflate-streaming-first", "brotli-streaming-first"):
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
    metrics_exit_code: int = 0
    diagnostics_exit_code: int = 0


def _load_result(data: ScenarioResultInput) -> tuple[dict, float, float, float, float]:
    """Parse the selected load generator and return performance fields."""
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
    return load, rps, p50, p95, p99


def _is_numeric_count(value) -> bool:
    """A finite, non-negative counter; int or float, never bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        finite = math.isfinite(value)
    except OverflowError:
        # An oversized Python int overflows float conversion.
        return False
    return finite and value >= 0


def _path_metrics(
    nginx_metrics: Mapping[str, Any],
) -> tuple[
    tuple[dict[str, Any], dict[str, Any], float, float, float | None, float | None],
    int | float | None,
    int | float | None,
    float | None,
    int | float,
]:
    """Derive path ratios and streaming counters from the metrics adapter."""
    perf = nginx_metrics.get("perf", {}) or {}
    streaming = nginx_metrics.get("streaming", {}) or {}
    streaming_hits = _normalise_path_hits(
        nginx_metrics.get("streaming_path_hits")
    )
    fullbuffer_hits = _normalise_path_hits(
        nginx_metrics.get("fullbuffer_path_hits")
    )
    total_hits = streaming_hits + fullbuffer_hits
    streaming_ratio = streaming_hits / total_hits if total_hits > 0 else None
    fullbuffer_ratio = fullbuffer_hits / total_hits if total_hits > 0 else None
    requests_total = streaming.get("requests_total")
    streaming_fallback_total = streaming.get("fallback_total")
    streaming_failopen_total = streaming.get("precommit_failopen_total")

    if _is_numeric_count(requests_total) and _is_numeric_count(
        streaming_failopen_total
    ):
        # fallback_rate is the hard fail-open share:
        # precommit_failopen_total / streaming_requests_total (1.0 when every
        # streaming request fails open). Capability fallbacks are reported
        # separately and must not affect the release gate.
        assert requests_total is not None and streaming_failopen_total is not None
        if requests_total > 0:
            if streaming_failopen_total > requests_total:
                # Inconsistent counters: more fail-open events than requests
                # is missing/duplicated evidence, not a rate above 1.0.
                fallback_rate = None
            else:
                fallback_rate = streaming_failopen_total / requests_total
        elif streaming_failopen_total == 0:
            fallback_rate = 0.0
        else:
            fallback_rate = None
    else:
        fallback_rate = None
    return (
        perf,
        streaming,
        streaming_hits,
        fullbuffer_hits,
        streaming_ratio,
        fullbuffer_ratio,
    ), requests_total, streaming_fallback_total, fallback_rate, total_hits


def _normalise_path_hits(value: object) -> int | float:
    """Return a non-negative finite numeric path count, treating invalid or
    non-finite input (NaN, infinity) as 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return value if value >= 0 and math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0
        return parsed if parsed >= 0 and math.isfinite(parsed) else 0
    return 0


def _decompression_path_metrics(
    compression: str,
    perf: Mapping[str, Any],
    streaming_hits: float,
    fullbuffer_hits: float,
) -> tuple[float | None, float | None]:
    """Map decompression events to the benchmark's path fields."""
    # The aggregate counter also carries outcome="failure" samples; only
    # successful decompressions may populate the path totals. Fall back to
    # the aggregate when the success-labeled total is unavailable.
    events = perf.get(
        "decompression_success_total", perf.get("decompression_events_total", 0)
    )
    if compression != "none" and events:
        if streaming_hits == 0 and fullbuffer_hits == 0:
            return None, None
        if streaming_hits == 0:
            return 0, events
        if fullbuffer_hits == 0:
            return events, 0
        # The aggregate counter cannot be attributed safely when both paths
        # ran. Preserve the ambiguity as missing evidence instead of assigning
        # every event to one path.
        return None, None
    return (
        perf.get("decompression_streaming_total", 0),
        perf.get("decompression_fullbuffer_total", 0),
    )


def _scenario_metrics(
    data: ScenarioResultInput,
    performance: tuple[float, float, float, float],
    path,
) -> dict[str, Any]:
    """Assemble the stable metric payload for one scenario."""
    rps, p50, p95, p99 = performance
    (perf, streaming, streaming_hits, fullbuffer_hits, streaming_ratio,
     fullbuffer_ratio), requests_total, _, fallback_rate, _ = path
    decomp_streaming, decomp_fullbuffer = _decompression_path_metrics(
        data.compression, perf, streaming_hits, fullbuffer_hits
    )
    result = {
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
        # The harness reports bytes per request and requests per second.  Keep
        # the derived value tied to those measured fields instead of a
        # placeholder so baseline evidence remains numerically meaningful.
        # A zero rps is missing evidence, not a zero measurement.
        "throughput_mbytes_per_sec": (
            round(data.input_bytes * rps / 1_000_000.0, 6) if rps > 0.0 else None
        ),
        "decompression_streaming_total": decomp_streaming,
        "decompression_fullbuffer_total": decomp_fullbuffer,
        "pending_output_high_watermark_bytes": perf.get(
            "pending_output_high_watermark_bytes", 0
        ),
    }
    optional_fields = (
        ("precommit_failopen_total", streaming),
        ("copied_output_total", perf),
    )
    for field, source in optional_fields:
        if field in source:
            result[field] = source[field]
    return result


def build_scenario_result(data: ScenarioResultInput) -> dict:
    """Build a scenario report gated by load and endpoint integrity evidence."""
    load, rps, p50, p95, p99 = _load_result(data)
    path = _path_metrics(data.nginx_metrics)
    endpoint_failures = []
    if data.metrics_exit_code != 0:
        endpoint_failures.append(f"metrics_curl_exit: {data.metrics_exit_code}")
    if data.diagnostics_exit_code != 0:
        endpoint_failures.append(
            f"diagnostics_curl_exit: {data.diagnostics_exit_code}"
        )
    # Malformed or skipped Prometheus samples silently drop evidence: a
    # metrics document carrying unparseable lines must fail the scenario's
    # endpoint integrity instead of being treated as a clean snapshot.
    malformed_samples = data.nginx_metrics.get("malformed_samples", 0)
    if malformed_samples:
        endpoint_failures.append(
            f"metrics_malformed_samples: {malformed_samples}"
        )

    result = {
        "name": data.name,
        "scenario_config": _scenario_config_for_report(data.profile),
        "compression": data.compression,
        "transfer_encoding": data.transfer_encoding,
        "concurrency": data.concurrency,
        "status": (
            "completed"
            if load.get("verdict") == "pass" and not endpoint_failures
            else "failed"
        ),
        "load_integrity": load,
        "endpoint_integrity": {
            "metrics_curl_exit_code": data.metrics_exit_code,
            "diagnostics_curl_exit_code": data.diagnostics_exit_code,
            "verdict": "pass" if not endpoint_failures else "fail",
            "failure_reason": "; ".join(endpoint_failures),
        },
        "metrics": _scenario_metrics(data, (rps, p50, p95, p99), path),
    }
    if result["status"] == "failed":
        reasons = []
        if load.get("verdict") != "pass":
            reasons.append(
                "load_integrity_failed: "
                + str(load.get("failure_reason", "no failure reason recorded"))
            )
        reasons.extend(
            f"endpoint_integrity_failed: {failure}"
            for failure in endpoint_failures
        )
        result["reason"] = "; ".join(reasons)
    return result
