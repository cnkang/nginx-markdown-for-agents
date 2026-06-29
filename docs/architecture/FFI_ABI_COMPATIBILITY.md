# FFI ABI Compatibility Assumptions (v0.7.0)

## Purpose

This document defines the ABI compatibility assumptions that govern the FFI
boundary between the Rust conversion library and the NGINX C module. All
developers, CI tooling, and automated agents must respect these assumptions
when modifying FFI structs, adding fields, or changing feature gates.

Violation of any assumption is a **breaking ABI change** that requires a major
version bump and coordinated update of both sides.

---

## Assumption 1: Tail-Append Only

**Rule**: New fields are always appended to the **end** of `repr(C)` structs.
Fields are never inserted in the middle, reordered, or moved.

**Rationale**: C struct layout places fields at monotonically increasing
offsets. Existing code compiled against an older version of the struct accesses
fields by their fixed offsets. Inserting a field in the middle shifts all
subsequent field offsets, causing binary incompatibility without any compiler
warning.

**Guarantees**:
- Existing field offsets remain stable across versions.
- Older C code linked against a newer Rust library continues to read the
  correct values for all fields it knows about.
- The `sizeof` of the struct may grow, but never shrinks.

**Enforcement**:
- Rust layout tests (`abi.rs`) verify `offset_of!` for every field.
- C `static_assert` checks verify `offsetof` for critical structs.
- Code review rejects any PR that inserts a field before the last existing
  field in an FFI struct.

**Example**:

```rust
#[repr(C)]
pub struct MarkdownOptions {
    pub max_size: usize,              // offset 0 — stable since v0.5.0
    pub on_error: u8,                 // offset 8 — stable since v0.5.0
    // ... existing fields ...
    pub decompression_budget: usize,  // appended in v0.7.0
    pub parse_timeout_ms: u32,        // appended in v0.7.0
    pub parser_memory_budget: usize,  // appended in v0.7.0
}
```

---

## Assumption 2: `repr(C)` Deterministic Layout

**Rule**: All FFI structs use `#[repr(C)]` to guarantee deterministic layout
matching C struct layout rules.

**Rationale**: Without `repr(C)`, the Rust compiler is free to reorder fields,
insert arbitrary padding, or optimize layout in ways that differ between
compiler versions. `repr(C)` forces Rust to follow the same field ordering,
alignment, and padding rules as a C compiler targeting the same platform ABI.

**Guarantees**:
- Field order in memory matches declaration order.
- Padding is inserted according to platform C ABI rules (System V AMD64 ABI on
  Linux/macOS x86_64, AAPCS64 on ARM64).
- `size_of::<T>()` in Rust equals `sizeof(T)` in C.
- `align_of::<T>()` in Rust equals `_Alignof(T)` in C.
- `offset_of!(T, field)` in Rust equals `offsetof(T, field)` in C.

**Enforcement**:
- All FFI structs in `components/rust-converter/src/ffi/` carry `#[repr(C)]`.
- cbindgen generates the C header from these Rust definitions, ensuring the C
  side sees the identical layout.
- Rust layout tests compare `size_of`, `align_of`, and `offset_of` against
  expected constants.
- C `static_assert` checks mirror the Rust layout tests.

**Prohibited patterns**:
- `#[repr(Rust)]` (default) — non-deterministic layout.
- `#[repr(packed)]` — misaligned access on some architectures.
- `#[repr(align(N))]` without corresponding C-side `_Alignas(N)`.

---

## Assumption 3: Feature-Gated Fields Cause No Layout Drift

**Rule**: Fields behind `#[cfg(feature = "...")]` do NOT cause layout drift
because they are always placed at the struct tail AND the feature gate is
consistent between Rust and C compilation.

**Rationale**: Feature-gated fields (e.g., `#[cfg(feature = "streaming")]`)
are conditionally compiled. If the same feature flag state is not maintained
on both the Rust and C sides, the two sides would disagree on struct size and
field offsets — a silent ABI mismatch leading to memory corruption.

**Mechanism — cbindgen `[defines]` mapping**:

The `cbindgen.toml` `[defines]` section maps Cargo feature flags to C
preprocessor symbols:

```toml
[defines]
"feature = incremental" = "MARKDOWN_INCREMENTAL_ENABLED"
"feature = streaming" = "MARKDOWN_STREAMING_ENABLED"
```

When cbindgen generates the C header, feature-gated Rust fields become
`#ifdef`-guarded C fields:

```c
typedef struct MarkdownOptions {
    /* ... common fields ... */
#ifdef MARKDOWN_STREAMING_ENABLED
    uint32_t streaming_chunk_size;   /* only present when streaming enabled */
#endif
} MarkdownOptions;
```

**Guarantees**:
- The C module is compiled with the same feature defines as the Rust library.
- Feature-gated fields are always at the struct tail (combining with
  Assumption 1).
- Enabling or disabling a feature changes `sizeof` consistently on both sides.
- No "layout hole" exists between common fields and feature-gated fields.

**Enforcement**:
- CI builds both Rust and C with identical feature flag sets.
- The `make check-headers` target verifies the generated header (with all
  features expanded) matches the checked-in copy.
- Layout tests run with all features enabled to verify the maximum struct size.

---

## Assumption 4: No Field Removal

**Rule**: Fields are never removed from FFI structs. Deprecated fields are
retained in place, zeroed by init helpers, and documented as deprecated.

**Rationale**: Removing a field shifts all subsequent field offsets (same
problem as mid-insertion). Even if both sides are recompiled simultaneously,
any third-party code or cached shared library linked against the old layout
would silently corrupt memory.

**Deprecation protocol**:
1. Mark the field with a `/* DEPRECATED since vX.Y.Z */` comment in the C
   header. When the field is read on the Rust side, add a Rust doc comment
   documenting the deprecation; the generated C header comment (via cbindgen)
   carries the deprecation note to C consumers.
2. The `markdown_*_init()` helper sets the deprecated field to zero/null.
3. The C consumer ignores the field (reads are no-ops, writes are discarded).
4. The field remains at its original offset indefinitely.
5. After two major versions, the field name may be renamed to `_reserved_N`
   but its offset and size must not change.

> **Note on `#[deprecated]`**: The example below uses `#[deprecated]` to
> illustrate the *intended future protocol*. As of the 0.8.x release line no
> FFI struct field in `components/rust-converter/src/ffi/abi.rs` carries a
> `#[deprecated]` attribute. The normative requirement today is: a
> deprecation comment in the C header, init-helper zeroing, layout tests
> pinning the field's offset/size, and C consumer no-op behavior. Adopting
> `#[deprecated]` on Rust FFI fields is a future hardening candidate tracked
> in `docs/project/0.9.0-migration-tracking.md`, not a current ABI property.

**Example** (illustrative — not a current struct definition):

```rust
#[repr(C)]
pub struct MarkdownResult {
    pub output: *const u8,
    pub output_len: usize,
    // Hypothetical future deprecation showing the intended protocol.
    // Today `MarkdownResult` does not contain a `legacy_error_code` field.
    #[deprecated(since = "0.8.0", note = "Use error_category instead")]
    pub legacy_error_code: u32,   // offset preserved, always zeroed by init
    pub error_category: u32,      // appended after legacy field
}
```

---

## Assumption 5: Alignment Guarantees

**Rule**: All FFI structs maintain natural alignment. On LP64 platforms
(Linux x86_64, macOS x86_64, Linux aarch64), the maximum field alignment is
8 bytes (`alignof(uintptr_t) == alignof(size_t) == 8`), so struct alignment
is 8 bytes.

**Platform ABI details**:

| Platform | Pointer size | `size_t` | `uintptr_t` | Max natural align | Struct align |
|----------|-------------|----------|-------------|-------------------|-------------|
| Linux x86_64 (LP64) | 8 | 8 | 8 | 8 | 8 |
| macOS x86_64 (LP64) | 8 | 8 | 8 | 8 | 8 |
| Linux aarch64 (LP64) | 8 | 8 | 8 | 8 | 8 |
| Linux x86 (ILP32) | 4 | 4 | 4 | 4 | 4 |

**Guarantees**:
- No FFI struct uses `#[repr(packed)]` (which would violate alignment).
- No FFI struct uses `#[repr(align(N))]` with N > 8 (which would introduce
  platform-specific over-alignment).
- All pointer and `usize`/`isize` fields are naturally aligned (offset is a
  multiple of 8 on LP64).
- Smaller fields (`u8`, `u16`, `u32`) are naturally aligned to their own size.
- Padding bytes between fields follow C ABI rules and are zeroed by init
  helpers.

**Enforcement**:
- Rust layout tests verify `align_of::<T>()` equals the expected platform
  alignment for each FFI struct.
- C `static_assert` checks verify `_Alignof(T)` matches.
- CI runs on both x86_64 and aarch64 targets to catch alignment divergence.

---

## Header Synchronization

Two copies of `markdown_converter.h` exist:
1. `components/rust-converter/include/markdown_converter.h` — cbindgen output
2. `components/nginx-module/src/markdown_converter.h` — checked-in C module copy

The `make check-headers` target and CI `header-sync` job verify these are
byte-identical. Any divergence is a CI failure.

---

## Versioning

The ABI is versioned by the Cargo.toml package version. Breaking ABI changes
(field insertion, removal, reordering, alignment change) require a major
version bump. v0.7.0 is ABI-compatible with v0.6.x: all new fields were
appended to struct tails or placed in new structs.

---

## Summary of Invariants

| # | Invariant | Violation consequence | Detection |
|---|-----------|----------------------|-----------|
| 1 | Tail-append only | Offset shift → memory corruption | Layout tests + review |
| 2 | `repr(C)` on all FFI structs | Non-deterministic layout | cbindgen + CI |
| 3 | Feature gates consistent (cbindgen `[defines]`) | Size mismatch → corruption | `check-headers` + CI |
| 4 | No field removal | Offset shift → memory corruption | Layout tests + review |
| 5 | 8-byte alignment on LP64 | Misaligned access → UB/SIGBUS | Layout tests + CI |

---

## References

- `docs/architecture/FFI_MIGRATION_CONTRACT.md` — FFI function/struct registry
  and migration status
- `AGENTS.md` Rule 15 — FFI cross-language boundary requirements
- `components/rust-converter/cbindgen.toml` — cbindgen configuration and
  `[defines]` mapping
- `components/rust-converter/src/ffi/abi.rs` — Rust layout tests
- Design document §2.3 — Boundary rules
- Design document §6 — FFI synchronization strategy

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial ABI compatibility assumptions document |
| 0.7.0-a07.7 | 2026-05-18 | kiro | Expanded: 5 formal assumptions (tail-append, repr(C), feature-gate no-drift, no-removal, alignment), enforcement details, examples, invariant summary table |
