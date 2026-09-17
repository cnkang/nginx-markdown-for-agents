# Brotli E2E Test Fixtures

Brotli-compressed test data for the streaming decompression E2E test suite.

## Fixture Inventory

The Brotli payloads are regenerable build products, not source. The
regenerator stays in the repository, and the compressed `.br` outputs are
created on demand. They are not committed, so a checkout carries no binary
fixture that can drift from its generator.

| File | Description | Committed |
|------|-------------|-----------|
| `small.md` | Source Markdown (small document) | yes |
| `large.md` | Source Markdown (large document, ~68 KiB) | yes |
| `generate-brotli-fixtures.sh` | Regenerator for every `.br` payload | yes |
| `small.md.br` | Valid Brotli-compressed small Markdown | no |
| `large.md.br` | Valid Brotli-compressed large Markdown | no |
| `trailing-garbage.md.br` | Valid Brotli stream + 16 trailing garbage bytes | no |
| `truncated.md.br` | First half of `large.md.br` (incomplete stream) | no |

## Purpose

- **small.md.br**: Verifies basic streaming decompression correctness for
  payloads under one NGINX buffer (< 4096 bytes decompressed).
- **large.md.br**: Exercises streaming across multiple NGINX buffer boundaries
  (> 64 KiB decompressed). Used for TTFB comparison tests.
- **trailing-garbage.md.br**: Triggers `NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR`
  with diagnostic reason `brotli_trailing_data`.
- **truncated.md.br**: Triggers `NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT` at
  EOF when the decoder has not reached `BROTLI_DECODER_RESULT_SUCCESS`.

The active Brotli streaming E2E scenario (`tools/e2e-harness`, scenario
`brotli-streaming`) and the C unit tests build their compressed payloads in
memory, so no test reads these files from disk. The set below stays available
for manual inspection and future fixture-based coverage.

## Regeneration

```bash
tests/corpus/brotli/generate-brotli-fixtures.sh
```

Requires the `brotli` CLI (`brew install brotli` or system package).

The script is deterministic: the same source files reproduce the same bytes
for every `.br` output. Do not commit the generated `.br` files. Regenerate
them locally when a test or an inspection needs them.
