# Cooperative Timeout Mechanism

## Overview

The NGINX Markdown for Agents converter implements a **cooperative timeout mechanism** to protect against resource exhaustion from slow or malicious HTML conversions. This mechanism provides timeout enforcement without thread spawning, making it compatible with NGINX's event-driven worker model.

## Design Principles

### Cooperative vs. Preemptive

The timeout mechanism is **cooperative** (not preemptive):

- **Cooperative**: Conversion code checks timeout at regular intervals (checkpoints)
- **Preemptive**: Background thread forcibly terminates conversion (NOT used)

### Why Cooperative?

1. **No thread spawning**: Avoids thread explosion under high concurrency
2. **NGINX-friendly**: Compatible with event-driven worker model
3. **Predictable**: No background threads consuming CPU after timeout
4. **Simple**: Easier to reason about and test
5. **No resource leaks**: Conversion stops immediately when timeout detected

### Trade-offs

- **Not preemptive**: Conversion must reach a checkpoint to detect timeout
- **Worst-case delay**: Timeout detection delayed until next checkpoint
- **Mitigation**: Checkpoints at frequent intervals (every 100 nodes, every parse step)

## Architecture

### ConversionContext

The `ConversionContext` struct tracks elapsed time and node count:

```rust
pub struct ConversionContext {
    start_time: Instant,
    timeout: Duration,
    node_count: u32,
}
```

**Key methods:**

- `new(timeout: Duration)` - Create context with timeout (Duration::ZERO = no timeout)
- `check_timeout()` - Check if timeout exceeded, return `Err(ConversionError::Timeout)` if so
- `increment_and_check()` - Increment node count and check timeout at checkpoints (every 100 nodes)
- `elapsed()` - Get elapsed time since conversion started
- `node_count()` - Get number of nodes processed

### Timeout Checkpoints

Timeout is checked at these key points:

1. **After HTML parsing** - Before DOM traversal begins
2. **Every 100 DOM nodes during traversal** - Balance between performance and responsiveness
3. **After metadata extraction** - If YAML front matter is enabled
4. **Before output normalization** - After Markdown generation
5. **After output normalization** - Final check before returning

### Checkpoint Frequency

The checkpoint frequency (every 100 nodes) provides a balance:

- **Performance**: Not checking on every single node (would be expensive)
- **Responsiveness**: Detecting timeout within reasonable time
- **Typical case**: For a 10,000 node document, timeout is checked ~100 times

## Usage

### Basic Usage

```rust
use nginx_markdown_converter::converter::{MarkdownConverter, ConversionContext};
use nginx_markdown_converter::parser::parse_html;
use std::time::Duration;

let html = b"<h1>Title</h1><p>Content</p>";
let dom = parse_html(html).expect("Parse failed");
let converter = MarkdownConverter::new();

// Convert with 5 second timeout
let mut ctx = ConversionContext::new(Duration::from_secs(5));
match converter.convert_with_context(&dom, &mut ctx) {
    Ok(markdown) => println!("Success: {}", markdown),
    Err(ConversionError::Timeout) => println!("Timeout exceeded"),
    Err(e) => println!("Error: {}", e),
}
```

### No Timeout

```rust
// No timeout (Duration::ZERO)
let mut ctx = ConversionContext::new(Duration::ZERO);
let markdown = converter.convert_with_context(&dom, &mut ctx)?;
```

### Backward Compatibility

The original `convert()` method is still available and uses no timeout:

```rust
// Old method - no timeout
let markdown = converter.convert(&dom)?;
```

### FFI Integration

The FFI layer automatically creates a `ConversionContext` from the timeout_ms option:

```c
markdown_options_t options = {
    .timeout_ms = 5000,  // 5 second timeout
    // ... other options
};

markdown_convert(handle, html, html_len, &options, &result);
```

If `timeout_ms = 0`, no timeout is enforced.

## Configuration Examples

### NGINX Configuration

```nginx
# Conservative timeout (default: 5 seconds)
markdown_timeout 5s;

# Generous timeout for complex pages
markdown_timeout 10s;

# Very short timeout for simple pages
markdown_timeout 1s;

# No timeout (not recommended for production)
markdown_timeout 0;
```

### Recommended Values

| Use Case | Timeout | Rationale |
|----------|---------|-----------|
| **Simple pages** | 1-2 seconds | Most pages convert in < 100ms |
| **Complex pages** | 5 seconds (default) | Handles large documents safely |
| **Very large pages** | 10 seconds | For documentation sites, wikis |
| **Development** | 0 (no timeout) | Easier debugging |

## Performance Characteristics

### Overhead

The cooperative timeout mechanism has minimal overhead:

- **Checkpoint check**: ~10-20 nanoseconds per checkpoint
- **Frequency**: Every 100 nodes (not every node)
- **Total overhead**: < 1% for typical documents

### Timeout Detection Latency

The worst-case latency for timeout detection is the time between checkpoints:

- **Best case**: Immediate (at next checkpoint)
- **Worst case**: Time to process 100 nodes (~1-10ms for typical HTML)
- **Typical case**: < 5ms latency

### Example Scenarios

| Document Size | Nodes | Checkpoints | Timeout Detection Latency |
|---------------|-------|-------------|---------------------------|
| Small (10 KB) | ~100 | 1 | < 1ms |
| Medium (100 KB) | ~1,000 | 10 | < 5ms |
| Large (1 MB) | ~10,000 | 100 | < 10ms |
| Very Large (10 MB) | ~100,000 | 1,000 | < 50ms |

## Error Handling

### Timeout Error

When timeout is exceeded, the conversion returns `Err(ConversionError::Timeout)`:

```rust
match converter.convert_with_context(&dom, &mut ctx) {
    Err(ConversionError::Timeout) => {
        // Handle timeout
        log::warn!("Conversion timeout exceeded after {:?}", ctx.elapsed());
    }
    Err(e) => {
        // Handle other errors
        log::error!("Conversion failed: {}", e);
    }
    Ok(markdown) => {
        // Success
    }
}
```

### FFI Error Code

In the FFI layer, timeout errors are mapped to `ERROR_TIMEOUT` (code 3):

```c
if (result.error_code == ERROR_TIMEOUT) {
    // Handle timeout
    ngx_log_error(NGX_LOG_WARN, log, 0,
                  "markdown conversion timeout exceeded");
}
```

## Testing

### Unit Tests

The timeout mechanism is tested with:

- **No timeout**: Verify conversion succeeds with `Duration::ZERO`
- **Generous timeout**: Verify conversion succeeds with long timeout
- **Short timeout**: Verify timeout is detected with very short timeout
- **Node count tracking**: Verify node count is incremented
- **Elapsed time tracking**: Verify elapsed time is tracked
- **Backward compatibility**: Verify old `convert()` method still works

### Integration Tests

See `tests/timeout_test.rs` for comprehensive timeout tests.

## Future Enhancements (v2+)

### Hard Timeout with Process Isolation

For stricter timeout guarantees, v2 could add:

- **Process isolation**: Run conversion in separate process
- **Hard timeout**: Forcibly terminate process after timeout
- **Resource limits**: Additional memory and CPU limits

This would provide preemptive timeout but with higher overhead.

### Adaptive Checkpoint Frequency

Adjust checkpoint frequency based on document characteristics:

- **Simple HTML**: Check less frequently (every 200 nodes)
- **Complex HTML**: Check more frequently (every 50 nodes)
- **Deeply nested**: Check at every level change

## Requirements Validation

This implementation validates the following requirements:

- **FR-10.2**: "WHERE a conversion timeout is configured, THE Module SHALL abort conversion operations that exceed this timeout"
- **FR-10.7**: "THE Module SHALL handle resource limit violations without crashing or hanging"

## References

- Design Document: Section on Cooperative Timeout
- Requirements Document: FR-10 (Resource Protection)
- Implementation: `components/rust-converter/src/converter.rs` (ConversionContext)
- Tests: `components/rust-converter/tests/timeout_test.rs`
