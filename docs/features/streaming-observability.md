# Streaming Observability and Diagnostics

> **0.9.0 Note**: The module-wide reason code registry has been renamed to
> lowercase snake_case in 0.9.0. The streaming block reason codes documented
> below (numeric codes 0–8) are a **separate system** used internally by the
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
| 0 | `streaming_block_full_cache_validation` | full_buffer | `cache_validation = full` forces full-buffer for ETag |
| 1 | `streaming_block_content_encoding` | full_buffer | Upstream `Content-Encoding` requires decompression |
| 2 | `streaming_block_content_length_unknown` | full_buffer | `auto` mode cannot size response without `Content-Length` |
| 3 | `streaming_block_range_request` | passthrough | `Range` request bypasses conversion |
| 4 | `streaming_block_no_transform` | passthrough | `Cache-Control: no-transform` bypasses conversion |
| 5 | `streaming_block_engine_off` | full_buffer | No streaming backend (policy `off` or engine `off`) |
| 6 | `streaming_block_small_body` | full_buffer | `auto` mode response below streaming threshold |
| 7 | `streaming_block_head_request` | passthrough | `HEAD` request: header decisions only, no body |
| 8 | `streaming_block_304_response` | passthrough | `304 Not Modified` carries no body |

**Note:** Reason codes are additive only. Removal requires a major version bump.
The 9-variant enum is defined in `components/rust-converter/src/decision/streaming.rs`
(`StreamingBlockReason`, `#[repr(u8)]`).

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
   `markdown_error_policy`.

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
