# Streaming Compression Strategy (v0.9.2)

## Purpose

This document describes how the 0.9.2 streaming conversion engine handles
compressed upstream responses, including its relationship to bounded
full-buffer decompression.

## Summary

In 0.9.2, gzip, deflate, and Brotli responses are eligible for streaming
decompression when `markdown_streaming force` or `auto` selects streaming,
automatic decompression turns on, and cache validation is not `full`.
The `auto` route uses an internal bounded size heuristic. It is not an
operator-facing threshold directive.

Brotli streaming requires `NGX_HTTP_BROTLI` at compile time (enabled by
default in official release artifacts via `NGX_MARKDOWN_BROTLI_STREAMING=on`).
When `NGX_HTTP_BROTLI` is not defined, Brotli falls back to bounded full-buffer
decompression via the Rust FFI path.

| Encoding | Streaming-eligible conditions | 0.9.2 path |
|----------|-------------------------------|------------|
| identity | streaming selected | streaming conversion |
| deflate (RFC 1950 zlib-wrapped, raw RFC 1951 fallback) | automatic decompression on; streaming selected; cache validation not `full` | streaming decompression |
| gzip | automatic decompression on; streaming selected; cache validation not `full` | member-aware streaming decompression |
| Brotli (`br`) | automatic decompression on; streaming selected; cache validation not `full`; `NGX_HTTP_BROTLI` defined | streaming decompression |
| Brotli (`br`) | `NGX_HTTP_BROTLI` not defined | bounded full-buffer decompression (Rust FFI) |
| malformed / unknown token / excessive depth | automatic decompression on | `markdown_error_policy` (`pass` / `fail_closed` / `status <code>`); passthrough unchanged when automatic decompression is off |

## Routing Decision

When the header filter detects a `Content-Encoding` header on an otherwise
eligible response, the following logic applies:

1. If `markdown_auto_decompress` is **off**, the response passes through
   unchanged (no conversion attempted).
2. If `markdown_auto_decompress` is **on** and the module selects streaming with cache
   validation not `full`:
   - **Deflate** (zlib-wrapped RFC 1950, with raw RFC 1951 compatibility fallback) uses streaming decompression.
   - **Gzip** uses streaming decompression with gzip member/trailer validation.
   - **Brotli** uses streaming decompression (single-stream, trailing-data
     rejection, no-progress guard) when the build defines `NGX_HTTP_BROTLI`.
3. Full cache validation selects the bounded full-buffer path for all codecs.
4. Brotli without `NGX_HTTP_BROTLI` defined routes to bounded full-buffer
   decompression via the Rust FFI path.
5. A known codec whose streaming decoder is unavailable in the build (for
   example Brotli without `NGX_HTTP_BROTLI`) uses bounded full-buffer
   decompression — it does **not** pass through.
6. A malformed `Content-Encoding` value, an unknown encoding token, or an
   excessively deep chain follows the configured `markdown_error_policy`:
   `pass` forwards the original response unchanged. `fail_closed` and
   `status <code>` reject it. These cases are **not** plain passthrough.
7. Uncompressed responses continue to be eligible for streaming conversion as
   normal.

```text
Upstream response
  │
  ├─ Content-Encoding present?
  │    │
  │    ├─ auto_decompress OFF
  │    │    └─ Passthrough (no conversion)
  │    │
  │    ├─ auto_decompress ON + known codec
  │    │    ├─ gzip, deflate, or Brotli (compiled) + streaming/cache gates pass
  │    │    │    └─ Streaming decompression → streaming conversion
  │    │    ├─ Brotli (not compiled) or full cache validation
  │    │    │    └─ Bounded full-buffer decompression → conversion
  │    │    └─ known codec, streaming decoder unavailable
  │    │         └─ Bounded full-buffer decompression → conversion
  │    │
  │    └─ auto_decompress ON + malformed / unknown / excessive depth
  │         └─ markdown_error_policy (pass → original response; fail_closed/status → reject)
  │
  └─ No Content-Encoding
       └─ Eligible for streaming conversion
```

## Lifecycle and Decompression-Bomb Safety

Both paths enforce `markdown_limits decompressed_size=<size>` and
`decompression_ratio=<N>`. Streaming accounting is response-wide: a gzip
member reset does not reset the budget. A gzip
`Z_STREAM_END` completes one member, so remaining bytes in the same chunk or a
later chunk begin another member. Finalization succeeds only at a complete
member boundary. The module rejects a truncated final member.

**Deflate trailing-data integrity**: zlib-wrapped deflate (RFC 1950) does not
support concatenated members. A complete deflate stream
must consume every byte of the compressed payload. If zlib reaches
`Z_STREAM_END` with `avail_in > 0`, the remaining bytes are trailing data and
the module rejects the response as `FORMAT_ERROR` rather than silently
truncating it. The same applies to non-empty chunks arriving after the
deflate stream has already finished: empty chunks remain a safe no-op, but
any non-empty subsequent input classifies as trailing data and the module
rejects it. Gzip is exempt from this constraint because it supports
concatenated members.

If decompressed output exceeds the limit, decompression terminates immediately
and the configured `markdown_error_policy` applies before commit:

- **pass** (default): original compressed response served to client unchanged.
- **fail_closed**: 502 Bad Gateway returned.

After the module commits streaming output, the existing post-commit
safe-finish or abort behavior applies. The module does not attempt impossible
original-body replay. Downstream `NGX_AGAIN` suspends delivery without
changing compressed source ownership, so remaining input stays retained and
gets consumed exactly once on resume.

## Rationale

The 0.9.2 boundary rests on validated decoder lifecycles:

- Operates within bounded memory (no full-response buffering).
- Handles chunk boundaries that may split compressed frames.
- Enforces the decompression budget incrementally without needing to see the
  full decompressed output upfront.
- Preserves backpressure semantics (NGX_AGAIN handling) while decompression
  state is in-flight.

- Deflate uses the zlib-wrapped RFC 1950 framing and also accepts raw RFC 1951
  framing as a fallback for servers that emit raw deflate. The paths decide
  differently: the **full-buffer path** tries RFC 1950 first and retries the
  same input in raw RFC 1951 mode when RFC 1950 decoding fails with a format
  error before producing any output. The **streaming path** defers decoder
  initialization until the first two bytes arrive, sniffs the zlib header,
  and initializes as zlib-wrapped or raw accordingly. It cannot replay
  consumed chunks, so a stream misclassified by the sniff fails closed with
  a format error instead of retrying.
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
| `markdown_limits decompressed_size=<size>` | Maximum decompressed output size. Prevents decompression bombs. |
| `markdown_limits decompression_ratio=<N>` | Maximum decompression expansion ratio. |
| `markdown_limits streaming_buffer=<size>` | Total per-request streaming working-set and pre-commit replay budget. It is not a transport chunk size. |

## Operator Guidance

- **Uncompressed upstreams**: No action needed. Streaming works normally.
- **Gzip/deflate upstreams, streaming desired**: Use `markdown_streaming force`,
  keep `markdown_auto_decompress on`, and avoid
  `markdown_cache_validation full`.
- **Brotli upstreams, streaming desired**: Same as gzip/deflate — use
  `markdown_streaming force` with `markdown_auto_decompress on`. Brotli streaming is
  active in official release artifacts. Custom builds must have `libbrotlidec`
  available (see Build Compatibility below).
- **Brotli upstreams, Brotli-disabled build**: The module routes responses to bounded
  full-buffer decompression via the Rust FFI path. No streaming TTFB benefit.
- **Budget tuning**: Set `markdown_limits decompressed_size=<size>` and
  `markdown_limits decompression_ratio=<N>` to accommodate legitimate compressed responses
  while still protecting against decompression bombs.

## Build Compatibility

Brotli streaming requires `libbrotlidec` at build time and runtime. The
`NGX_MARKDOWN_BROTLI_STREAMING` environment variable controls detection:

- `on` (default in release artifacts): probe and link. Fail if missing.
- `auto`: probe silently. Enable if available, fall back to full-buffer if not.
- `off`: skip probing. Brotli uses bounded full-buffer only.

When `markdown_cache_validation full` is set, all codecs (including Brotli)
route to bounded full-buffer decompression regardless of streaming preference.

## Related Documentation

- [DECOMPRESSION.md](DECOMPRESSION.md) — decompression behavior, budget enforcement, and error categories
- [../guides/CONFIGURATION.md](../guides/CONFIGURATION.md) — Directive syntax and defaults
- [Rollout Cookbook — Streaming-Focused Rollout](../guides/ROLLOUT_COOKBOOK.md#streaming-focused-rollout)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Deflate framing section now states the two decision paths precisely: full-buffer retries after a zero-output RFC 1950 format error; streaming sniffs the first two bytes and fails closed on misclassification |
| 0.9.2 | 2026-08-12 | Codex | Align the public deflate contract with RFC 1950 zlib-wrapped decoding and mark raw framing as historical compatibility behavior |
| 0.9.1 | 2026-07-18 | Kang | Promoted Brotli from bounded full-buffer to streaming decompression path; updated routing table, flowchart, rationale, and operator guidance; replaced Deferred Work with Build Compatibility section |
| 0.9.1 | 2026-07-17 | Kang | Document deflate trailing-data integrity: complete input consumption required, trailing bytes after Z_STREAM_END rejected as FORMAT_ERROR, gzip concatenated members remain supported |
| 0.9.1 | 2026-07-14 | Codex | Document gzip plus zlib/raw-deflate streaming routing, gzip member lifecycle, and bounded Brotli full-buffer boundary |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.8.0 | 2026-06-16 | Kang | Initial v0.8.0 streaming compression strategy and security enforcement guidance |
