# Decompression

This page is the canonical decompression contract for 0.9.2. It covers the
runtime flow, supported encodings, resource budgets, error classification,
fail-open behavior, and observability. There is no separate automatic
decompression page. This document is the single source for both.

## Overview

When an upstream response carries `Content-Encoding: gzip`, `deflate`, or
`br`, the module decodes it before HTML-to-Markdown conversion. Uncompressed
responses stay on the normal fast path.

## Runtime flow

```text
Content-Encoding header
  -> header filter records the codec
  -> body filter performs bounded full-buffer or streaming decompression
  -> Rust converter receives HTML bytes
  -> Markdown headers/body are emitted
```

Gzip uses zlib gzip framing, and deflate first uses the zlib-wrapped RFC 1950
format required by HTTP. If the full-buffer zlib-wrapped attempt produces a
format error before producing output, the C and Rust full-buffer paths retry
the same bytes as raw RFC 1951 deflate for compatibility with legacy servers
(RFC 2616-era implementations). The streaming C path cannot replay consumed
chunks: it sniffs the first two bytes, selects zlib framing for a valid header
and raw framing otherwise. A raw stream whose prefix happens to look like a
zlib header is therefore reported as a format error in streaming mode rather
than replayed. Deflate rejects trailing bytes. Gzip validates trailers and
supports concatenated members while retaining one response-wide budget. Brotli
build control `NGX_MARKDOWN_BROTLI_STREAMING` (default `auto`) manages the
compile-time probe. A successful probe defines `NGX_HTTP_BROTLI` and enables
the native streaming decoder. Builds without it use the bounded full-buffer
path.

`markdown_auto_decompress off;` preserves a compressed response without
conversion. With the default `on`, `markdown_streaming` and
`markdown_cache_validation` control the selected streaming/full-buffer path.

## Supported encodings

| Encoding | Framing | Streaming-eligible | Notes |
| --- | --- | --- | --- |
| `gzip` | zlib gzip framing | Yes | Validates trailers; supports concatenated members with one response-wide budget |
| `deflate` | zlib-wrapped RFC 1950, raw RFC 1951 fallback | Yes (sniffed) | Full-buffer retries raw on early format error; streaming sniffs first two bytes; rejects trailing bytes |
| `br` | Brotli | Yes when `NGX_HTTP_BROTLI` defined | Otherwise bounded full-buffer via Rust FFI |

## Resource budgets

Decompression limits are keys of `markdown_limits`. There is no standalone
decompression-size directive in the live command registry.

```nginx
location / {
    markdown_filter on;
    markdown_limits conversion_memory=64m decompressed_size=20m
        decompression_ratio=100 conversion_timeout=30s;
}
```

- `decompressed_size` caps the cumulative decompressed output.
- `decompression_ratio` caps expansion relative to compressed input.
- `conversion_memory` bounds the cumulative input bytes accepted for conversion. `parser_memory` bounds the estimated parser working set separately.
- `markdown_error_policy pass` preserves the original response. `fail_closed`
  returns the configured error status.

The module checks the limits in both full-buffer and streaming paths. Gzip member
resets do not reset the response-wide accounting, and truncated final streams
get rejected. Parser and streaming memory remain independently bounded by
`parser_memory` and `streaming_buffer`. All growing buffers have explicit
limits and error paths release auxiliary storage.

## Error classification

The decoder reports four bounded failure reasons:

| Reason | Meaning |
| --- | --- |
| `budget` | Output or expansion limit would be exceeded. |
| `format` | Compressed bytes do not match the advertised codec. |
| `truncated` | The stream ended before its final marker/trailer. |
| `io` | Decoder or buffer I/O failed. |

These failures also participate in the generated reason-code registry through
`decompression_budget_exceeded`, `decompression_format_error`,
`decompression_truncated_input`, and `decompression_io_error`.

Malformed, unknown, or excessively deep `Content-Encoding` chains follow the
configured `markdown_error_policy`. Only the explicit `pass` policy forwards
the original response. `fail_closed` and status policies reject it.

### Empty body with declared encodings

A zero-byte response body for a **recognized, supported `Content-Encoding`
chain** is a legal empty payload (design decision, user-confirmed): there is
nothing to decode, so the chain decoder succeeds with an empty output instead
of classifying the input as truncated. This differs from the single-format
decompressors (`decompress_gzip`/`decompress_deflate`/`decompress_brotli`),
which classify an empty compressed input as `TruncatedInput`. The chain
decoder follows HTTP semantics where an empty body means "no content".
Conversion of an empty body yields an empty Markdown document. Malformed or
unknown chains still follow the configured `markdown_error_policy` exactly as
documented above. The empty-input contract applies only after the chain
passes recognition as supported.

## Fail-open sequence

1. The decoder classifies the error and records the reason.
2. The module logs the decision and applies `markdown_error_policy`.
3. For `pass`, the module delivers the original response through the normal
   downstream path. `NGX_AGAIN` is not counted as delivery.
4. For `fail_closed`, the module finalizes the request with the configured status.

After streaming commit, the module uses post-commit safe-finish or abort
semantics and does not attempt impossible replay of already-sent bytes. A
safe-finish can preserve valid Markdown from earlier gzip members and complete
one converted delivery after a later member fails. If safe-finish cannot
complete, the abort path terminates the response without replay.

## Observability

The metrics endpoint is Prometheus text exposition format only. The frozen
decompression family is:

```text
nginx_markdown_decompression_events_total{
  encoding="gzip|deflate|brotli",
  outcome="success|failure",
  reason="budget_exceeded|format_error|io_error|ok|truncated_input"
}
```

The `ok` label is a metric-only success sentinel. Metric labels keep the
short names above. Each failure label maps to exactly one canonical
reason-code registry key:

| Metric label | Reason registry key |
|---|---|
| `budget_exceeded` | `decompression_budget_exceeded` |
| `format_error` | `decompression_format_error` |
| `truncated_input` | `decompression_truncated_input` |
| `io_error` | `decompression_io_error` |

The module counts them after the decoder
classifies the error and before it handles fail-open or fail-closed responses.
This makes event totals independent of whether the original body is ultimately
delivered. The module records successful streaming decompressions when
finalization succeeds, so a decoder that spans body chunks counts exactly once.
The renderer also exposes the frozen request, conversion, streaming, dynconf,
and build families described in [`prometheus-metrics.md`](../guides/prometheus-metrics.md).

## Verification

```bash
make test-e2e-rust
make -C components/nginx-module/tests unit-gzip_deflate_decompression
make -C components/nginx-module/tests unit-streaming_decomp_brotli
```

The machine-readable metrics registry and public-surface inventory are the
authoritative sources for exact labels and family membership. See
[`CONFIGURATION.md`](../guides/CONFIGURATION.md) for operator syntax.

## Document Updates

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.9.2 | 2026-08-28 | Hermes | Merged AUTOMATIC_DECOMPRESSION.md into this canonical page (runtime flow, encodings, empty-body contract, fail-open, observability) |
| 0.9.2 | 2026-08-19 | Hermes | Document the empty-body-with-declared-encodings contract (empty payload is legal, distinct from single-format truncated-input semantics) |
| 0.9.2 | 2026-08-15 | Hermes | Document Brotli build control NGX_MARKDOWN_BROTLI_STREAMING and the NGX_HTTP_BROTLI probe outcome |
| 0.9.2 | 2026-08-08 | Hermes | Non-native-reader writing pass: active voice, removed prose semicolons. |
| 0.9.2 | 2026-08-04 | Hermes | Align decompression controls and metrics with the frozen release contract. |
