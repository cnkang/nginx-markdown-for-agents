# Streaming Feature Compatibility

This document lists which module features are available in each conversion engine
mode. Use it to understand behavioral differences before enabling streaming.

> **Status**: The project has supported streaming since v0.8.0. Behavior may change in future
> releases.

## Compatibility Matrix

| Feature | Full-Buffer | Streaming | Notes |
|---------|:-----------:|:---------:|-------|
| Content negotiation (Accept header) | ✅ | ✅ | Works identically in both modes |
| HTML-to-Markdown conversion | ✅ | ✅ | Same output quality |
| ETag generation | ✅ | ❌ | No ETag for committed streaming responses |
| Conditional requests (304) | ✅ | ❌ | Requires ETag; streaming bypasses |
| Fail-open (pre-commit) | ✅ | ✅ | Streaming: configurable via `markdown_error_policy` |
| Fail-open (post-commit) | N/A | ❌ | Post-commit errors produce truncated output |
| Memory budget | ✅ | ✅ | Enforced in both paths |
| Prometheus metrics | ✅ | ✅ | Additional streaming-specific counters |
| Token estimation header | ✅ | ❌ | Requires full output; not available in streaming |
| Front matter (YAML) | ✅ | ✅ | Emitted in pre-commit phase |
| Noise pruning | ✅ | ✅ | Applied during parsing |
| Dynamic configuration | ✅ | ✅ | Runtime engine switching supported |
| Shadow mode | ✅ | N/A | Runs streaming in background against full-buffer result |
| Streaming decompression (gzip) | N/A | ✅ | Member-aware; since 0.9.1 |
| Streaming decompression (deflate) | N/A | ✅ | RFC 1950 zlib-wrapped plus raw RFC 1951 compatibility fallback |
| Streaming decompression (Brotli) | N/A | ✅ | Requires `NGX_HTTP_BROTLI`; since 0.9.1 |

## Legend

- ✅ Supported — feature works as expected in this mode
- ❌ Not supported — feature is unavailable or cannot function in this mode
- N/A — not applicable to this mode

## Key Differences

### ETag and conditional requests

Full-buffer mode computes an ETag from the complete Markdown output and supports
`If-None-Match` / `If-Modified-Since` for 304 responses. Streaming mode commits
the response headers before the full output is available, so ETag generation and
conditional request handling are not possible.

### Fail-open behavior

With `markdown_error_policy pass`, full-buffer conversion errors return the
original HTML. In streaming mode, errors that occur before the module commits
the response to the client (pre-commit) handle the same way. With
`markdown_error_policy fail_closed`, pre-commit errors return the configured
error status and do not pass through the original body. `status N` uses that
explicit status policy.
Errors that occur after headers have already been sent (post-commit) cannot
roll back. When a later gzip member fails, the module preserves earlier
decompressed output and appends Markdown closing bytes to safely close the
partial response. The client receives a truncated but structurally valid
Markdown response.

### Token estimation

The `X-Markdown-Token-Estimate` header requires knowing the full output length.
Since streaming sends chunks incrementally, this header is not emitted.

### Shadow mode

Shadow mode is a validation tool: it runs the streaming engine in the background
while serving the full-buffer result to the client. This lets operators compare
output and metrics without affecting live traffic. It is not a delivery mode
itself.

## Deciding Which Mode to Use

Use **full-buffer** when:

- You need ETag-based caching and conditional requests
- Response sizes are moderate (within `markdown_limits conversion_memory=<size>`, the hard cumulative input-size cap shared by both buffered and streaming paths)
- Downstream consumers require token estimation headers

Use **streaming** when:

- Responses are large and you want bounded memory usage
- Time-to-first-byte matters more than conditional caching
- You accept that post-commit errors produce truncated output

Use **auto** (default since 0.8.0) to let the module choose based on response size
thresholds.

## Related Documentation

- [Streaming Rollout Cookbook](../guides/streaming-rollout-cookbook.md)
- [Configuration Reference — Streaming Directives](../guides/CONFIGURATION.md)
- [Migration Guide](../guides/MIGRATION-0.8.md)
- [Streaming Observability](streaming-observability.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-18 | Kang | Added streaming decompression rows (gzip, deflate, Brotli) to compatibility matrix |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.8.0 | 2026-06-16 | Kang  | Initial feature compatibility matrix |
