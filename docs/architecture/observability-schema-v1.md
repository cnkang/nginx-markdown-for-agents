# Observability Schema v1 (DRAFT)

**Status**: DRAFT (0.9.1) — will be frozen at 1.0.0
**Single Source of Truth**: `components/rust-converter/src/decision/reason_code.rs`

---

## Overview

The observability schema v1 defines the contract for all machine-readable
observability surfaces in `nginx-markdown-for-agents` 0.9.0+:

- **Reason codes** — 26 canonical decision outcomes
- **Metric families** — 5 unified Prometheus counter families with `reason` label
- **Label whitelist** — 4 allowed metric labels (bounded cardinality)
- **Diagnostics schema** — JSON v1 structure for the `/nginx-markdown/diagnostics` endpoint
- **Response headers** — `X-Markdown-Tokens` contract

After the 1.0.0 freeze, only **additive changes** are permitted: new reason
codes, new metric families, new optional diagnostics fields. No renames,
no removals.

---

## Reason Code Registry

All 26 reason codes are defined in a single Rust enum (`ReasonCode`, `#[repr(u8)]`).
C code accesses them via FFI. The `as_str()` values are lowercase snake_case.

| # | Variant | `as_str()` | `metric_key()` | `log_callsite()` |
|--:|---------|-----------|----------------|-------------------|
| 0 | `Converted` | `converted` | `nginx_markdown_conversions_total` | body_filter: after successful conversion and downstream NGX_OK |
| 1 | `SkippedAccept` | `skipped_accept` | `nginx_markdown_skipped_total` | header_filter: Accept negotiation determined text/html preferred |
| 2 | `SkippedNoAccept` | `skipped_no_accept` | `nginx_markdown_skipped_total` | header_filter: no Accept header present |
| 3 | `SkippedConditional` | `skipped_conditional` | `nginx_markdown_skipped_total` | header_filter: conditional request matched (304) |
| 4 | `DecompressionError` | `decompression_error` | `nginx_markdown_errors_total` | body_filter: decompression error |
| 5 | `DecompressionBudgetExceeded` | `decompression_budget_exceeded` | `nginx_markdown_errors_total` | body_filter: decompression output exceeded budget |
| 6 | `DecompressionFormatError` | `decompression_format_error` | `nginx_markdown_errors_total` | body_filter: invalid compression format |
| 7 | `DecompressionTruncatedInput` | `decompression_truncated_input` | `nginx_markdown_errors_total` | body_filter: truncated compressed input |
| 8 | `DecompressionIoError` | `decompression_io_error` | `nginx_markdown_errors_total` | body_filter: decompression I/O error |
| 9 | `Timeout` | `timeout` | `nginx_markdown_errors_total` | body_filter: timeout |
| 10 | `BudgetExceeded` | `budget_exceeded` | `nginx_markdown_errors_total` | body_filter: budget exceeded |
| 11 | `ReplayError` | `replay_error` | `nginx_markdown_errors_total` | body_filter: replay error |
| 12 | `SkippedAcceptReject` | `skipped_accept_reject` | `nginx_markdown_skipped_total` | header_filter: Accept explicitly rejects text/markdown (q=0) |
| 13 | `FfiPanic` | `ffi_panic` | `nginx_markdown_errors_total` | body_filter: FFI panic |
| 14 | `NotEligible` | `not_eligible` | `nginx_markdown_skipped_total` | header_filter: response not eligible (method/status/content-type) |
| 15 | `Disabled` | `disabled` | `nginx_markdown_skipped_total` | header_filter: module disabled for this location |
| 16 | `FailedOpen` | `failed_open` | `nginx_markdown_failed_open_total` | body_filter: fail-open path triggered |
| 17 | `FailedClosed` | `failed_closed` | `nginx_markdown_failed_closed_total` | body_filter: fail-closed path triggered |
| 18 | `ConversionError` | `conversion_error` | `nginx_markdown_errors_total` | body_filter: conversion error |
| 19 | `MemoryBudgetExceeded` | `memory_budget_exceeded` | `nginx_markdown_errors_total` | body_filter: memory budget exceeded |
| 20 | `Overload` | `overload` | `nginx_markdown_errors_total` | header_filter: inflight guard overload |
| 21 | `InvalidDynconf` | `invalid_dynconf` | `nginx_markdown_errors_total` | header_filter: invalid dynconf |
| 22 | `DegradedSnapshot` | `degraded_snapshot` | `nginx_markdown_errors_total` | header_filter: degraded dynconf snapshot |
| 23 | `HeaderPlanApplyError` | `header_plan_apply_error` | `nginx_markdown_errors_total` | header_filter: header plan apply error |
| 24 | `StreamingMidFlightError` | `streaming_mid_flight_error` | `nginx_markdown_errors_total` | body_filter: streaming mid-flight error |
| 25 | `BypassNoTransform` | `bypass_no_transform` | `nginx_markdown_skipped_total` | header_filter: no-transform bypass |

### Naming Convention

- All `as_str()` values are **lowercase snake_case** (regex: `^[a-z][a-z0-9_]*$`).
- Discriminants are contiguous `[0, 26)` with no gaps.
- The enum uses `#[repr(u8)]` for FFI safety.

### FFI Accessors

| Function | Purpose |
|----------|---------|
| `markdown_reason_code_str(code, out_len)` | Get `as_str()` by discriminant |
| `markdown_reason_code_metric_key(code, out_len)` | Get `metric_key()` by discriminant |
| `markdown_reason_code_count()` | Total reason code count (26) |

---

## Metrics Label Whitelist

Only 4 label keys are permitted in Prometheus metrics output. This whitelist
prevents high-cardinality label explosion.

### Allowed Labels

| Label Key | Source | Example Values |
|-----------|--------|----------------|
| `reason` | `ReasonCode::as_str()` | `converted`, `timeout`, `failed_open` |
| `profile` | Active profile name | `balanced`, `strict_cache`, `streaming_first` |
| `path_mode` | Processing path | `full_buffer`, `streaming` |
| `cache_validation` | Cache validation setting | `off`, `ims_only`, `full` |

### Blocked Labels (High-Cardinality)

The following labels are **explicitly forbidden** and will be rejected:

`url`, `path`, `uri`, `host`, `ip`, `client_ip`, `remote_addr`,
`user_agent`, `ua`, `request_id`, `trace_id`, `session_id`

### Label Value Normalization

All label values are normalized to lowercase snake_case:
1. Replace hyphens and spaces with underscores
2. Convert to lowercase
3. Remove non-alphanumeric/non-underscore characters
4. Collapse consecutive underscores
5. Trim leading/trailing underscores

Implementation: `components/rust-converter/src/metrics/labels.rs`

---

## Unified Metric Families

0.9.1 consolidates per-reason counters into 5 unified families. Each family
uses the `reason` label to distinguish individual reason codes. All metrics are prefixed with `nginx_markdown_`.

| Metric Family | Category | Reason Codes (by `reason` label) |
|---------------|----------|----------------------------------|
| `nginx_markdown_conversions_total` | Success | `converted` |
| `nginx_markdown_skipped_total` | Skip | `skipped_accept`, `skipped_no_accept`, `skipped_conditional`, `skipped_accept_reject`, `not_eligible`, `disabled`, `bypass_no_transform` |
| `nginx_markdown_errors_total` | Error | `decompression_error`, `decompression_budget_exceeded`, `decompression_format_error`, `decompression_truncated_input`, `decompression_io_error`, `timeout`, `budget_exceeded`, `replay_error`, `ffi_panic`, `conversion_error`, `memory_budget_exceeded`, `overload`, `invalid_dynconf`, `degraded_snapshot`, `header_plan_apply_error`, `streaming_mid_flight_error` |
| `nginx_markdown_failed_open_total` | Fail-Open | `failed_open` |
| `nginx_markdown_failed_closed_total` | Fail-Closed | `failed_closed` |

### New 0.9.1 Specialized Metrics

In addition to the reason-based families, 0.9.1 introduces specific performance and resource metrics:

- `nginx_markdown_backpressure_total`: Total backpressure events encountered.
- `nginx_markdown_backpressure_resume_total`: Total resumes after backpressure.
- `nginx_markdown_pending_output_high_watermark_bytes`: Peak buffered output size.
- `nginx_markdown_decompression_streaming_total`: Number of responses decompressed via the streaming path.
- `nginx_markdown_decompression_fullbuffer_total`: Number of responses decompressed via the full-buffer path.
- `nginx_markdown_perf_decompression_budget_exceeded_total`: Total decompression budget violations.
- `nginx_markdown_zero_copy_output_total`: Total bytes delivered via zero-copy path.
- `nginx_markdown_copied_output_total`: Total bytes delivered via copy path.

### Prometheus Example

```text
# TYPE nginx_markdown_conversions_total counter
nginx_markdown_conversions_total{reason="converted"} 12450

# TYPE nginx_markdown_skipped_total counter
nginx_markdown_skipped_total{reason="skipped_accept"} 340
nginx_markdown_skipped_total{reason="not_eligible"} 28
nginx_markdown_skipped_total{reason="disabled"} 5

# TYPE nginx_markdown_errors_total counter
nginx_markdown_errors_total{reason="timeout"} 3
nginx_markdown_errors_total{reason="decompression_error"} 1

# TYPE nginx_markdown_failed_open_total counter
nginx_markdown_failed_open_total{reason="failed_open"} 7

# TYPE nginx_markdown_failed_closed_total counter
nginx_markdown_failed_closed_total{reason="failed_closed"} 0
```

---

## Diagnostics Schema v1

The `/nginx-markdown/diagnostics` endpoint returns a JSON object conforming
to this schema. The `schema_version` field is always `1`.

### Top-Level Structure

```json
{
  "schema_version": 1,
  "decision": { ... },
  "inflight": { ... },
  "error": { ... },
  "streaming": { ... },
  "conditional": { ... },
  "etag": { ... }
}
```

### Section: `decision`

| Field | Type | Description |
|-------|------|-------------|
| `last_reason` | string | Last reason code (`ReasonCode::as_str()`) |
| `profile` | string | Active profile name |
| `path_mode` | string | Processing path (full_buffer / streaming) |
| `cache_validation` | string | Cache validation mode (off / ims_only / full) |
| `total_decisions` | u64 | Total decisions made |

### Section: `inflight`

| Field | Type | Description |
|-------|------|-------------|
| `current` | u32 | Current inflight count |
| `max` | u32 | Configured maximum |
| `high_watermark` | u32 | Peak since last reset |
| `overload_total` | u64 | Total overload rejections |

### Section: `error`

| Field | Type | Description |
|-------|------|-------------|
| `total` | u64 | Total errors |
| `failed_open_total` | u64 | Total failed-open deliveries |
| `failed_closed_total` | u64 | Total failed-closed rejections |
| `last_error_reason` | string | Last error reason code string |

### Section: `streaming`

| Field | Type | Description |
|-------|------|-------------|
| `eligible` | bool | Whether streaming is eligible |
| `block_reason` | string | If blocked, the reason |
| `streaming_total` | u64 | Total streaming conversions |
| `fallback_total` | u64 | Total streaming fallbacks |

### Section: `conditional`

| Field | Type | Description |
|-------|------|-------------|
| `evaluated_header` | string | Last evaluated conditional header type |
| `result` | string | Last result (not_modified / proceed / skipped) |
| `cache_validation_mode` | string | Active cache validation mode |

### Section: `etag`

| Field | Type | Description |
|-------|------|-------------|
| `policy` | string | ETag generation policy (off / weak / strong) |
| `generated` | bool | Whether ETag was generated for last conversion |
| `reason` | string | If not generated, the reason |

Implementation: `components/rust-converter/src/diagnostics/schema.rs`

---

## Response Headers

### `X-Markdown-Tokens`

| Header | Condition | Value |
|--------|-----------|-------|
| `X-Markdown-Tokens` | `markdown_token_estimate on` | Estimated token count (integer) |

This header provides a directional estimate of the token count in the Markdown
output, enabling AI agent clients to make informed decisions about content
processing. The estimate uses a byte-ratio heuristic; it is not a precise
tokenizer count.

---

## Stability Contract

### Before 1.0.0 (Current — DRAFT)

Breaking changes are permitted. 0.9.0 is the last breaking opportunity.

### After 1.0.0 (Frozen)

- **Additive only**: new reason codes, new metric families, new optional
  diagnostics fields.
- **No renames**: existing `as_str()` values, metric keys, and JSON field
  names are permanent.
- **No removals**: existing reason codes, metrics, and diagnostics sections
  are permanent.
- **No reordering**: discriminant assignments are permanent.
- **Label whitelist expansion**: new labels may be added to the whitelist;
  existing labels are never removed.

### Versioning

The `schema_version` field in diagnostics output tracks the schema version.
Clients should check this field and handle unknown fields gracefully.

---

## Related Documents

- [Prometheus Metrics Guide](../guides/prometheus-metrics.md) — scrape config, PromQL examples
- [Migration Guide: 0.9.0](../guides/MIGRATION-0.9.md) — 0.8.x → 0.9.0 migration
- [Streaming Observability](../features/streaming-observability.md) — streaming-specific metrics
- [ADR-0018](ADR/0018-090-observability-schema-v1-reason-registry.md) — design rationale
