# FFI ABI Compatibility Assumptions (v0.7.0)

## Layout Contract

All FFI structs use `#[repr(C)]` for deterministic C-compatible layout.
The following assumptions must hold for ABI compatibility between the
Rust conversion library and the NGINX C module:

### Tail-Append Only

New fields may only be appended to the **end** of existing structs.
Inserting fields in the middle or removing fields is a breaking ABI change
that requires a major version bump.

### Feature-Gated Fields

Fields behind `#[cfg(feature = "streaming")]` do not affect layout when
the feature is disabled. The C module must be compiled with matching
feature flags as the Rust library.

### Field Types

- All integer fields use fixed-width types (`u32`, `u8`, `uintptr_t`)
- Pointer fields use raw pointers (`*const u8`, `*mut T`)
- No Rust-specific types (`String`, `Vec`, `Option`) cross the FFI boundary

### Alignment

Struct alignment is determined by the most restrictive field alignment.
Both C and Rust must agree on `alignof(T)` for each struct. The Rust
layout tests in `abi.rs` verify `size_of` and `align_of` at compile time.

## Header Synchronization

Two copies of `markdown_converter.h` exist:
1. `components/rust-converter/include/markdown_converter.h` — cbindgen output
2. `components/nginx-module/src/markdown_converter.h` — checked-in C module copy

The `make check-headers` target and CI `header-sync` job verify these
are identical. Any divergence is a CI failure.

## Versioning

The ABI is versioned by the Cargo.toml package version. Breaking ABI
changes require a major version bump. v0.7.0 is ABI-compatible with
v0.6.x (all new fields were added to new structs or to configuration
structs that are not part of the hot FFI call path).

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial ABI compatibility assumptions document |
