# Streaming Eligibility Audit (0.9.2)

This is the current implementation audit for the frozen Wave 0–2 contract.
It records observable gates and their owning surfaces; internal heuristic
thresholds are deliberately not configuration keys.

## Eligibility order

The request must pass all of these gates before streaming conversion is
selected:

1. The request method, status, range behavior, and response content type are
   eligible for Markdown conversion.
2. `markdown_filter` and `markdown_accept` allow conversion.
3. `markdown_streaming` is not `off`.
4. `markdown_cache_validation` does not require complete output before the
   headers are committed.
5. The response is not listed by `markdown_stream_excluded_types`.
6. If compressed, `markdown_auto_decompress` is `on` and the encoding is
   supported by the selected build path.
7. The bounded streaming buffers and `markdown_limits max_inflight` permit
   the request.

`auto` applies the internal bounded response-shape heuristic. `force` asks
for streaming after the hard gates above. Neither policy bypasses cache,
encoding, memory, or backpressure safety rules.

## Configuration ownership

| Concern | Current public setting | Enforcement |
|---|---|---|
| Conversion opt-in | `markdown_filter` | NGINX directive and request decision chain |
| Client negotiation | `markdown_accept` | Accept parser and eligibility gate |
| Engine request | `markdown_streaming off\|auto\|force` | Streaming selector |
| Cache semantics | `markdown_cache_validation off\|ims_only\|full` | Header/ETag gate |
| Compressed input | `markdown_auto_decompress` | Encoding detection and decoder dispatch |
| Excluded types | `markdown_stream_excluded_types` | Case-insensitive media-type matcher |
| Streaming memory | `markdown_limits streaming_buffer=` | Bounded working/replay storage |
| Conversion memory | `markdown_limits conversion_memory=` and `parser_memory=` | C/Rust budget enforcement |
| Decompression safety | `decompressed_size=` and `decompression_ratio=` | Response-wide decoder budget |
| Time bounds | `conversion_timeout=` and `parser_timeout=` | C/Rust timeout checks |
| Concurrency | `max_inflight=` | Per-worker inflight guard |

## Compressed response routing

Gzip and both supported deflate framings use the incremental decoder when the
streaming gates pass. Brotli uses the incremental decoder only when the
feature is compiled; otherwise it uses the bounded full-buffer Rust FFI path.
Unsupported encodings pass through or follow the configured error policy.

Every decoder has a terminal success or failure event. Gzip member resets do
not reset the response-wide decompression budget, and truncated final input is
rejected rather than treated as a successful partial response.

## Commit and backpressure boundary

Before headers are committed, a replay or resource failure can fall back to
the bounded full-buffer path when the error policy permits it. After commit,
the original body cannot be replayed: the module starts safe-finish or aborts
the response according to the terminal error path.

`NGX_AGAIN` suspends delivery and preserves ownership of pending buffers. It
does not increment delivery counters, clear terminal latches, or finalize the
request. Delivery is recorded only after downstream accepts the terminal
converted buffer.

## Evidence surfaces

- Effective request configuration: diagnostics JSON, including provenance.
- Terminal decisions: `nginx_markdown_requests_total`.
- Engine selection: `nginx_markdown_conversion_attempts_total`.
- Successful delivery: `nginx_markdown_conversion_deliveries_total`.
- Streaming transitions: `nginx_markdown_streaming_events_total`.
- Decoder terminal events: `nginx_markdown_decompression_events_total`.
- Bounded resource state: `nginx_markdown_inflight_requests` and the
  `markdown_limits` configuration.

The public metric registry and diagnostics schema are checked by the Wave 2
release gates. This document must remain explanatory and must not introduce a
second configuration or metric vocabulary.
