# ADR-0021: Gzip and Deflate Streaming Decompression Routing

## Status

Accepted

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
0.9.1. Keep Brotli on bounded full-buffer decompression.

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
  requirements are supported; it does not make Brotli streaming-eligible.

## Consequences

### Positive Consequences
- Extends incremental decompression and streaming TTFB benefits to gzip, the
  common HTTP content coding, while retaining both deflate framings.
- Preserves gzip member/trailer integrity across arbitrary chunks and resumes.
- Gives streaming, Rust full-buffer, and C fallback paths the same
  concatenated-member and cumulative-budget contract.
- Keeps Brotli's decoder-state and memory validation outside the 0.9.1 scope.

### Negative Consequences
- Gzip member reset and cumulative-budget behavior add codec-specific state to
  the incremental decompressor.
- Brotli responses do not receive streaming TTFB benefits in 0.9.1.

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

## Date

2026-07-08

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-08 | Kang | Initial ADR for Streaming Decompression Routing |
| 0.9.1 | 2026-07-14 | Kang | Enabled member-aware gzip streaming alongside zlib/raw deflate; retained bounded Brotli full-buffer routing |
| 0.9.1 | 2026-07-14 | Codex | Aligned Rust and C full-buffer gzip with the same multi-member, truncation, and response-budget contract |
