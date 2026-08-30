# ADR-0002: Full Buffering Approach for v1

## Status

Accepted. The ADR records a historical baseline that ADR-0004,
ADR-0011, and ADR-0013 later partially superseded or extended. The
full-buffer engine itself remains part of the active dual-engine
architecture, and this ADR records why v1 originally chose full
buffering.

## Context

The NGINX module needs to convert HTML responses to Markdown. There are two main approaches:

1. **Full Buffering**: Buffer the entire response, then convert
2. **Streaming**: Convert chunks as they arrive

Key considerations:
- Conversion requires complete HTML document for accurate parsing
- NGINX filter chain operates on buffers/chains
- Performance and memory usage
- Implementation complexity
- Correctness guarantees

## Decision

Version 1 uses a **full buffering approach**:

1. Buffer the entire HTML response in memory
2. Once complete, perform conversion
3. Output the complete Markdown response

This ADR is the historical v1 baseline. Streaming conversion was subsequently
implemented and is now a second, policy-selected engine. This document does
not describe the active routing contract.

> **Note (v0.8.0+):** This ADR established the baseline full-buffer architecture. True streaming conversion arrived in v0.8.0 via [ADR-0004](0004-streaming-bounded-memory-conversion.md), [ADR-0011](0011-true-streaming-contract.md), and [ADR-0013](0013-streaming-default-policy.md). The dual-engine architecture (full-buffer + streaming) appears in [SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md#dual-engine-full-buffering--streaming-since-v080) and [LARGE_RESPONSE_DESIGN.md](../LARGE_RESPONSE_DESIGN.md).

## Consequences

### Positive Consequences

1. **Correctness**: Complete HTML document ensures accurate parsing and conversion
   - Can properly handle document structure
   - Can extract metadata from anywhere in document
   - Can generate accurate table of contents

2. **Simplicity**: Simpler implementation and testing
   - Single conversion call
   - No state management across chunks
   - Easier to reason about

3. **Accurate Content-Length**: Can set correct Content-Length header
   - Better for caching
   - Better for client progress indicators

4. **Deterministic Output**: Same input always produces same output
   - Enables consistent ETag generation
   - Simplifies testing

5. **Better Error Handling**: Can detect errors before sending any output
   - Can fall back to original HTML cleanly
   - No partial responses

### Negative Consequences

1. **Memory Usage**: Requires buffering entire response
   - ~2x response size in memory (input + output)
   - Mitigated by the then-current full-buffer size limit

2. **Latency**: Must wait for complete response before conversion
   - Time to first byte (TTFB) increased
   - Mitigated by fast conversion (typically < 50ms)

3. **Not Suitable for Large Responses**: Cannot handle very large documents
   - Mitigated by size limit and bypass
   - Most web pages are < 1MB

4. **Not Suitable for Streaming Content**: Cannot convert SSE, WebSockets, and so on
   - Mitigated by eligibility checks
   - Can explicitly exclude streaming endpoints

## Alternatives Considered

### Streaming Conversion

**Approach**: Convert HTML chunks as they arrive, output Markdown incrementally.

**Pros:**
- Lower memory usage
- Lower latency (TTFB)
- Can handle larger documents
- Can handle streaming content

**Cons:**
- Much more complex implementation
- Harder to ensure correctness
- Cannot set accurate Content-Length
- Difficult to handle errors mid-stream
- May produce inconsistent output
- Harder to test

**Why not chosen:** Complexity and correctness concerns outweigh benefits for v1. Most web pages are small enough for buffering.

### Hybrid Approach

**Approach**: Buffer up to a limit, then switch to streaming.

**Pros:**
- Combines benefits of both approaches
- Handles both small and large documents

**Cons:**
- Most complex implementation
- Two code paths to maintain
- Inconsistent behavior
- Still has streaming drawbacks for large docs

**Why not chosen:** Adds significant complexity without clear benefit. Better to have one well-tested approach.

## Implementation Details

### Buffering Strategy

1. **Check eligibility first**: Before buffering, verify response is eligible
2. **Enforce size limit**: Stop buffering if exceeds the full-buffer size limit (historically `markdown_max_size`, now `markdown_limits conversion_memory=`)
3. **Use NGINX buffer chain**: Leverage NGINX's existing buffer management
4. **Single allocation**: Allocate output buffer once conversion size is known

### Memory Management

- Use NGINX pool allocation for buffers
- Buffers automatically freed when request completes
- Size limit prevents excessive memory usage
- Monitor memory usage in production

### Performance Optimization

- Fast-path checks before buffering
- Efficient buffer chain traversal
- Minimize memory copies
- Use Rust's efficient string handling

## Future Considerations

### Historical Follow-up Context

The original v1 decision recorded these questions for future maintainers:
1. Users frequently request very large documents (> 10MB)
2. Latency becomes a significant issue
3. Memory usage becomes problematic
4. Streaming use cases become important

### Implemented Successor

The implemented streaming design answers the historical questions in
[ADR-0004](0004-streaming-bounded-memory-conversion.md),
[ADR-0011](0011-true-streaming-contract.md), and
[ADR-0013](0013-streaming-default-policy.md). Those ADRs supersede the design
bullets that were originally listed here.

## Metrics to Monitor

Track these metrics to inform future decisions:
- Average response size
- 95th percentile response size
- Memory usage per worker
- Conversion latency
- Bypass rate due to size limits

## References

- [NGINX Buffer Management](https://nginx.org/en/docs/dev/development_guide.html#buffers)
- System Architecture: `../SYSTEM_ARCHITECTURE.md`
- Buffer Implementation: `../../components/nginx-module/src/ngx_http_markdown_buffer.c`
- Performance Baselines: `../testing/PERFORMANCE_BASELINES.md`

## Date

2026-02-27

## Authors

Project Team

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
