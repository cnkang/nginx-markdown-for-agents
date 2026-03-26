# Prometheus Metrics Guide

## Table of Contents

1. [Overview](#overview)
2. [Enabling Prometheus Format](#enabling-prometheus-format)
3. [Scrape Configuration](#scrape-configuration)
4. [Metric Catalog](#metric-catalog)
5. [Verifying the Endpoint](#verifying-the-endpoint)
6. [Prometheus Labels and Decision Log Reason Codes](#prometheus-labels-and-decision-log-reason-codes)
7. [Interpreting Metrics for Rollout Judgment](#interpreting-metrics-for-rollout-judgment)
8. [Token Savings Estimate](#token-savings-estimate)
9. [Metric Stability Policy](#metric-stability-policy)

---

## Overview

The NGINX Markdown filter module exposes module-level operational metrics via the existing `markdown_metrics` endpoint. Starting with version 0.4.0, the endpoint supports Prometheus text exposition format (`text/plain; version=0.0.4; charset=utf-8`) as an opt-in output format alongside the existing JSON and plain-text formats.

These metrics help operators answer three questions:

1. **Is the module working?** — Conversion success rate, failure breakdown, skip reasons.
2. **How effective is it?** — Byte reduction ratio, estimated token savings, conversion throughput.
3. **Is it safe to continue rollout?** — Fail-open rate, error trends, latency distribution.

Only module-internal semantics that generic NGINX exporters (such as `nginx-prometheus-exporter`) cannot recover are exposed. This is not a replacement for general NGINX monitoring.

### Target Audience

- Site Reliability Engineers (SREs)
- DevOps Engineers
- System Administrators operating the NGINX Markdown filter module

### Related Documents

- [CONFIGURATION.md](CONFIGURATION.md) — Full directive reference
- [OPERATIONS.md](OPERATIONS.md) — General operational guide and metrics reference
- [ROLLOUT_COOKBOOK.md](ROLLOUT_COOKBOOK.md) — Rollout planning and phased deployment

---

## Enabling Prometheus Format

Prometheus text exposition format is off by default. To enable it, add the `markdown_metrics_format` directive to the location block that serves the metrics endpoint.

### Configuration

```nginx
location /markdown-metrics {
    markdown_metrics;
    markdown_metrics_format prometheus;

    # Restrict access to localhost only
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

### Directive Reference

| Directive | Values | Default | Context |
|---|---|---|---|
| `markdown_metrics_format` | `auto` \| `prometheus` | `auto` | http, server, location |

- `auto` — Existing behavior. JSON for `Accept: application/json`, plain text otherwise. Backward compatible with 0.3.0.
- `prometheus` — Prometheus text exposition format for non-JSON requests. `Accept: application/json` still returns JSON.

### Content Negotiation Behavior

When `markdown_metrics_format prometheus` is configured:

| Accept Header | Output Format |
|---|---|
| `application/json` | JSON |
| `text/plain; version=0.0.4` | Prometheus |
| `application/openmetrics-text` | Prometheus |
| `text/plain` | Plain text (backward compatible) |
| (any other / none) | Plain text (backward compatible) |

When `markdown_metrics_format auto` (default):

| Accept Header | Output Format |
|---|---|
| `application/json` | JSON |
| `text/plain` | Plain text (existing human-readable format) |
| (any other / none) | Plain text (existing human-readable format) |

Prometheus format is served only when the client explicitly requests it via `Accept: text/plain; version=0.0.4` or `Accept: application/openmetrics-text`. Plain `text/plain` without the version parameter falls back to the legacy human-readable format, preserving backward compatibility for operators who curl without a specific Accept header. No external dependencies, sidecars, or agents are required.

---

## Scrape Configuration

### Prometheus `prometheus.yml` Example

```yaml
scrape_configs:
  - job_name: 'nginx-markdown'
    scrape_interval: 15s
    metrics_path: '/markdown-metrics'
    static_configs:
      - targets: ['localhost:80']
    # Prometheus sends Accept: application/openmetrics-text by default,
    # which the module recognizes when markdown_metrics_format is prometheus.
```

If NGINX listens on a non-standard port or the metrics location uses a different path, adjust `targets` and `metrics_path` accordingly.

### Access Control

The metrics endpoint enforces localhost-only access by default. Prometheus must scrape from the same host (or via a local proxy).

**Security recommendations:**

- Expose the metrics endpoint only on internal networks.
- Use NGINX `allow`/`deny` directives for additional access control.
- Do not expose the metrics endpoint on the public internet.

---

## Metric Catalog

All metrics use the `nginx_markdown_` prefix. Counter metrics use the `_total` suffix. Byte-valued counters use `_bytes_total`.

### Counter Metrics (no labels)

| Metric Name | Type | Description |
|---|---|---|
| `nginx_markdown_requests_total` | counter | Total requests entering the module decision chain. This is the denominator for conversion rate calculations. |
| `nginx_markdown_conversions_total` | counter | Successful HTML-to-Markdown conversions. |
| `nginx_markdown_passthrough_total` | counter | Requests not converted (skipped or failed-open). Derived as the sum of all skip-reason counters plus fail-open count. |
| `nginx_markdown_failopen_total` | counter | Conversions failed with original HTML served (`markdown_on_error pass`). |
| `nginx_markdown_large_response_path_total` | counter | Requests routed to the incremental processing path. |
| `nginx_markdown_input_bytes_total` | counter | Cumulative HTML input bytes from successful conversions. |
| `nginx_markdown_output_bytes_total` | counter | Cumulative Markdown output bytes from successful conversions. |
| `nginx_markdown_estimated_token_savings_total` | counter | Estimated cumulative token savings. Requires `markdown_token_estimate on` to produce non-zero values. See [Token Savings Estimate](#token-savings-estimate). |
| `nginx_markdown_decompression_failures_total` | counter | Failed decompression attempts. |

### Counter Metrics (with labels)

| Metric Name | Type | Label Key | Label Values | Description |
|---|---|---|---|---|
| `nginx_markdown_skips_total` | counter | `reason` | `SKIP_CONFIG`, `SKIP_METHOD`, `SKIP_STATUS`, `SKIP_CONTENT_TYPE`, `SKIP_SIZE`, `SKIP_STREAMING`, `SKIP_AUTH`, `SKIP_RANGE`, `SKIP_ACCEPT` | Requests skipped by reason. |
| `nginx_markdown_failures_total` | counter | `stage` | `FAIL_CONVERSION`, `FAIL_RESOURCE_LIMIT`, `FAIL_SYSTEM` | Conversion failures by stage. |
| `nginx_markdown_decompressions_total` | counter | `format` | `gzip`, `deflate`, `brotli` | Decompression operations by compression format. |

### Gauge Metrics (latency buckets)

| Metric Name | Type | Label Key | Label Values | Description |
|---|---|---|---|---|
| `nginx_markdown_conversion_duration_seconds` | gauge | `le` | `0.01`, `0.1`, `1.0`, `+Inf` | Cumulative conversion count per latency bucket. Each bucket value is an independent atomic counter, not a native Prometheus histogram. |

### Total Time Series

The endpoint produces exactly 28 unique time series:

- 9 unlabeled counters (requests, conversions, passthrough, failopen, large response path, input bytes, output bytes, token savings, decompression failures)
- 9 skip reason labels
- 3 failure stage labels
- 3 decompression format labels
- 4 latency bucket labels

This count is deterministic and bounded. No request-specific or high-cardinality labels are used.

---

## Verifying the Endpoint

Use `curl` to verify the endpoint is producing valid Prometheus output.

### Basic Scrape

```bash
# Scrape Prometheus format (requires explicit Accept header)
curl -H "Accept: text/plain; version=0.0.4" http://localhost/markdown-metrics
```

### Explicit Accept Header

```bash
# Request Prometheus format explicitly
curl -H "Accept: text/plain; version=0.0.4" http://localhost/markdown-metrics
```

### JSON Format (still available)

```bash
# JSON output is always available regardless of markdown_metrics_format setting
curl -H "Accept: application/json" http://localhost/markdown-metrics
```

### Validate with promtool

If you have the Prometheus `promtool` CLI installed, validate the output:

```bash
curl -s -H "Accept: text/plain; version=0.0.4" http://localhost/markdown-metrics | promtool check metrics
```

Note: The explicit `Accept` header is required so the endpoint returns Prometheus text exposition format. Without it, the endpoint returns the legacy plain-text format, which `promtool` cannot parse. Alternatively, if `markdown_metrics_format prometheus` is configured and you prefer to omit the header, plain `text/plain` requests still return the legacy format — only `text/plain; version=0.0.4` or `application/openmetrics-text` triggers Prometheus output.

### Example Output

```text
# HELP nginx_markdown_requests_total Total requests entering the module decision chain.
# TYPE nginx_markdown_requests_total counter
nginx_markdown_requests_total 1250

# HELP nginx_markdown_conversions_total Successful HTML-to-Markdown conversions.
# TYPE nginx_markdown_conversions_total counter
nginx_markdown_conversions_total 1180

# HELP nginx_markdown_passthrough_total Requests not converted (skipped or failed-open).
# TYPE nginx_markdown_passthrough_total counter
nginx_markdown_passthrough_total 70

# HELP nginx_markdown_skips_total Requests skipped by reason.
# TYPE nginx_markdown_skips_total counter
nginx_markdown_skips_total{reason="SKIP_METHOD"} 10
nginx_markdown_skips_total{reason="SKIP_STATUS"} 3
nginx_markdown_skips_total{reason="SKIP_CONTENT_TYPE"} 20
nginx_markdown_skips_total{reason="SKIP_SIZE"} 2
nginx_markdown_skips_total{reason="SKIP_STREAMING"} 0
nginx_markdown_skips_total{reason="SKIP_AUTH"} 8
nginx_markdown_skips_total{reason="SKIP_RANGE"} 0
nginx_markdown_skips_total{reason="SKIP_ACCEPT"} 15
nginx_markdown_skips_total{reason="SKIP_CONFIG"} 5

# HELP nginx_markdown_failures_total Conversion failures by stage.
# TYPE nginx_markdown_failures_total counter
nginx_markdown_failures_total{stage="FAIL_CONVERSION"} 3
nginx_markdown_failures_total{stage="FAIL_RESOURCE_LIMIT"} 4
nginx_markdown_failures_total{stage="FAIL_SYSTEM"} 0

# HELP nginx_markdown_failopen_total Conversions failed with original HTML served (fail-open).
# TYPE nginx_markdown_failopen_total counter
nginx_markdown_failopen_total 7

# HELP nginx_markdown_large_response_path_total Requests routed to incremental processing path.
# TYPE nginx_markdown_large_response_path_total counter
nginx_markdown_large_response_path_total 12

# HELP nginx_markdown_input_bytes_total Cumulative HTML input bytes from successful conversions.
# TYPE nginx_markdown_input_bytes_total counter
nginx_markdown_input_bytes_total 52428800

# HELP nginx_markdown_output_bytes_total Cumulative Markdown output bytes from successful conversions.
# TYPE nginx_markdown_output_bytes_total counter
nginx_markdown_output_bytes_total 15728640

# HELP nginx_markdown_estimated_token_savings_total Estimated cumulative token savings (requires markdown_token_estimate on).
# TYPE nginx_markdown_estimated_token_savings_total counter
nginx_markdown_estimated_token_savings_total 15000

# HELP nginx_markdown_decompressions_total Decompression operations by format.
# TYPE nginx_markdown_decompressions_total counter
nginx_markdown_decompressions_total{format="gzip"} 160
nginx_markdown_decompressions_total{format="deflate"} 25
nginx_markdown_decompressions_total{format="brotli"} 20

# HELP nginx_markdown_decompression_failures_total Failed decompression attempts.
# TYPE nginx_markdown_decompression_failures_total counter
nginx_markdown_decompression_failures_total 1

# HELP nginx_markdown_conversion_duration_seconds Cumulative conversion count per latency bucket (not a native Prometheus histogram; no _sum/_count).
# TYPE nginx_markdown_conversion_duration_seconds gauge
nginx_markdown_conversion_duration_seconds{le="0.01"} 140
nginx_markdown_conversion_duration_seconds{le="0.1"} 1030
nginx_markdown_conversion_duration_seconds{le="1.0"} 1170
nginx_markdown_conversion_duration_seconds{le="+Inf"} 1180
```

---

## Prometheus Labels and Decision Log Reason Codes

Prometheus metric label values use the same reason code strings as the module's decision log entries. This alignment allows operators to correlate metric spikes with specific log entries.

### Skip Reason Codes

| Prometheus Label Value | Decision Log Reason | Meaning |
|---|---|---|
| `SKIP_CONFIG` | `SKIP_CONFIG` | Module disabled by configuration (`markdown_filter off`) |
| `SKIP_METHOD` | `SKIP_METHOD` | Request method is not GET or HEAD |
| `SKIP_STATUS` | `SKIP_STATUS` | Response status is not 200 |
| `SKIP_CONTENT_TYPE` | `SKIP_CONTENT_TYPE` | Response Content-Type is not `text/html` |
| `SKIP_SIZE` | `SKIP_SIZE` | Response exceeds `markdown_max_size` |
| `SKIP_STREAMING` | `SKIP_STREAMING` | Content-Type matches `markdown_stream_types` |
| `SKIP_AUTH` | `SKIP_AUTH` | Authenticated request with `markdown_auth_policy deny` |
| `SKIP_RANGE` | `SKIP_RANGE` | Range request (partial content) |
| `SKIP_ACCEPT` | `SKIP_ACCEPT` | Client Accept header does not include `text/markdown` |

### Failure Stage Codes

| Prometheus Label Value | Decision Log Reason | Meaning |
|---|---|---|
| `FAIL_CONVERSION` | `FAIL_CONVERSION` | HTML parsing or Markdown generation failed |
| `FAIL_RESOURCE_LIMIT` | `FAIL_RESOURCE_LIMIT` | Size limit or timeout exceeded during conversion |
| `FAIL_SYSTEM` | `FAIL_SYSTEM` | Memory allocation or system error |

### Correlation Example

If `nginx_markdown_skips_total{reason="SKIP_CONTENT_TYPE"}` spikes, search the decision log for entries with `reason=SKIP_CONTENT_TYPE` to identify which specific requests are being skipped and from which upstream paths.

```bash
# Find decision log entries matching a specific reason code
grep "SKIP_CONTENT_TYPE" /var/log/nginx/error.log | tail -20
```

---

## Interpreting Metrics for Rollout Judgment

Use these metrics to assess whether the module is operating correctly during a phased rollout.

### Healthy Patterns

These patterns indicate normal, healthy operation:

- **Conversion rate is stable.** `nginx_markdown_conversions_total / nginx_markdown_requests_total` remains consistent over time (typically 60–90% depending on traffic mix).
- **Fail-open rate is near zero.** `nginx_markdown_failopen_total` grows slowly or not at all. A small number of fail-opens is acceptable; a sustained rate above 1% warrants investigation.
- **Skip reasons are expected.** The dominant skip reasons match your deployment (e.g., `SKIP_ACCEPT` is high because most clients do not request Markdown).
- **Latency is concentrated in fast buckets.** Most conversions fall in the `le="0.01"` and `le="0.1"` buckets. Few or no conversions in the `le="+Inf"` (>1s) bucket.
- **Byte reduction is positive.** `nginx_markdown_output_bytes_total < nginx_markdown_input_bytes_total`, indicating Markdown output is smaller than HTML input.

### Problem Patterns

These patterns indicate issues that may require action:

- **Rising failure rate.** `sum(nginx_markdown_failures_total)` growing faster than `nginx_markdown_conversions_total`. Check the `stage` label breakdown to identify the failure category.
- **Fail-open rate above 1%.** `nginx_markdown_failopen_total / nginx_markdown_requests_total > 0.01` sustained over 5 minutes. The module is silently serving HTML instead of Markdown.
- **Latency drift to slow buckets.** Increasing counts in `le="1.0"` or `le="+Inf"` buckets. May indicate large documents, resource contention, or upstream slowness.
- **Unexpected skip reason spikes.** A sudden increase in a specific skip reason (e.g., `SKIP_STATUS`) may indicate upstream issues returning non-200 responses.
- **System errors.** Any non-zero `nginx_markdown_failures_total{stage="FAIL_SYSTEM"}` warrants immediate investigation — these indicate memory allocation or system-level failures.

### Recommended Alerting Thresholds

| Condition | Severity | Threshold | Action |
|---|---|---|---|
| Failure rate | Critical | > 10% for 5 min | Page on-call |
| Failure rate | Warning | > 5% for 10 min | Notify team |
| System error rate | Critical | > 1% for 5 min | Page on-call |
| Fail-open rate | Warning | > 1% for 5 min | Investigate |
| Slow conversions (>1s bucket) | Warning | > 5% of total for 10 min | Investigate |
| Decompression failure rate | Warning | > 1% of decompressions for 10 min | Investigate |

### PromQL Examples for Dashboards

```promql
# Conversion success rate (percentage)
nginx_markdown_conversions_total / clamp_min(nginx_markdown_requests_total, 1) * 100

# Failure rate (percentage)
sum(nginx_markdown_failures_total) / clamp_min(nginx_markdown_requests_total, 1) * 100

# Fail-open rate (percentage)
nginx_markdown_failopen_total / clamp_min(nginx_markdown_requests_total, 1) * 100

# Byte reduction ratio (percentage)
(1 - nginx_markdown_output_bytes_total / clamp_min(nginx_markdown_input_bytes_total, 1)) * 100

# Skip breakdown by reason
nginx_markdown_skips_total

# Slow conversion ratio (>1s bucket as percentage of total conversions)
nginx_markdown_conversion_duration_seconds{le="+Inf"} - nginx_markdown_conversion_duration_seconds{le="1.0"}
/ clamp_min(nginx_markdown_conversion_duration_seconds{le="+Inf"}, 1) * 100
```

---

## Token Savings Estimate

The `nginx_markdown_estimated_token_savings_total` metric provides a cumulative estimate of token savings achieved by serving Markdown instead of HTML.

### Disclaimer

**This value is an approximation, not a precise tokenizer count.** The estimate is computed using the module's built-in byte-ratio heuristic when `markdown_token_estimate on` is configured. Different LLM tokenizers produce different token counts for the same text. Use this metric as a directional indicator of value, not as an exact measurement.

### Prerequisites

The counter remains at zero unless `markdown_token_estimate on` is configured:

```nginx
location /docs {
    markdown_filter on;
    markdown_token_estimate on;
}
```

### Interpretation

- A growing `estimated_token_savings_total` indicates the module is reducing token consumption for AI agent consumers.
- Compare the growth rate of this counter against `nginx_markdown_conversions_total` to estimate average per-request savings.
- The estimate is most useful as a trend indicator over time, not as an absolute value.

---

## Metric Stability Policy

The module follows a metric stability policy to protect operator alerting rules and dashboards across upgrades.

### Guarantees

1. **No renames or removals without notice.** Once a metric name and its label set are published in a release, the metric name will not be renamed or removed in any subsequent 0.x release without a deprecation notice in the prior release's changelog.
2. **Label key sets are frozen.** For existing metrics, no label keys will be added or removed. The set of label keys for a metric is fixed at the release where the metric is introduced.
3. **New metrics may be added.** New metric names may be introduced in any release without a deprecation period.
4. **New label values may be added.** New values for existing label keys (e.g., a new `reason` code) may be added without a deprecation period. Operators should use label matchers that tolerate new values.
5. **The `nginx_markdown_` prefix is reserved.** All module metrics use this prefix. No other module or exporter should use it.

### Published Metrics (0.4.0)

All metrics listed in the [Metric Catalog](#metric-catalog) section are considered stable as of the 0.4.0 release. Their names, types, and label key sets are covered by the stability policy above.

| Metric Name | Type | Label Keys | Stability |
|---|---|---|---|
| `nginx_markdown_requests_total` | counter | — | Stable |
| `nginx_markdown_conversions_total` | counter | — | Stable |
| `nginx_markdown_passthrough_total` | counter | — | Stable |
| `nginx_markdown_skips_total` | counter | `reason` | Stable |
| `nginx_markdown_failures_total` | counter | `stage` | Stable |
| `nginx_markdown_failopen_total` | counter | — | Stable |
| `nginx_markdown_large_response_path_total` | counter | — | Stable |
| `nginx_markdown_input_bytes_total` | counter | — | Stable |
| `nginx_markdown_output_bytes_total` | counter | — | Stable |
| `nginx_markdown_estimated_token_savings_total` | counter | — | Stable |
| `nginx_markdown_decompressions_total` | counter | `format` | Stable |
| `nginx_markdown_decompression_failures_total` | counter | — | Stable |
| `nginx_markdown_conversion_duration_seconds` | gauge | `le` | Stable |
