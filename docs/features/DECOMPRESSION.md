# Decompression Budget and Error Handling (v0.7.0)

This document describes the bounded decompression and error classification
features introduced in v0.7.0. For the base automatic decompression behavior,
see [AUTOMATIC_DECOMPRESSION.md](AUTOMATIC_DECOMPRESSION.md).

## Bounded Decompression

The `markdown_decompress_max_size` directive limits decompressed output size
independently from `markdown_max_size`, preventing zip-bomb attacks and
unbounded memory growth.

### Configuration

| Directive | Syntax | Default | Context |
|-----------|--------|---------|---------|
| `markdown_decompress_max_size` | `markdown_decompress_max_size <size>;` | Inherits `markdown_max_size` | http, server, location |

### Example

```nginx
location /api/ {
    markdown_filter on;
    markdown_max_size 10m;
    markdown_decompress_max_size 50m;  # Allow high compression ratios
}
```

### Error Handling

When decompressed output exceeds `markdown_decompress_max_size`:

1. Decompression terminates immediately
2. `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` (code 9) is returned
3. C classifies this as `NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT`
4. `failures_resource_limit` Prometheus counter is incremented
5. `markdown_on_error` policy determines client response (fail-open or fail-closed)

## Error Code Classification

FFI error codes are classified into three categories for metrics and logging:

| Category | Meaning | FFI Codes |
|----------|---------|-----------|
| `CONVERSION` | HTML parse/conversion error | 1 (PARSE), 2 (ENCODING), 5 (INVALID_INPUT) |
| `RESOURCE_LIMIT` | Timeout, memory, or budget exceeded | 3 (TIMEOUT), 4 (MEMORY_LIMIT), 6 (BUDGET_EXCEEDED), 9 (DECOMPRESSION_BUDGET_EXCEEDED), 10 (PARSE_TIMEOUT), 11 (PARSE_BUDGET_EXCEEDED) |
| `SYSTEM` | Internal/system error | 99 (INTERNAL) |

Streaming-specific codes (7=FALLBACK, 8=POST_COMMIT) are feature-gated.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial document for v0.7.0 decompression budget and error classification |
