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

## Configuration

Directive:

```nginx
markdown_auto_decompress on | off;
```

Default: `on`

Recommended baseline:

```nginx
http {
    markdown_filter on;
    markdown_on_error pass;
    # markdown_auto_decompress is on by default
}
```

If you disable it (`off`), ensure upstream returns uncompressed HTML for
requests expected to be converted.

## Failure Handling

Unsupported compression formats:

- Treated as non-convertible for decompression.
- Logged as warnings.
- Preserved graceful behavior (no crash path).

Decompression failures (corrupt data, resource limits, system errors):

- Categorized and logged.
- Counted in decompression metrics.
- Controlled by `markdown_on_error`:
  - `pass` returns original content (fail-open).
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
