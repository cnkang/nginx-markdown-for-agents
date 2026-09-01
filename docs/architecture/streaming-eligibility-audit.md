# Streaming Eligibility Audit (0.9.2)

This is the current implementation audit for the frozen release contract.
It records observable gates and their owning surfaces. Internal heuristic
thresholds are deliberately not configuration keys.

## Eligibility order

The request must pass all of these gates before the module selects streaming
conversion:

1. The request method, status, range behavior, and response content type are
   eligible for Markdown conversion.
2. `markdown_filter` and `markdown_accept` allow conversion.
3. `markdown_streaming` is not `off`.
4. `markdown_cache_validation` does not require complete output before
   the module commits headers.
5. The response is not listed by `markdown_stream_excluded_types`.
6. If compressed, `markdown_auto_decompress` is `on` and the selected build
   path supports the encoding.
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
| Decompression safety | `markdown_limits decompressed_size=` and `decompression_ratio=` | Response-wide decoder budget |
| Time bounds | `markdown_limits conversion_timeout=` and `parser_timeout=` | C/Rust timeout checks |
| Concurrency | `markdown_limits max_inflight=` | Per-worker inflight guard |

## Compressed response routing

Gzip and both supported deflate framings use the streaming decoder when the
streaming gates pass. Brotli uses the streaming decoder only when the
build compiles the feature. Otherwise it uses the bounded full-buffer Rust FFI path.
Unsupported encodings pass through or follow the configured error policy.

Every decoder has a terminal success or failure event. Gzip member resets do
not reset the response-wide decompression budget. The module rejects
truncated final input rather than treating it as a successful partial response.

## Commit and backpressure boundary

Before the module commits headers, a replay or resource failure can fall back to
the bounded full-buffer path when the error policy permits it. After commit,
the original body cannot replay: the module starts safe-finish or aborts
the response according to the terminal error path.

`NGX_AGAIN` suspends delivery and preserves ownership of pending buffers. It
does not increment delivery counters, clear terminal latches, or finalize the
request. The module records delivery only after downstream accepts the terminal
converted buffer. It treats `NGX_AGAIN` as a suspension, not delivery.

## Evidence surfaces

- Effective request configuration: diagnostics JSON, including provenance.
- Terminal decisions: `nginx_markdown_requests_total`.
- Engine selection: `nginx_markdown_conversion_attempts_total`.
- Successful delivery: `nginx_markdown_conversion_deliveries_total`.
- Streaming transitions: `nginx_markdown_streaming_events_total`.
- Decoder terminal events: `nginx_markdown_decompression_events_total`.
- Bounded resource state: the diagnostics-only in-flight counter (not a
  Prometheus family) and the `markdown_limits` configuration.

The drift gate checks the public metric registry and diagnostics schema
release gates. This document must remain explanatory and must not introduce a
second configuration or metric vocabulary.
