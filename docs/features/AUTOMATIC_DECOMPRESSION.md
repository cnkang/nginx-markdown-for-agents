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
  - **Note**: `Content-Encoding: deflate` is first attempted as zlib-wrapped
    deflate (RFC 1950 + RFC 1951), matching the HTTP/1.1 specification
    (RFC 7230 §4.2.2). If the zlib-wrapped attempt fails with a format
    error, the buffered path automatically retries with raw deflate
    (RFC 1951 only) for compatibility with older servers (Microsoft IIS,
    older Java servlets). The streaming path sniffs the first two bytes and
    supports both zlib-wrapped and raw deflate without replay.
  - Gzip uses streaming gzip framing under the streaming-eligible gates;
    member boundaries may cross feeds, trailers are validated, and a truncated
    final member is rejected.
  - When policy selects full-buffer conversion, both the default Rust FFI
    decoder and the C no-Rust fallback consume every concatenated gzip member,
    reject a truncated later member, and enforce one response-wide output
    budget. Deflate (zlib-wrapped or raw) must completely consume its
    compressed payload; trailing bytes after `Z_STREAM_END` are rejected as
    `FORMAT_ERROR` (deflate does not support concatenated members).
- Supports `br` when Brotli support is compiled in; Brotli remains on bounded
  full-buffer decompression in 0.9.1.
- Uses a fast path for uncompressed responses (no decompression work).
- Applies `markdown_error_policy` strategy on decompression failures.

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
- Controlled by `markdown_error_policy`:
  - `pass` returns the original eligible HTML response (fail-open).
  - `fail_closed` propagates an error response (fail-closed).

## Safety and Resource Controls

- Decompressed output is bounded by `markdown_decompress_max_size` (introduced in v0.7.0).
  When not explicitly set, it inherits the value of `markdown_limits memory=<size>` as a fallback.
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
output size independently from `markdown_limits memory=<size>`. This prevents memory
exhaustion from highly compressible (zip-bomb) upstream content.

- **Default**: inherits `markdown_limits memory=<size>` when not explicitly set
- **Directive**: `markdown_decompress_max_size <size>;`
- **Error code**: `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` (9)
- **Error category**: `NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT`
- **Metric**: `decompressions_failed` (incremented on budget exceeded)

When decompressed output exceeds the budget, decompression terminates
immediately. All auxiliary buffers are freed on every exit path.
The `markdown_error_policy` policy determines whether the original compressed
response is served (fail-open) or an error is returned (fail-closed).

## Decompression Error Categories (v0.7.0)

Decompression failures are classified into specific error codes that map
to the `ConversionError` enum in Rust and are categorized in C:

| Rust Error Variant | FFI Code | C Error Category | Description |
|--------------------|----------|-------------------|-------------|
| `DecompressionBudgetExceeded` | 9 | `resource_limit` | Decompressed output exceeds `decompress_max_size` |
| `ConversionError::Timeout` | 3 | `resource_limit` | Decompression timed out |
| `ConversionError::MemoryLimit` | 4 | `resource_limit` | Memory allocation during decompression exceeded limit |
| `ConversionError::Parse` | 1 | `conversion` | Invalid compressed data (corrupt gzip/deflate/brotli) |

The `ngx_http_markdown_classify_error()` function maps FFI error codes to
| the three-level error category enum (`conversion`, `resource_limit`,
| `system`), ensuring correct Prometheus counter routing and log annotation.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-14 | Codex | Document full-buffer concatenated-gzip handling, later-member truncation rejection, and cumulative budget enforcement |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.7.0 | 2026-05-17 | Kang | Added Resource Budgets and Error Categories sections for v0.7.0 |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
