# ADR-0020: Hybrid Zero-Copy Streaming Output with Pool Cleanup

## Status

Accepted

## Context

0.9.1 introduces a zero-copy output path where `ngx_buf_t` references Rust-owned memory directly without intermediate pool-copy. The ownership lifecycle (Rust alloc → `as_mut_ptr` + `mem::forget` → buffer factory → `ngx_http_output_filter` → pool cleanup) must be safe across `NGX_AGAIN` backpressure, fail-open, and request pool destruction.

## Decision

Reuse existing `markdown_streaming_output_free` FFI for deallocation. Register NGINX pool cleanup handler with a `freed` flag for double-free prevention. The buffer factory creates an `ngx_buf_t` referencing Rust memory and registers the cleanup handler atomically.

The feature is **Default OFF** (`markdown_streaming_zero_copy off`) — opt-in via location-level directive, togglable via HUP reload.

### Key Invariants
- The `freed` flag is never set during `NGX_AGAIN`.
- The pool cleanup handler checks the `freed` flag before calling the free FFI.
- `catch_unwind` wraps Rust deallocation to prevent panic propagation into NGINX.

## Consequences

### Positive Consequences
- Reduces `memcpy` overhead for non-terminal streaming chunks when enabled.
- Minimal FFI surface change as it reuses existing deallocation paths.

### Negative Consequences
- Terminal chunks and backpressure-active chunks always fall back to pool-copy to ensure safety.
- Reliance on pool cleanup as the primary safety net for Rust buffer lifetime increases complexity of the memory ownership state machine.

## Alternatives Considered
- **Pure Rust-managed buffers**: Rejected because NGINX's output filter chain expects buffer ownership to be tied to the request pool for fail-safe cleanup.
- **Slab allocator**: Considered but rejected for 0.9.1 to avoid introducing new global state and synchronization overhead.

## References
- [ADR-0011: True Streaming Contract](0011-true-streaming-contract.md)
- NGINX `ngx_pool_t` cleanup documentation

## Date

2026-07-08

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-08 | Kang | Initial ADR for Hybrid Zero-Copy Output |
