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
- html5ever's `discard_bom` flag strips U+FEFF at the start of **every**
  `feed()` call, not just the first.  When a BOM's lead byte (0xEF) is split
  into `utf8_tail` by `split_utf8_tail` and reassembled at the start of the
  next `feed()`, html5ever strips it — diverging from single-chunk conversion
  where the same BOM is mid-stream and preserved.  The streaming tokenizer
  must set `discard_bom: false` and strip the stream-start BOM once in the
  converter (after `utf8_tail` reassembly, so a split BOM is detected as a
  complete 3-byte unit).  The `bom_stripped` flag must not be set prematurely
  when the effective bytes start with 0xEF but are shorter than 3 bytes —
  defer until the next chunk reassembles the full sequence.

---

### 44. Streaming decompression codec and member lifecycle consistency
Historical issues: e76c1584, 13189d71, b9e5fe4d.

Required:
- Supported streaming content codings must match production routing and test
  payload formats.  In 0.9.1, gzip and deflate are streaming-eligible under
  the configured decompression/cache gates; Brotli remains on the bounded
  full-buffer path.
- Codec-specific lifecycle state must survive arbitrary NGINX input chunk
  boundaries and downstream backpressure resumes.  Downstream `NGX_AGAIN`
  must not imply that compressed source input was consumed or may be advanced.
- A gzip `Z_STREAM_END` completes one gzip member, not necessarily the HTTP
  response.  Reset the gzip inflater while preserving remaining `avail_in`,
  accept a boundary exactly between feeds, and consume later members exactly
  once.  Response finalization succeeds at a complete member boundary and
  rejects an incomplete final member.
- Decompression size accounting is response-wide.  Inflater reset at a gzip
  member boundary must not reset `total_decompressed` or independently grant
  another `max_decompressed_size` budget.
- Streaming decompression must correctly handle both deflate formats.
  The streaming decompressor defers `inflateInit2` until the first 2
  bytes arrive, then sniffs the zlib wrapper:
  - zlib-wrapped (RFC 1950, RFC 9110-compliant): `windowBits = 15`
    (`MAX_WBITS`)
  - raw deflate (RFC 1951): `windowBits = -15` (`-MAX_WBITS`)
  Mixing the two formats without sniffing causes silent data corruption
  or decompression failures on chunk boundaries.  The buffered path
  retries on format mismatch; the streaming path cannot retry once
  chunks are consumed, so the sniff is mandatory.
- Truncated gzip members and deflate streams (either deflate format) must be
  explicitly rejected
  with a budget or integrity error, not silently accepted.  When
  `inflate()` returns `Z_BUF_ERROR` or `Z_DATA_ERROR` on a terminal
  chunk, the decompressor must propagate a `DECOMP_CATEGORY_TRUNCATED`
  error rather than returning partial output.
- Test harnesses that produce compressed payloads for streaming
  decompression tests must use the correct deflate format matching
  the test intent (raw deflate: `windowBits = -15`; zlib-wrapped:
  `windowBits = 15`).  Mismatched compression modes between test
  payload and production decompressor produce false passes or false
  failures.
- When the decompression implementation is shared between full-buffer and
  streaming,
  both paths must handle the same deflate formats.  If full-buffer uses
  `ngx_http_markdown_decompress_gzip`, the streaming path must independently
  configure gzip framing and sniff both deflate formats; do not assume the
  two paths share format configuration or member lifecycle.

- `Z_OK` and `Z_BUF_ERROR` have distinct semantics in `inflate()`:
  `Z_OK` means inflate made progress (consumed input and/or produced
  output); `Z_BUF_ERROR` means no progress was made.  When the output
  buffer is exhausted (`avail_out == 0`), both codes are recoverable by
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
- Verify streaming decompression sniffs the zlib header and selects
  `MAX_WBITS` (zlib-wrapped) or `-MAX_WBITS` (raw deflate).
- Verify gzip concatenated-member tests cover one feed, a boundary between
  feeds, a boundary inside a feed, a truncated later member, and cumulative
  response budget enforcement.
- `grep -rn 'TRUNCATED\|truncated.*\(gzip\|deflat\)\|Z_BUF_ERROR\|Z_DATA_ERROR\|no.progress' components/rust-converter/src/ components/nginx-module/src/`
- Verify truncated-stream rejection propagates a budget/integrity error.
- Verify the no-progress guard detects `Z_BUF_ERROR` with no state change.
- `make test-rust` — streaming decompression tests cover both deflate
  formats and truncated-stream rejection.
- `make test-nginx-unit` — C unit tests cover the no-progress guard via
  `TEST_INFLATE_MODE_FEED_BUF_ERROR_NO_PROGRESS` and gzip member lifecycle.
- `make verify-chunked-native-e2e-smoke` — native gzip streaming exercises
  production routing, backpressure/resume, exact output equivalence, and
  pre-/post-commit truncation behavior.
