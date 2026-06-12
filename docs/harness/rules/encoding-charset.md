---
domain: encoding-charset
rules: [4, 44]
paths:
  - "components/rust-converter/src/charset/**"
  - "components/rust-converter/src/streaming/**"
  - "components/nginx-module/src/**"
---

## Encoding & Charset

### 4. UTF-8/charset cross-chunk corruption
Historical issues: `0eae34b`, `1b0df51`, `77a46d6`.

Required:
- Preserve incomplete UTF-8 tails across chunk boundaries and prepend to next chunk.
- Flush charset decoders at EOF (`last=true`) so trailing buffered bytes are emitted or reported.
- Do not rely on blanket lossy conversion before handling chunk-tail semantics.
- When post-commit wrappers re-map errors, preserve original error classification/code for downstream handling and metrics.

---

### 44. Streaming decompression deflate semantics consistency
Historical issues: e76c1584, 13189d71, b9e5fe4d.

Required:
- Streaming decompression must use raw deflate (no zlib/gzip wrapper), not
  zlib-wrapped deflate.  The streaming decompressor feeds chunks to
  `inflate()` with `windowBits = -15` (raw deflate).  Mixing zlib-wrapped
  and raw deflate inputs causes silent data corruption or decompression
  failures on chunk boundaries.
- Truncated raw deflate streams must be explicitly rejected with a budget or
  integrity error, not silently accepted.  When `inflate()` returns
  `Z_BUF_ERROR` or `Z_DATA_ERROR` on a terminal chunk, the decompressor
  must propagate a `DECOMP_CATEGORY_TRUNCATED` error rather than returning
  partial output.
- Test harnesses that produce compressed payloads for streaming
  decompression tests must use raw deflate (for example
  `CompressMode::Raw` / `windowBits = -15`), not zlib-wrapped deflate.
  Mismatched compression modes between test payload and production
  decompressor produce false passes or false failures.
- When the decompression path is shared between full-buffer and streaming,
  both paths must use the same deflate format.  If full-buffer uses
  zlib-wrapped (gzip) decompression via `ngx_http_markdown_decompress_gzip`,
  the streaming path must independently enforce raw deflate — do not assume
  the two paths share format configuration.

Verification:
- `grep -rn 'windowBits\|Z_RAW\|inflateInit' components/nginx-module/src/ components/rust-converter/src/`
- Verify streaming decompression uses raw deflate (`windowBits = -15` or
  equivalent).
- `grep -rn 'TRUNCATED\|truncated.*deflat\|Z_BUF_ERROR\|Z_DATA_ERROR' components/rust-converter/src/`
- Verify truncated-stream rejection propagates a budget/integrity error.
- `make test-rust` — streaming decompression tests cover both raw deflate
  and truncated-stream rejection.
