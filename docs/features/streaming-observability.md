# Streaming Observability and Diagnostics

> **0.9.0 Note**: The module-wide reason code registry has been renamed to
> lowercase snake_case in 0.9.0. The streaming reason codes documented below
> (numeric codes 0–13) are a **separate system** used internally by the
> streaming engine for routing decisions. They are not part of the unified
> `ReasonCode` enum. See the
> [Observability Schema v1](../architecture/observability-schema-v1.md) for
> the unified reason code registry.

## Overview

The streaming observability feature adds comprehensive metrics, structured
logging, and diagnostics for the streaming conversion engine. This enables
operators to monitor streaming performance, diagnose issues, and set up
alerts based on stable reason codes.

**Requirement**: Streaming Observability and Diagnostics (v0.8.0)

---

## Metrics Reference

### Engine Choice Counters

| Metric | Type | Description |
|--------|------|-------------|
| `nginx_markdown_streaming_engine_choice_total{engine="streaming"}` | counter | Requests using true streaming |
| `nginx_markdown_streaming_engine_choice_total{engine="full_buffer"}` | counter | Requests routed to full-buffer |
| `nginx_markdown_streaming_engine_choice_total{engine="passthrough"}` | counter | Requests marked passthrough |
| `nginx_markdown_streaming_engine_choice_total{engine="not_eligible"}` | counter | Requests not eligible |

### Fallback Counters

| Metric | Type | Description |
|--------|------|-------------|
| `nginx_markdown_streaming_fallback_total{phase="precommit",action="pass"}` | counter | Pre-commit fallback with HTML pass-through |
| `nginx_markdown_streaming_fallback_total{phase="precommit",action="reject"}` | counter | Pre-commit fallback with rejection |

### Failure Counters

| Metric | Type | Description |
|--------|------|-------------|
| `nginx_markdown_streaming_failure_total{phase="postcommit",action="abort"}` | counter | Post-commit abort |
| `nginx_markdown_streaming_failure_total{phase="postcommit",action="safe_finish"}` | counter | Post-commit safe finish |

### Performance Counters

| Metric | Type | Description |
|--------|------|-------------|
| `nginx_markdown_streaming_candidate_total` | counter | Candidates evaluated |
| `nginx_markdown_true_streaming_selected_total` | counter | Final streaming selections |
| `nginx_markdown_streaming_output_bytes_total` | counter | Markdown bytes emitted |
| `nginx_markdown_excluded_content_type_total` | counter | Excluded by content type |

---

## Reason Code Reference

Reason codes are stable string identifiers. They are additive only between
versions; removal requires a major version bump.

| Code | String | Engine Path | Description |
|------|--------|-------------|-------------|
| 0 | `eligible` | streaming | Request eligible for true streaming |
| 1 | `content_length_known` | full_buffer | Content-Length present, below threshold |
| 2 | `below_threshold` | full_buffer | Response size below `markdown_stream_threshold` |
| 3 | `config_disabled` | full_buffer | Streaming disabled by configuration |
| 4 | `excluded_content_type` | passthrough | Excluded by stream_types list |
| 5 | `not_html` | passthrough | Response is not HTML |
| 6 | `not_candidate` | not_eligible | Not a streaming candidate |
| 7 | `accept_mismatch` | not_eligible | Accept header doesn't match |
| 8 | `precommit_html_error` | fallback | HTML parse error in pre-commit |
| 9 | `precommit_budget` | fallback | Memory budget exceeded in pre-commit |
| 10 | `precommit_timeout` | fallback | Parse timeout in pre-commit |
| 11 | `postcommit_parse_error` | failure | Parse error after commit |
| 12 | `postcommit_budget_exceeded` | failure | Budget exceeded after commit |
| 13 | `postcommit_io_error` | failure | I/O error after commit |

**Note:** Reason codes are additive only. Removal requires a major version bump.

---

## Structured Log Fields

All streaming decision logs include these fields:

| Field | Description | Values |
|-------|-------------|--------|
| `engine` | Engine selected | streaming, full_buffer, passthrough, rejected |
| `phase` | Decision phase | header_filter, precommit, postcommit |
| `committed` | Headers sent? | 0 (no), 1 (yes) |
| `fallback_available` | Can fall back? | 0 (no), 1 (yes) |
| `reason` | Reason code string | See table above |
| `content_type` | Response Content-Type | e.g., text/html |
| `content_length_known` | CL header present? | 0 (no), 1 (yes) |
| `chunked` | Chunked transfer? | 0 (no), 1 (yes) |
| `markdown_error_policy` | Error policy | pass, fail_closed |

### Log Levels

- **Debug**: Normal engine choice decisions (zero cost in production)
- **Info**: Precommit fallback events
- **Error**: Post-commit failures (non-recoverable)

---

## Diagnostics Endpoint

When `markdown_diagnostics on` is configured, the `/nginx-markdown/diagnostics`
endpoint includes streaming sections:

### streaming_config

```json
{
  "streaming_config": {
    "engine": "auto",
    "on_error": "pass",
    "threshold": 1048576,
    "precommit_buffer": 262144,
    "flush_min": 16384,
    "threshold_explicit": false
  }
}
```

`threshold_explicit: false` means the threshold came from the v0.8
`markdown_stream_threshold` default, not from an explicit configuration.

### streaming_metrics

```json
{
  "streaming_metrics": {
    "requests_total": 1234,
    "succeeded_total": 1200,
    "failed_total": 4,
    "fallback_total": 30,
    "candidate_total": 1500,
    "output_bytes_total": 5678900,
    "engine_choice_streaming": 1234,
    "engine_choice_full_buffer": 266
  }
}
```

---

## Troubleshooting

### High fallback rate

If `nginx_markdown_streaming_fallback_total` is high relative to
`nginx_markdown_streaming_candidate_total`:

1. Check `reason` field in info-level logs for common patterns.
2. Common causes: malformed HTML, exceeded budgets, excluded content types.
3. Consider adjusting `markdown_stream_threshold` or
   `markdown_streaming_on_error`.

### Post-commit failures

If `nginx_markdown_streaming_failure_total{phase="postcommit"}` is non-zero:

1. These are non-recoverable — headers were already sent.
2. Check error-level logs for `reason` field.
3. Common causes: parser budget exhaustion on complex pages, I/O timeouts.
4. Consider increasing `markdown_parser_budget` for affected endpoints.

### No streaming selections

If `nginx_markdown_streaming_engine_choice_total{engine="streaming"}` is 0
while `nginx_markdown_streaming_candidate_total` > 0:

1. Check if `markdown_streaming_engine` is set to `on` or `auto`.
2. Verify `markdown_stream_threshold` is smaller than typical response sizes.
3. Check for Content-Encoding headers (compressed responses force full-buffer).
