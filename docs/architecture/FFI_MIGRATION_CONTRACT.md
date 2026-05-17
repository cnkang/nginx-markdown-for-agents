# FFI Migration Contract — v0.7.0

## Purpose

This document defines the ownership, migration status, and compatibility
constraints for every FFI function and struct at the Rust↔C boundary.
It serves as the single source of truth for the Rust-first architecture
migration strategy.

## Boundary Overview

The FFI boundary is implemented in:
- **Rust**: `components/rust-converter/src/ffi/` (abi.rs, exports.rs, convert.rs, options.rs, memory.rs, streaming.rs, incremental.rs)
- **C header** (auto-generated): `components/rust-converter/include/markdown_converter.h`
- **C header** (checked-in copy): `components/nginx-module/src/markdown_converter.h`
- **C consumer**: `components/nginx-module/src/ngx_http_markdown_*.c/h`

## FFI Function Registry

| FFI Function | Rust Owner | C Consumer | Migration Status | Compat Constraint |
|-------------|-----------|-----------|-----------------|------------------|
| `markdown_converter_new` | Rust | C | **Stable** | Returns opaque handle |
| `markdown_convert` | Rust | C | **Stable** | Full-buffer conversion |
| `markdown_converter_free` | Rust | C | **Stable** | Releases handle |
| `markdown_result_free` | Rust | C | **Stable** | Releases result buffers |
| `markdown_negotiate_accept` | Rust | C | **v0.7.0 NEW** | Accept header negotiation |
| `markdown_streaming_create` | Rust | C | **Stable** | Streaming converter handle |
| `markdown_streaming_feed` | Rust | C | **Stable** | Incremental input |
| `markdown_streaming_finalize` | Rust | C | **Stable** | End-of-stream |
| `markdown_streaming_abort` | Rust | C | **Stable** | Error cleanup |
| `markdown_streaming_output_free` | Rust | C | **Stable** | Releases output buffer |
| `markdown_incremental_*` | Rust | C | **Stable** | Incremental API |

## FFI Struct Registry

| Struct | Rust Owner | Size (bytes) | Layout Stability | Migration Status |
|--------|-----------|-------------|-----------------|-----------------|
| `MarkdownOptions` | Rust→C (input) | — | `repr(C)`, tail-append only | **Stable** |
| `MarkdownResult` | Rust→C (output) | 64 | `repr(C)`, tail-append only | **Stable** |
| `FFIAcceptResult` | Rust→C (output) | 2 | `repr(C)` | **v0.7.0 NEW** |
| `StreamingConverterHandle` | Rust (opaque) | — | Opaque pointer | **Stable** |
| `MarkdownConverterHandle` | Rust (opaque) | — | Opaque pointer | **Stable** |

## Error Code Registry

| Code | Constant | Category | Owner | Since |
|------|----------|----------|-------|-------|
| 0 | `ERROR_SUCCESS` | — | Rust | v0.5.0 |
| 1 | `ERROR_PARSE` | conversion | Rust | v0.5.0 |
| 2 | `ERROR_ENCODING` | conversion | Rust | v0.5.0 |
| 3 | `ERROR_TIMEOUT` | resource_limit | Rust | v0.5.0 |
| 4 | `ERROR_MEMORY_LIMIT` | resource_limit | Rust | v0.5.0 |
| 5 | `ERROR_INVALID_INPUT` | conversion | Rust | v0.5.0 |
| 6 | `ERROR_BUDGET_EXCEEDED` | resource_limit | Rust (streaming) | v0.6.0 |
| 7 | `ERROR_STREAMING_FALLBACK` | system | Rust (streaming) | v0.6.0 |
| 8 | `ERROR_POST_COMMIT` | conversion | Rust (streaming) | v0.6.0 |
| 9 | `ERROR_DECOMPRESSION_BUDGET_EXCEEDED` | resource_limit | Rust | v0.7.0 |
| 10 | `ERROR_PARSE_TIMEOUT` | resource_limit | Rust | v0.7.0 |
| 11 | `ERROR_PARSE_BUDGET_EXCEEDED` | resource_limit | Rust | v0.7.0 |
| 99 | `ERROR_INTERNAL` | system | Rust | v0.5.0 |

## Migration Priority

Functions ordered by migration risk and complexity (highest first):

1. **`markdown_convert`** — Core conversion; already Rust-owned. No migration needed.
2. **`markdown_negotiate_accept`** — New in v0.7.0; Rust-first from inception.
3. **Error classification** — C-side `ngx_http_markdown_classify_error()` must stay in sync with Rust `ConversionError::code()`. Adding new Rust variants requires updating C-side switch.
4. **Config option structs** — `MarkdownOptions` fields added at tail only; C-side init sites must be updated.

## Compatibility Rules

1. **Tail-append only**: New fields in `repr(C)` structs MUST be appended after existing fields. Never reorder or insert.
2. **Feature-gated fields**: Streaming/incremental-only fields are `#ifdef`-gated in the C header. Layout drift within a feature gate is a breaking change.
3. **Header sync**: Both copies of `markdown_converter.h` MUST be byte-identical. `make check-headers` enforces this.
4. **Error code uniqueness**: Every `ERROR_*` constant MUST have a unique value. The Rust `layout_tests::test_error_codes_distinct` test enforces this.
5. **Opaque pointers**: `MarkdownConverterHandle` and `StreamingConverterHandle` are opaque to C. C never dereferences them.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0-draft | 2026-05-17 | agent | Initial FFI migration contract for v0.7.0 |
