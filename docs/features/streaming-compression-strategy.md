# Streaming Compression Strategy (v0.9.1)

## Purpose

This document describes how the 0.9.1 streaming conversion engine handles
compressed upstream responses, including its relationship to bounded
full-buffer decompression.

## Summary

In 0.9.1, gzip, deflate, and Brotli responses are eligible for incremental
decompression when streaming is selected, automatic decompression is enabled,
and cache validation is not `full`. `streaming_first` prefers streaming where
the selected codec and validation requirements are supported; it does not
guarantee that every content encoding streams.

Brotli streaming requires `NGX_HTTP_BROTLI` at compile time (enabled by
default in official release artifacts via `NGX_MARKDOWN_BROTLI_STREAMING=on`).
When `NGX_HTTP_BROTLI` is not defined, Brotli falls back to bounded full-buffer
decompression via the Rust FFI path.

| Encoding | Streaming-eligible conditions | 0.9.1 path |
|----------|-------------------------------|------------|
| identity | streaming selected | streaming conversion |
| deflate RFC 1950 | auto decompress on; cache validation not `full` | streaming decompression |
| deflate RFC 1951 | auto decompress on; cache validation not `full` | streaming decompression |
| gzip | auto decompress on; cache validation not `full` | member-aware streaming decompression |
| Brotli (`br`) | auto decompress on; cache validation not `full`; `NGX_HTTP_BROTLI` defined | streaming decompression |
| Brotli (`br`) | `NGX_HTTP_BROTLI` not defined | bounded full-buffer decompression (Rust FFI) |
| unknown/unsupported | none | existing passthrough/error-policy behavior |

## Routing Decision

When the header filter detects a `Content-Encoding` header on an otherwise
eligible response, the following logic applies:

1. If `markdown_auto_decompress` is **off**, or the encoding is unsupported,
   the response passes through unchanged (no conversion attempted).
2. If `markdown_auto_decompress` is **on** and streaming is selected with cache
   validation not `full`:
   - **Deflate** (zlib-wrapped RFC 1950 or raw RFC 1951) is decompressed
     incrementally after a two-byte framing sniff.
   - **Gzip** is decompressed incrementally with gzip member/trailer validation.
   - **Brotli** is decompressed incrementally (single-stream, trailing-data
     rejection, no-progress guard) when `NGX_HTTP_BROTLI` is defined.
3. Full cache validation selects the bounded full-buffer path for all codecs.
4. Brotli without `NGX_HTTP_BROTLI` defined routes to bounded full-buffer
   decompression via the Rust FFI path.
5. Uncompressed responses continue to be eligible for streaming conversion as
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
  │         ├─ gzip, deflate, or Brotli (compiled) + streaming/cache gates pass
  │         │    └─ Incremental decompression → streaming conversion
  │         ├─ Brotli (not compiled) or full cache validation
  │         │    └─ Bounded full-buffer decompression → conversion
  │         └─ unknown encoding
  │              └─ Passthrough/error-policy behavior
  │
  └─ No Content-Encoding
       └─ Eligible for streaming conversion
```

## Lifecycle and Decompression-Bomb Safety

Both paths enforce `markdown_decompress_max_size`. Streaming accounting is
response-wide: a gzip member reset does not reset the budget. A gzip
`Z_STREAM_END` completes one member, so remaining bytes in the same chunk or a
later chunk begin another member. Finalization succeeds only at a complete
member boundary; a truncated final member is rejected.

**Deflate trailing-data integrity**: deflate (zlib-wrapped RFC 1950 or raw
RFC 1951) does not support concatenated members. A complete deflate stream
must consume every byte of the compressed payload. If `Z_STREAM_END` is
reached with `avail_in > 0`, the remaining bytes are trailing data and the
response is rejected as `FORMAT_ERROR` rather than silently truncated. The
same applies to non-empty chunks arriving after the deflate stream has
already finished: empty chunks remain a safe no-op, but any non-empty
subsequent input is classified as trailing data and rejected. Gzip is
exempt from this constraint because it supports concatenated members.

If decompressed output exceeds the limit, decompression terminates immediately
and the configured `markdown_error_policy` applies before commit:

- **pass** (default): original compressed response served to client unchanged.
- **fail_closed**: 502 Bad Gateway returned.

After streaming output is committed, the existing post-commit safe-finish or
abort behavior applies; the module does not attempt impossible original-body
replay. Downstream `NGX_AGAIN` suspends delivery without changing compressed
source ownership, so remaining input is retained and consumed exactly once on
resume.

## Rationale

The 0.9.1 boundary is based on validated decoder lifecycles:

- Operates within bounded memory (no full-response buffering).
- Handles chunk boundaries that may split compressed frames.
- Enforces the decompression budget incrementally without needing to see the
  full decompressed output upfront.
- Preserves backpressure semantics (NGX_AGAIN handling) while decompression
  state is in-flight.

- Deflate has deterministic RFC 1950/RFC 1951 framing selection before input
  is irreversibly consumed.
- Gzip uses zlib's gzip wrapper plus member-aware reset, cumulative budget,
  truncation, backpressure, and terminal-once validation.
- Brotli uses the official `BrotliDecoderDecompressStream` C API with
  single-stream semantics (no concatenated members), trailing-data rejection,
  truncation detection, no-progress guard, and the same cumulative budget
  enforcement as gzip/deflate.

## Relevant Directives

| Directive | Role in Compression Strategy |
|-----------|------------------------------|
| `markdown_auto_decompress` | Controls whether the module attempts decompression at all. Default: `on`. When off, compressed responses pass through unconverted. |
| `markdown_decompress_max_size` | Maximum decompressed output size. Prevents decompression bombs. Default: inherits `markdown_limits memory=<size>`. |

## Operator Guidance

- **Uncompressed upstreams**: No action needed. Streaming works normally.
- **Gzip/deflate upstreams, streaming desired**: Use `streaming_first`, keep
  `markdown_auto_decompress on`, and avoid `markdown_cache_validation full`.
- **Brotli upstreams, streaming desired**: Same as gzip/deflate — use
  `streaming_first` with `markdown_auto_decompress on`. Brotli streaming is
  active in official release artifacts. Custom builds must have `libbrotlidec`
  available (see Build Compatibility below).
- **Brotli upstreams, Brotli-disabled build**: Responses are routed to bounded
  full-buffer decompression via the Rust FFI path. No streaming TTFB benefit.
- **Budget tuning**: Set `markdown_decompress_max_size` to a value that
  accommodates your largest legitimate compressed responses while still
  protecting against decompression bombs.

## Build Compatibility

Brotli streaming requires `libbrotlidec` at build time and runtime. The
`NGX_MARKDOWN_BROTLI_STREAMING` environment variable controls detection:

- `on` (default in release artifacts): probe and link; fail if missing.
- `auto`: probe silently; enable if available, fall back to full-buffer if not.
- `off`: skip probing; Brotli uses bounded full-buffer only.

When `markdown_cache_validation full` is set, all codecs (including Brotli)
route to bounded full-buffer decompression regardless of streaming preference.

## Related Documentation

- [AUTOMATIC_DECOMPRESSION.md](AUTOMATIC_DECOMPRESSION.md) — decompression behavior and error handling
- [DECOMPRESSION.md](DECOMPRESSION.md) — Budget enforcement and error categories
- [../guides/CONFIGURATION.md](../guides/CONFIGURATION.md) — Directive syntax and defaults
- [../guides/streaming-rollout-cookbook.md](../guides/streaming-rollout-cookbook.md) — Streaming rollout guidance

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-18 | Kang | Promoted Brotli from bounded full-buffer to streaming decompression path; updated routing table, flowchart, rationale, and operator guidance; replaced Deferred Work with Build Compatibility section |
| 0.9.1 | 2026-07-17 | Kang | Document deflate trailing-data integrity: complete input consumption required, trailing bytes after Z_STREAM_END rejected as FORMAT_ERROR, gzip concatenated members remain supported |
| 0.9.1 | 2026-07-14 | Codex | Document gzip plus zlib/raw-deflate streaming routing, gzip member lifecycle, and bounded Brotli full-buffer boundary |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.8.0 | 2026-06-16 | Kang | Initial v0.8.0 streaming compression strategy and security enforcement guidance |
