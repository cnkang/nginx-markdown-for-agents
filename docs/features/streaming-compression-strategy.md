# Streaming Compression Strategy (v0.8.0)

## Purpose

This document describes how the streaming conversion engine handles compressed
upstream responses in v0.8.0. It covers the routing decision, safety rationale,
and relationship to the existing full-buffer decompression path.

## Summary

In v0.8.0, compressed responses (those with a `Content-Encoding` header) are
**routed to the full-buffer path** rather than the streaming parser. The
full-buffer path already provides safe, bounded decompression with budget
enforcement via `markdown_decompress_max_size`. True streaming decompression
(incremental, bounded-memory decompression fed chunk-by-chunk into the streaming
parser) is deferred to future 0.8.x work.

## Routing Decision

When the header filter detects a `Content-Encoding` header on an otherwise
eligible response, the following logic applies:

1. If `markdown_auto_decompress` is **off**, or the encoding is unsupported,
   the response passes through unchanged (no conversion attempted).
2. If `markdown_auto_decompress` is **on** and the encoding is supported
   (`gzip`, `deflate`, `br`), the response is routed to the **full-buffer
   conversion path** — not the streaming parser.
3. The full-buffer path decompresses the response using the existing controlled
   decompression pipeline, subject to `markdown_decompress_max_size`.
4. Uncompressed responses continue to be eligible for streaming conversion as
   normal.

```text
Upstream response
  │
  ├─ Content-Encoding present?
  │    │
  │    ├─ auto_decompress OFF or unsupported encoding
  │    │    └─ Passthrough (no conversion)
  │    │
  │    └─ auto_decompress ON + supported encoding
  │         └─ Route to full-buffer path
  │              └─ Decompression with budget enforcement
  │                   └─ HTML → Markdown conversion
  │
  └─ No Content-Encoding
       └─ Eligible for streaming conversion
```

## Decompression Bomb Mitigation

The full-buffer decompression path enforces the `markdown_decompress_max_size`
budget. If decompressed output exceeds this limit, decompression terminates
immediately and the configured `markdown_error_policy` policy applies:

- **pass** (default): original compressed response served to client unchanged.
- **fail_closed**: 502 Bad Gateway returned.

This prevents decompression bombs from exhausting worker memory regardless of
whether the response would have been a streaming or full-buffer candidate.

## Rationale

Streaming decompression requires an incremental decompressor that:

- Operates within bounded memory (no full-response buffering).
- Handles chunk boundaries that may split compressed frames.
- Enforces the decompression budget incrementally without needing to see the
  full decompressed output upfront.
- Preserves backpressure semantics (NGX_AGAIN handling) while decompression
  state is in-flight.

This is a significant engineering effort. The v0.8.0 release instead leverages
the proven full-buffer decompression path, which already handles all supported
encodings safely with budget enforcement. The tradeoff is that compressed
responses do not benefit from streaming's bounded-memory advantage — they are
fully buffered before conversion. In practice, most agent-facing APIs serve
uncompressed responses (or operators configure `proxy_set_header
Accept-Encoding ""` to disable upstream compression), so this routing covers
the common case without risk.

True streaming decompression is planned for a future release once the
incremental decompression state machine is validated against the same safety
properties.

## Relevant Directives

| Directive | Role in Compression Strategy |
|-----------|------------------------------|
| `markdown_auto_decompress` | Controls whether the module attempts decompression at all. Default: `on`. When off, compressed responses pass through unconverted. |
| `markdown_decompress_max_size` | Maximum decompressed output size. Prevents decompression bombs. Default: inherits `markdown_max_size`. |

## Operator Guidance

- **Uncompressed upstreams**: No action needed. Streaming works normally.
- **Compressed upstreams, streaming desired**: Configure `proxy_set_header
  Accept-Encoding "";` to request uncompressed responses from upstream, allowing
  the streaming path to handle them directly.
- **Compressed upstreams, full-buffer acceptable**: No action needed. The module
  automatically routes to full-buffer and decompresses safely.
- **Budget tuning**: Set `markdown_decompress_max_size` to a value that
  accommodates your largest legitimate compressed responses while still
  protecting against decompression bombs.

## Future Work

- **0.8.x**: Incremental streaming decompression with bounded-memory guarantees,
  enabling compressed responses to flow through the streaming parser without
  full buffering.

## Related Documentation

- [AUTOMATIC_DECOMPRESSION.md](AUTOMATIC_DECOMPRESSION.md) — Full-buffer decompression behavior and error handling
- [DECOMPRESSION.md](DECOMPRESSION.md) — Budget enforcement and error categories
- [../guides/CONFIGURATION.md](../guides/CONFIGURATION.md) — Directive syntax and defaults
- [../guides/streaming-rollout-cookbook.md](../guides/streaming-rollout-cookbook.md) — Streaming rollout guidance

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.8.0 | 2026-06-16 | Kang | Initial document for v0.8.0 streaming compression strategy (streaming security enforcement task 3.3) |
