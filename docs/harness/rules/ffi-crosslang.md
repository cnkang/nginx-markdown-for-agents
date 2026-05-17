---
domain: ffi-crosslang
rules: [15]
paths:
  - "components/rust-converter/src/**"
  - "components/rust-converter/include/**"
  - "components/nginx-module/src/**"
---

# FFI & Cross-Language Interface

### 15. Cross-language interface and FFI synchronization
Historical issues: `dbb5722`, `dfeffc4`, `ceeaf38`, `5970807`.

Required:
- When Rust FFI structs, options, defaults, or error codes change, update all affected boundaries in the same change set: Rust ABI/options code, public C headers, NGINX call sites, tests, and operator-facing scripts.
- Treat FFI comments and header docs as part of the interface contract; stale interface docs are a bug, not a cleanup item.
- Add at least one boundary-level test when introducing or changing an FFI option/error path (for example header-level, feature-independence, or end-to-end verification).
- **When adding a field to any FFI struct (for example `MarkdownResult`, `MarkdownOptions`, `StreamingStats`), apply this complete-sync checklist:**
  1. **Rust side**: the field is added to the `#[repr(C)]` struct with correct type and documentation.
  2. **Public C headers**: both `components/rust-converter/include/` and `components/nginx-module/src/` copies of `markdown_converter.h` are updated with the field and contract comments (semantics, lifecycle, when valid).
  3. **All initialization sites**: grep for `StructName {` across all test files (`tests/`), examples (`examples/`), and production code. Every struct literal must include the new field. **Do not rely on `..Default::default()` or partial initialization** — FFI structs crossing language boundaries must be explicitly fully initialized.
  4. **Cleanup/reset functions**: if the struct has associated `reset_*()`, `free_*()`, or cleanup helpers, they must zero/reset the new field to maintain API symmetry.
  5. **Test helper constructors**: any `empty_result()`, `zeroed_result()`, or similar test helper must include the new field.
- **Prefer helper functions over literal initialization for FFI structs**: When writing test code that needs a zeroed/empty FFI struct, **always use existing helper functions** (for example `empty_result()`, `zeroed_result()`, `ffi_test_empty_result()`) rather than writing `StructName { field: value, ... }` literals. This reduces future maintenance cost when the struct gains new fields — only the helper needs updating, not every call site. If no helper exists for a struct, create one before writing multiple literal initializations.
- **Lifecycle ordering rule: read data from foreign-owned structs BEFORE calling their associated free/release function.** Do not access fields after `markdown_result_free()`, `markdown_converter_free()`, or similar cleanup calls — the memory may be invalidated, zeroed, or returned to the allocator. Capture values to local variables first, then free.
- In C unit tests that stub FFI lifecycle helpers (for example
  `markdown_result_free()`), clear **all** struct fields covered by the public
  ABI (pointer, length, and numeric/error fields). Partial clears create false
  confidence and can mask stale-state regressions.
- When C code classifies FFI error codes into semantic categories, the
  classification branch must cover **all** codes defined in the FFI header
  that map to that category.  Do not assume a 1:1 mapping between C-local
  codes and FFI codes — the other language may define multiple distinct
  codes for the same semantic (for example both a general memory-limit code
  and a streaming-specific budget code).  Before writing a classification
  branch, grep the FFI header for all related `#define` constants and
  confirm the branch covers the full set.  This applies equally to error
  codes, feature flags, and enum values that cross the FFI boundary.
