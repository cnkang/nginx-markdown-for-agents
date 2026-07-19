# Brotli E2E Test Fixtures

Brotli-compressed test data for the streaming decompression E2E test suite.

## Fixture Inventory

| File | Description | Decompressed Size |
|------|-------------|-------------------|
| `small.md` | Source Markdown (small document) | 583 bytes |
| `small.md.br` | Valid Brotli-compressed small Markdown | — |
| `large.md` | Source Markdown (large document) | ~68 KiB |
| `large.md.br` | Valid Brotli-compressed large Markdown | — |
| `trailing-garbage.md.br` | Valid Brotli stream + 16 trailing garbage bytes | — |
| `truncated.md.br` | First half of `large.md.br` (incomplete stream) | — |

## Purpose

- **small.md.br**: Verifies basic streaming decompression correctness for
  payloads under one NGINX buffer (< 4096 bytes decompressed).
- **large.md.br**: Exercises streaming across multiple NGINX buffer boundaries
  (> 64 KiB decompressed). Used for TTFB comparison tests.
- **trailing-garbage.md.br**: Triggers `NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR`
  with diagnostic reason `brotli_trailing_data`.
- **truncated.md.br**: Triggers `NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT` at
  EOF when the decoder has not reached `BROTLI_DECODER_RESULT_SUCCESS`.

## Regeneration

```bash
tests/corpus/brotli/generate-brotli-fixtures.sh
```

Requires the `brotli` CLI (`brew install brotli` or system package).
