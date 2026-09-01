# ADR-0024: Brotli Streaming Decompression

## Status

Accepted — Supersedes ADR-0021 Brotli full-buffer section

## Context

0.9.1 routes gzip and deflate through the streaming decompression pipeline
(ADR-0021). Brotli was explicitly kept on the bounded full-buffer path because
decoder-state and memory validation were not yet proven.

The streaming decompression skeleton already exists in
`ngx_http_markdown_streaming_decomp_impl.h`: `BrotliDecoderState`, I/O cursors
(`brotli_next_in`, `brotli_avail_in`, `brotli_next_out`, `brotli_avail_out`),
create/cleanup/feed/finish paths guarded by `#ifdef NGX_HTTP_BROTLI`. The
production routing function `ngx_http_markdown_route_streaming_compression()`
in `ngx_http_markdown_request_impl.h` is the codec-selection boundary.

RFC 7932 defines Brotli as a single-stream format with no concatenated members,
unlike gzip. This simplifies lifecycle: one `BROTLI_DECODER_RESULT_SUCCESS`
per response, no member resets, and mandatory trailing-data rejection.

The four existing decompression error codes
(`NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR`, `TRUNCATED_INPUT`,
`BUDGET_EXCEEDED`, `IO_ERROR`) already cover the Brotli failure taxonomy.
Five diagnostic reasons (`brotli_decode_error`, `brotli_trailing_data`,
`brotli_truncated_input`, `brotli_no_progress`, `brotli_budget_exceeded`)
provide operator-level log detail.

## Decision

Promote Brotli from bounded full-buffer to the existing streaming
decompression path. The public routing change remains one compile-time-guarded
codec expansion, while focused internal helpers enforce Brotli-specific error
classification, completion, and cleanup invariants.

### Routing Gate Expansion

Add `NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI` to the streaming-eligible condition
in `ngx_http_markdown_route_streaming_compression()`:

```c
    if (ctx->decompression.type
        == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE
        || ctx->decompression.type
           == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
#ifdef NGX_HTTP_BROTLI
        || ctx->decompression.type
           == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
#endif
        )
```

The same four-condition gate applies uniformly: `markdown_auto_decompress` ON,
streaming selected, `markdown_cache_validation` not `full`, and a supported
codec.
The change introduces no new public directive or runtime policy branch. The
configure-time `NGX_MARKDOWN_BROTLI_STREAMING=auto|on|off` environment input
controls whether the build includes the optional decoder. It is not a
request-time directive, variable, or runtime default.

### No New Public Directives

No new NGINX runtime directive, configuration parameter, or runtime
configuration default gets introduced. The existing
`markdown_decompress_max_size` budget
applies identically to Brotli. The existing `markdown_error_policy` governs
fail-open/reject behavior.

### Decoder Workspace Budget

The module creates the Brotli decoder with a custom allocator. The allocator
charges live allocations to an atomic counter in the main configuration and
caps the total at `NGX_HTTP_MARKDOWN_BROTLI_WORKSPACE_LIMIT` (32 MiB) per
worker. Concurrent requests in that worker share the counter, and the
allocator releases each reservation from its allocation header before NGINX
reclaims the request pool.

Reservation failures fall into three cases based on decoder input consumption, tracked independently from HTTP/output commit:

- **Before input consumption (pre-commit)**: The decoder has not yet processed any compressed chunk. The allocator reports an allocation failure and the module follows the existing pre-commit replay strategy (same as other decompression failures before output commitment).
- **After input consumption but before response commit (intermediate state)**: The decoder has consumed one or more compressed chunks, but the module has not yet committed the HTTP response headers (`ngx_http_send_header()` not called). The workspace expansion failure triggers the configured pre-commit error policy (`markdown_error_policy`): `pass` replays the original upstream body, `fail_closed` or `status <code>` returns the configured error. The module cannot replay the consumed compressed input, but the original upstream response is still available for replay via the pre-commit buffer.
- **After response commit (post-commit)**: The decoder has consumed input and `ngx_http_send_header()` has returned `NGX_OK`. The workspace expansion failure places the decoder in a terminal abort state. The module does not re-feed the consumed chunk. Existing terminal/error state fields enforce this non-retryable invariant (see the State Machine section below).

Decoded output remains governed separately by the cumulative
`markdown_decompress_max_size` budget.

### Error Code Semantics — Two-Layer Model

The decompressor implements a two-layer error model:

**Layer 1 — C return codes (numeric constants):**

| Constant | Meaning |
|----------|---------|
| `NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR` | Malformed data, trailing bytes, FORMAT-class decoder errors (-1 to -17) |
| `NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT` | Incomplete stream at EOF |
| `NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED` | Output exceeds configured limit |
| `NGX_ERROR` + ALLOCATION origin | `BrotliDecoderCreateInstance` NULL, `ngx_alloc` NULL, cleanup failure, Brotli `ERROR_ALLOC_*` codes (-21 to -30) |
| `NGX_ERROR` + INTERNAL origin | Brotli codes -18 to -20, -31, unknown/out-of-range values, invariant violations |

**Layer 2 — Diagnostic reasons (logged strings):**

| Reason | Condition |
|--------|-----------|
| `brotli_decode_error` | `BROTLI_DECODER_RESULT_ERROR` with FORMAT-class code |
| `brotli_trailing_data` | Non-empty input after `SUCCESS`, or input after `finished` flag |
| `brotli_truncated_input` | EOF without `BrotliDecoderIsFinished()` |
| `brotli_no_progress` | Decoder stall (no input consumed, no output produced) |
| `brotli_budget_exceeded` | Cumulative output exceeds `markdown_decompress_max_size` |

The ALLOCATION and INTERNAL origins (Layer 1 rows above) log
`class=allocation`/`class=internal` with `category=system` and map to the
canonical `decompression_error` reason in the reason registry (allowed origin
`internal`). They never report corrupt input (the `format` origin).

**Three-way error classifier (frozen):**

```c
ngx_http_markdown_brotli_classify_error(BrotliDecoderErrorCode code):
  FORMAT:     code >= -17 && code <= -1
  ALLOCATION: code <= -21 && code >= -30
  INTERNAL:   everything else (-18..-20, -31, unknown/out-of-range)
```

The classifier uses integer-range comparisons only (no named enum constants
that may be absent in Brotli 1.0.9 / Ubuntu 22.04). `brotli_loop()` propagates
all typed `NGX_HTTP_MARKDOWN_DECOMP_*` return codes from `brotli_step()`
without folding to generic `NGX_ERROR`.

### State Machine — Feed/Finish Lifecycle

```
                 +---------+
                 |  IDLE   |  (BrotliDecoderState created)
                 +----+----+
                      |
                      v  feed(in_data, in_len)
                +-----+-----+
                |  DECODING  |  brotli_loop → brotli_step
                +-----+-----+
                      |
          +-----------+-----------+
          |           |           |
          v           v           v
    NEEDS_MORE    SUCCESS     ERROR/BUDGET
      INPUT                   /TRUNCATED
          |           |           |
          v           v           v
    return NGX_OK   set          return typed
    (await next     finished=1   error code
     chunk)         return 1
          |           |
          v           v
    next chunk    finish() →
    or finish()   BrotliDecoderIsFinished?
                      |
               +------+------+
               |             |
               v             v
           finished=1     TRUNCATED_INPUT
           return NGX_OK
```

**`finished` flag semantics:** Guards compressed-input acceptance and prevents
further `BrotliDecoderDecompressStream` invocation after stream completion.
Does NOT suppress Rust converter finalization or HTTP terminal output
production. `main_terminal_sent` and `subrequest_terminal_sent` latches
enforce HTTP terminal at-most-once independently.

**Semantic non-retryable invariant:** After a post-decode workspace expansion
failure, the decoder has consumed input that cannot replay. Existing
terminal/error state fields enforce the non-retryable invariant. The team
adds a dedicated field only if code inspection proves existing states
insufficient.
No subsequent call may re-feed the same compressed data.

### ABI Freeze

- **No new public runtime directive** or configuration default
- **No new project-exported FFI or module symbol** — `BrotliDecoder*` symbols
  are external library link-imports from `libbrotlidec`, not project exports
- **Brotli-disabled builds** (`NGX_HTTP_BROTLI` undefined): identical public
  ABI, zero Brotli linker references, no unconditional dependency added
- **Official Brotli-enabled builds** (release artifacts): Debian/Ubuntu use
  `libbrotli-dev` at build time and `libbrotli1` at runtime. RPM/Fedora use
  `brotli-devel` at build time and `libbrotli` at runtime. Homebrew uses the
  `brotli` formula for both.
- All Brotli streaming code guarded by `#ifdef NGX_HTTP_BROTLI` — enforced by
  `detect_ifdef_guard_visibility.sh` CI gate

### Build-Control Mechanism

The configure-time environment variable
`NGX_MARKDOWN_BROTLI_STREAMING` (values `auto` | `on` | `off`, default `auto`)
is a build input, not an NGINX runtime directive or request parameter. The
implementation adds no runtime configuration key or default:

- `on`: probe for `<brotli/decode.h>` + `libbrotlidec`, define
  `NGX_HTTP_BROTLI`, link decoder. Configure failure if dependency missing.
- `off`: no probing, no linking, Brotli remains on full-buffer Rust FFI path.
- `auto`: probe silently, success enables streaming, failure falls back.

### Go/No-Go Freeze Criteria

1. All applicable `make` targets pass (`test-nginx-unit`, `test-rust`,
   `coverage-c`, `harness-check`, `docs-check`, `release-gates-check-091`).
   An approved skip is acceptable only when the skipped dependency or artifact
   the evidence records the skipped dependency or artifact capability,
   consistent with ADR-0019. An unrecorded skip does not satisfy this
   criterion.
2. ASan/UBSan clean (no new findings). An approved, evidence-backed skip is
   likewise required to satisfy this criterion when the sanitizer is unavailable.
3. No new public ABI surface (confirmed by symbol table diff)
4. Performance evidence shows streaming TTFB improvement over full-buffer on
   representative large responses. A skip must carry the ADR-0019-approved
   environment and evidence record.
5. Build matrix passes with and without `NGX_HTTP_BROTLI`. The no-Brotli leg
   must record its expected-unavailable Brotli result.
6. No typed decompression error folded to generic `NGX_ERROR`

## Consequences

### Positive Consequences

- Extends streaming incremental TTFB benefits to Brotli — the remaining
  supported content coding that was on the full-buffer path
- Identical error policy, budget semantics, observability surface, and
  backpressure model as gzip/deflate — no operator relearning
- RFC 7932 single-stream semantics simplify lifecycle compared to gzip
  (no member resets, no concatenated member tracking)
- Single routing-point expansion minimizes blast radius
- Trailing-data rejection hardened beyond what existing full-buffer path
  provides
- No-progress guard prevents malformed-stream busy-loops

### Negative Consequences

- Adds a build-time and runtime dependency on `libbrotlidec` for
  Brotli-enabled builds (official release artifacts)
- Brotli decoder internal allocations (ring buffers, context maps) use a
  separate fixed 32 MiB per-worker workspace budget rather than the decoded
  output budget. Standard RFC 7932 streams use WBITS 10–24. The module does
  not set `BROTLI_DECODER_PARAM_LARGE_WINDOW`, so the decoder rejects the RFC
  9841 large-window extension (WBITS > 24). Performance evidence must include
  high-standard-window and high-compression-ratio RSS measurements.
- CI matrix grows: the project must test both Brotli-enabled and
  Brotli-disabled builds,
  including at least one Ubuntu 22.04 (Brotli 1.0.9) environment

## Alternatives Considered

- **Keep Brotli full-buffer indefinitely**: Rejected because the streaming
  skeleton already exists and the single-stream RFC 7932 semantics are
  simpler than gzip member handling which is already proven.
- **New public directive for Brotli streaming control**: Rejected because the
  existing `markdown_auto_decompress` + routing gates provide sufficient
  control without directive proliferation.
- **Custom allocator for Brotli decoder internal memory**: Adopted. A
  per-worker atomic byte budget bounds concurrent decoder state while the
  allocation header lets the free callback release the exact reservation.
- **Concatenated Brotli stream support**: Not applicable. RFC 7932 defines a
  single-stream format. It needs no concatenated member handling.

## References

- RFC 7932 (Brotli Compressed Data Format)
- RFC 9841 (Large Window Brotli)
- [ADR-0021: Gzip and Deflate Streaming Decompression Routing](0021-gzip-deflate-streaming-decompression-routing.md) (Brotli full-buffer section superseded)
- [ADR-0022: 0.9.1 Performance Evidence Release Gate](0022-performance-evidence-release-gate.md)
- [ADR-0019: 0.9.0 Production Readiness Release Gate Framework](0019-090-production-readiness-release-gates.md)
- `components/nginx-module/src/ngx_http_markdown_streaming_decomp_impl.h`
- `components/nginx-module/src/ngx_http_markdown_request_impl.h`

## Date

2026-07-17

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-17 | Kang | Initial ADR for Brotli streaming decompression promotion |
