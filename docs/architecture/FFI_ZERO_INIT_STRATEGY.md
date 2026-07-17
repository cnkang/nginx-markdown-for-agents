# FFI Zero/Default Initialization Strategy

> **Note**: This document originated in v0.7.0. The zero-initialization
> policy remains in effect for v0.9.1; however, the FFI struct inventory
> has evolved. See [FFI_MIGRATION_CONTRACT.md](FFI_MIGRATION_CONTRACT.md)
> for the current v0.9.1 FFI boundary.

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
| `FFIHeaderPlan` | `handle=NULL, entries=NULL, count=0` | New in v0.7.0; must be released with `markdown_header_plan_free` after successful build |
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
| 0.7.0-impl | 2026-05-18 | codex | Add FFIHeaderPlan zero-init and free lifecycle rule |
