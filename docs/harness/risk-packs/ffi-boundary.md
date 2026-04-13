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

## Minimum Verification

```bash
make harness-check
make build
make test-rust
```

## Canonical References

- [../../architecture/REPOSITORY_STRUCTURE.md](../../architecture/REPOSITORY_STRUCTURE.md)
- [../../testing/README.md](../../testing/README.md)
- [../../../AGENTS.md](../../../AGENTS.md)
