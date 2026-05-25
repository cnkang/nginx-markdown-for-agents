# Automatic Decompression

## Purpose

When an upstream service or CDN returns compressed HTML (`gzip`, `deflate`, or `br`),
the module must decompress before HTML-to-Markdown conversion. Without this step,
the Rust converter receives compressed bytes and conversion fails.

Automatic decompression is the built-in fallback path for this scenario.

## Behavior Summary

- Detects upstream `Content-Encoding` in the header filter.
- Decompresses in the body filter before conversion when needed.
- Supports `gzip` and `deflate` via zlib.
- Supports `br` when Brotli support is compiled in.
- Uses a fast path for uncompressed responses (no decompression work).
- Applies `markdown_on_error` strategy on decompression failures.

## Request Flow

```text
Upstream response (possibly compressed HTML)
  -> Header filter: detect Content-Encoding
  -> Body filter: decompress if needed
  -> Rust converter: HTML -> Markdown
  -> Response headers/body updated for Markdown variant
```

## Interface Surface

Public user-facing docs currently describe automatic decompression as built-in behavior in the conversion path. This page documents the runtime flow and safeguards, not rollout guidance.

For deployment and troubleshooting guidance, use:

- `docs/guides/CONFIGURATION.md`
- `docs/guides/OPERATIONS.md`
- `docs/architecture/REQUEST_LIFECYCLE.md`

## Failure Handling

Unsupported compression formats:

- Treated as non-convertible for decompression.
- Logged as warnings.
- Preserved graceful behavior (no crash path).

Decompression failures (corrupt data, resource limits, system errors):

- Categorized and logged.
- Counted in decompression metrics.
- Controlled by `markdown_on_error`:
  - `pass` returns the original eligible HTML response (fail-open).
  - `reject` propagates an error response (fail-closed).

## Safety and Resource Controls

- Decompressed output is bounded by `markdown_max_size`.
- Input/output buffers are validated before use.
- Memory is allocated from request pools and cleaned automatically.
- Error paths perform structured cleanup.

## Observability

Decompression-specific counters are exposed through the module metrics endpoint:

- `decompressions_attempted`
- `decompressions_succeeded`
- `decompressions_failed`
- `decompressions_gzip`
- `decompressions_deflate`
- `decompressions_brotli`

Expected log patterns include decompression detection, success, and failure
with reason classification.

## Performance Notes

- Uncompressed responses stay on the fast path.
- Decompression adds overhead only when upstream content is compressed.
- In typical deployments this overhead is smaller than total conversion cost,
  but it depends on content size and compression ratio.

## Testing References

For decompression-specific validation coverage and runnable commands:

- `docs/testing/DECOMPRESSION_TESTS.md`
- `docs/testing/INTEGRATION_TESTS.md`
- `docs/testing/E2E_TESTS.md`

For operational troubleshooting:

- `docs/guides/OPERATIONS.md`


## Resource Budgets (v0.7.0)

The `markdown_decompress_max_size` directive controls the maximum decompressed
output size independently from `markdown_max_size`. This prevents memory
exhaustion from highly compressible (zip-bomb) upstream content.

- **Default**: inherits `markdown_max_size` when not explicitly set
- **Directive**: `markdown_decompress_max_size <size>;`
- **Error code**: `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` (9)
- **Error category**: `NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT`
- **Metric**: `decompressions_failed` (incremented on budget exceeded)

When decompressed output exceeds the budget, decompression terminates
immediately. All auxiliary buffers are freed on every exit path.
The `markdown_on_error` policy determines whether the original compressed
response is served (fail-open) or an error is returned (fail-closed).

## Decompression Error Categories (v0.7.0)

Decompression failures are classified into specific error codes that map
to the `ConversionError` enum in Rust and are categorized in C:

| Rust Error Variant | FFI Code | C Error Category | Description |
|--------------------|----------|-------------------|-------------|
| `DecompressionBudgetExceeded` | 9 | `RESOURCE_LIMIT` | Decompressed output exceeds `decompress_max_size` |
| `ConversionError::Timeout` | 3 | `RESOURCE_LIMIT` | Decompression timed out |
| `ConversionError::MemoryLimit` | 4 | `RESOURCE_LIMIT` | Memory allocation during decompression exceeded limit |
| `ConversionError::Parse` | 1 | `CONVERSION` | Invalid compressed data (corrupt gzip/deflate/brotli) |

The `ngx_http_markdown_classify_error()` function maps FFI error codes to
the three-level error category enum (`CONVERSION`, `RESOURCE_LIMIT`,
`SYSTEM`), ensuring correct Prometheus counter routing and log annotation.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.7.0 | 2026-05-17 | Kang | Added Resource Budgets and Error Categories sections for v0.7.0 |
