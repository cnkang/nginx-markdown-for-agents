# Rust Converter Component

This directory contains the HTML-to-Markdown conversion engine used by the NGINX module.

It focuses on predictable output, memory safety, and a stable C FFI boundary so the NGINX module can call into it without re-implementing parsing or sanitization logic in C.

This is the part of the project that turns eligible HTML into a deterministic Markdown representation while enforcing the converter's safety rules.

## Responsibilities

- parse HTML input safely
- remove or neutralize dangerous content
- generate deterministic Markdown output
- expose conversion results through a C-compatible FFI
- provide optional metadata such as token estimates and YAML front matter

If you are changing parsing, sanitization, Markdown rendering, or metadata generation, this is usually the component you need to inspect first.

## Quick Commands

```bash
# Build
cargo build --release

# Run all tests
cargo test --all

# Run short fuzz smoke checks (requires nightly + cargo-fuzz)
make test-rust-fuzz-smoke

# Generate the C header
cbindgen --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h
```

## Source Layout

```text
src/
  lib.rs                public API
  ffi.rs                C FFI entrypoint and public re-exports
  ffi/                  ABI structs, option decoding, memory helpers, and exported FFI functions
  parser.rs             HTML parsing
  converter.rs          Markdown converter entrypoint and shared test module
  converter/            renderer internals (traversal, blocks, inline, tables, normalization)
  security.rs           sanitization and safety checks
  metadata.rs           metadata extraction entrypoint and public types
  metadata/             metadata extraction, URL resolution, and metadata tests
  token_estimator.rs    token estimation
  etag_generator.rs     ETag generation
  charset.rs            charset detection and encoding
  error.rs              error types
fuzz/
  fuzz_targets/         parser, FFI, and security-validator fuzz targets
```

## Where to Read More

- Canonical architecture and repository layout live in:
  - [../../docs/architecture/SYSTEM_ARCHITECTURE.md](../../docs/architecture/SYSTEM_ARCHITECTURE.md)
  - [../../docs/architecture/REPOSITORY_STRUCTURE.md](../../docs/architecture/REPOSITORY_STRUCTURE.md)
- [../../docs/features/security.md](../../docs/features/security.md)
- [../../docs/features/deterministic-output.md](../../docs/features/deterministic-output.md)
- [../../docs/features/TOKEN_ESTIMATOR.md](../../docs/features/TOKEN_ESTIMATOR.md)
- [../../docs/features/YAML_FRONT_MATTER.md](../../docs/features/YAML_FRONT_MATTER.md)
- [../../docs/features/charset-detection.md](../../docs/features/charset-detection.md)

## Development Notes

- Keep FFI changes deliberate and version-conscious.
- Treat deterministic output and safety behavior as compatibility-sensitive.
- Prefer adding focused tests when changing parsing, sanitization, or output normalization behavior.
