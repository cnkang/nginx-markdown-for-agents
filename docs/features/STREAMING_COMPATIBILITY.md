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
| Conditional requests (304) | ✅ | ⚠️ | Only If-None-Match/ETag validation requires full buffering; `ims_only` remains streaming-compatible via upstream Last-Modified and NGINX If-Modified-Since handling. Streaming mode is therefore not blanket-incompatible with conditional requests — it cannot validate via ETag, but IMS validation still works |
| Fail-open (pre-commit) | ✅ | ✅ | Streaming: configurable via `markdown_error_policy` |
| Fail-open (post-commit) | N/A | ❌ | Post-commit errors produce truncated output |
| `parser_memory` budget | ✅ | ✅ | Rust parser allocation bound (`parser_memory_budget`): enforced by the conservative pre-parse estimate on the full-buffer path and checked continuously on the streaming path |
| `conversion_memory` budget | ✅ | ✅ | Cumulative input-size cap shared by buffered and streaming paths |
| Prometheus metrics | ✅ | ✅ | Additional streaming-specific counters |
| Token estimation header | ✅ | ❌ | Requires full output; not available in streaming |
| Front matter (YAML) | ✅ | ✅ | Emitted in pre-commit phase |
| Noise pruning | ✅ | ✅ | Applied during parsing |
| Dynamic configuration | ✅ | ✅ | Runtime engine switching supported |
| Shadow mode | ✅ | N/A | Runs streaming in background against full-buffer result |
| Decompression (gzip) | ✅ | ✅ | Member-aware; streaming since 0.9.1 |
| Decompression (deflate) | ✅ | ✅ | RFC 1950 zlib-wrapped plus raw RFC 1951 fallback: full-buffer retries as raw after a zero-output format error; streaming detects once on the first two bytes and reports a format error for misclassified streams; streaming since 0.9.1 |
| Decompression (Brotli) | ✅ | ✅ | Requires `NGX_HTTP_BROTLI`; streaming since 0.9.1 |

## Legend

- ✅ Supported — feature works as expected in this mode
- ❌ Not supported — feature is unavailable or cannot function in this mode
- N/A — not applicable to this mode

## Key Differences

### ETag and conditional requests

Full-buffer mode computes an ETag from the complete Markdown output and supports
`If-None-Match` / `If-Modified-Since` for 304 responses. Streaming mode commits
the response headers before the full output is available, so ETag generation
and `If-None-Match`-based conditional validation are not possible.

Only ETag validation requires full buffering. With `markdown_cache_validation
ims_only`, streaming remains compatible: the module forwards the upstream
`Last-Modified` header and NGINX's standard `If-Modified-Since` handling
performs the 304 decision, so the ims_only mode does not force the full-buffer
path.

**Known constraint (user-confirmed):** the same URL can therefore
yield an ETag for small responses (full-buffer path) and no ETag for large
responses (streaming path). Clients and caches lose strong validation for
large pages. This is an accepted trade-off of streaming header commitment.
A deferred header commit is out of scope for 0.9.2.

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

The `X-Markdown-Tokens` header requires knowing the full output length.
Since streaming sends chunks incrementally, this header is not emitted.

### Shadow mode

Shadow mode is a validation tool: it runs the streaming engine in the background
while serving the full-buffer result to the client. This lets operators compare
output and metrics without affecting live traffic. It is not a delivery mode
itself.

## Deciding Which Mode to Use

Use **full-buffer** when:

- You need ETag-based caching and conditional requests
- Response sizes are moderate (within `markdown_limits conversion_memory=<size>`, the hard cumulative input-size cap shared by both buffered and streaming paths). Streaming adds `parser_memory=` for the Rust parser allocation bound
- Downstream consumers require token estimation headers

Use **streaming** when:

- Responses are large and you want bounded memory usage
- Time-to-first-byte matters more than conditional caching
- You accept that post-commit errors produce truncated output

Use **auto** (default since 0.8.0) to let the module choose based on response size thresholds.

## Related Documentation

- [Streaming Rollout Cookbook](../guides/streaming-rollout-cookbook.md)
- [Configuration Reference — Streaming Directives](../guides/CONFIGURATION.md)
- [Migration Guide](../guides/MIGRATION-0.8.md)
- [Streaming Observability](streaming-observability.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Corrected the parser_memory budget row: the bound covers both paths (full-buffer pre-parse estimate plus streaming enforcement), not streaming only |
| 0.9.2 | 2026-08-19 | Hermes | Document the accepted no-ETag-for-streaming constraint (full-buffer vs streaming path divergence, user-confirmed) |
| 0.9.2 | 2026-08-15 | Hermes | Deflate streaming misclassification reports a format error instead of failing closed |
| 0.9.1 | 2026-07-18 | Kang | Added streaming decompression rows (gzip, deflate, Brotli) to compatibility matrix |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.8.0 | 2026-06-16 | Kang  | Initial feature compatibility matrix |
