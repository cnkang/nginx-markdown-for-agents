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
- Flush charset decoders at EOF (`last=true`) so trailing buffered bytes get emitted or reported.
- Do not rely on blanket lossy conversion before handling chunk-tail semantics.
- When post-commit wrappers re-map errors, preserve original error classification/code for downstream handling and metrics.
- html5ever's `discard_bom` flag strips U+FEFF at the start of **every**
  `feed()` call, not just the first.  When a BOM's lead byte (0xEF) is split
  into `utf8_tail` by `split_utf8_tail` and reassembled at the start of the
  next `feed()`, html5ever strips it — diverging from single-chunk conversion
  where the same BOM is mid-stream and preserved.  The streaming tokenizer
  must set `discard_bom: false` and strip the stream-start BOM once in the
  converter (after `utf8_tail` reassembly, so a split BOM reads as a
  complete 3-byte unit).  The `bom_stripped` flag must not be set prematurely
  when the effective bytes start with 0xEF but are shorter than 3 bytes —
  defer until the next chunk reassembles the full sequence.

---

### 44. Decompression codec and member lifecycle consistency
Historical issues: e76c1584, 13189d71, b9e5fe4d.

Required:
- Supported streaming content codings must match production routing and test
  payload formats.  In 0.9.1, gzip and deflate are streaming-eligible under
  the configured decompression/cache gates.  Brotli additionally requires a
  successful `libbrotlidec` probe under
  `NGX_MARKDOWN_BROTLI_STREAMING=auto|on`, `off` or an `auto` probe failure
  selects bounded full-buffer decompression instead of defining
  `NGX_HTTP_BROTLI`.  Brotli streaming reuses the same codec/member
  lifecycle invariants as gzip/deflate: the module must reject tail data,
  detect and reject truncated final streams, guard no-progress,
  and keep decompression accounting response-wide.
- Codec-specific lifecycle state must survive arbitrary NGINX input chunk
  boundaries and downstream backpressure resumes.  Downstream `NGX_AGAIN`
  must not imply that compressed source input got consumed or may advance.
- A gzip `Z_STREAM_END` completes one gzip member, not necessarily the HTTP
  response.  Both full-buffer and streaming decoders must consume every
  concatenated member exactly once.  The C fallback resets the inflater while
  preserving remaining `avail_in`, the Rust full-buffer path uses a
  multi-member decoder.  Streaming additionally accepts a boundary exactly
  between feeds.  Response finalization succeeds at a complete member boundary
  and rejects an incomplete final member.
- Decompression size accounting is response-wide.  Inflater reset at a gzip
  member boundary must not reset `total_decompressed` or independently grant
  another `max_decompressed_size` budget.
- The frozen 0.9.2 public contract supports zlib-wrapped deflate (RFC 1950)
  only. The decoder must initialize deflate with `windowBits = 15`
  (`MAX_WBITS`) and must not promise a raw RFC 1951 fallback. Older C
  compatibility tests keep raw probes as historical coverage. Mark those
  probes explicitly and do not use them to define new public behavior.
- Truncated gzip members and zlib-wrapped deflate streams must be
  explicitly rejected
  with a budget or integrity error, not silently accepted.  When
  `inflate()` returns `Z_BUF_ERROR` or `Z_DATA_ERROR` on a terminal
  chunk, the decompressor must propagate a `DECOMP_CATEGORY_TRUNCATED`
  error rather than returning partial output.
- Test harnesses that produce new compressed payloads for streaming
  decompression tests must use zlib-wrapped deflate (`windowBits = 15`).
  Mismatched compression modes between a test payload and the frozen public
  decoder contract produce false passes or false failures.
- When the decompression implementation shares between full-buffer and
  streaming,
  both paths must handle the same public deflate format. If full-buffer uses
  `ngx_http_markdown_decompress_gzip`, the streaming path must independently
  configure gzip framing and `MAX_WBITS` deflate framing. Do not assume the
  two paths share format configuration or member lifecycle.

- `Z_OK` and `Z_BUF_ERROR` have distinct semantics in `inflate()`:
  `Z_OK` means inflate made progress (consumed input and/or produced
  output), `Z_BUF_ERROR` means no progress was made.  When the output
  buffer exhausts (`avail_out == 0`), both codes stay recoverable by
  growing the buffer and retrying.  However, `Z_BUF_ERROR` with available
  output space, remaining input, and no change in `total_out` is an
  unexpected stall (potential format error or malformed stream) — the
  no-progress guard must return an error immediately rather than
  re-calling inflate with the same state (infinite loop).  `Z_OK` with
  `avail_out > 0` simply means more data is available — loop again
  without intervention.  Never merge the two branches into a single
  `if (zrc == Z_OK || zrc == Z_BUF_ERROR)` without first checking
  `avail_out` to distinguish the recoverable stall from the
  normal-progress case (see Rule 31: semantic-equivalence requirement
  for duplicate consolidation).

Verification:
- `grep -rn 'windowBits\|Z_RAW\|inflateInit\|inflateReset\|zlib_header' components/nginx-module/src/ components/rust-converter/src/`
- Verify streaming decompression selects `MAX_WBITS` for the frozen
  zlib-wrapped deflate contract.
- Verify gzip concatenated-member tests cover one feed, a boundary between
  feeds, a boundary inside a feed, a truncated later member, and cumulative
  response budget enforcement.  Full-buffer tests must cover both the default
  Rust FFI decoder and the C fallback, including an empty later member at an
  exact output budget.
- `grep -rn 'TRUNCATED\|truncated.*\(gzip\|deflat\|brotli\)\|Z_BUF_ERROR\|Z_DATA_ERROR\|no.progress' components/rust-converter/src/ components/nginx-module/src/`
- Verify truncated-stream rejection propagates a budget/integrity error.
- Verify the no-progress guard detects `Z_BUF_ERROR` with no state change.
- `make test-rust` — streaming decompression tests cover zlib-wrapped
  deflate and truncated-stream rejection.
- `make test-nginx-unit` — C unit tests cover the no-progress guard via
  `TEST_INFLATE_MODE_FEED_BUF_ERROR_NO_PROGRESS` and gzip member lifecycle.
- `make verify-chunked-native-e2e-smoke` — native gzip streaming exercises
  production routing, backpressure/resume, exact output equivalence, and
  pre-/post-commit truncation behavior.
- `tools/e2e/verify_brotli_streaming_e2e.sh` — native Brotli streaming covers
  arbitrary chunking, full-buffer equivalence, malformed/truncated/trailing
  input, budget fail-open, backpressure, worker survival, and runtime metrics.
