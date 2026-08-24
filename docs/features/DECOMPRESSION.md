# Decompression Budget and Error Handling

This page describes the bounded decompression contract in 0.9.2. The runtime
flow appears in [`AUTOMATIC_DECOMPRESSION.md`](AUTOMATIC_DECOMPRESSION.md).

## Configuration

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
- `conversion_memory` remains the overall conversion working-memory cap.
- `markdown_error_policy pass` preserves the original response. `fail_closed`
  returns the configured error status.

The module checks the limits in both full-buffer and streaming paths. Gzip member
resets do not reset the response-wide accounting, and truncated final streams
get rejected.

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

## Prometheus contract

The endpoint emits Prometheus text format only. A single frozen family
represents every decompression event:

```text
nginx_markdown_decompression_events_total{
  encoding="gzip|deflate|brotli",
  outcome="success|failure",
  reason="budget_exceeded|format_error|io_error|ok|truncated_input"
}
```

The module counts the `ok` reason once with the `success` outcome after a decoder has completely
finalized. It counts failures at the error classification point, before
fail-open or fail-closed response handling. This makes event totals
independent of whether the original body is ultimately delivered.

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

## Verification

```bash
make test-e2e-rust
make -C components/nginx-module/tests unit-gzip_deflate_decompression
make -C components/nginx-module/tests unit-streaming_decomp_brotli
```

The machine-readable metrics registry and public-surface inventory are the
authoritative sources for exact labels and family membership.
