# Migration Guide: 0.7.x → 0.8.0

**Audience**: Operators upgrading from 0.7.x to 0.8.0

## Overview

v0.8.0 introduces the **true streaming contract**: the module can now convert
HTML to Markdown incrementally without buffering the entire upstream response
body. This is opt-in via `markdown_streaming_engine auto` (the new default)
and only activates for responses that meet size/transfer criteria.

**Key guarantee**: Setting `markdown_streaming_engine off` produces behavior
identical to 0.7.x. No existing configuration breaks on upgrade.

---

## Behavioral Changes

These are observable differences operators will notice after upgrading from
0.7.x to 0.8.0.

### 1. Auto-mode streaming threshold increased to 1 MB

In 0.6.0–0.7.x, `markdown_streaming_auto_threshold` defaulted to `32k`. In
0.8.0, the new `markdown_stream_threshold` directive replaces it with a default
of `1m`. This means fewer responses enter the streaming path by default,
targeting only genuinely large responses where bounded-memory conversion
provides material benefit.

| Version | Directive | Default |
|---------|-----------|---------|
| 0.6.0–0.7.x | `markdown_streaming_auto_threshold` | `32k` |
| 0.8.0 | `markdown_stream_threshold` | `1m` |

**Impact**: If you previously relied on responses between 32 KB and 1 MB
entering the streaming path, they will now use full-buffer unless you lower
the threshold explicitly:

```nginx
markdown_stream_threshold 32k;
```

### 2. True streaming produces chunked transfer encoding

When a response enters the true streaming path, the module emits Markdown
output incrementally. The downstream response uses chunked transfer encoding
rather than a single buffer with a known `Content-Length`. Clients and
downstream proxies must handle `Transfer-Encoding: chunked` responses.

**Impact**: If you have infrastructure that requires `Content-Length` on all
responses, either set `markdown_streaming_engine off` for those paths or
ensure downstream components support chunked responses.

### 3. Pre-commit/post-commit failure semantics

0.8.0 formalizes two-phase error handling:

- **Pre-commit** (before any Markdown is sent to client): errors can fall back
  to original HTML transparently. Controlled by `markdown_streaming_on_error`.
- **Post-commit** (after Markdown headers/bytes are already sent): errors
  result in truncated output (empty `last_buf`). This is not configurable
  because `Content-Type: text/markdown` headers have already been sent.

In 0.7.x, the streaming path did not have this formal two-phase distinction.

### 4. New streaming metrics and reason codes

0.8.0 adds streaming-specific Prometheus counters and reason codes. If you
scrape the metrics endpoint or parse structured logs, you will see new series:

- `nginx_markdown_streaming_engine_choice_total{engine=...}` — engine selection decisions
- `nginx_markdown_streaming_fallback_total{phase=...,action=...}` — pre-commit fallback events
- `nginx_markdown_streaming_failure_total{phase=...,action=...}` — post-commit failures
- `nginx_markdown_streaming_candidate_total` — candidates evaluated
- `nginx_markdown_true_streaming_selected_total` — final streaming selections
- `nginx_markdown_streaming_output_bytes_total` — Markdown bytes emitted via streaming
- `nginx_markdown_excluded_content_type_total` — requests excluded by content type

New reason codes (see [streaming-observability](../features/streaming-observability.md)):

| Code | Meaning |
|------|---------|
| `eligible` | Request eligible for true streaming |
| `content_length_known` | Content-Length present, below threshold |
| `below_threshold` | Response size below auto threshold |
| `config_disabled` | Streaming disabled by configuration |
| `excluded_content_type` | Excluded by stream type list |
| `precommit_html_error` | HTML parse error in pre-commit |
| `precommit_budget` | Memory budget exceeded in pre-commit |
| `precommit_timeout` | Parse timeout in pre-commit |
| `postcommit_parse_error` | Parse error after commit |
| `postcommit_budget_exceeded` | Budget exceeded after commit |
| `postcommit_io_error` | I/O error after commit |

### 5. Deprecated directives

Per the deprecation schedule announced in 0.6.0, the following directives
have changed status in 0.8.0:

| Directive | Status in 0.8.0 | Replacement |
|-----------|-----------------|-------------|
| `markdown_max_size` | Deprecated (accepted with info-level warning) | `markdown_memory_budget` |
| `markdown_streaming_budget` | Still available as path-specific override | `markdown_memory_budget` (unified) |
| `markdown_streaming_auto_threshold` | Removed — `nginx -t` rejects | `markdown_stream_threshold` |

If your configuration still uses `markdown_streaming_auto_threshold`,
`nginx -t` will reject the configuration. Migrate before upgrading:

```nginx
# Before (0.7.x)
markdown_streaming_auto_threshold 64k;

# After (0.8.0)
markdown_stream_threshold 64k;
```

**Note**: `markdown_max_size` still works but emits a deprecation warning.
Migrate to `markdown_memory_budget` at your convenience.
`markdown_streaming_budget` remains available as an explicit override for
the streaming path budget (see
[CONFIGURATION.md](CONFIGURATION.md#markdown_streaming_budget)).

---

## New Directives

All new directives are in the `http`, `server`, and `location` contexts.

### markdown_stream_threshold

**Syntax:** `markdown_stream_threshold <size>;`
**Default:** `1m`

Minimum response size for streaming candidacy. In `auto` mode, responses with
`Content-Length` at or above this threshold (or using chunked transfer
encoding) become streaming candidates. Responses below the threshold use
full-buffer.

```nginx
# Stream responses >= 512 KB
markdown_stream_threshold 512k;

# Only stream very large responses
markdown_stream_threshold 5m;
```

### markdown_stream_precommit_buffer

**Syntax:** `markdown_stream_precommit_buffer <size>;`
**Default:** `256k`

Size of the pre-commit replay buffer. During the streaming pre-commit phase,
converted output is buffered up to this size. If an error occurs before the
commit boundary, the buffered content is discarded and the original HTML is
replayed to the client (fail-open behavior per `markdown_streaming_on_error`).

Setting this to `0` disables pre-commit HTML replay — errors immediately
trigger the configured error policy without fallback.

```nginx
# Larger replay safety margin
markdown_stream_precommit_buffer 512k;

# Disable pre-commit replay
markdown_stream_precommit_buffer 0;
```

### markdown_stream_flush_min

**Syntax:** `markdown_stream_flush_min <size>;`
**Default:** `16k`

Minimum Markdown output batch size before flushing downstream. The engine
accumulates converted output until this threshold is reached, then flushes.
This reduces per-byte overhead and backpressure from many small writes.

Must be greater than zero (`0` is rejected by `nginx -t`).

```nginx
# Lower latency with smaller batches
markdown_stream_flush_min 4k;

# Higher throughput with larger batches
markdown_stream_flush_min 64k;
```

### markdown_stream_excluded_types

**Syntax:** `markdown_stream_excluded_types <type> [<type> ...];`
**Default:** none (only built-in hard exclusions apply)

Additional MIME types to exclude from streaming. These are additive to the
built-in hard exclusions (`text/event-stream`, `application/x-ndjson`,
`application/stream+json`). Built-in exclusions cannot be removed.

```nginx
# Exclude CSV and XML feeds from streaming
markdown_stream_excluded_types text/csv application/atom+xml;
```

### Reserved: markdown_stream_flush_interval

> **Not implemented in 0.8.0.** Using this directive causes `nginx -t` to fail.
> Reserved for a future 0.8.x release for time-based flush control. Do not
> include it in configuration files.

---

## Unchanged Behavior

The following behaviors are identical between 0.7.x and 0.8.0. No operator
action is required for these areas.

### Full-buffer conversion path

When `markdown_streaming_engine off` is set (or when auto mode selects
full-buffer for a given response), behavior is identical to 0.7.x. The entire
response is buffered, converted, and sent with a `Content-Length` header.

### All non-streaming directives

The following directives work exactly as before:

- `markdown_filter` — enable/disable conversion
- `markdown_memory_budget` — unified memory budget
- `markdown_on_error` — full-buffer failure policy
- `markdown_flavor` — Markdown output flavor
- `markdown_timeout` — conversion timeout
- `markdown_decompress_max_size` — decompression budget
- `markdown_parse_timeout` — parser timeout
- `markdown_parser_budget` — parser memory budget
- `markdown_token_estimate` — token count header
- `markdown_front_matter` — YAML front matter
- `markdown_on_wildcard` — wildcard Accept matching
- `markdown_auth_policy` / `markdown_auth_cookies` — authentication handling
- `markdown_etag` — ETag generation
- `markdown_conditional_requests` — conditional request modes
- `markdown_log_verbosity` — module log verbosity
- `markdown_buffer_chunked` — chunked response buffering control
- `markdown_stream_types` — content types excluded from conversion
- `markdown_trust_forwarded_headers` — forwarded header trust
- `markdown_content_types` — eligible content types
- `markdown_prune_noise` / `markdown_prune_selectors` / `markdown_prune_protection_selectors` — noise pruning

### Content negotiation

Accept header parsing and q-value negotiation remain unchanged. The module
still requires `text/markdown` preference (or wildcard with
`markdown_on_wildcard on`) to trigger conversion.

### Conditional request handling

ETag generation, `If-None-Match`, and `If-Modified-Since` behavior remain
unchanged. For streaming responses that have already committed, ETags are
not available (chunked output has no pre-computed ETag).

### Fail-open behavior

The default `markdown_on_error pass` policy is unchanged. Conversion failures
in the full-buffer path still serve original HTML transparently.

### Metrics endpoint

The `/markdown-metrics` endpoint format is unchanged. New streaming counters
are additive — existing metric series remain with identical semantics.
Prometheus and JSON formats are both still supported.

### Dynamic configuration (dynconf)

The dynamic configuration watcher, file format, and supported runtime keys
are unchanged. Streaming-specific runtime tuning continues to use the
`streaming_budget` dynconf key.

### Shadow mode

`markdown_streaming_shadow` continues to work as before for verifying engine
output parity before enabling streaming for live traffic.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.8.0 | 2026-06-04 | spec-42 | Initial migration guide |
