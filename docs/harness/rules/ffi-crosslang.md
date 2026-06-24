---
domain: ffi-crosslang
rules: [15, 46]
paths:
  - "components/rust-converter/src/**"
  - "components/rust-converter/include/**"
  - "components/nginx-module/src/**"
---

## FFI & Cross-Language Interface

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
- **Panic safety across the FFI boundary**: All non-trivial Rust FFI export
  functions (any `#[no_mangle] pub extern "C"` that performs allocation,
  parsing, or resource management) must wrap their body in
  `std::panic::catch_unwind`.  A Rust panic that unwinds across the FFI
  boundary is undefined behavior in C.  On caught panic, return the
  appropriate error code (for example `ERROR_INTERNAL` or equivalent) —
  never abort or unwind into C.  Trivial functions that only read a
  constant or return a fixed value may omit the wrapper with a comment
  documenting why.
- **Fail-closed fallback initialization**: Before calling `catch_unwind`,
  initialize any FFI output struct (for example `FFIDecisionResult`,
  `MarkdownResult`) to a safe fail-closed default value.  If the closure
  panics after partially writing struct fields, the caller receives a
  defined, safe state instead of stale or uninitialized bytes.  For
  decision results, the fail-closed default is `decision=1` (skip) with
  `reason_code=FfiCallError`.  For URL validation, the fail-closed
  default is `0` (validation failed = safe reject) or `1` (dangerous =
  safe default), depending on the function's safe fallback.
- **Atomic write after catch**: The `catch_unwind` closure should compute
  return values as local variables, NOT directly write to the FFI output
  struct's fields.  After `catch_unwind` returns `Ok`, write all fields
  atomically.  This prevents a panic from leaving the output struct in a
  partially-written, inconsistent state.  Example pattern:
  ```rust
  result_ref.decision = 1; // fail-closed default
  result_ref.reason_code = ReasonCode::FfiCallError.discriminant() as u8;
  let outcome = catch_unwind(|| -> (u8, u8) { /* compute only */ });
  if let Ok((d, r)) = outcome {
      result_ref.decision = d;     // atomic write after success
      result_ref.reason_code = r;
  }
  ```
- **FFI resource ownership and Drop guards**: When an FFI export allocates
  or holds a resource (for example `HeaderPlan`, `MarkdownConverterHandle`)
  that must be released by a matching `*_free()` or `safe_finish()` call,
  the resource must be managed by a `Drop` guard or centralized reset
  within the `catch_unwind` closure.  If the function panics after
  acquiring the resource, the Drop guard must release it so the caller
  does not leak or double-free.  Do not rely on the C caller to clean up
  after a failed FFI call — the C side may not know which resources were
  acquired before the failure.
- **FFI enum layout safety**: All enums that cross the FFI boundary must
  use an explicit `#[repr(u8)]`, `#[repr(i32)]`, or similar fixed-width
  representation.  Rust's default enum layout is implementation-defined
  and can change between compiler versions, causing silent discriminant
  truncation when C reads the wrong width.  When a new enum is exposed via
  FFI, add a compile-time assertion (`const _: () = assert!(size_of::<E>()
  == expected);`) or a debug guard that verifies the discriminant fits the
  expected width.
- **FFI handle consumption contract**: When an FFI handle (for example
  `MarkdownConverterHandle`, `HeaderPlanHandle`) is passed to a
  consumption function (for example `safe_finish`, `markdown_result_free`),
  the handle is **consumed** and must not be used again by the caller.
  After consumption, the underlying memory may be freed, zeroed, or
  returned to the allocator.  The C caller must not retain any pointer
  derived from the handle (for example a pointer obtained via
  `markdown_converter_get_result()`) across the consumption call — capture
  needed data into local variables first, then consume the handle.  When
  migrating an FFI interface from borrowed pointers to an opaque handle
  model, update all C-side call sites in the same changeset: any code that
  held a raw pointer into Rust-owned memory must switch to copying data
  out before the consumption call.

Verification:
- `grep -rn 'catch_unwind' components/rust-converter/src/` — verify
  non-trivial FFI exports are wrapped.
- `grep -rnE '#\[no_mangle\]|#\[unsafe\(no_mangle\)\]' components/rust-converter/src/`
  — every hit should either be inside a `catch_unwind` or have a comment
  explaining why it is trivial enough to skip.  Both the legacy
  `#[no_mangle]` and the Rust 2024 `#[unsafe(no_mangle)]` forms must be
  recognised; the detector `detect_ffi_panic_safety.sh` matches both.
- `bash tools/harness/detect_ffi_panic_safety.sh --strict` — classifies
  every FFI export into direct_catch / delegated_catch /
  safe_init_helper / safe_static_lookup / free_helper /
  unsafe_business_logic / unknown and fails (exit 1) on
  unsafe_business_logic, unknown, or free_helper_without_catch.  The
  Makefile `harness-security-checks` target runs this in strict mode.
- `grep -rn '#\[repr(' components/rust-converter/src/ | grep -i 'enum\|reason\|error_code'`
  — verify FFI-exposed enums have explicit repr.
- `grep -rn 'safe_finish\|markdown_result_free\|markdown_converter_free' components/nginx-module/src/`
  — verify no pointer derived from the handle is used after the consumption call.
- `make check-headers` — verify both C header copies are in sync.
- **FFI header drift CI gate**: When C headers are generated by `cbindgen`,
  a CI step must regenerate the header from Rust source and compare it
  against the checked-in copy.  If they differ, the CI step must fail
  with "FFI header drift detected — run `make copy-headers` and commit
  the result."  This prevents the recurring pattern of Rust source
  changes that forget to regenerate the C header.  The `cbindgen` version
  used in CI must be pinned to the same version across all workflows
  (for example `cbindgen 0.29.2`) to avoid version-dependent output
  differences.

---

### 46. FFI operation NULL and empty-input boundary guards
Historical issues: 0f2247a7.

Required:
- Every FFI operation that accepts a key, name, or identifier parameter
  must validate that the parameter is not NULL and not zero-length before
  processing.  This applies to all operation types including collection
  mutation operations (for example delete-all, clear, reset) where a NULL
  or empty key would be ambiguous or cause undefined behavior on the other
  side of the FFI boundary.
- When adding a new FFI operation (for example `DeleteAll`, `Clear`,
  `Reset`), verify the C-side handler explicitly checks for NULL data
  pointer and zero length on all string/buffer inputs before passing them
  to Rust.  A NULL `ngx_str_t.data` with non-zero `len` is undefined
  behavior; a zero `len` with non-NULL `data` is semantically empty.
- On the Rust side, FFI entry points that receive raw pointers must
  validate them before dereferencing.  Use `if key.is_null() || key_len
  == 0` guard patterns.  Do not rely on the caller to have already
  validated — the FFI boundary is the trust boundary.
- When a new FFI operation is added, the test suite must include at least
  one test for the NULL/empty input case on each side of the boundary.
  This prevents regressions if a later refactor removes the guard.
- **Zero-length value boundary**: When an FFI operation receives a
  value with `value_len == 0` (but `value != NULL`), the C-side handler
  must explicitly set the target field to `NULL`/`0` rather than
  allocating a zero-length buffer via `ngx_pnalloc(pool, 0)`.  Zero-
  length pool allocations have implementation-defined behavior (may
  return NULL or a non-NULL pointer) and the resulting `ngx_str_t` would
  have `len=0` but a dangling or NULL `data` pointer.  The correct
  pattern is:
  ```c
  if (entry->value_len == 0) {
      h->value.data = NULL;
      h->value.len = 0;
      return NGX_OK;
  }
  ```
  This applies to all FFI-to-C value copy paths, including header plan
  operations (SET, MODIFY) and any new operation that copies FFI string
  data into NGINX pool-owned structures.

Verification:
- `grep -rn 'op_type\|operation_type' components/rust-converter/src/ffi/`
  — verify each FFI entry point validates key/name inputs.
- `grep -rn 'key\.data.*==.*NULL\|key_len.*==.*0\|\.is_null()' components/nginx-module/src/ components/rust-converter/src/`
  — verify guards exist on both sides of the FFI boundary.
- `make test-rust` and `make test-nginx-unit` — NULL/empty-input FFI
  tests must pass.
