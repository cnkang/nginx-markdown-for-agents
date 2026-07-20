# ADR-0021: Gzip and Deflate Streaming Decompression Routing

## Status

Accepted — Brotli full-buffer section superseded by [ADR-0024](0024-brotli-streaming-decompression.md)

## Context

0.9.1 routes compressed responses through streaming decompression when the
streaming engine is selected, `auto_decompress` is on, and cache validation is
not `full`. Deflate already supports both RFC 1950 zlib-wrapped and RFC 1951
raw framing through deferred header sniffing. Gzip uses zlib's gzip wrapper
mode (`MAX_WBITS + 16`), including header and trailer validation.

The lifecycle distinction is important: `Z_STREAM_END` completes one gzip
member, not necessarily the compressed HTTP response. Concatenated members may
continue in the same NGINX buffer or a later buffer, including after downstream
backpressure has suspended delivery. The same rule applies when policy selects
the bounded full-buffer path: completing the first member is not completion of
the response.

## Decision

Make **gzip** and **deflate** streaming-eligible under the same routing gates in
0.9.1. The original decision kept Brotli on bounded full-buffer decompression;
that restriction is historical and was superseded by ADR-0024, which adds
Brotli under the same runtime gates in the final 0.9.1 implementation.

The deflate inflater sniffs the first two bytes to distinguish zlib-wrapped
(RFC 1950, RFC 9110-compliant) from raw deflate (RFC 1951). The gzip inflater
uses gzip framing and treats each valid `Z_STREAM_END` as a member boundary.
It resets the inflater while preserving remaining compressed input, accepts a
member boundary between feed calls, and consumes later members exactly once.
The actual upstream terminal finalizes the compressed response: a complete
member boundary succeeds and an incomplete final member fails.

`total_decompressed` and `max_decompressed_size` remain response-wide across
gzip member resets. Source input ownership remains independent of downstream
`NGX_AGAIN`; unconsumed input stays retained for resume, and terminal delivery
is recorded only after downstream success.

The default Rust full-buffer decoder consumes all concatenated members through
`MultiGzDecoder`. The C no-Rust fallback performs member-aware `inflateReset`
and tracks completed-member output separately from zlib's member-local
`total_out`. Both paths reject a truncated later member and apply one cumulative
output budget across the complete response. An empty later member remains valid
when earlier output exactly fills that budget.

### Error Handling
- **Pre-commit errors**: Trigger fail-open via the replay buffer.
- **Post-commit errors**: Use the existing post-commit safe-finish/abort
  semantics; original compressed-body replay is not possible after commit.

### Routing and Gating
- The routing decision is made in the header filter.
- Existing decompression and streaming counters classify routing, budget,
  pre-commit, and post-commit outcomes.
- `streaming_first` prefers streaming when the selected codec and validation
  requirements are supported. The original Brotli exclusion was superseded by
  ADR-0024.

## Consequences

### Positive Consequences
- Extends incremental decompression and streaming TTFB benefits to gzip, the
  common HTTP content coding, while retaining both deflate framings.
- Preserves gzip member/trailer integrity across arbitrary chunks and resumes.
- Gives streaming, Rust full-buffer, and C fallback paths the same
  concatenated-member and cumulative-budget contract.
- The initial decision kept Brotli's decoder-state and memory validation out of
  scope. This historical consequence was superseded when ADR-0024 promoted
  Brotli within the same 0.9.1 release.

### Negative Consequences
- Gzip member reset and cumulative-budget behavior add codec-specific state to
  the incremental decompressor.
- The initial decision did not give Brotli responses streaming TTFB benefits.
  ADR-0024 superseded that restriction before the final 0.9.1 release.

## Alternatives Considered
- **Keep gzip full-buffer in 0.9.1**: Rejected because the existing zlib-backed
  streaming implementation can validate gzip framing once member lifecycle,
  truncation, backpressure, and cumulative-budget behavior are proven.
- **Generic multi-codec state machine**: Rejected as unnecessary for one
  additional zlib-backed content coding.
- **External Decompressor**: Rejected to keep the module dependency-free and avoid IPC overhead.

## References
- RFC 1950 (ZLIB Compressed Data Format)
- RFC 1951 (DEFLATE Compressed Data Format)
- [ADR-0019: 0.9.0 Production Readiness Release Gate Framework](0019-090-production-readiness-release-gates.md)
- [ADR-0024: Brotli Streaming Decompression](0024-brotli-streaming-decompression.md) (supersedes Brotli full-buffer section)

## Date

2026-07-08

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-17 | Kang | Added cross-reference to ADR-0024 (Brotli streaming decompression); marked Brotli full-buffer section superseded |
| 0.9.1 | 2026-07-14 | Kang | Enabled member-aware gzip streaming alongside zlib/raw deflate; retained bounded Brotli full-buffer routing |
| 0.9.1 | 2026-07-14 | Codex | Aligned Rust and C full-buffer gzip with the same multi-member, truncation, and response-budget contract |
| 0.9.1 | 2026-07-08 | Kang | Initial ADR for Streaming Decompression Routing |
