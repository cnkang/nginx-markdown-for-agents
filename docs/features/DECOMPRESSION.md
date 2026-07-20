# Decompression Budget and Error Handling (v0.9.1)

This document describes the bounded decompression and error classification
features introduced in v0.7.0. For the base automatic decompression behavior,
see [AUTOMATIC_DECOMPRESSION.md](AUTOMATIC_DECOMPRESSION.md).

## Bounded Decompression

The `markdown_decompress_max_size` directive limits decompressed output size
independently from `markdown_limits memory=<size>`, preventing zip-bomb attacks and
unbounded memory growth.

### Configuration

| Directive | Syntax | Default | Context |
|-----------|--------|---------|---------|
| `markdown_decompress_max_size` | `markdown_decompress_max_size <size>;` | Inherits `markdown_limits memory=<size>` | http, server, location |

### Example

```nginx
location /api/ {
    markdown_filter on;
    markdown_limits memory=10m;
    markdown_decompress_max_size 50m;  # Allow high compression ratios
}
```

---

## Decompression Error Categories

v0.7.0 introduces fine-grained decompression error classification. Each error
category has a unique FFI error code, a dedicated Prometheus counter, and a
specific reason code for structured logging and diagnostics.

### Category Reference

| Category | Reason Code String | Rust Enum Variant | Reason Code Value | Meaning |
|----------|-------------------|-------------------|-------------------|---------|
| Budget Exceeded | `decompression_budget_exceeded` | `ReasonCode::DecompressionBudgetExceeded` | 5 | Decompressed output exceeded the configured `markdown_decompress_max_size` budget. Indicates potential zip bomb or unexpectedly large compressed content. |
| Format Error | `decompression_format_error` | `ReasonCode::DecompressionFormatError` | 6 | The compressed input has an invalid format (not valid gzip/deflate/brotli). Indicates corrupted or misidentified content. |
| Truncated Input | `decompression_truncated_input` | `ReasonCode::DecompressionTruncatedInput` | 7 | The compressed input was truncated (incomplete stream). Indicates network issues or upstream sending partial content. |
| I/O Error | `decompression_io_error` | `ReasonCode::DecompressionIoError` | 8 | An I/O error occurred during decompression. Indicates system-level issues (disk, memory mapping, etc.). |

There is also a generic `failed_decompression` (reason code value 4) used when
the error does not map to a specific category above.

---

## FFI Error Code Mapping

The Rust converter communicates decompression results to the C module through
FFI error codes defined in `markdown_converter.h`. The decompression-specific
code is:

| FFI Error Code | C Define | Value | Rust Constant | Description |
|----------------|----------|-------|---------------|-------------|
| Budget Exceeded | `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` | 9 | `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` | Decompressed output exceeds `markdown_decompress_max_size` |

The C module classifies FFI code 9 as `NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT`,
which increments the `failures_resource_limit` counter in addition to the
category-specific counter.

For format errors, truncated input, and I/O errors that occur during
decompression, the C module receives the error through the reason code system
(values 5–8 in the `ReasonCode` enum) rather than through a separate FFI error
code. The reason code is set by the Rust decision engine and propagated to
metrics and logging.

### Reason Code to Metric Mapping

| Reason Code Value | Reason Code String | Prometheus Metric |
|-------------------|-------------------|-------------------|
| 5 | `decompression_budget_exceeded` | `nginx_markdown_perf_decompression_budget_exceeded_total` |
| 6 | `decompression_format_error` | `nginx_markdown_decompression_format_error_total` |
| 7 | `decompression_truncated_input` | `nginx_markdown_decompression_truncated_input_total` |
| 8 | `decompression_io_error` | `nginx_markdown_decompression_io_error_total` |

---

## Prometheus Metrics

Each decompression error category increments its own dedicated Prometheus
counter. This enables operators to distinguish between different failure modes
and respond appropriately.

| Metric Name | Type | Description |
|-------------|------|-------------|
| `nginx_markdown_perf_decompression_budget_exceeded_total` | counter | Number of decompression operations terminated because output exceeded the configured budget. |
| `nginx_markdown_decompression_format_error_total` | counter | Number of decompression operations that failed due to invalid compressed format. |
| `nginx_markdown_decompression_truncated_input_total` | counter | Number of decompression operations that failed due to truncated/incomplete input. |
| `nginx_markdown_decompression_io_error_total` | counter | Number of decompression operations that failed due to I/O errors. |
| `nginx_markdown_decompression_failures_total` | counter | Total failed decompression attempts (aggregate, all categories). |

In addition, the broad failure classification counter
`nginx_markdown_failures_total{reason="resource_limit"}` is incremented for
budget-exceeded errors (FFI code 9).

### PromQL Examples

```promql
# Rate of budget-exceeded errors (potential zip bomb attempts)
rate(nginx_markdown_decompression_budget_exceeded_total[5m])

# Total decompression error rate across all categories
sum(rate(nginx_markdown_decompression_format_error_total[5m]))
+ sum(rate(nginx_markdown_decompression_truncated_input_total[5m]))
+ sum(rate(nginx_markdown_decompression_io_error_total[5m]))
+ sum(rate(nginx_markdown_decompression_budget_exceeded_total[5m]))

# Ratio of truncated inputs to total decompressions (network health indicator)
rate(nginx_markdown_decompression_truncated_input_total[5m])
/ rate(nginx_markdown_decompressions_total[5m])
```

---

## Fail-Open Behavior

When any decompression error occurs, the module follows the fail-open strategy
(unless `markdown_error_policy fail_closed` is configured for fail-closed):

| Error Category | Fail-Open Behavior | Fail-Closed Behavior |
|----------------|-------------------|---------------------|
| Budget Exceeded | Pass-through original compressed/uncompressed response to client | Return 502 Bad Gateway |
| Format Error | Pass-through original response to client | Return 502 Bad Gateway |
| Truncated Input | Pass-through original response to client | Return 502 Bad Gateway |
| I/O Error | Pass-through original response to client | Return 502 Bad Gateway |

### Fail-Open Sequence

1. Decompression error detected by Rust converter
2. Error category and reason code set on the result
3. C module logs the error with full category information
4. Category-specific Prometheus counter incremented
5. `failures_resource_limit` counter incremented (for budget exceeded)
6. Decision counter incremented (delivery counter is NOT incremented)
7. Original upstream response passed through to client unchanged
8. `failopen_completed` flag set to prevent duplicate finalization

The client receives the original upstream response (typically gzip/deflate/brotli
compressed HTML) without any Markdown conversion applied.

---

## Structured Logging

When a decompression error occurs, the module emits a structured log entry at
`WARN` level containing:

```
markdown filter: conversion failed, error_code=9, category=RESOURCE_LIMIT,
message="decompression budget exceeded: output 52428800 > budget 10485760",
elapsed_ms=12
```

Key fields in the log entry:

| Field | Description |
|-------|-------------|
| `error_code` | The FFI error code (e.g., 9 for budget exceeded) |
| `category` | The broad error classification (`resource_limit`, `conversion`, `system`) |
| `message` | Detailed error message from the Rust converter |
| `elapsed_ms` | Time spent before the error was detected |

The reason code is also available through the diagnostics endpoint
(`/nginx-markdown/diagnostics`) in the `recent_decisions` array.

---

## Operator Guidance

### Budget Exceeded (`decompression_budget_exceeded`)

**What it means**: The decompressed output would exceed the configured
`markdown_decompress_max_size` limit. This is a safety mechanism against zip
bombs and unexpectedly large compressed payloads.

**Common causes**:
- Upstream serving highly compressed content (compression ratio > 20:1)
- Zip bomb attack attempt
- Legitimate large documents compressed with high efficiency

**How to respond**:
1. Check if the budget is appropriately sized for your workload:
   ```nginx
   markdown_decompress_max_size 50m;  # Increase if legitimate content is large
   ```
2. If the rate is low and sporadic, it may be legitimate large content — consider
   increasing the budget.
3. If the rate is high or sudden, investigate for potential zip bomb attacks.
   Check source IPs and request patterns.
4. Monitor `nginx_markdown_decompression_budget_exceeded_total` for trends.

### Format Error (`decompression_format_error`)

**What it means**: The compressed input does not conform to the expected
compression format (gzip, deflate, or brotli). The data is corrupted or the
`Content-Encoding` header is incorrect.

**Common causes**:
- Upstream misconfigured `Content-Encoding` header (claims gzip but sends plain)
- Data corruption in transit (proxy/CDN issue)
- Upstream bug producing malformed compressed output

**How to respond**:
1. Check the upstream `Content-Encoding` header matches actual encoding.
2. Verify upstream health and compression configuration.
3. If a specific upstream consistently produces format errors, investigate its
   compression pipeline.
4. Consider adding the upstream to a bypass list if it cannot be fixed.

### Truncated Input (`decompression_truncated_input`)

**What it means**: The compressed stream ended prematurely — the decompressor
expected more data but reached end-of-input. The compressed payload is
incomplete.

**Common causes**:
- Network interruption between upstream and NGINX
- Upstream timeout or crash mid-response
- Proxy/CDN truncating the response body
- `Content-Length` mismatch (header says more bytes than actually sent)

**How to respond**:
1. Check upstream connectivity and health.
2. Look for correlated network errors or upstream timeouts.
3. Verify proxy/CDN configuration between upstream and NGINX.
4. If persistent, check for `Content-Length` mismatches in upstream responses.
5. Monitor `nginx_markdown_decompression_truncated_input_total` alongside
   upstream error rates.

### I/O Error (`decompression_io_error`)

**What it means**: A system-level I/O error occurred during the decompression
operation. This is typically not related to the compressed data itself but to
the runtime environment.

**Common causes**:
- Memory pressure on the worker process
- System resource exhaustion (file descriptors, memory mapping limits)
- Rare internal library errors in the decompression implementation

**How to respond**:
1. Check system resource utilization (memory, file descriptors).
2. Review NGINX worker process health and resource limits.
3. If persistent, check for memory leaks or resource exhaustion patterns.
4. Consider increasing worker process resource limits if the system is
   under-provisioned.

---

## Error Code Classification (Broad Categories)

FFI error codes are classified into three broad categories for aggregate metrics
and logging:

| Category | Meaning | FFI Codes |
|----------|---------|-----------|
| `conversion` | HTML parse/conversion error | 1 (PARSE), 2 (ENCODING), 5 (INVALID_INPUT) |
| `resource_limit` | Timeout, memory, or budget exceeded | 3 (TIMEOUT), 4 (MEMORY_LIMIT), 6 (BUDGET_EXCEEDED), 9 (DECOMPRESSION_BUDGET_EXCEEDED), 10 (PARSE_TIMEOUT), 11 (PARSE_BUDGET_EXCEEDED) |
| `system` | Internal/system error | 99 (INTERNAL) |

Streaming-specific codes (7=FALLBACK, 8=POST_COMMIT) are feature-gated.

---

## Related Documentation

- [AUTOMATIC_DECOMPRESSION.md](AUTOMATIC_DECOMPRESSION.md) — Base decompression behavior
- [DECISION_CHAIN.md](DECISION_CHAIN.md) — Full decision chain and reason codes
- [../guides/CONFIGURATION.md](../guides/CONFIGURATION.md) — Configuration directives
- [../guides/prometheus-metrics.md](../guides/prometheus-metrics.md) — Full metrics reference

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.7.0 | 2026-05-18 | Kang | Added detailed decompression error categories, FFI mapping, per-category Prometheus metrics, fail-open behavior, and operator guidance (TASK-A04.4) |
| 0.7.0 | 2026-05-17 | Kang | Initial document for v0.7.0 decompression budget and error classification |
