# Automatic Decompression

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

Gzip and deflate use zlib. Deflate accepts zlib-wrapped and raw RFC 1951
streams, but rejects trailing bytes. Gzip validates trailers and supports
concatenated members while retaining one response-wide budget. Brotli uses the
native streaming decoder when the build compiles `NGX_HTTP_BROTLI` in.
Otherwise it uses the bounded full-buffer path.

`markdown_auto_decompress off;` preserves a compressed response without
conversion. With the default `on`, `markdown_streaming` and
`markdown_cache_validation` control the selected streaming/full-buffer path.

## Safety controls

The `decompressed_size` key bounds the decompressed output:

```nginx
markdown_limits conversion_memory=64m decompressed_size=20m
    decompression_ratio=100 conversion_timeout=30s;
```

`decompressed_size` protects the output allocation and `decompression_ratio`
limits expansion relative to compressed input. Parser and streaming memory
remain independently bounded by `parser_memory` and `streaming_buffer`.
All growing buffers have explicit limits and error paths release auxiliary
storage.

Malformed, unknown, or excessively deep `Content-Encoding` chains follow the
configured `markdown_error_policy`. Only the explicit `pass` policy forwards
the original response. `fail_closed` and status policies reject it.

## Failure behavior

The module classifies decompression failures as budget, format,
truncated-input, or I/O errors. `markdown_error_policy pass` (the default)
returns the original response after the fail-open delivery has succeeded.
`fail_closed` returns the configured error status. A failure after streaming
commit follows the post-commit safe-finish/abort semantics. It does not
attempt to replay an already delivered response.

## Observability

The metrics endpoint is Prometheus text exposition format only. The frozen
decompression family is:

```text
nginx_markdown_decompression_events_total{
  encoding="gzip|deflate|brotli",
  outcome="success|failure",
  reason="ok|budget_exceeded|format_error|truncated_input|io_error"
}
```

The module records successful streaming decompressions when finalization
succeeds, so a decoder that spans body chunks counts exactly once. The
renderer also exposes the frozen request, conversion, streaming, dynconf, and
build families described in [`prometheus-metrics.md`](../guides/prometheus-metrics.md).

## Verification

```bash
make test-e2e-rust
make -C components/nginx-module/tests unit-streaming_decomp_brotli
```

See [`DECOMPRESSION.md`](DECOMPRESSION.md) for error-classification details
and [`CONFIGURATION.md`](../guides/CONFIGURATION.md) for operator syntax.

## Document Updates

| Version | Date | Changes |
| --- | --- | --- |
| 0.9.2 | 2026-08-08 | Non-native-reader writing pass: active voice, removed prose semicolons. |
| 0.9.2 | 2026-08-04 | Align decompression controls and metrics with the frozen release contract. |
