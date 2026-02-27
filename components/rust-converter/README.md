# Rust Converter Component

HTML-to-Markdown converter with memory-safe implementation and C FFI bridge for NGINX integration.

## Features

- 🔒 Memory-safe HTML parsing and Markdown generation
- 🎯 Deterministic output (same input produces same output)
- 🛡️ Security protections (XSS, XXE, SSRF prevention)
- ⚡ High-performance conversion
- 🔌 C FFI interface for NGINX integration
- 📊 Token estimation for LLM context management
- 📝 YAML front matter generation
- 🌐 Charset detection and entity decoding

## Quick Start

### Build

```bash
cargo build --release
```

### Run Tests

```bash
cargo test --all
```

### Generate C Header

```bash
cbindgen --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h
```

## Usage Examples

### As a Rust Library

```rust
use nginx_markdown_converter::{convert_html_to_markdown, ConversionOptions};

let html = "<h1>Hello</h1><p>World</p>";
let options = ConversionOptions::default();
let markdown = convert_html_to_markdown(html, options)?;
println!("{}", markdown);
// Output: # Hello\n\nWorld
```

### Via FFI (C Integration)

See `tests/ffi_test.rs` for complete examples of FFI usage patterns.

## API Documentation

Generate and view complete API documentation:

```bash
cargo doc --no-deps --open
```

## Architecture

- `src/lib.rs` - Library entry point and public API
- `src/ffi.rs` - C FFI interface for NGINX integration
- `src/parser.rs` - HTML5 parser (html5ever-based)
- `src/converter.rs` - Markdown converter implementation
- `src/security.rs` - Security checks and sanitization
- `src/metadata.rs` - Metadata extraction (title, description, etc.)
- `src/token_estimator.rs` - Token count estimation for LLMs
- `src/etag_generator.rs` - ETag generation (BLAKE3-based)
- `src/error.rs` - Error types and handling

## Detailed Documentation

Canonical feature documentation:

- [Security Features](../../docs/features/security.md)
- [Deterministic Output](../../docs/features/deterministic-output.md)
- [Token Estimation](../../docs/features/TOKEN_ESTIMATOR.md)
- [YAML Front Matter](../../docs/features/YAML_FRONT_MATTER.md)
- [Charset Detection](../../docs/features/charset-detection.md)

## Development

### Run Specific Tests

```bash
# FFI tests
cargo test --test ffi_test

# Library tests
cargo test --lib

# Integration tests
cargo test --test '*'
```

### Performance Benchmarking

```bash
# Run performance baseline
cargo run --release --example perf_baseline

# Test timeout behavior
cargo run --release --example timeout_demo

# Test token estimation
cargo run --release --example token_estimation
```

### Code Coverage

```bash
# Install tarpaulin if not already installed
cargo install cargo-tarpaulin

# Generate coverage report
cargo tarpaulin --out Html
```

## Dependencies

Main dependencies:

- `html5ever` - HTML5 parsing
- `markup5ever_rcdom` - DOM tree representation
- `blake3` - Fast hashing for ETags
- `encoding_rs` - Charset detection and conversion

See `Cargo.toml` for complete dependency list.

## Testing

The converter includes comprehensive test coverage:

- Unit tests for core conversion logic
- Property-based tests for invariants
- FFI integration tests
- Security tests for sanitization
- Performance regression tests

Run all tests:

```bash
cargo test --all
```

## License

Licensed under the BSD 2-Clause "Simplified" License (`BSD-2-Clause`).
