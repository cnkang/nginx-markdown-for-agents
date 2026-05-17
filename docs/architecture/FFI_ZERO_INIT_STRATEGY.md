# FFI Zero/Default Initialization Strategy (v0.7.0)

## Policy

All `#[repr(C)]` FFI structs must be zero-initialized before population.
This ensures that:

1. Every field has a defined value (no uninitialized memory across FFI)
2. Pointer fields are NULL (safe to call `free` on)
3. Length fields are 0 (safe for C code to check before dereference)

## Implementation

### Rust Side

Before calling any FFI function that populates a result struct, the C
caller must zero-initialize the struct:

```c
struct MarkdownResult result;
ngx_memzero(&result, sizeof(struct MarkdownResult));
```

The Rust FFI implementation assumes the result struct is zero-initialized
on entry. It writes success or error fields, leaving unused pointer fields
as NULL (which `markdown_result_free` handles safely).

### C Side

NGINX's `ngx_pcalloc` provides zero-initialized pool allocation. All
context structures allocated from the request pool are zero-initialized
by default. Stack-allocated FFI structs must use `ngx_memzero` explicitly.

### New Structs in v0.7.0

| Struct | Zero-init Fields | Notes |
|--------|-----------------|-------|
| `MarkdownResult` | All pointer/length fields NULL/0 | Existing; v0.7.0 adds no new fields (tail-append only in future) |
| `FFIAcceptResult` | `should_convert=0, reason=0` | New in v0.7.0; 2 bytes total |
| `MarkdownOptions` | All pointer/length fields NULL/0 | Existing; v0.7.0 adds no breaking changes |

## Safety Invariant

After FFI call returns, the C code must check `error_code` before
dereferencing any pointer field in `MarkdownResult`. On error (non-zero
`error_code`), only `error_message` and `error_len` are valid; all other
pointer fields may be NULL.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial zero/default initialization strategy document |
