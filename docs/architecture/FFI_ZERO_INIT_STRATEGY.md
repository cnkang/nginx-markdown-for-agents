# FFI Zero/Default Initialization Strategy

> **Note**: This document originated in v0.7.0. Its zero/default
> initialization safety goals remain in effect for v0.9.2, however, the FFI struct inventory
> has evolved. See [FFI_MIGRATION_CONTRACT.md](FFI_MIGRATION_CONTRACT.md)
> for the current v0.9.2 FFI boundary.

## Policy

Every `#[repr(C)]` FFI struct needs its matching production helper when one
exists. Zero-initialization is only a fallback for
a struct with no semantic defaults and no initializer. This ensures that:

1. Every field has a defined value (no uninitialized memory across FFI)
2. Pointer fields are NULL (safe to call `free` on)
3. Length fields are 0 (safe for C code to check before dereference)

## Implementation

### Rust Side

Before calling any FFI function that populates a result struct, the C
caller must use the matching production initializer:

```c
struct MarkdownResult result;
markdown_result_init(&result);
```

The Rust FFI implementation assumes the result struct has the semantic
defaults established by that initializer. It writes success or error fields,
leaving unused pointer fields as NULL (which `markdown_result_free` handles
safely).

### C Side

NGINX's `ngx_pcalloc` provides zero-initialized pool allocation, but that does
not replace an initializer when a shared struct has non-zero semantic
defaults. Stack-allocated FFI structs must call the matching initializer.
`ngx_memzero` is appropriate only for a documented zero-default struct.

### New Structs in v0.7.0

| Struct | Zero-init Fields | Notes |
|--------|-----------------|-------|
| `MarkdownResult` | `markdown_result_init` sets pointer/length fields NULL/0 | Existing; tail-append only in future |
| `FFIAcceptResult` | `should_convert=0, reason=0` | Use the owning decision initializer |
| `FFIHeaderPlan` | `markdown_header_plan_init` sets `handle=NULL`, `entries=NULL`, `count=0` | Must be released with `markdown_header_plan_free` after successful build |
| `MarkdownOptions` | `markdown_options_init` applies semantic defaults as well as NULL/0 fields | Do not replace it with a blind memset |

## Safety Invariant

After FFI call returns, the C code must check `error_code` before
dereferencing any pointer field in `MarkdownResult`. On error (non-zero
`error_code`), only `error_message` and `error_len` are valid, all other
pointer fields may be NULL.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-08 | Kang | Updated version references to v0.9.2 |
| 0.7.0 | 2026-05-17 | Kang | Initial zero/default initialization strategy document |
| 0.7.0-impl | 2026-05-18 | codex | Add FFIHeaderPlan zero-init and free lifecycle rule |
