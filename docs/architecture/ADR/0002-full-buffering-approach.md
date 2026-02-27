# ADR-0002: Full Buffering Approach for v1

## Status

Accepted

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

For version 1.0, we will use a **full buffering approach**:

1. Buffer the entire HTML response in memory
2. Once complete, perform conversion
3. Output the complete Markdown response

Streaming conversion may be considered for future versions if needed.

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
   - Mitigated by `markdown_max_size` limit (default 10MB)

2. **Latency**: Must wait for complete response before conversion
   - Time to first byte (TTFB) increased
   - Mitigated by fast conversion (typically < 50ms)

3. **Not Suitable for Large Responses**: Cannot handle very large documents
   - Mitigated by size limit and bypass
   - Most web pages are < 1MB

4. **Not Suitable for Streaming Content**: Cannot convert SSE, WebSockets, etc.
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
2. **Enforce size limit**: Stop buffering if exceeds `markdown_max_size`
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

### When to Reconsider Streaming

Consider streaming conversion if:
1. Users frequently request very large documents (> 10MB)
2. Latency becomes a significant issue
3. Memory usage becomes problematic
4. Streaming use cases become important

### Potential Streaming Design

If streaming is needed in the future:
- Use incremental HTML parser (html5ever supports this)
- Maintain parser state across chunks
- Output Markdown incrementally
- Handle errors gracefully (may need to abort mid-stream)
- Document limitations (no Content-Length, no ETag, etc.)

## Metrics to Monitor

Track these metrics to inform future decisions:
- Average response size
- 95th percentile response size
- Memory usage per worker
- Conversion latency
- Bypass rate due to size limits

## References

- [NGINX Buffer Management](https://nginx.org/en/docs/dev/development_guide.html#buffers)
- Design Document: `../../.kiro/specs/nginx-markdown-for-agents/design.md`
- Buffer Implementation: `../../components/nginx-module/src/ngx_http_markdown_buffer.c`
- Performance Baselines: `../testing/PERFORMANCE_BASELINES.md`

## Date

2026-02-27

## Authors

Project Team
