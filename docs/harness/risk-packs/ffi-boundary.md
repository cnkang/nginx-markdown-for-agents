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
  use `as_mut_ptr` + `mem::forget`, never `Box::into_raw` for slices;
  empty buffers return NULL instead of zero-length allocations

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
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.8.3 | 2026-06-26 | Kang | No changes; version alignment with 0.8.3 release |
