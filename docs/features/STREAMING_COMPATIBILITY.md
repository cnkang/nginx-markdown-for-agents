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
| Conditional requests (304) | ✅ | ⚠️ | Only If-None-Match/ETag validation requires full buffering. In `ims_only` mode a converted response never produces a 304: the stream commit clears the upstream `Last-Modified` (both fields), so source `If-Modified-Since` cannot validate the converted representation. Source IMS still applies to pass-through responses |
| Fail-open (pre-commit) | ✅ | ✅ | Streaming: configurable via `markdown_error_policy` |
| Fail-open (post-commit) | N/A | ⚠️ | Later gzip-member failures finish the remaining Markdown safely (safe finish); only failures where safe completion is impossible abort with truncated output |
| `parser_budget` budget | ✅ | ✅ | Rust parser modeled working-set ceiling (`parser_memory_budget`): enforced by the conservative pre-parse estimate on the full-buffer path and checked continuously on the streaming path |
| `conversion_memory` budget | ✅ | ✅ | Hard cumulative input-size cap shared by buffered and streaming paths; the same value also funds the full-buffer generated-output budget and transient scratch allocations (see [Parser Budget](PARSER_BUDGET.md)) |
| Prometheus metrics | ✅ | ✅ | Additional streaming-specific counters |
| Token estimation header | ✅ | ❌ | Requires full output; not available in streaming |
| Front matter (YAML) | ✅ | ✅ | Emitted in pre-commit phase |
| Noise pruning | ✅ | ✅ | Applied during parsing |
| Decompression (gzip) | ✅ | ✅ | Member-aware; streaming since 0.9.1 |
| Decompression (deflate) | ✅ | ✅ | RFC 1950 zlib-wrapped plus raw RFC 1951 fallback: full-buffer retries as raw after a zero-output format error; streaming detects once on the first two bytes and reports a format error for misclassified streams; streaming since 0.9.1 |
| Decompression (Brotli) | ✅ | ✅ | Requires `NGX_HTTP_BROTLI`; streaming since 0.9.1 |

For an empty wire body, an identity-only `Content-Encoding` chain remains
transparent. A declared non-identity decoder still runs at end of input and
reports truncated input, so a missing encoded body is not accepted as a valid
compressed response.

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
ims_only`, streaming stays compatible with pass-through conditional handling:
the module forwards the upstream `Last-Modified` for responses it does not
convert, and NGINX's standard `If-Modified-Since` handling decides those. A
converted streaming response is different: the stream commit clears the
upstream `Last-Modified`, so no source-IMS 304 can apply to it. The mode
therefore never forces the full-buffer path, and it never produces a 304 for
converted content.

**Known constraint (user-confirmed):** the same URL can therefore
yield an ETag when the full-buffer path serves it and no ETag when the
streaming path does, because a streaming response commits its headers before
the converted body exists. The trigger is the selected path, not a response
size: `markdown_streaming off`, a hard blocker, or an ineligible response keeps
strong validation, while `auto` or `force` that actually streams gives it up.
This is an accepted trade-off of streaming header commitment. A deferred
header commit is out of scope for 0.9.2.

### Fail-open behavior

With `markdown_error_policy pass`, full-buffer conversion errors return the
original HTML. In streaming mode, errors that occur before the module commits
the response to the client (pre-commit) handle the same way. With
`markdown_error_policy fail_closed`, pre-commit errors return the configured
error status and do not pass through the original body. `status N` uses that
explicit status policy.
Errors that occur after headers have already been sent (post-commit) cannot
roll back, and the outcome depends on whether a safe finish is possible.
When a later gzip member fails, the module can still finish safely: it
completes the remaining Markdown, preserving earlier decompressed output and
appending the Markdown closing bytes. When a safe finish is not possible,
the module aborts the conversion without replaying output, and the client
receives a truncated response.

### Token estimation

The `X-Markdown-Tokens` header requires knowing the full output length.
Since streaming sends chunks incrementally, this header is not emitted.

## Deciding Which Mode to Use

Use **full-buffer** when:

- You need ETag-based caching and conditional requests
- Response sizes are moderate (within `markdown_limits conversion_memory=<size>`, the hard cumulative input-size cap shared by both buffered and streaming paths). Both engines use `parser_budget=` as the modeled working-set ceiling for the Rust parser. Streaming additionally uses `streaming_buffer=` for its bounded working/replay storage.
- Downstream consumers require token estimation headers

Use **streaming** when:

- Responses are large and you want bounded memory usage
- Time-to-first-byte matters more than conditional caching
- You accept that post-commit errors that cannot finish safely truncate the
  response (safe-finish failures complete the remaining Markdown)

Use **auto** to prefer streaming for eligible responses. The module selects
the processing path from the policy and hard compatibility constraints, not
a response-size heuristic. The 0.9.2 default is `off` (bounded full-buffer).
`auto` must be written explicitly to opt in.

### Frozen 0.9.2 selection contract

| Policy | Selection |
|--------|-----------|
| `markdown_streaming off` (default) | Always bounded full-buffer conversion. No response streams. |
| `markdown_streaming auto` | Prefer streaming for every response that clears the hard gates. Ineligible responses fall back to bounded full-buffer conversion. Response size takes no part in the decision. |
| `markdown_streaming force` | Require streaming for every compatible response. A combination that can never satisfy it, such as `markdown_streaming force` with `markdown_front_matter on`, is rejected at `nginx -t` time. |

The hard gates apply to every policy: HEAD requests, 304 responses, full
conditional validation, excluded content types, the streaming memory budget,
and front matter (`markdown_front_matter on` routes to full-buffer). No size
threshold and no internal candidate boundary takes part in path selection.

### GFM constructs and the streaming path

`markdown_flavor gfm` selects GitHub Flavored Markdown. Both engines emit the
same representation for the constructs below. The streaming engine falls back to
the full-buffer engine for the ones it cannot stream, instead of producing a
different Markdown document.

| Construct | Streaming | Full buffer |
|-----------|-----------|-------------|
| Tables (`<table>`) | Falls back before commit | GFM table |
| Strikethrough (`<del>`, `<s>`, `<strike>`) | `~~text~~` | `~~text~~` |
| Task list (`<input type="checkbox">`) | `- [x]` / `- [ ]` | `- [x]` / `- [ ]` |
| Embedded content (`<svg>`, `<math>`, `<canvas>`) | Falls back before commit | Converted or skipped |

A fallback before commit is transparent: the client receives the full-buffer
result. After a streaming response has committed its headers, a construct that
requires fallback raises a post-commit error as described above. Under the
default `commonmark` flavor the two strikethrough and task-list constructs
contribute their text without markers, and both engines agree without a
fallback.

## Related Documentation

- [Rollout Cookbook — Streaming-Focused Rollout](../guides/ROLLOUT_COOKBOOK.md#streaming-focused-rollout)
- [Configuration Reference — Streaming Directives](../guides/CONFIGURATION.md)
- [Migration Guide](../guides/MIGRATION-0.8.md)
- [Streaming Observability](streaming-observability.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-09-07 | Kang | Split the post-commit fail-open outcome: later gzip-member failures finish the remaining Markdown safely; only impossible-safe-finish failures abort with truncated output |
| 0.9.2 | 2026-08-24 | Kang | Corrected the parser_budget budget row: the bound covers both paths (full-buffer pre-parse estimate plus streaming enforcement), not streaming only |
| 0.9.2 | 2026-08-19 | Hermes | Document the accepted no-ETag-for-streaming constraint (full-buffer vs streaming path divergence, user-confirmed) |
| 0.9.2 | 2026-08-15 | Hermes | Deflate streaming misclassification reports a format error instead of failing closed |
| 0.9.1 | 2026-07-18 | Kang | Added streaming decompression rows (gzip, deflate, Brotli) to compatibility matrix |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.8.0 | 2026-06-16 | Kang  | Initial feature compatibility matrix |
