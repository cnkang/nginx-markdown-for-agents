# FFI Boundary Pack

Use this as the primary pack when Rust/C structs, headers, defaults, option
plumbing, or error-code classification change.

## Triggers

- touched files under `components/rust-converter/src/ffi*`
- touched files under `components/rust-converter/include/**`
- touched files under `components/nginx-module/src/markdown_converter.h`
- keywords like `repr(C)`, `markdown_converter.h`, `error code`, `struct field`

## Common Supporting Packs

- `observability-metrics` when a new field or error path becomes operator-visible

## Sync Points

- Rust struct field changes vs generated/public header copies
- FFI option defaults vs NGINX call sites
- test helper constructors vs new struct fields
- operator docs vs changed enum or error naming
- initialization of all FFI output fields before transferring ownership or
  consuming handles, so panic/error paths cannot expose stale values
- fat-pointer safety when transferring slice/Vec ownership to C (Rule 53):
  use `as_mut_ptr` + `mem::forget`, never `Box::into_raw` for slices.
  Vec transfers must pass pointer, length, and original capacity back together
  and reconstruct with `Vec::from_raw_parts`. Do NOT `shrink_to_fit` before
  transfer. The documented streaming output ABI is the deliberate exception:
  it transfers a `Box<[u8]>`, whose allocation has the exact invariant
  `len == capacity`, so `(data, len)` is sufficient for
  `markdown_streaming_output_free`. Do not apply the Vec rule to that exact
  capacity path without bumping the ABI and all consumers.
- Empty results return NULL instead of zero-length allocations (Rule 53).

## Minimum Verification

```bash
make harness-check
make build
make test-rust
make test-nginx-unit
```

## Canonical References

- [../../architecture/REPOSITORY_STRUCTURE.md](../../architecture/REPOSITORY_STRUCTURE.md)
- [../../testing/README.md](../../testing/README.md)
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Ownership guidance now requires passing the original Vec capacity back through the free path with `Vec::from_raw_parts`; shrink_to_fit before transfer is prohibited |
| 0.8.3 | 2026-06-26 | Kang | No changes; version alignment with 0.8.3 release |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
