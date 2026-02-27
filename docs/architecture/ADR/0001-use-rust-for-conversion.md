# ADR-0001: Use Rust for HTML-to-Markdown Conversion

## Status

Accepted

## Context

The project requires a robust HTML-to-Markdown conversion engine that can be integrated with NGINX. The conversion logic needs to:

- Parse potentially malformed HTML safely
- Generate consistent Markdown output
- Handle edge cases and security concerns
- Provide good performance
- Be maintainable and testable

Several implementation approaches were possible:

1. Pure C implementation
2. Rust implementation with FFI
3. External service (HTTP API)
4. Embedded scripting language (Lua, JavaScript)

## Decision

We will implement the HTML-to-Markdown conversion engine in Rust and integrate it with the NGINX C module via FFI (Foreign Function Interface).

The Rust component will:
- Handle all HTML parsing and Markdown generation
- Provide a C-compatible FFI interface
- Manage its own memory safely
- Expose a simple API for the NGINX module

The NGINX module will:
- Handle HTTP request/response processing
- Manage NGINX-specific concerns (configuration, logging, etc.)
- Call into Rust via FFI for conversion

## Consequences

### Positive Consequences

1. **Memory Safety**: Rust's ownership system prevents common memory bugs (use-after-free, double-free, buffer overflows)

2. **Strong Ecosystem**: Access to high-quality HTML parsing libraries (html5ever) and other Rust crates

3. **Better Testing**: Rust's testing framework makes it easier to write comprehensive unit and property-based tests

4. **Type Safety**: Rust's type system catches many bugs at compile time

5. **Performance**: Rust provides C-like performance with safety guarantees

6. **Maintainability**: Clearer separation of concerns between NGINX integration (C) and conversion logic (Rust)

7. **Security**: Rust's safety features reduce attack surface for parsing untrusted HTML

### Negative Consequences

1. **Build Complexity**: Requires Rust toolchain in addition to C compiler

2. **FFI Overhead**: Small performance cost for crossing the FFI boundary (typically < 1ms)

3. **Learning Curve**: Contributors need to know both C and Rust

4. **Binary Size**: Rust static library adds to module size (~2-3MB)

5. **Deployment**: Need to ensure Rust library is available at runtime

## Alternatives Considered

### Pure C Implementation

**Pros:**
- No FFI overhead
- Single language
- Smaller binary

**Cons:**
- Higher risk of memory bugs
- Limited HTML parsing libraries
- More difficult to test thoroughly
- Security concerns with manual memory management

**Why not chosen:** Memory safety and maintainability concerns outweigh the simplicity benefits.

### External Service (HTTP API)

**Pros:**
- Language agnostic
- Easy to scale independently
- Can be updated without NGINX restart

**Cons:**
- Network latency overhead (10-100ms+)
- Additional infrastructure complexity
- Single point of failure
- Harder to deploy

**Why not chosen:** Latency overhead is unacceptable for inline conversion. Adds operational complexity.

### Embedded Scripting (Lua/JavaScript)

**Pros:**
- Dynamic, no recompilation needed
- Easier to modify

**Cons:**
- Performance overhead (10-50x slower)
- Limited HTML parsing libraries
- Memory management concerns
- Less type safety

**Why not chosen:** Performance is critical for inline conversion. Type safety is important for correctness.

## Implementation Notes

### FFI Design Principles

1. **Simple C API**: Keep FFI surface minimal and C-compatible
2. **Panic Safety**: Catch Rust panics at FFI boundary
3. **Memory Management**: Clear ownership rules (Rust allocates, C frees via provided function)
4. **Error Handling**: Return error codes, provide error message retrieval
5. **Thread Safety**: Document thread-safety guarantees

### Build Integration

- Use `cbindgen` to generate C header from Rust code
- Static linking for simplicity (dynamic linking possible if needed)
- Separate build steps: Rust library first, then NGINX module

## References

- [Rust FFI Guide](https://doc.rust-lang.org/nomicon/ffi.html)
- [html5ever Documentation](https://docs.rs/html5ever/)
- [cbindgen Documentation](https://github.com/eqrion/cbindgen)
- Design Document: `../../.kiro/specs/nginx-markdown-for-agents/design.md`
- FFI Implementation: `../../components/rust-converter/src/ffi.rs`

## Date

2026-02-27

## Authors

Project Team
